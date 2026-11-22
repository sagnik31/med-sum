# Eval results: suite-reasoning

Generated 2026-11-22T09:34:27+00:00 on dataset med-sum synthetic v2; stage reasoning (from the expected transcriptions); models: `qwen3.5:4b`, `gemma4:e2b`.

## Model choice

**No model chosen:** no model passes the safety gate.
- `qwen3.5:4b` excluded: unsafe-error rate 4.90% is above 1.00% (10 of 204 clinical items).
- `gemma4:e2b` excluded: unsafe-error rate 5.39% is above 1.00% (11 of 204 clinical items).

## Overview

| Metric | qwen3.5:4b | gemma4:e2b |
| --- | --- | --- |
| Documents | 25 | 25 |
| Type right | 25/25 (100.0%) | 25/25 (100.0%) |
| Field precision | 91.5% | 91.1% |
| Field recall | 90.8% | 91.8% |
| Field F1 | 91.1% | 91.4% |
| Derived F1 | 96.7% | 96.4% |
| Medicine recall | 90.0% | 100.0% |
| Citations right | 366/419 (87.4%) | 368/407 (90.4%) |
| Remark attribution | 18/21 (85.7%) | 17/21 (81.0%) |
| Frequency exactness | 27/27 (100.0%) | 30/30 (100.0%) |
| Items with an unsafe error | 10/236 (4.2%, 95% CI 2.3%-7.6%) | 11/236 (4.7%, 95% CI 2.6%-8.2%) |
| Invented items | 22 | 28 |
| Missed items | 9 | 6 |
| Median time per document | 321 s | 476 s |

## Routing and risk coverage

Auto-verified documents reach no clinician; the unsafe errors inside them are what the safety net let through.

| Metric | qwen3.5:4b | gemma4:e2b |
| --- | --- | --- |
| End state right | 20/25 (80.0%, 95% CI 60.9%-91.1%) | 16/25 (64.0%, 95% CI 44.5%-79.8%) |
| Signals as expected | 20/25 (80.0%, 95% CI 60.9%-91.1%) | 15/25 (60.0%, 95% CI 40.7%-76.6%) |
| Auto-verified | 17/25 (68.0%, 95% CI 48.4%-82.8%) | 13/25 (52.0%, 95% CI 33.5%-70.0%) |
| Items with an unsafe error, verified | 3/111 (2.7%, 95% CI 0.9%-7.6%) | 0/88 (0.0%, 95% CI 0.0%-4.2%) |
| Records by the fallback | 3 | 19 |
| Failed runs | 0 | 0 |

Documents ending in the wrong state:

- `qwen3.5:4b` d01: needs_review ['LC-03', 'LC-04', 'LC-07', 'LC-17', 'LC-22'], expected verified []
- `qwen3.5:4b` d03: needs_review ['LC-04', 'LC-22'], expected verified []
- `qwen3.5:4b` d06: needs_review ['LC-03'], expected verified []
- `qwen3.5:4b` d20: needs_review ['LC-12', 'LC-22'], expected verified []
- `qwen3.5:4b` d25: needs_review ['LC-03', 'LC-04', 'LC-17', 'LC-18'], expected verified []
- `gemma4:e2b` d01: needs_review ['LC-03', 'LC-04'], expected verified []
- `gemma4:e2b` d03: needs_review ['LC-07'], expected verified []
- `gemma4:e2b` d04: needs_review ['LC-12', 'LC-22'], expected verified []
- `gemma4:e2b` d06: needs_review ['LC-04'], expected verified []
- `gemma4:e2b` d09: needs_review ['LC-12', 'LC-22'], expected verified []
- `gemma4:e2b` d10: needs_review ['LC-12', 'LC-22'], expected verified []
- `gemma4:e2b` d11: needs_review ['LC-04'], expected verified []
- `gemma4:e2b` d20: needs_review ['LC-12', 'LC-22'], expected verified []
- `gemma4:e2b` d25: needs_review ['LC-03', 'LC-04', 'LC-17'], expected verified []

## By split

Dev documents were used while writing and checking the prompts; the test documents were not.

| Split | Model | Docs | Field F1 | Unsafe | Value recall |
| --- | --- | --- | --- | --- | --- |
| dev | `qwen3.5:4b` | 2 | 97.9% | 0/12 (0.0%, 95% CI 0.0%-24.2%) | - |
| dev | `gemma4:e2b` | 2 | 93.5% | 0/12 (0.0%, 95% CI 0.0%-24.2%) | - |
| held_out | `qwen3.5:4b` | 7 | 90.5% | 0/47 (0.0%, 95% CI 0.0%-7.6%) | - |
| held_out | `gemma4:e2b` | 7 | 93.7% | 0/47 (0.0%, 95% CI 0.0%-7.6%) | - |
| test | `qwen3.5:4b` | 16 | 90.7% | 10/177 (5.6%, 95% CI 3.1%-10.1%) | - |
| test | `gemma4:e2b` | 16 | 90.5% | 11/177 (6.2%, 95% CI 3.5%-10.8%) | - |

## By case

Field F1, unsafe errors, and (where the case has them) remark attribution, frequency exactness and transcription value recall, over the documents that show each case.

### `qwen3.5:4b`

| Case | Docs | Field F1 | Unsafe | Attribution | Frequency | Value recall |
| --- | --- | --- | --- | --- | --- | --- |
| as_needed | 2 | 80.6% | 3 | 2/2 (100.0%) | 6/6 (100.0%) | - |
| bmi_and_height | 1 | 94.5% | 0 | 2/2 (100.0%) | 3/3 (100.0%) | - |
| bun_in_kidney_panel | 1 | 87.6% | 2 | 1/1 (100.0%) | - | - |
| classified_other | 1 | - | 0 | - | - | - |
| counts_in_other_units | 1 | 90.7% | 0 | 1/1 (100.0%) | - | - |
| date_month_in_words | 2 | 83.8% | 0 | 2/2 (100.0%) | 4/4 (100.0%) | - |
| date_with_dots | 1 | 96.5% | 0 | 1/1 (100.0%) | 3/3 (100.0%) | - |
| dense_page_over_output_cap | 1 | 96.0% | 1 | - | - | - |
| document_remark | 3 | 85.3% | 2 | 3/3 (100.0%) | - | - |
| document_remark_after_single_test | 1 | 79.6% | 2 | 2/2 (100.0%) | - | - |
| drug_allergy | 2 | 95.5% | 0 | 3/3 (100.0%) | 4/4 (100.0%) | - |
| duration_in_days | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | - |
| duration_in_months | 2 | 80.7% | 3 | 3/3 (100.0%) | 5/5 (100.0%) | - |
| duration_in_weeks | 3 | 96.0% | 0 | 3/3 (100.0%) | 6/6 (100.0%) | - |
| duration_not_given | 1 | 100.0% | 0 | - | 2/2 (100.0%) | - |
| e_prescription_text_layer | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | - |
| flag_as_arrow | 1 | 91.3% | 0 | 0/1 (0.0%) | - | - |
| flag_as_word | 3 | 86.8% | 0 | 2/2 (100.0%) | - | - |
| flag_beside_result | 3 | 89.7% | 2 | 2/2 (100.0%) | - | - |
| generic_name | 1 | 91.8% | 0 | 2/2 (100.0%) | 3/3 (100.0%) | - |
| header_as_table | 1 | 90.7% | 0 | 1/1 (100.0%) | - | - |
| header_fields_on_one_line | 4 | 89.6% | 0 | 3/4 (75.0%) | 7/7 (100.0%) | - |
| history | 4 | 87.5% | 3 | 6/6 (100.0%) | 11/11 (100.0%) | - |
| image_only_pdf | 4 | 90.5% | 2 | 2/2 (100.0%) | - | - |
| impression | 1 | 67.3% | 3 | 1/1 (100.0%) | 2/2 (100.0%) | - |
| indian_digit_grouping | 1 | 86.9% | 2 | 0/1 (0.0%) | - | - |
| iso_date | 1 | 91.3% | 0 | 0/1 (0.0%) | - | - |
| jpeg | 1 | 67.3% | 3 | 1/1 (100.0%) | 2/2 (100.0%) | - |
| lab_report_as_image | 1 | 91.3% | 0 | 0/1 (0.0%) | - | - |
| less_than_value | 1 | 96.3% | 0 | - | - | - |
| medicine_without_strength | 1 | 98.1% | 0 | 2/2 (100.0%) | 3/3 (100.0%) | - |
| medicines_as_numbered_lines | 1 | 95.1% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | - |
| multi_page | 7 | 91.1% | 7 | 3/5 (60.0%) | - | - |
| multiple_ranges | 6 | 89.9% | 5 | 3/4 (75.0%) | - | - |
| named_frequency | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | - |
| no_known_allergies | 3 | 95.1% | 0 | 5/5 (100.0%) | 10/10 (100.0%) | - |
| no_report_date | 1 | 79.6% | 2 | 2/2 (100.0%) | - | - |
| non_numeric_values | 3 | 92.3% | 3 | 0/1 (0.0%) | - | - |
| one_sided_range | 7 | 87.7% | 4 | 3/5 (60.0%) | - | - |
| page_parsed_in_parts | 1 | 96.0% | 1 | - | - | - |
| panel_remark | 1 | 97.7% | 0 | 1/1 (100.0%) | - | - |
| panel_remark_after_last_row | 2 | 88.8% | 2 | 0/2 (0.0%) | - | - |
| patient_mismatch | 1 | 95.7% | 0 | - | - | - |
| printed_flag_without_computed_flag | 1 | 85.7% | 0 | - | - | - |
| provisional_diagnosis | 1 | 94.5% | 0 | 2/2 (100.0%) | 3/3 (100.0%) | - |
| range_before_unit | 2 | 91.5% | 0 | 1/1 (100.0%) | - | - |
| results_as_lines | 1 | 77.4% | 0 | 1/1 (100.0%) | - | - |
| route_given | 1 | 95.1% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | - |
| single_creatinine | 1 | 87.6% | 2 | 1/1 (100.0%) | - | - |
| sixty_results | 1 | 96.0% | 1 | - | - | - |
| statin_without_lipid_panel | 1 | 94.5% | 0 | 2/2 (100.0%) | 3/3 (100.0%) | - |
| strength_in_own_column | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | - |
| table_continued_on_next_page | 1 | 92.2% | 0 | - | - | - |
| temperature | 1 | 97.1% | 0 | 1/1 (100.0%) | 1/1 (100.0%) | - |
| test_name_variant | 2 | 83.7% | 0 | 2/2 (100.0%) | - | - |
| test_remark | 3 | 86.2% | 4 | 2/4 (50.0%) | - | - |
| trend_two_values | 5 | 94.9% | 1 | 1/2 (50.0%) | - | - |
| two_column_layout | 1 | 96.5% | 0 | 1/1 (100.0%) | 3/3 (100.0%) | - |
| two_diagnoses_one_line | 2 | 96.6% | 0 | 3/3 (100.0%) | 7/7 (100.0%) | - |
| undated | 1 | 100.0% | 0 | - | 2/2 (100.0%) | - |
| unused_complaint_line | 2 | 97.4% | 0 | 3/3 (100.0%) | 6/6 (100.0%) | - |
| unused_date_line | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | - |
| uploaded_before_older_documents | 2 | 78.1% | 3 | 2/2 (100.0%) | 4/4 (100.0%) | - |
| vitals | 3 | 95.2% | 0 | 4/4 (100.0%) | 11/11 (100.0%) | - |
| weekly_frequency | 2 | 95.6% | 0 | 2/2 (100.0%) | 5/5 (100.0%) | - |

### `gemma4:e2b`

| Case | Docs | Field F1 | Unsafe | Attribution | Frequency | Value recall |
| --- | --- | --- | --- | --- | --- | --- |
| as_needed | 2 | 91.0% | 0 | 2/2 (100.0%) | 9/9 (100.0%) | - |
| bmi_and_height | 1 | 94.5% | 0 | 2/2 (100.0%) | 3/3 (100.0%) | - |
| bun_in_kidney_panel | 1 | 90.9% | 1 | 1/1 (100.0%) | - | - |
| classified_other | 1 | - | 0 | - | - | - |
| counts_in_other_units | 1 | 90.7% | 0 | 1/1 (100.0%) | - | - |
| date_month_in_words | 2 | 95.3% | 0 | 2/2 (100.0%) | 4/4 (100.0%) | - |
| date_with_dots | 1 | 96.5% | 0 | 1/1 (100.0%) | 3/3 (100.0%) | - |
| dense_page_over_output_cap | 1 | 92.9% | 5 | - | - | - |
| document_remark | 3 | 93.1% | 1 | 3/3 (100.0%) | - | - |
| document_remark_after_single_test | 1 | 78.8% | 4 | 1/2 (50.0%) | - | - |
| drug_allergy | 2 | 91.9% | 0 | 3/3 (100.0%) | 4/4 (100.0%) | - |
| duration_in_days | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | - |
| duration_in_months | 2 | 90.8% | 0 | 3/3 (100.0%) | 8/8 (100.0%) | - |
| duration_in_weeks | 3 | 93.2% | 0 | 3/3 (100.0%) | 6/6 (100.0%) | - |
| duration_not_given | 1 | 100.0% | 0 | - | 2/2 (100.0%) | - |
| e_prescription_text_layer | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | - |
| flag_as_arrow | 1 | 91.3% | 0 | 0/1 (0.0%) | - | - |
| flag_as_word | 3 | 93.7% | 0 | 2/2 (100.0%) | - | - |
| flag_beside_result | 3 | 87.3% | 4 | 1/2 (50.0%) | - | - |
| generic_name | 1 | 91.8% | 0 | 2/2 (100.0%) | 3/3 (100.0%) | - |
| header_as_table | 1 | 90.7% | 0 | 1/1 (100.0%) | - | - |
| header_fields_on_one_line | 4 | 95.3% | 0 | 3/4 (75.0%) | 7/7 (100.0%) | - |
| history | 4 | 93.2% | 0 | 6/6 (100.0%) | 14/14 (100.0%) | - |
| image_only_pdf | 4 | 88.8% | 4 | 1/2 (50.0%) | - | - |
| impression | 1 | 87.0% | 0 | 1/1 (100.0%) | 5/5 (100.0%) | - |
| indian_digit_grouping | 1 | 88.5% | 1 | 1/1 (100.0%) | - | - |
| iso_date | 1 | 91.3% | 0 | 0/1 (0.0%) | - | - |
| jpeg | 1 | 87.0% | 0 | 1/1 (100.0%) | 5/5 (100.0%) | - |
| lab_report_as_image | 1 | 91.3% | 0 | 0/1 (0.0%) | - | - |
| less_than_value | 1 | 96.3% | 0 | - | - | - |
| medicine_without_strength | 1 | 98.1% | 0 | 2/2 (100.0%) | 3/3 (100.0%) | - |
| medicines_as_numbered_lines | 1 | 95.1% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | - |
| multi_page | 7 | 90.3% | 11 | 3/5 (60.0%) | - | - |
| multiple_ranges | 6 | 90.1% | 10 | 3/4 (75.0%) | - | - |
| named_frequency | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | - |
| no_known_allergies | 3 | 95.1% | 0 | 5/5 (100.0%) | 10/10 (100.0%) | - |
| no_report_date | 1 | 78.8% | 4 | 1/2 (50.0%) | - | - |
| non_numeric_values | 3 | 90.8% | 6 | 1/1 (100.0%) | - | - |
| one_sided_range | 7 | 90.9% | 2 | 4/5 (80.0%) | - | - |
| page_parsed_in_parts | 1 | 92.9% | 5 | - | - | - |
| panel_remark | 1 | 87.8% | 0 | 0/1 (0.0%) | - | - |
| panel_remark_after_last_row | 2 | 89.9% | 1 | 1/2 (50.0%) | - | - |
| patient_mismatch | 1 | 85.0% | 0 | - | - | - |
| printed_flag_without_computed_flag | 1 | 80.0% | 0 | - | - | - |
| provisional_diagnosis | 1 | 94.5% | 0 | 2/2 (100.0%) | 3/3 (100.0%) | - |
| range_before_unit | 2 | 91.5% | 0 | 1/1 (100.0%) | - | - |
| results_as_lines | 1 | 98.1% | 0 | 1/1 (100.0%) | - | - |
| route_given | 1 | 95.1% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | - |
| single_creatinine | 1 | 90.9% | 1 | 1/1 (100.0%) | - | - |
| sixty_results | 1 | 92.9% | 5 | - | - | - |
| statin_without_lipid_panel | 1 | 94.5% | 0 | 2/2 (100.0%) | 3/3 (100.0%) | - |
| strength_in_own_column | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | - |
| table_continued_on_next_page | 1 | 92.2% | 0 | - | - | - |
| temperature | 1 | 87.1% | 0 | 1/1 (100.0%) | 1/1 (100.0%) | - |
| test_name_variant | 2 | 94.6% | 0 | 2/2 (100.0%) | - | - |
| test_remark | 3 | 87.1% | 5 | 2/4 (50.0%) | - | - |
| trend_two_values | 5 | 91.9% | 5 | 0/2 (0.0%) | - | - |
| two_column_layout | 1 | 96.5% | 0 | 1/1 (100.0%) | 3/3 (100.0%) | - |
| two_diagnoses_one_line | 2 | 96.6% | 0 | 3/3 (100.0%) | 7/7 (100.0%) | - |
| undated | 1 | 100.0% | 0 | - | 2/2 (100.0%) | - |
| unused_complaint_line | 2 | 97.4% | 0 | 3/3 (100.0%) | 6/6 (100.0%) | - |
| unused_date_line | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | - |
| uploaded_before_older_documents | 2 | 90.1% | 0 | 2/2 (100.0%) | 7/7 (100.0%) | - |
| vitals | 3 | 95.2% | 0 | 4/4 (100.0%) | 11/11 (100.0%) | - |
| weekly_frequency | 2 | 95.6% | 0 | 2/2 (100.0%) | 5/5 (100.0%) | - |

## By document

### `qwen3.5:4b`

| Doc | Type | Type right | Field F1 | Unsafe | Missed | Invented | CER | Value recall | Time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d01 | lab_report | yes | 86.9% | 2 | 3 | 1 | - | - | 2020 s |
| d02 | prescription | yes | 98.1% | 0 | 0 | 0 | - | - | 547 s |
| d03 | lab_report | yes | 79.6% | 2 | 0 | 1 | - | - | 880 s |
| d04 | prescription | yes | 67.3% | 3 | 3 | 3 | - | - | 255 s |
| d05 | lab_report | yes | 93.1% | 0 | 0 | 0 | - | - | 321 s |
| d06 | lab_report | yes | 87.6% | 2 | 0 | 3 | - | - | 801 s |
| d07 | prescription | yes | 94.5% | 0 | 1 | 1 | - | - | 558 s |
| d08 | lab_report | yes | 96.3% | 0 | 0 | 0 | - | - | 247 s |
| d09 | lab_report | yes | 97.7% | 0 | 0 | 0 | - | - | 549 s |
| d10 | prescription | yes | 97.1% | 0 | 0 | 0 | - | - | 185 s |
| d11 | lab_report | yes | 85.7% | 0 | 0 | 0 | - | - | 147 s |
| d12 | prescription | yes | 94.6% | 0 | 0 | 0 | - | - | 388 s |
| d13 | lab_report | yes | 86.2% | 0 | 0 | 0 | - | - | 314 s |
| d14 | prescription | yes | 91.8% | 0 | 1 | 2 | - | - | 235 s |
| d15 | prescription | yes | 100.0% | 0 | 0 | 0 | - | - | 110 s |
| d16 | lab_report | yes | 95.7% | 0 | 0 | 0 | - | - | 147 s |
| d17 | other | yes | - | 0 | 0 | 0 | - | - | 7 s |
| d18 | lab_report | yes | 77.4% | 0 | 0 | 0 | - | - | 243 s |
| d19 | prescription | yes | 95.1% | 0 | 0 | 0 | - | - | 493 s |
| d20 | lab_report | yes | 90.7% | 0 | 0 | 1 | - | - | 875 s |
| d21 | prescription | yes | 96.5% | 0 | 0 | 0 | - | - | 189 s |
| d22 | lab_report | yes | 91.3% | 0 | 0 | 0 | - | - | 224 s |
| d23 | lab_report | yes | 92.2% | 0 | 0 | 1 | - | - | 757 s |
| d24 | prescription | yes | 91.8% | 0 | 1 | 2 | - | - | 431 s |
| d25 | lab_report | yes | 96.0% | 1 | 0 | 7 | - | - | 6064 s |

### `gemma4:e2b`

| Doc | Type | Type right | Field F1 | Unsafe | Missed | Invented | CER | Value recall | Time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d01 | lab_report | yes | 88.5% | 1 | 1 | 4 | - | - | 1972 s |
| d02 | prescription | yes | 98.1% | 0 | 0 | 0 | - | - | 789 s |
| d03 | lab_report | yes | 78.8% | 4 | 0 | 1 | - | - | 587 s |
| d04 | prescription | yes | 87.0% | 0 | 2 | 1 | - | - | 226 s |
| d05 | lab_report | yes | 93.1% | 0 | 0 | 0 | - | - | 823 s |
| d06 | lab_report | yes | 90.9% | 1 | 0 | 2 | - | - | 1248 s |
| d07 | prescription | yes | 94.5% | 0 | 1 | 1 | - | - | 755 s |
| d08 | lab_report | yes | 96.3% | 0 | 0 | 0 | - | - | 419 s |
| d09 | lab_report | yes | 87.8% | 0 | 0 | 0 | - | - | 55 s |
| d10 | prescription | yes | 87.1% | 0 | 0 | 0 | - | - | 96 s |
| d11 | lab_report | yes | 80.0% | 0 | 0 | 1 | - | - | 550 s |
| d12 | prescription | yes | 94.6% | 0 | 0 | 0 | - | - | 560 s |
| d13 | lab_report | yes | 85.7% | 0 | 0 | 1 | - | - | 476 s |
| d14 | prescription | yes | 91.8% | 0 | 1 | 2 | - | - | 416 s |
| d15 | prescription | yes | 100.0% | 0 | 0 | 0 | - | - | 191 s |
| d16 | lab_report | yes | 85.0% | 0 | 0 | 0 | - | - | 31 s |
| d17 | other | yes | - | 0 | 0 | 0 | - | - | 2 s |
| d18 | lab_report | yes | 98.1% | 0 | 0 | 0 | - | - | 379 s |
| d19 | prescription | yes | 95.1% | 0 | 0 | 0 | - | - | 628 s |
| d20 | lab_report | yes | 90.7% | 0 | 0 | 1 | - | - | 72 s |
| d21 | prescription | yes | 96.5% | 0 | 0 | 0 | - | - | 327 s |
| d22 | lab_report | yes | 91.3% | 0 | 0 | 0 | - | - | 342 s |
| d23 | lab_report | yes | 92.2% | 0 | 0 | 1 | - | - | 491 s |
| d24 | prescription | yes | 91.8% | 0 | 1 | 2 | - | - | 765 s |
| d25 | lab_report | yes | 92.9% | 5 | 0 | 11 | - | - | 8466 s |

## Errors

### `qwen3.5:4b`

- d01 unsafe: observations obs-25 flag_printed expected `*`, got `L`
- d01 unsafe: invented observations item `null`
- d01 missed observations obs-16 `VLDL Cholesterol`
- d01 missed observations obs-22 `Appearance`
- d01 missed remarks rem-2 `HbA1c reflects average blood glucose over the past 2-3 months.`
- d03 unsafe: observations obs-1 value_text expected `11.2`, got `11.2 L`
- d03 unsafe: observations obs-4 value_text expected `79`, got `79 L`
- d03 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d04 unsafe: invented medications item `Telma 40 mg`
- d04 unsafe: invented medications item `Pan 40 mg`
- d04 unsafe: invented medications item `Dolo 650 mg`
- d04 missed medications med-2 `Telma`
- d04 missed medications med-4 `Pan`
- d04 missed medications med-5 `Dolo`
- d06 unsafe: invented observations item `Test`
- d06 unsafe: invented observations item `---`
- d06 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d07 missed conditions cond-2 `Chronic kidney disease, to be evaluated`
- d07 invented conditions `Chronic kidney disease`
- d14 missed conditions cond-1 `CAD, post PTCA (2019)`
- d14 invented conditions `CAD`
- d14 invented conditions `post PTCA (2019)`
- d20 invented conditions `autoimmune thyroiditis`
- d23 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d24 missed conditions cond-1 `Hypertension, better controlled`
- d24 invented conditions `Hypertension`
- d24 invented conditions `better controlled`
- d25 unsafe: invented observations item `ESR`
- d25 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d25 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d25 invented remarks `All results relate only to the sample as received. Reference intervals are those of this laboratory for adults and may differ from other laboratories.`
- d25 invented remarks `A result outside its reference interval is not by itself a disease; about one healthy person in twenty falls outside any single interval.`
- d25 invented remarks `Results are to be interpreted by a registered medical practitioner, together with the history, the examination and other investigations.`
- d25 invented remarks `Values flagged H or L lie above or below the printed reference interval. Critical values are telephoned to the referring doctor.`

### `gemma4:e2b`

- d01 unsafe: invented observations item ` | `
- d01 missed remarks rem-2 `HbA1c reflects average blood glucose over the past 2-3 months.`
- d01 invented remarks `Interpretation: HbA1c reflects average blood glucose over the past 2-3 months.`
- d01 invented remarks `Method: Hexokinase`
- d01 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d03 unsafe: observations obs-1 value_text expected `11.2`, got `11.2 L`
- d03 unsafe: observations obs-4 value_text expected `79`, got `79 L`
- d03 unsafe: observations obs-5 value_text expected `7.4`, got `7.4 H`
- d03 unsafe: observations obs-6 value_text expected `132`, got `132 H`
- d03 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d04 missed conditions cond-1 `T2DM`
- d04 missed conditions cond-2 `HTN`
- d04 invented conditions `T2DM, HTN`
- d06 unsafe: invented observations item `Sodium`
- d06 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d07 missed conditions cond-2 `Chronic kidney disease, to be evaluated`
- d07 invented conditions `Chronic kidney disease`
- d11 invented remarks `*** End of Report ***`
- d13 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d14 missed conditions cond-1 `CAD, post PTCA (2019)`
- d14 invented conditions `CAD`
- d14 invented conditions `post PTCA (2019)`
- d20 invented conditions `autoimmune thyroiditis`
- d23 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d24 missed conditions cond-1 `Hypertension, better controlled`
- d24 invented conditions `Hypertension`
- d24 invented conditions `better controlled`
- d25 unsafe: invented observations item `Test`
- d25 unsafe: invented observations item `---`
- d25 unsafe: invented observations item `null`
- d25 unsafe: invented observations item `null`
- d25 unsafe: invented observations item `null`
- d25 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d25 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d25 invented remarks `All results relate only to the sample as received. Reference intervals are those of this laboratory for adults and may differ from other laboratories.`
- d25 invented remarks `A result outside its reference interval is not by itself a disease; about one healthy person in twenty falls outside any single interval.`
- d25 invented remarks `Results are to be interpreted by a registered medical practitioner, together with the history, the examination and other investigations.`
- d25 invented remarks `Values flagged H or L lie above or below the printed reference interval. Critical values are telephoned to the referring doctor.`

Every field error, layout difference and missing transcribed value is in the JSON report.
