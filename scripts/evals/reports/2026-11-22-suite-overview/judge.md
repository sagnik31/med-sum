# Judging the patient overviews

You are judging short overviews of a patient's records, each written by a
small local model from a list of facts. The facts were computed by code
and are correct; judge each overview against its facts only, never
against what you know of medicine or of the patient.

For each item of judge.json:

1. Split the overview into its sentences. For each sentence, `supported`
   is true when everything it says follows from the facts: the values,
   the tests or medicines they belong to, dates and their order, flags
   and trends as the facts give them. Otherwise false, with `why` saying
   in a few words what does not follow.
2. `omissions`: what a clinician would need first and the overview leaves
   out, in a few words each: a fact listed for attention, an allergy, the
   latest prescription. Not every fact belongs in a few sentences; list
   only what a clinician reading the overview alone would be wrong
   without. An empty list is fine.
3. `misleading`: true when the overview as a whole would lead a clinician
   to a wrong reading (a flagged value made to sound normal, a trend in
   the wrong direction, the wrong order of events, one document's value
   given as current), even if each sentence is supported on its own.

Write verdicts.json:

    {"judge": "<who judged>",
     "verdicts": [
       {"id": "<item id>",
        "sentences": [{"text": "...", "supported": true, "why": ""}],
        "omissions": ["..."],
        "misleading": false}
     ]}

One verdict per item, every item judged, nothing else in the file.
