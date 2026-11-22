# Repair eval: suite-repair

Generated 2026-11-22T15:39:05+00:00; 15 cases, each one injected fault that leaves an error finding.

## qwen3.5:4b

Supports: **fixed** (tools were slower per repair (220.0 s vs 150.1 s))

| | fixed | tools |
|---|---|---|
| Cases repaired | 14/15 (93%, CI 70%-99%) | 15/15 (100%, CI 80%-100%) |
| Repaired on the first repair | 14 | 14 |
| Repairs per case | 1.07 | 1.07 |
| Model calls per repair | 1.0 | 3.0 |
| Tokens per repair | 3677 | 11433 |
| Seconds per repair | 150.1 | 220.0 |
| Median seconds per case | 161.7 | 221.3 |
| Unsafe errors before / after | 13 / 1 | 13 / 0 |
| Failed runs | 0 | 0 |

Tool calls (tool repair): tool_calls 32, malformed 0, unknown_tool 0, not_found 1, inexact_types 0, over_limit 0, well_formed_rate 1.0, turns_per_repair 3.0, fix_in_reading_turn 0, fix_in_final_turn 16, part_repairs 16; by tool: get_item 26, get_lines 3, search_transcription 3


| Fault | fixed | tools |
|---|---|---|
| date_swap | 1/1 | 1/1 |
| decimal_shift | 1/1 | 1/1 |
| digit_swap | 1/1 | 1/1 |
| dropped_medicine | 1/1 | 1/1 |
| dropped_result | 1/1 | 1/1 |
| duplicated_item | 1/1 | 1/1 |
| frequency_change | 1/1 | 1/1 |
| invented_medicine | 0/1 | 1/1 |
| invented_result | 1/1 | 1/1 |
| qualifier_dropped | 1/1 | 1/1 |
| range_bound | 1/1 | 1/1 |
| remark_moved | 1/1 | 1/1 |
| strength_shift | 1/1 | 1/1 |
| unit_swap | 1/1 | 1/1 |
| values_swapped | 1/1 | 1/1 |

## gemma4:e2b

Supports: **fixed** (tools repaired fewer cases (13 vs 14); tools were slower per repair (76.2 s vs 43.4 s))

| | fixed | tools |
|---|---|---|
| Cases repaired | 14/15 (93%, CI 70%-99%) | 13/15 (87%, CI 62%-96%) |
| Repaired on the first repair | 13 | 12 |
| Repairs per case | 1.13 | 1.2 |
| Model calls per repair | 1.0 | 2.0 |
| Tokens per repair | 3886 | 8102 |
| Seconds per repair | 43.4 | 76.2 |
| Median seconds per case | 42.3 | 79.1 |
| Unsafe errors before / after | 13 / 2 | 13 / 2 |
| Failed runs | 0 | 0 |

Tool calls (tool repair): tool_calls 0, malformed 0, unknown_tool 0, not_found 0, inexact_types 0, over_limit 0, well_formed_rate None, turns_per_repair 2.0, fix_in_reading_turn 0, fix_in_final_turn 18, part_repairs 18; by tool: none


| Fault | fixed | tools |
|---|---|---|
| date_swap | 1/1 | 1/1 |
| decimal_shift | 1/1 | 1/1 |
| digit_swap | 1/1 | 1/1 |
| dropped_medicine | 1/1 | 1/1 |
| dropped_result | 1/1 | 1/1 |
| duplicated_item | 1/1 | 1/1 |
| frequency_change | 1/1 | 1/1 |
| invented_medicine | 1/1 | 0/1 |
| invented_result | 0/1 | 0/1 |
| qualifier_dropped | 1/1 | 1/1 |
| range_bound | 1/1 | 1/1 |
| remark_moved | 1/1 | 1/1 |
| strength_shift | 1/1 | 1/1 |
| unit_swap | 1/1 | 1/1 |
| values_swapped | 1/1 | 1/1 |

