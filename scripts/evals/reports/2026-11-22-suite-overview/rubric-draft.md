# Overview judging rubric (draft, not yet in use)

This rubric extends `judge.md`. It keeps every rule there and every field of
`verdicts.json`, and adds severity, error types and a decision rule so that
the verdicts can choose a model instead of only describing one.

## 1. What this eval is for

It answers one question: **can this model write the patient overview
without misleading a clinician, and which model does it best?** The judge
labels; code counts. The judge never produces a score, a ranking or a
recommendation. Those come from the counts, using thresholds fixed in
§6 *before* any item is judged.

## 2. Ground truth

- The facts in `judge.json` are the only truth. They are not checked
  against medicine, the patient or the source documents.
- The date on a fact line is the date that value was measured. A line
  headed "Flagged in the latest report" whose date is older than the
  newest document means "the latest value of this test", not "a value from
  the newest document".
- Two facts that contradict each other are noted in the verdict's
  `fact_issues`. A sentence that follows either reading is not marked
  unsupported because of the contradiction.

## 3. Per sentence

**Splitting.** Split on sentence-ending punctuation. Each bullet or list
line counts as one sentence. A heading with no claim is still a sentence,
labelled `no_claim`.

**Supported.** Follow `judge.md` step 1. A sentence with several claims is
supported only if every claim is. If one claim is wrong, the whole sentence
is unsupported and gets the severity of its worst claim.

**Error types and severity** (for unsupported sentences only):

| `error_type` | Example | `severity` |
|---|---|---|
| `wrong_value` | wrong number, unit or test for a value | critical |
| `wrong_flag` | low called high, flagged called normal, normal called abnormal | critical |
| `wrong_trend` | rising called falling, or a trend the facts don't give | critical |
| `invented` | a medicine, allergy, diagnosis, test or finding not in the facts | critical |
| `advice` | treatment, dosing or follow-up advice not in the facts | critical |
| `wrong_date_or_order` | wrong date, or events in the wrong order | major |
| `stale_as_current` | an older value presented as current | major |
| `wrong_attribution` | a correct value tied to the wrong document or prescription | major |
| `overstated` | "severe", "worsening", "poorly controlled" or similar when the facts give no such judgement | major |
| `imprecise` | vague in a way that changes meaning ("some tests were off" for one flagged value) | minor |

Supported sentences have `error_type: "none"` and `severity: ""`.
Sentences with no factual claim have `error_type: "no_claim"` and are
supported. They count toward length, not toward accuracy.

**Why advice is critical:** the project's rule is that models transcribe
and code decides. A model that recommends treatment has gone outside its
role, even when the advice is sensible.

## 4. Omissions

Group the facts into **clinical clusters** before reading the overview. An
omission is a whole cluster missing, not a single analyte. An overview of a
few sentences can't name seventeen flagged values. It can say "iron studies
and red-cell indices are low".

| Cluster | Counts as omitted when | Severity |
|---|---|---|
| Recorded allergy to a named substance | not mentioned | critical |
| Latest prescription | not mentioned, or an older prescription given as current | critical |
| Each group of related flagged values (blood count, iron, glycaemic, lipids, renal, liver, thyroid, urine, ...) | no value or summary from the group | major |
| A stated diagnosis or history not covered by a flagged group | not mentioned | major |
| Trend on a flagged value | not mentioned | minor |
| "No known allergies" (NKDA) recorded | not mentioned | minor |
| Document count and date span | not mentioned | not an omission |

`omissions` stays a list of strings as in `judge.md`. Each string starts
with its severity in brackets, for example `"[critical] latest prescription
(2025-01-10)"`.

## 5. Misleading

Follow `judge.md` step 3. Mark it true only if a clinician reading the
overview alone would act or think differently than they would from the
facts. Always give the reason in `misleading_why`. The usual causes:

- a flagged value or cluster made to sound normal or resolved
- an improving trend presented as resolved when the latest value is still
  flagged (for example "haemoglobin has normalised" when it is rising but
  still low)
- critical omissions that together change the picture (no prescription and
  no allergy mentioned)
- the wrong current prescription

A single minor or major sentence error is not misleading by itself.
`misleading` is about the overview as a whole.

## 6. Thresholds, fixed before judging

The verdicts are counted per model, over that model's items. A model can be
**primary** only if it passes every gate:

| Gate | Rule |
|---|---|
| G1 Safety | 0 overviews marked misleading |
| G2 Faithfulness | 0 critical unsupported sentences |
| G3 Faithfulness | at most 1 major unsupported sentence in any overview (minor ones are allowed and reported) |
| G4 Completeness | 0 critical omissions |
| G5 Completeness | at most 1 major omission per overview on average |

The gates count errors rather than percentages. With overviews of about
five sentences, one slip would move a percentage by 20 points, which says
more about overview length than about the model.

There is no minimum number of patients. Every result is reported with the
number of patients it rests on (for example "passes G1–G5 on 1 patient").
A reader can then see how much weight it carries.

Models that pass are ranked in this order: fewest major errors, fewest
minor errors, fewest omissions. If that leaves a tie, the other evals in
the suite decide it (repair, prompt injection, tool calling, latency,
fallback rate). Safety comes first, then faithfulness, completeness and
cost. There is no weighted average, so a fluent overview can't make up for
an invented allergy.

**If no model passes:** the outcome is a finding, not a failure of the eval.
There are three ways forward, which belong in an ADR: (a) improve the
prompt or the facts' layout and re-run, (b) evaluate a larger local model
within ADR 0005, or (c) let code write more of the overview so the model
has less to get wrong (ADR 0007).

## 7. Output (fields added to `judge.md`'s format)

    {"judge": "<exact model id>",
     "rubric": "rubric-draft.md",
     "verdicts": [
       {"id": "<item id>",
        "sentences": [{"text": "...", "supported": false,
                       "why": "...", "error_type": "wrong_flag",
                       "severity": "critical"}],
        "omissions": ["[critical] ..."],
        "misleading": false,
        "misleading_why": "",
        "fact_issues": []}
     ]}

All the new fields are additions. Whether the scorer accepts unknown
fields has not been checked.

## 8. Limits of this run

- **Sample size.** There are 6 items: `qwen3.5:4b` on 5 patients and
  `gemma4:e2b` on 1 (fatima). The only like-for-like comparison is that one
  patient. Report counts ("0 of 5"), not percentages, and give each model's
  patient count next to its result. A ranking between a model judged on 5
  patients and one judged on 1 should say so in the same line.
- **Blinding.** The item ids contain the model name, so the judge knows
  which model wrote what. Future runs should give the judge opaque ids and
  map them back afterwards.
- **One judge.** A single LLM judge has its own biases. Check it by
  re-judging a few items to see if the labels agree, and by having a person
  spot-check a sample. Report how often they agree.
- **Scope.** This scores overview quality only. The primary-model decision
  combines it with the suite's other evals.
