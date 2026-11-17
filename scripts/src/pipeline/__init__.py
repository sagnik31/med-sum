"""
The document pipeline as a LangGraph graph: classify, transcribe, parse,
validate, repair and render, with every node recorded as a step of its
run (observability) and the graph's state checkpointed in Postgres.

Local only: LangSmith (LangChain's cloud tracing) is switched off here,
whatever the environment says, so no run is ever sent anywhere. Runs are
traced in the database instead (observability).
"""

import os

# LangSmith reads each setting under two prefixes, LANGSMITH_ first, and
# TRACING_V2 before TRACING, so every one of the four names is set: one left
# unset or "true" would turn tracing back on. The key and endpoints are
# removed too, so nothing could be sent even if a later library version
# read another switch. See docs/adr/0006-durable-traceable-runs.md.
_PREFIXES = ("LANGSMITH", "LANGCHAIN")
for _prefix in _PREFIXES:
    for _name in ("TRACING_V2", "TRACING"):
        os.environ[f"{_prefix}_{_name}"] = "false"
    for _name in ("API_KEY", "ENDPOINT", "RUNS_ENDPOINTS"):
        os.environ.pop(f"{_prefix}_{_name}", None)
