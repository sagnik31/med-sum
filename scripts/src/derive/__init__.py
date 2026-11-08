"""
Values derived from printed text by code, never by the model (decisions
10-13): numbers, reference ranges, computed flags, unit conversions, dose
schedules, durations and dates. build.py turns a parse output into the
stored record.

- numbers.py: numbers, reference ranges, computed flags
- units.py: the unit table (spellings, analytes, conversions)
- medicines.py: dose schedules and durations
- dates.py: printed dates and the document date
- build.py: parse output -> DocumentRecord

Every parser returns None rather than guess: a value that can't be read
without guessing keeps only its printed text.
"""

from derive.build import build_record

__all__ = ["build_record"]
