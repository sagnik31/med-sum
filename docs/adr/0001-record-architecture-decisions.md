# 0001. Record architecture decisions as ADRs

- **Status:** Accepted, 2026-11-13.

## Context

Med-Sum's design was worked out in a private planning document. Code comments used to point at numbered entries in that document ("decision N", "section N.N"). A reader of the repository can't open that document, so those pointers explained nothing, and the reasons behind the design weren't visible to anyone reviewing the code.

## Decision

- Every significant architectural decision is written up as an architecture decision record in `docs/adr/`, one short file per decision, named `00NN-<kebab-name>.md`, and committed. [`README.md`](README.md) in this folder is the index.
- Each record has a status, the context, the decision, the alternatives rejected, the consequences, and the evidence that would change it.
- ADRs record decisions only. Planning, open questions and progress stay out of the repository.
- Where code follows a recorded decision, its comment says `See docs/adr/00NN-<name>.md` and also states the rule in a sentence or two, so the code can be read without opening the ADR.
- No committed file cites the planning document's numbering. A test fails on a decision, section, question or plan-step number followed by digits.

## Alternatives rejected

- **Commit the planning document.** It mixes decisions with action items, open questions and a running log. A reader would have to tell which parts still hold.
- **Explain everything in code comments only.** Comments explain the rule where it is applied, but not the alternatives that were weighed or what would reopen the question.
- **A wiki or an external site.** It drifts away from the code it describes, and isn't versioned with it.

## Consequences

- A decision that changes gets a new ADR (or its status changes to "Superseded by 00NN"), so the history of the reasoning is kept.
- New code that follows a recorded rule cites the ADR, and the reference test keeps planning numbers out.

## What would change it

Nothing expected. If the records grow past what one folder can hold readably, they could be grouped by area.
