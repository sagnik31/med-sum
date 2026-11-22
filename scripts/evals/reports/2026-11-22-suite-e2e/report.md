# Eval results: suite-e2e

Generated 2026-11-22T23:57:07+00:00 on dataset med-sum synthetic v2; stage e2e (the whole pipeline); models: `qwen3.5:4b`, `gemma4:e2b`.

## Model choice

**No model chosen:** no model passes the safety gate.
- `qwen3.5:4b` excluded: unsafe-error rate 4.90% is above 1.00% (10 of 204 clinical items).
- `gemma4:e2b` excluded: unsafe-error rate 13.24% is above 1.00% (27 of 204 clinical items).

## Overview

| Metric | qwen3.5:4b | gemma4:e2b |
| --- | --- | --- |
| Documents | 25 | 25 |
| Type right | 25/25 (100.0%) | 25/25 (100.0%) |
| Field precision | 92.5% | 88.3% |
| Field recall | 90.3% | 90.4% |
| Field F1 | 91.4% | 89.3% |
| Derived F1 | 96.6% | 95.6% |
| Medicine recall | 80.0% | 86.7% |
| Citations right | 373/423 (88.2%) | 393/429 (91.6%) |
| Remark attribution | 19/22 (86.4%) | 18/22 (81.8%) |
| Frequency exactness | 23/24 (95.8%) | 24/26 (92.3%) |
| Items with an unsafe error | 10/236 (4.2%, 95% CI 2.3%-7.6%) | 26/245 (10.6%, 95% CI 7.3%-15.1%) |
| Invented items | 18 | 31 |
| Missed items | 10 | 8 |
| Transcription CER | 11.0% | 11.3% |
| Transcription line F1 | 88.6% | 85.4% |
| Transcription value recall | 1443/1445 (99.9%) | 1420/1445 (98.3%) |
| Layout kept | 601/618 (97.2%) | 515/596 (86.4%) |
| Unreadable lines | 0 | 0 |
| Median time per document | 633 s | 637 s |

## Routing and risk coverage

Auto-verified documents reach no clinician; the unsafe errors inside them are what the safety net let through.

| Metric | qwen3.5:4b | gemma4:e2b |
| --- | --- | --- |
| End state right | 21/25 (84.0%, 95% CI 65.3%-93.6%) | 15/25 (60.0%, 95% CI 40.7%-76.6%) |
| Signals as expected | 21/25 (84.0%, 95% CI 65.3%-93.6%) | 14/25 (56.0%, 95% CI 37.1%-73.3%) |
| Auto-verified | 18/25 (72.0%, 95% CI 52.4%-85.7%) | 12/25 (48.0%, 95% CI 30.0%-66.5%) |
| Items with an unsafe error, verified | 8/149 (5.4%, 95% CI 2.7%-10.2%) | 9/78 (11.5%, 95% CI 6.2%-20.5%) |
| Records by the fallback | 2 | 23 |
| Failed runs | 0 | 0 |

Documents ending in the wrong state:

- `qwen3.5:4b` d03: needs_review ['LC-04', 'LC-07', 'LC-17', 'LC-22'], expected verified []
- `qwen3.5:4b` d21: needs_review ['LC-15'], expected verified []
- `qwen3.5:4b` d22: needs_review ['LC-04', 'LC-12', 'LC-17', 'LC-22'], expected verified []
- `qwen3.5:4b` d25: needs_review ['LC-04', 'LC-18'], expected verified []
- `gemma4:e2b` d01: needs_review ['LC-03', 'LC-04', 'LC-07', 'LC-17'], expected verified []
- `gemma4:e2b` d03: needs_review ['LC-10'], expected verified []
- `gemma4:e2b` d08: needs_review ['LC-10'], expected verified []
- `gemma4:e2b` d09: needs_review ['LC-04'], expected verified []
- `gemma4:e2b` d11: needs_review ['LC-03'], expected verified []
- `gemma4:e2b` d18: needs_review ['LC-03'], expected verified []
- `gemma4:e2b` d20: needs_review ['LC-03'], expected verified []
- `gemma4:e2b` d22: needs_review ['LC-04'], expected verified []
- `gemma4:e2b` d23: needs_review ['LC-04'], expected verified []
- `gemma4:e2b` d25: needs_review ['LC-03', 'LC-04'], expected verified []

## By split

Dev documents were used while writing and checking the prompts; the test documents were not.

| Split | Model | Docs | Field F1 | Unsafe | Value recall |
| --- | --- | --- | --- | --- | --- |
| dev | `qwen3.5:4b` | 2 | 84.2% | 2/14 (14.3%, 95% CI 4.0%-39.9%) | 100.0% |
| dev | `gemma4:e2b` | 2 | 97.9% | 0/12 (0.0%, 95% CI 0.0%-24.2%) | 97.7% |
| held_out | `qwen3.5:4b` | 7 | 94.0% | 0/47 (0.0%, 95% CI 0.0%-7.6%) | 100.0% |
| held_out | `gemma4:e2b` | 7 | 91.9% | 2/47 (4.3%, 95% CI 1.2%-14.2%) | 98.2% |
| test | `qwen3.5:4b` | 16 | 91.2% | 8/175 (4.6%, 95% CI 2.3%-8.8%) | 99.8% |
| test | `gemma4:e2b` | 16 | 87.7% | 24/186 (12.9%, 95% CI 8.8%-18.5%) | 98.4% |

## By case

Field F1, unsafe errors, and (where the case has them) remark attribution, frequency exactness and transcription value recall, over the documents that show each case.

### `qwen3.5:4b`

| Case | Docs | Field F1 | Unsafe | Attribution | Frequency | Value recall |
| --- | --- | --- | --- | --- | --- | --- |
| as_needed | 2 | 80.6% | 3 | 2/2 (100.0%) | 6/6 (100.0%) | 100.0% |
| bmi_and_height | 1 | 89.1% | 1 | 2/2 (100.0%) | 3/3 (100.0%) | 100.0% |
| bun_in_kidney_panel | 1 | 96.8% | 0 | 1/1 (100.0%) | - | 100.0% |
| classified_other | 1 | - | 0 | - | - | - |
| counts_in_other_units | 1 | 100.0% | 0 | 1/1 (100.0%) | - | 100.0% |
| date_month_in_words | 2 | 94.2% | 0 | 2/2 (100.0%) | 4/4 (100.0%) | 100.0% |
| date_with_dots | 1 | 96.5% | 0 | 1/1 (100.0%) | 3/3 (100.0%) | 100.0% |
| dense_page_over_output_cap | 1 | 96.5% | 0 | - | - | 100.0% |
| document_remark | 3 | 97.6% | 0 | 3/3 (100.0%) | - | 100.0% |
| document_remark_after_single_test | 1 | 79.6% | 2 | 2/2 (100.0%) | - | 100.0% |
| drug_allergy | 2 | 90.4% | 2 | 3/3 (100.0%) | 3/4 (75.0%) | 98.8% |
| duration_in_days | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | 100.0% |
| duration_in_months | 2 | 78.0% | 4 | 3/3 (100.0%) | 5/5 (100.0%) | 100.0% |
| duration_in_weeks | 3 | 94.7% | 1 | 3/3 (100.0%) | 5/6 (83.3%) | 99.0% |
| duration_not_given | 1 | 100.0% | 0 | - | 2/2 (100.0%) | 100.0% |
| e_prescription_text_layer | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | 100.0% |
| flag_as_arrow | 1 | 84.7% | 0 | 0/1 (0.0%) | - | 100.0% |
| flag_as_word | 3 | 96.0% | 0 | 2/2 (100.0%) | - | 100.0% |
| flag_beside_result | 3 | 90.0% | 2 | 2/2 (100.0%) | - | 100.0% |
| generic_name | 1 | 79.6% | 1 | 2/2 (100.0%) | 2/2 (100.0%) | 97.6% |
| header_as_table | 1 | 100.0% | 0 | 1/1 (100.0%) | - | 100.0% |
| header_fields_on_one_line | 4 | 93.4% | 0 | 3/4 (75.0%) | 7/7 (100.0%) | 100.0% |
| history | 4 | 77.9% | 6 | 6/6 (100.0%) | 8/8 (100.0%) | 99.4% |
| image_only_pdf | 4 | 90.7% | 2 | 2/2 (100.0%) | - | 100.0% |
| impression | 1 | 67.3% | 3 | 1/1 (100.0%) | 2/2 (100.0%) | 100.0% |
| indian_digit_grouping | 1 | 93.0% | 0 | 1/2 (50.0%) | - | 100.0% |
| iso_date | 1 | 84.7% | 0 | 0/1 (0.0%) | - | 100.0% |
| jpeg | 1 | 67.3% | 3 | 1/1 (100.0%) | 2/2 (100.0%) | 100.0% |
| lab_report_as_image | 1 | 84.7% | 0 | 0/1 (0.0%) | - | 100.0% |
| less_than_value | 1 | 97.2% | 0 | - | - | 100.0% |
| medicine_without_strength | 1 | 72.5% | 2 | 2/2 (100.0%) | 1/1 (100.0%) | 100.0% |
| medicines_as_numbered_lines | 1 | 95.1% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | 100.0% |
| multi_page | 7 | 93.1% | 2 | 4/6 (66.7%) | - | 100.0% |
| multiple_ranges | 6 | 92.8% | 2 | 4/5 (80.0%) | - | 100.0% |
| named_frequency | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | 100.0% |
| no_known_allergies | 3 | 82.5% | 3 | 5/5 (100.0%) | 7/7 (100.0%) | 99.3% |
| no_report_date | 1 | 79.6% | 2 | 2/2 (100.0%) | - | 100.0% |
| non_numeric_values | 3 | 94.0% | 0 | 1/2 (50.0%) | - | 100.0% |
| one_sided_range | 7 | 93.2% | 0 | 4/6 (66.7%) | - | 100.0% |
| page_parsed_in_parts | 1 | 96.5% | 0 | - | - | 100.0% |
| panel_remark | 1 | 97.7% | 0 | 1/1 (100.0%) | - | 100.0% |
| panel_remark_after_last_row | 2 | 93.0% | 0 | 1/3 (33.3%) | - | 100.0% |
| patient_mismatch | 1 | 95.7% | 0 | - | - | 100.0% |
| printed_flag_without_computed_flag | 1 | 85.7% | 0 | - | - | 100.0% |
| provisional_diagnosis | 1 | 89.1% | 1 | 2/2 (100.0%) | 3/3 (100.0%) | 100.0% |
| range_before_unit | 2 | 95.9% | 0 | 1/1 (100.0%) | - | 100.0% |
| results_as_lines | 1 | 96.2% | 0 | 1/1 (100.0%) | - | 100.0% |
| route_given | 1 | 95.1% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | 100.0% |
| single_creatinine | 1 | 96.8% | 0 | 1/1 (100.0%) | - | 100.0% |
| sixty_results | 1 | 96.5% | 0 | - | - | 100.0% |
| statin_without_lipid_panel | 1 | 89.1% | 1 | 2/2 (100.0%) | 3/3 (100.0%) | 100.0% |
| strength_in_own_column | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | 100.0% |
| table_continued_on_next_page | 1 | 92.2% | 0 | - | - | 100.0% |
| temperature | 1 | 92.5% | 1 | 1/1 (100.0%) | 0/1 (0.0%) | 96.8% |
| test_name_variant | 2 | 98.1% | 0 | 2/2 (100.0%) | - | 100.0% |
| test_remark | 3 | 88.9% | 2 | 3/5 (60.0%) | - | 100.0% |
| trend_two_values | 5 | 95.2% | 0 | 1/2 (50.0%) | - | 100.0% |
| two_column_layout | 1 | 96.5% | 0 | 1/1 (100.0%) | 3/3 (100.0%) | 100.0% |
| two_diagnoses_one_line | 2 | 83.9% | 2 | 3/3 (100.0%) | 5/5 (100.0%) | 100.0% |
| undated | 1 | 100.0% | 0 | - | 2/2 (100.0%) | 100.0% |
| unused_complaint_line | 2 | 83.4% | 2 | 3/3 (100.0%) | 4/4 (100.0%) | 100.0% |
| unused_date_line | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | 100.0% |
| uploaded_before_older_documents | 2 | 78.1% | 3 | 2/2 (100.0%) | 4/4 (100.0%) | 100.0% |
| vitals | 3 | 86.2% | 2 | 4/4 (100.0%) | 9/9 (100.0%) | 100.0% |
| weekly_frequency | 2 | 95.6% | 0 | 2/2 (100.0%) | 5/5 (100.0%) | 100.0% |

### `gemma4:e2b`

| Case | Docs | Field F1 | Unsafe | Attribution | Frequency | Value recall |
| --- | --- | --- | --- | --- | --- | --- |
| as_needed | 2 | 76.9% | 4 | 2/2 (100.0%) | 6/6 (100.0%) | 98.0% |
| bmi_and_height | 1 | 89.1% | 1 | 2/2 (100.0%) | 3/3 (100.0%) | 100.0% |
| bun_in_kidney_panel | 1 | 96.1% | 0 | 1/1 (100.0%) | - | 100.0% |
| classified_other | 1 | - | 0 | - | - | - |
| counts_in_other_units | 1 | 96.1% | 1 | 1/1 (100.0%) | - | 98.0% |
| date_month_in_words | 2 | 92.1% | 0 | 2/2 (100.0%) | 4/4 (100.0%) | 97.8% |
| date_with_dots | 1 | 94.1% | 0 | 1/1 (100.0%) | 3/3 (100.0%) | 97.4% |
| dense_page_over_output_cap | 1 | 95.1% | 2 | - | - | 98.7% |
| document_remark | 3 | 94.9% | 1 | 3/3 (100.0%) | - | 98.1% |
| document_remark_after_single_test | 1 | 82.0% | 1 | 1/2 (50.0%) | - | 89.6% |
| drug_allergy | 2 | 91.0% | 3 | 3/3 (100.0%) | 4/4 (100.0%) | 97.5% |
| duration_in_days | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | 100.0% |
| duration_in_months | 2 | 77.1% | 5 | 3/3 (100.0%) | 5/5 (100.0%) | 99.0% |
| duration_in_weeks | 3 | 92.9% | 3 | 3/3 (100.0%) | 5/6 (83.3%) | 96.1% |
| duration_not_given | 1 | 21.6% | 2 | - | 0/1 (0.0%) | 94.4% |
| e_prescription_text_layer | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | 100.0% |
| flag_as_arrow | 1 | 89.1% | 0 | 0/1 (0.0%) | - | 100.0% |
| flag_as_word | 3 | 92.9% | 2 | 2/2 (100.0%) | - | 97.4% |
| flag_beside_result | 3 | 90.1% | 2 | 1/2 (50.0%) | - | 94.4% |
| generic_name | 1 | 91.8% | 0 | 2/2 (100.0%) | 3/3 (100.0%) | 100.0% |
| header_as_table | 1 | 96.1% | 1 | 1/1 (100.0%) | - | 98.0% |
| header_fields_on_one_line | 4 | 91.2% | 0 | 3/4 (75.0%) | 7/7 (100.0%) | 97.8% |
| history | 4 | 86.5% | 4 | 6/6 (100.0%) | 11/11 (100.0%) | 98.9% |
| image_only_pdf | 4 | 90.2% | 3 | 1/2 (50.0%) | - | 95.6% |
| impression | 1 | 65.5% | 4 | 1/1 (100.0%) | 2/2 (100.0%) | 98.1% |
| indian_digit_grouping | 1 | 81.5% | 8 | 1/2 (50.0%) | - | 100.0% |
| iso_date | 1 | 89.1% | 0 | 0/1 (0.0%) | - | 100.0% |
| jpeg | 1 | 65.5% | 4 | 1/1 (100.0%) | 2/2 (100.0%) | 98.1% |
| lab_report_as_image | 1 | 89.1% | 0 | 0/1 (0.0%) | - | 100.0% |
| less_than_value | 1 | 97.2% | 0 | - | - | 98.1% |
| medicine_without_strength | 1 | 98.1% | 0 | 2/2 (100.0%) | 3/3 (100.0%) | 100.0% |
| medicines_as_numbered_lines | 1 | 89.3% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | 97.9% |
| multi_page | 7 | 89.8% | 14 | 3/6 (50.0%) | - | 98.7% |
| multiple_ranges | 6 | 88.7% | 14 | 3/5 (60.0%) | - | 98.2% |
| named_frequency | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | 100.0% |
| no_known_allergies | 3 | 93.1% | 0 | 5/5 (100.0%) | 10/10 (100.0%) | 99.3% |
| no_report_date | 1 | 82.0% | 1 | 1/2 (50.0%) | - | 89.6% |
| non_numeric_values | 3 | 89.3% | 12 | 1/2 (50.0%) | - | 99.2% |
| one_sided_range | 7 | 88.4% | 11 | 4/6 (66.7%) | - | 99.2% |
| page_parsed_in_parts | 1 | 95.1% | 2 | - | - | 98.7% |
| panel_remark | 1 | 97.7% | 0 | 1/1 (100.0%) | - | 95.2% |
| panel_remark_after_last_row | 2 | 84.6% | 8 | 1/3 (33.3%) | - | 100.0% |
| patient_mismatch | 1 | 91.3% | 1 | - | - | 95.7% |
| printed_flag_without_computed_flag | 1 | 81.0% | 1 | - | - | 95.5% |
| provisional_diagnosis | 1 | 89.1% | 1 | 2/2 (100.0%) | 3/3 (100.0%) | 100.0% |
| range_before_unit | 2 | 93.1% | 2 | 1/1 (100.0%) | - | 98.1% |
| results_as_lines | 1 | 92.5% | 0 | 1/1 (100.0%) | - | 96.1% |
| route_given | 1 | 89.3% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | 97.9% |
| single_creatinine | 1 | 96.1% | 0 | 1/1 (100.0%) | - | 100.0% |
| sixty_results | 1 | 95.1% | 2 | - | - | 98.7% |
| statin_without_lipid_panel | 1 | 89.1% | 1 | 2/2 (100.0%) | 3/3 (100.0%) | 100.0% |
| strength_in_own_column | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | 100.0% |
| table_continued_on_next_page | 1 | 90.4% | 1 | - | - | 98.2% |
| temperature | 1 | 94.1% | 2 | 1/1 (100.0%) | 1/1 (100.0%) | 93.5% |
| test_name_variant | 2 | 94.2% | 1 | 2/2 (100.0%) | - | 97.0% |
| test_remark | 3 | 82.9% | 9 | 2/5 (40.0%) | - | 98.0% |
| trend_two_values | 5 | 93.9% | 4 | 1/2 (50.0%) | - | 98.4% |
| two_column_layout | 1 | 94.1% | 0 | 1/1 (100.0%) | 3/3 (100.0%) | 97.4% |
| two_diagnoses_one_line | 2 | 93.7% | 0 | 3/3 (100.0%) | 7/7 (100.0%) | 98.9% |
| undated | 1 | 21.6% | 2 | - | 0/1 (0.0%) | 94.4% |
| unused_complaint_line | 2 | 96.3% | 0 | 3/3 (100.0%) | 6/6 (100.0%) | 98.8% |
| unused_date_line | 1 | 91.8% | 0 | 1/1 (100.0%) | 4/4 (100.0%) | 100.0% |
| uploaded_before_older_documents | 2 | 75.3% | 5 | 2/2 (100.0%) | 3/4 (75.0%) | 97.7% |
| vitals | 3 | 93.2% | 0 | 4/4 (100.0%) | 11/11 (100.0%) | 99.2% |
| weekly_frequency | 2 | 92.4% | 1 | 2/2 (100.0%) | 4/5 (80.0%) | 97.2% |

## By document

### `qwen3.5:4b`

| Doc | Type | Type right | Field F1 | Unsafe | Missed | Invented | CER | Value recall | Time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d01 | lab_report | yes | 93.0% | 0 | 0 | 1 | 0.0% | 100.0% | 1732 s |
| d02 | prescription | yes | 72.5% | 2 | 2 | 2 | 0.0% | 100.0% | 344 s |
| d03 | lab_report | yes | 79.6% | 2 | 0 | 1 | 0.0% | 100.0% | 1125 s |
| d04 | prescription | yes | 67.3% | 3 | 3 | 3 | 0.1% | 100.0% | 348 s |
| d05 | lab_report | yes | 93.1% | 0 | 0 | 0 | 0.0% | 100.0% | 794 s |
| d06 | lab_report | yes | 96.8% | 0 | 0 | 0 | 0.0% | 100.0% | 783 s |
| d07 | prescription | yes | 89.1% | 1 | 2 | 2 | 0.0% | 100.0% | 655 s |
| d08 | lab_report | yes | 97.2% | 0 | 0 | 0 | 0.0% | 100.0% | 489 s |
| d09 | lab_report | yes | 97.7% | 0 | 0 | 0 | 0.0% | 100.0% | 479 s |
| d10 | prescription | yes | 92.5% | 1 | 0 | 0 | 1.0% | 96.8% | 267 s |
| d11 | lab_report | yes | 85.7% | 0 | 0 | 0 | 0.0% | 100.0% | 393 s |
| d12 | prescription | yes | 94.6% | 0 | 0 | 0 | 0.0% | 100.0% | 273 s |
| d13 | lab_report | yes | 84.1% | 0 | 0 | 1 | 0.0% | 100.0% | 820 s |
| d14 | prescription | yes | 79.6% | 1 | 2 | 3 | 0.1% | 97.6% | 640 s |
| d15 | prescription | yes | 100.0% | 0 | 0 | 0 | 0.0% | 100.0% | 201 s |
| d16 | lab_report | yes | 95.7% | 0 | 0 | 0 | 0.0% | 100.0% | 387 s |
| d17 | other | yes | - | 0 | 0 | 0 | 5.3% | - | 223 s |
| d18 | lab_report | yes | 96.2% | 0 | 0 | 0 | 0.0% | 100.0% | 520 s |
| d19 | prescription | yes | 95.1% | 0 | 0 | 0 | 0.0% | 100.0% | 633 s |
| d20 | lab_report | yes | 100.0% | 0 | 0 | 0 | 0.1% | 100.0% | 848 s |
| d21 | prescription | yes | 96.5% | 0 | 0 | 0 | 19.4% | 100.0% | 313 s |
| d22 | lab_report | yes | 84.7% | 0 | 0 | 0 | 0.0% | 100.0% | 1087 s |
| d23 | lab_report | yes | 92.2% | 0 | 0 | 1 | 0.0% | 100.0% | 749 s |
| d24 | prescription | yes | 91.8% | 0 | 1 | 2 | 0.0% | 100.0% | 672 s |
| d25 | lab_report | yes | 96.5% | 0 | 0 | 2 | 25.0% | 100.0% | 8419 s |

### `gemma4:e2b`

| Doc | Type | Type right | Field F1 | Unsafe | Missed | Invented | CER | Value recall | Time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d01 | lab_report | yes | 81.5% | 8 | 0 | 10 | 0.0% | 100.0% | 3556 s |
| d02 | prescription | yes | 98.1% | 0 | 0 | 0 | 0.0% | 100.0% | 787 s |
| d03 | lab_report | yes | 82.0% | 1 | 0 | 1 | 1.0% | 89.6% | 594 s |
| d04 | prescription | yes | 65.5% | 4 | 3 | 3 | 0.3% | 98.1% | 476 s |
| d05 | lab_report | yes | 93.1% | 0 | 0 | 0 | 0.0% | 100.0% | 661 s |
| d06 | lab_report | yes | 96.1% | 0 | 0 | 1 | 0.0% | 100.0% | 711 s |
| d07 | prescription | yes | 89.1% | 1 | 2 | 2 | 0.0% | 100.0% | 805 s |
| d08 | lab_report | yes | 97.2% | 0 | 0 | 0 | 0.3% | 98.1% | 520 s |
| d09 | lab_report | yes | 97.7% | 0 | 0 | 0 | 1.4% | 95.2% | 1023 s |
| d10 | prescription | yes | 94.1% | 2 | 0 | 0 | 1.4% | 93.5% | 349 s |
| d11 | lab_report | yes | 81.0% | 1 | 0 | 0 | 1.1% | 95.5% | 266 s |
| d12 | prescription | yes | 90.4% | 1 | 0 | 0 | 0.3% | 97.0% | 318 s |
| d13 | lab_report | yes | 82.5% | 2 | 0 | 1 | 0.8% | 100.0% | 637 s |
| d14 | prescription | yes | 91.8% | 0 | 1 | 2 | 0.0% | 100.0% | 434 s |
| d15 | prescription | yes | 21.6% | 2 | 1 | 4 | 0.4% | 94.4% | 53 s |
| d16 | lab_report | yes | 91.3% | 1 | 0 | 0 | 0.2% | 95.7% | 291 s |
| d17 | other | yes | - | 0 | 0 | 0 | 0.2% | - | 44 s |
| d18 | lab_report | yes | 92.5% | 0 | 0 | 0 | 0.9% | 96.1% | 476 s |
| d19 | prescription | yes | 89.3% | 0 | 0 | 0 | 1.6% | 97.9% | 673 s |
| d20 | lab_report | yes | 96.1% | 1 | 0 | 0 | 0.2% | 98.0% | 830 s |
| d21 | prescription | yes | 94.1% | 0 | 0 | 0 | 19.2% | 97.4% | 427 s |
| d22 | lab_report | yes | 89.1% | 0 | 0 | 0 | 0.0% | 100.0% | 863 s |
| d23 | lab_report | yes | 90.4% | 1 | 0 | 1 | 0.5% | 98.2% | 1072 s |
| d24 | prescription | yes | 91.8% | 0 | 1 | 2 | 0.0% | 100.0% | 646 s |
| d25 | lab_report | yes | 95.1% | 2 | 0 | 4 | 25.3% | 98.7% | 7062 s |

## Errors

### `qwen3.5:4b`

- d01 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d02 unsafe: invented medications item `Glycomet 500 mg`
- d02 unsafe: invented medications item `Telma 40 mg`
- d02 missed medications med-1 `Glycomet`
- d02 missed medications med-2 `Telma`
- d03 unsafe: observations obs-1 value_text expected `11.2`, got `11.2 L`
- d03 unsafe: observations obs-4 value_text expected `79`, got `79 L`
- d03 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d04 unsafe: invented medications item `Telma 40 mg`
- d04 unsafe: invented medications item `Pan 40 mg`
- d04 unsafe: invented medications item `Dolo 650 mg`
- d04 missed medications med-2 `Telma`
- d04 missed medications med-4 `Pan`
- d04 missed medications med-5 `Dolo`
- d07 unsafe: invented allergies item `Sulfa drugs`
- d07 missed conditions cond-2 `Chronic kidney disease, to be evaluated`
- d07 missed allergies alg-1 `Sulfa drugs (rash)`
- d07 invented conditions `Chronic kidney disease`
- d10 unsafe: medications med-1 dose expected `1 tab`, got `1-0-0`
- d13 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d14 unsafe: invented medications item `Rosuvus`
- d14 missed medications med-1 `Rosuvas`
- d14 missed conditions cond-1 `CAD, post PTCA (2019)`
- d14 invented conditions `CAD`
- d14 invented conditions `post PTCA (2019)`
- d23 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d24 missed conditions cond-1 `Hypertension, better controlled`
- d24 invented conditions `Hypertension`
- d24 invented conditions `better controlled`
- d25 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d25 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`

### `gemma4:e2b`

- d01 unsafe: invented observations item `HbA1c`
- d01 unsafe: invented observations item `HbA1c`
- d01 unsafe: invented observations item `Fasting Blood Sugar`
- d01 unsafe: invented observations item `Fasting Blood Sugar`
- d01 unsafe: invented observations item `Total Cholesterol`
- d01 unsafe: invented observations item `Triglycerides`
- d01 unsafe: invented observations item `HDL Cholesterol`
- d01 unsafe: invented observations item `Synthetic test document: fictional patient, doctors and laboratory.`
- d01 invented remarks `Method: Hexokinase`
- d01 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d03 unsafe: observations obs-2 unit expected `103/μL`, got `10^3/μL`
- d03 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d04 unsafe: medications med-1 name expected `Glycomet GP 1`, got `Glycomet GP`
- d04 unsafe: invented medications item `Telma 40 mg`
- d04 unsafe: invented medications item `Pan 40 mg`
- d04 unsafe: invented medications item `Dolo 650 mg`
- d04 missed medications med-2 `Telma`
- d04 missed medications med-4 `Pan`
- d04 missed medications med-5 `Dolo`
- d06 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d07 unsafe: invented allergies item `Sulfa drugs`
- d07 missed conditions cond-2 `Chronic kidney disease, to be evaluated`
- d07 missed allergies alg-1 `Sulfa drugs (rash)`
- d07 invented conditions `Chronic kidney disease`
- d10 unsafe: medications med-1 strength expected `50 mcg`, got `50`
- d10 unsafe: medications med-1 dose expected `1 tab`, got `tatcg`
- d11 unsafe: observations obs-1 unit expected `μIU/mL`, got `\u03bcU/mL`
- d12 unsafe: medications med-1 dose expected `1 tab`, got `1-0-0`
- d13 unsafe: observations obs-7 unit expected (none), got `Absent`
- d13 unsafe: observations obs-6 unit expected (none), got `Absent`
- d13 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d14 missed conditions cond-1 `CAD, post PTCA (2019)`
- d14 invented conditions `CAD`
- d14 invented conditions `post PTCA (2019)`
- d15 unsafe: invented medications item `Escoprin`
- d15 unsafe: invented allergies item `Synthetic test document: fictional patient, doctors and laboratory.`
- d15 missed medications med-2 `Ecosprin`
- d15 invented conditions `Age / Sex: 66 Y / M`
- d15 invented remarks `Medication card (continue as advised)`
- d16 unsafe: observations obs-2 unit expected `103/μL`, got `10^3/μL`
- d20 unsafe: observations obs-6 unit expected `103/μL`, got `10^3/μL`
- d23 unsafe: observations obs-8 unit expected `μIU/mL`, got `$μ$IU/mL`
- d23 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d24 missed conditions cond-1 `Hypertension, better controlled`
- d24 invented conditions `Hypertension`
- d24 invented conditions `better controlled`
- d25 unsafe: invented observations item `null`
- d25 unsafe: invented observations item `null`
- d25 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`
- d25 invented remarks `Synthetic test document: fictional patient, doctors and laboratory.`

Every field error, layout difference and missing transcribed value is in the JSON report.
