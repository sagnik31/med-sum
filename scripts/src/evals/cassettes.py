"""
Recorded model replies (cassettes), so an eval can run the real graph
without Ollama.

A recording client wraps the Ollama client during a live eval and stores
every reply with what produced it; a replay client stands in for Ollama
and answers each request from the recording. Both sit where llm.py keeps
its client (`llm._client`), so everything above them (the breaker, the
model call rows, the graph) runs as it does live.

Each reply is keyed by the model tag and a hash of the whole request: the
messages (a page image by its SHA-256), the generation options and the
output schema. So a changed prompt, a changed page, a changed option or a
changed schema is a miss, and a miss is an error (`CassetteMiss`) that says
which part differs from the nearest recording and to re-record, which is a
live run. Each entry also keeps the model digest it was recorded with, the
prompt hash (the system and instruction text) and the input hash (pages
and document text), so a replay can be refused when the installed model is
not the one recorded (`expect_digests`).

Cassettes hold reply text, which for the synthetic dataset is synthetic
patient content; never record a real document.
"""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

CASSETTE_VERSION = 1

# The counts and timings Ollama puts on a reply's final chunk.
DONE_FIELDS = (
    "prompt_eval_count",
    "eval_count",
    "total_duration",
    "load_duration",
    "prompt_eval_duration",
    "eval_duration",
)


class CassetteMiss(RuntimeError):
    """A request with no recording: re-record with a live run."""


def _sha(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _image(image) -> str:
    if isinstance(image, bytes | bytearray):
        return "sha256:" + _sha(bytes(image))
    return str(image)


def _message(m: dict) -> dict:
    out = {k: v for k, v in m.items() if k != "images"}
    if m.get("images"):
        out["images"] = [_image(i) for i in m["images"]]
    return out


@dataclass(frozen=True)
class RequestKey:
    model: str
    request: str  # the whole request
    prompt: str  # system and instruction text
    input: str  # page images and the document's own text
    options: str  # generation options and output schema


def request_key(*, model, messages, options, format=None) -> RequestKey:
    """
    The hashes of one chat request. The prompt is the text of the system
    messages and of a user message's text when it carries no image; the
    input is every image and every other message. A repair's findings and
    an earlier reply count as input.
    """
    msgs = [_message(m) for m in messages]
    prompt = [m["content"] for m in msgs if m.get("role") == "system"]
    inputs = [m for m in msgs if m.get("role") != "system"]
    opts = {"options": options, "format": format}
    return RequestKey(
        model=model,
        request=_sha(_canonical({"model": model, "messages": msgs, **opts})),
        prompt=_sha(_canonical(prompt)),
        input=_sha(_canonical(inputs)),
        options=_sha(_canonical(opts)),
    )


@dataclass
class Entry:
    model: str
    digest: str | None
    request: str
    prompt: str
    input: str
    options: str
    reply: str
    done_reason: str | None
    done: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Cassette:
    """The recorded replies of one eval run of one document (or more)."""

    entries: list[Entry] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Cassette":
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != CASSETTE_VERSION:
            raise ValueError(f"{path}: cassette version {data.get('version')!r}")
        return cls([Entry(**e) for e in data["entries"]])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": CASSETTE_VERSION,
            "entries": [e.as_dict() for e in self.entries],
        }
        path.write_text(
            json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def digests(self) -> dict[str, str | None]:
        return {e.model: e.digest for e in self.entries}


def _chunks(reply: str, done_reason: str | None, done: dict) -> Iterator:
    """The reply as a stream of one content chunk and a final chunk."""
    yield SimpleNamespace(
        message=SimpleNamespace(content=reply), done=False, done_reason=None
    )
    yield SimpleNamespace(
        message=SimpleNamespace(content=""),
        done=True,
        done_reason=done_reason,
        **done,
    )


def _models(digests: dict[str, str | None]):
    return SimpleNamespace(
        models=[SimpleNamespace(model=m, digest=d) for m, d in digests.items()]
    )


class RecordingClient:
    """Passes every call to the real client and records the reply."""

    def __init__(self, inner, cassette: Cassette):
        self.inner = inner
        self.cassette = cassette
        self._digests: dict[str, str | None] = {}

    def list(self):
        return self.inner.list()

    def digest(self, model: str) -> str | None:
        if model not in self._digests:
            listed = {m.model: m.digest for m in self.inner.list().models}
            self._digests[model] = listed.get(model)
        return self._digests[model]

    def chat(self, *, model, messages, stream, think, options, format=None):
        key = request_key(
            model=model, messages=messages, options=options, format=format
        )
        extra = {"format": format} if format is not None else {}
        reply = ""
        for chunk in self.inner.chat(
            model=model,
            messages=messages,
            stream=stream,
            think=think,
            options=options,
            **extra,
        ):
            reply += chunk.message.content or ""
            if chunk.done:
                self.cassette.entries.append(
                    Entry(
                        model=model,
                        digest=self.digest(model),
                        request=key.request,
                        prompt=key.prompt,
                        input=key.input,
                        options=key.options,
                        reply=reply,
                        done_reason=chunk.done_reason,
                        done={
                            f: getattr(chunk, f)
                            for f in DONE_FIELDS
                            if getattr(chunk, f, None) is not None
                        },
                    )
                )
            yield chunk


class ReplayClient:
    """
    Answers from a cassette. The same request made twice gets the recorded
    replies in order. `expect_digests` (model -> digest) refuses entries
    recorded with another model build.
    """

    def __init__(self, cassette: Cassette, *, expect_digests: dict | None = None):
        self.cassette = cassette
        self.expect_digests = expect_digests or {}
        self._used: dict[str, int] = {}

    def list(self):
        return _models(self.cassette.digests())

    def chat(self, *, model, messages, stream, think, options, format=None):
        key = request_key(
            model=model, messages=messages, options=options, format=format
        )
        matches = [
            e
            for e in self.cassette.entries
            if e.model == model and e.request == key.request
        ]
        n = self._used.get(key.request, 0)
        if n >= len(matches):
            raise CassetteMiss(self._why(key, bool(matches)))
        entry = matches[n]
        expected = self.expect_digests.get(model)
        if expected is not None and entry.digest != expected:
            raise CassetteMiss(
                f"{model}: recorded with digest {entry.digest}, installed "
                f"{expected}; re-record with a live run"
            )
        self._used[key.request] = n + 1
        return _chunks(entry.reply, entry.done_reason, entry.done)

    def _why(self, key: RequestKey, seen: bool) -> str:
        if seen:
            return f"{key.model}: the request was made more often than recorded"
        same_model = [e for e in self.cassette.entries if e.model == key.model]
        if not same_model:
            return f"{key.model}: nothing recorded for this model; re-record"
        if any(e.input == key.input and e.prompt != key.prompt for e in same_model):
            what = "the prompt changed"
        elif any(e.prompt == key.prompt and e.input != key.input for e in same_model):
            what = "the input changed (page, document text or repair findings)"
        elif any(e.prompt == key.prompt and e.input == key.input for e in same_model):
            what = "the options or output schema changed"
        else:
            what = "no recorded request matches"
        return f"{key.model}: {what} since the recording; re-record with a live run"
