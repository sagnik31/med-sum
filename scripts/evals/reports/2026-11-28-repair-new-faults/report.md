# Repair eval: repair-new-faults

Generated 2026-11-28T00:51:54+00:00; 5 cases, each one injected fault that leaves an error finding.

## qwen3.5:4b

Supports: **fixed** (both variants are needed to compare)

| | fixed |
|---|---|
| Cases repaired | 4/5 (80%, CI 38%-96%) |
| Repaired on the first repair | 4 |
| Repairs per case | 1.2 |
| Model calls per repair | 1.0 |
| Tokens per repair | 3797 |
| Seconds per repair | 158.2 |
| Median seconds per case | 193.8 |
| Unsafe errors before / after | 5 / 1 |
| Failed runs | 0 |

| Fault | fixed |
|---|---|
| allergy_text_cut | 1/1 |
| flag_in_value | 1/1 |
| frequency_as_dose | 1/1 |
| header_row_as_result | 0/1 |
| strength_in_name | 1/1 |

