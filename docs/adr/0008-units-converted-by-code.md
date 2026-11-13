# 0008. Units converted only by code, from a fixed table

- **Status:** Accepted, 2026-11-07. Built.

## Context

The same test is printed in different units by different labs (glucose in mg/dL or mmol/L, counts in /cumm, lakh/cumm or 10³/µL). Trends need values in one unit. A model asked to convert would get factors wrong now and then, with nothing to show it had.

## Decision

- Conversions are done by code from a fixed table (`scripts/src/derive/units.py`): about 25 units with their spellings, grouped by dimension, and about 40 analytes, each with the test names labs print, a canonical unit, and a molar mass or valence where a conversion needs it.
- A conversion is a derived value marked "converted". The printed value stays and is what is shown by default.
- An analyte or unit not in the table is never converted, so values in different units are not put on one trend.
- Conversions that are not a factor (HbA1c % and mmol/mol) are not done.

## Alternatives rejected

- **Model conversions.** Unverifiable.
- **A general unit library.** Knows units, not which lab test is which or the molar masses involved; the table would still be needed.

## Consequences

- Coverage is limited to the table, and growing it is a code change with tests.
- Trends are skipped rather than guessed when units differ and no conversion applies.

## What would change it

New analytes in real documents, or a coded vocabulary (see [0009](0009-no-medical-codes-or-brand-mapping.md)) that carries units of its own.
