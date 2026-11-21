# Eval results: suite-vision

Generated 2026-11-21T23:05:54+00:00 on dataset med-sum synthetic v2; stage vision (reading the pages only); models: `qwen3.5:4b`, `gemma4:e2b`.

## Model choice

A vision run chooses no model. The primary must also read at least 95% of the answer keys' printed values:
- `qwen3.5:4b`: passes.
- `gemma4:e2b`: passes.

## Overview

| Metric | qwen3.5:4b | gemma4:e2b |
| --- | --- | --- |
| Documents | 0 | 0 |
| Transcription CER | 11.0% | 11.3% |
| Transcription line F1 | 88.6% | 85.4% |
| Transcription value recall | 1443/1445 (99.9%) | 1420/1445 (98.3%) |
| Layout kept | 601/618 (97.2%) | 515/596 (86.4%) |
| Unreadable lines | 0 | 0 |
| Median time per document | 232 s | 45 s |

## By split

Dev documents were used while writing and checking the prompts; the test documents were not.

| Split | Model | Docs | Field F1 | Unsafe | Value recall |
| --- | --- | --- | --- | --- | --- |
| dev | `qwen3.5:4b` | 0 | - | - | 100.0% |
| dev | `gemma4:e2b` | 0 | - | - | 97.7% |
| held_out | `qwen3.5:4b` | 0 | - | - | 100.0% |
| held_out | `gemma4:e2b` | 0 | - | - | 98.2% |
| test | `qwen3.5:4b` | 0 | - | - | 99.8% |
| test | `gemma4:e2b` | 0 | - | - | 98.4% |

## By case

Field F1, unsafe errors, and (where the case has them) remark attribution, frequency exactness and transcription value recall, over the documents that show each case.

### `qwen3.5:4b`

| Case | Docs | Field F1 | Unsafe | Attribution | Frequency | Value recall |
| --- | --- | --- | --- | --- | --- | --- |
| as_needed | 0 | - | 0 | - | - | 100.0% |
| bmi_and_height | 0 | - | 0 | - | - | 100.0% |
| bun_in_kidney_panel | 0 | - | 0 | - | - | 100.0% |
| classified_other | 0 | - | 0 | - | - | - |
| counts_in_other_units | 0 | - | 0 | - | - | 100.0% |
| date_month_in_words | 0 | - | 0 | - | - | 100.0% |
| date_with_dots | 0 | - | 0 | - | - | 100.0% |
| dense_page_over_output_cap | 0 | - | 0 | - | - | 100.0% |
| document_remark | 0 | - | 0 | - | - | 100.0% |
| document_remark_after_single_test | 0 | - | 0 | - | - | 100.0% |
| drug_allergy | 0 | - | 0 | - | - | 98.8% |
| duration_in_days | 0 | - | 0 | - | - | 100.0% |
| duration_in_months | 0 | - | 0 | - | - | 100.0% |
| duration_in_weeks | 0 | - | 0 | - | - | 99.0% |
| duration_not_given | 0 | - | 0 | - | - | 100.0% |
| e_prescription_text_layer | 0 | - | 0 | - | - | 100.0% |
| flag_as_arrow | 0 | - | 0 | - | - | 100.0% |
| flag_as_word | 0 | - | 0 | - | - | 100.0% |
| flag_beside_result | 0 | - | 0 | - | - | 100.0% |
| generic_name | 0 | - | 0 | - | - | 97.6% |
| header_as_table | 0 | - | 0 | - | - | 100.0% |
| header_fields_on_one_line | 0 | - | 0 | - | - | 100.0% |
| history | 0 | - | 0 | - | - | 99.4% |
| image_only_pdf | 0 | - | 0 | - | - | 100.0% |
| impression | 0 | - | 0 | - | - | 100.0% |
| indian_digit_grouping | 0 | - | 0 | - | - | 100.0% |
| iso_date | 0 | - | 0 | - | - | 100.0% |
| jpeg | 0 | - | 0 | - | - | 100.0% |
| lab_report_as_image | 0 | - | 0 | - | - | 100.0% |
| less_than_value | 0 | - | 0 | - | - | 100.0% |
| medicine_without_strength | 0 | - | 0 | - | - | 100.0% |
| medicines_as_numbered_lines | 0 | - | 0 | - | - | 100.0% |
| multi_page | 0 | - | 0 | - | - | 100.0% |
| multiple_ranges | 0 | - | 0 | - | - | 100.0% |
| named_frequency | 0 | - | 0 | - | - | 100.0% |
| no_known_allergies | 0 | - | 0 | - | - | 99.3% |
| no_report_date | 0 | - | 0 | - | - | 100.0% |
| non_numeric_values | 0 | - | 0 | - | - | 100.0% |
| one_sided_range | 0 | - | 0 | - | - | 100.0% |
| page_parsed_in_parts | 0 | - | 0 | - | - | 100.0% |
| panel_remark | 0 | - | 0 | - | - | 100.0% |
| panel_remark_after_last_row | 0 | - | 0 | - | - | 100.0% |
| patient_mismatch | 0 | - | 0 | - | - | 100.0% |
| printed_flag_without_computed_flag | 0 | - | 0 | - | - | 100.0% |
| provisional_diagnosis | 0 | - | 0 | - | - | 100.0% |
| range_before_unit | 0 | - | 0 | - | - | 100.0% |
| results_as_lines | 0 | - | 0 | - | - | 100.0% |
| route_given | 0 | - | 0 | - | - | 100.0% |
| single_creatinine | 0 | - | 0 | - | - | 100.0% |
| sixty_results | 0 | - | 0 | - | - | 100.0% |
| statin_without_lipid_panel | 0 | - | 0 | - | - | 100.0% |
| strength_in_own_column | 0 | - | 0 | - | - | 100.0% |
| table_continued_on_next_page | 0 | - | 0 | - | - | 100.0% |
| temperature | 0 | - | 0 | - | - | 96.8% |
| test_name_variant | 0 | - | 0 | - | - | 100.0% |
| test_remark | 0 | - | 0 | - | - | 100.0% |
| trend_two_values | 0 | - | 0 | - | - | 100.0% |
| two_column_layout | 0 | - | 0 | - | - | 100.0% |
| two_diagnoses_one_line | 0 | - | 0 | - | - | 100.0% |
| undated | 0 | - | 0 | - | - | 100.0% |
| unused_complaint_line | 0 | - | 0 | - | - | 100.0% |
| unused_date_line | 0 | - | 0 | - | - | 100.0% |
| uploaded_before_older_documents | 0 | - | 0 | - | - | 100.0% |
| vitals | 0 | - | 0 | - | - | 100.0% |
| weekly_frequency | 0 | - | 0 | - | - | 100.0% |

### `gemma4:e2b`

| Case | Docs | Field F1 | Unsafe | Attribution | Frequency | Value recall |
| --- | --- | --- | --- | --- | --- | --- |
| as_needed | 0 | - | 0 | - | - | 98.0% |
| bmi_and_height | 0 | - | 0 | - | - | 100.0% |
| bun_in_kidney_panel | 0 | - | 0 | - | - | 100.0% |
| classified_other | 0 | - | 0 | - | - | - |
| counts_in_other_units | 0 | - | 0 | - | - | 98.0% |
| date_month_in_words | 0 | - | 0 | - | - | 97.8% |
| date_with_dots | 0 | - | 0 | - | - | 97.4% |
| dense_page_over_output_cap | 0 | - | 0 | - | - | 98.7% |
| document_remark | 0 | - | 0 | - | - | 98.1% |
| document_remark_after_single_test | 0 | - | 0 | - | - | 89.6% |
| drug_allergy | 0 | - | 0 | - | - | 97.5% |
| duration_in_days | 0 | - | 0 | - | - | 100.0% |
| duration_in_months | 0 | - | 0 | - | - | 99.0% |
| duration_in_weeks | 0 | - | 0 | - | - | 96.1% |
| duration_not_given | 0 | - | 0 | - | - | 94.4% |
| e_prescription_text_layer | 0 | - | 0 | - | - | 100.0% |
| flag_as_arrow | 0 | - | 0 | - | - | 100.0% |
| flag_as_word | 0 | - | 0 | - | - | 97.4% |
| flag_beside_result | 0 | - | 0 | - | - | 94.4% |
| generic_name | 0 | - | 0 | - | - | 100.0% |
| header_as_table | 0 | - | 0 | - | - | 98.0% |
| header_fields_on_one_line | 0 | - | 0 | - | - | 97.8% |
| history | 0 | - | 0 | - | - | 98.9% |
| image_only_pdf | 0 | - | 0 | - | - | 95.6% |
| impression | 0 | - | 0 | - | - | 98.1% |
| indian_digit_grouping | 0 | - | 0 | - | - | 100.0% |
| iso_date | 0 | - | 0 | - | - | 100.0% |
| jpeg | 0 | - | 0 | - | - | 98.1% |
| lab_report_as_image | 0 | - | 0 | - | - | 100.0% |
| less_than_value | 0 | - | 0 | - | - | 98.1% |
| medicine_without_strength | 0 | - | 0 | - | - | 100.0% |
| medicines_as_numbered_lines | 0 | - | 0 | - | - | 97.9% |
| multi_page | 0 | - | 0 | - | - | 98.7% |
| multiple_ranges | 0 | - | 0 | - | - | 98.2% |
| named_frequency | 0 | - | 0 | - | - | 100.0% |
| no_known_allergies | 0 | - | 0 | - | - | 99.3% |
| no_report_date | 0 | - | 0 | - | - | 89.6% |
| non_numeric_values | 0 | - | 0 | - | - | 99.2% |
| one_sided_range | 0 | - | 0 | - | - | 99.2% |
| page_parsed_in_parts | 0 | - | 0 | - | - | 98.7% |
| panel_remark | 0 | - | 0 | - | - | 95.2% |
| panel_remark_after_last_row | 0 | - | 0 | - | - | 100.0% |
| patient_mismatch | 0 | - | 0 | - | - | 95.7% |
| printed_flag_without_computed_flag | 0 | - | 0 | - | - | 95.5% |
| provisional_diagnosis | 0 | - | 0 | - | - | 100.0% |
| range_before_unit | 0 | - | 0 | - | - | 98.1% |
| results_as_lines | 0 | - | 0 | - | - | 96.1% |
| route_given | 0 | - | 0 | - | - | 97.9% |
| single_creatinine | 0 | - | 0 | - | - | 100.0% |
| sixty_results | 0 | - | 0 | - | - | 98.7% |
| statin_without_lipid_panel | 0 | - | 0 | - | - | 100.0% |
| strength_in_own_column | 0 | - | 0 | - | - | 100.0% |
| table_continued_on_next_page | 0 | - | 0 | - | - | 98.2% |
| temperature | 0 | - | 0 | - | - | 93.5% |
| test_name_variant | 0 | - | 0 | - | - | 97.0% |
| test_remark | 0 | - | 0 | - | - | 98.0% |
| trend_two_values | 0 | - | 0 | - | - | 98.4% |
| two_column_layout | 0 | - | 0 | - | - | 97.4% |
| two_diagnoses_one_line | 0 | - | 0 | - | - | 98.9% |
| undated | 0 | - | 0 | - | - | 94.4% |
| unused_complaint_line | 0 | - | 0 | - | - | 98.8% |
| unused_date_line | 0 | - | 0 | - | - | 100.0% |
| uploaded_before_older_documents | 0 | - | 0 | - | - | 97.7% |
| vitals | 0 | - | 0 | - | - | 99.2% |
| weekly_frequency | 0 | - | 0 | - | - | 97.2% |

## By document

### `qwen3.5:4b`

| Doc | CER | Line F1 | Value recall | Layout | Unreadable | Time |
| --- | --- | --- | --- | --- | --- | --- |
| d01 | 0.0% | 100.0% | 156/156 (100.0%) | 56/56 (100.0%) | 0 | 717 s |
| d02 | 0.0% | 100.0% | 45/45 (100.0%) | 22/22 (100.0%) | 0 | 79 s |
| d03 | 0.0% | 100.0% | 48/48 (100.0%) | 27/27 (100.0%) | 0 | 446 s |
| d04 | 0.1% | 95.5% | 53/53 (100.0%) | 19/21 (90.5%) | 0 | 80 s |
| d05 | 0.0% | 100.0% | 65/65 (100.0%) | 31/31 (100.0%) | 0 | 464 s |
| d06 | 0.0% | 100.0% | 60/60 (100.0%) | 30/30 (100.0%) | 0 | 456 s |
| d07 | 0.0% | 100.0% | 49/49 (100.0%) | 20/22 (90.9%) | 0 | 78 s |
| d08 | 0.0% | 100.0% | 54/54 (100.0%) | 25/25 (100.0%) | 0 | 249 s |
| d09 | 0.0% | 100.0% | 42/42 (100.0%) | 22/22 (100.0%) | 0 | 248 s |
| d10 | 1.0% | 94.1% | 30/31 (96.8%) | 14/16 (87.5%) | 0 | 69 s |
| d11 | 0.0% | 100.0% | 22/22 (100.0%) | 17/17 (100.0%) | 0 | 234 s |
| d12 | 0.0% | 100.0% | 33/33 (100.0%) | 16/18 (88.9%) | 0 | 70 s |
| d13 | 0.0% | 100.0% | 65/65 (100.0%) | 30/30 (100.0%) | 0 | 462 s |
| d14 | 0.1% | 95.2% | 41/42 (97.6%) | 18/20 (90.0%) | 0 | 76 s |
| d15 | 0.0% | 100.0% | 18/18 (100.0%) | 12/14 (85.7%) | 0 | 63 s |
| d16 | 0.0% | 100.0% | 23/23 (100.0%) | 15/15 (100.0%) | 0 | 225 s |
| d17 | 5.3% | 43.5% | - | 5/5 (100.0%) | 0 | 215 s |
| d18 | 0.0% | 100.0% | 51/51 (100.0%) | 17/17 (100.0%) | 0 | 243 s |
| d19 | 0.0% | 100.0% | 48/48 (100.0%) | 17/17 (100.0%) | 0 | 74 s |
| d20 | 0.1% | 95.0% | 49/49 (100.0%) | 19/19 (100.0%) | 0 | 252 s |
| d21 | 19.4% | 100.0% | 39/39 (100.0%) | 15/15 (100.0%) | 0 | 124 s |
| d22 | 0.0% | 100.0% | 45/45 (100.0%) | 20/20 (100.0%) | 0 | 133 s |
| d23 | 0.0% | 100.0% | 56/56 (100.0%) | 24/24 (100.0%) | 0 | 455 s |
| d24 | 0.0% | 100.0% | 39/39 (100.0%) | 18/18 (100.0%) | 0 | 232 s |
| d25 | 25.0% | 58.4% | 312/312 (100.0%) | 92/97 (94.8%) | 0 | 1747 s |

### `gemma4:e2b`

| Doc | CER | Line F1 | Value recall | Layout | Unreadable | Time |
| --- | --- | --- | --- | --- | --- | --- |
| d01 | 0.0% | 100.0% | 156/156 (100.0%) | 46/56 (82.1%) | 0 | 149 s |
| d02 | 0.0% | 100.0% | 45/45 (100.0%) | 20/22 (90.9%) | 0 | 22 s |
| d03 | 1.0% | 81.5% | 43/48 (89.6%) | 17/22 (77.3%) | 0 | 89 s |
| d04 | 0.3% | 95.5% | 52/53 (98.1%) | 19/21 (90.5%) | 0 | 23 s |
| d05 | 0.0% | 100.0% | 65/65 (100.0%) | 26/31 (83.9%) | 0 | 93 s |
| d06 | 0.0% | 100.0% | 60/60 (100.0%) | 25/30 (83.3%) | 0 | 90 s |
| d07 | 0.0% | 100.0% | 49/49 (100.0%) | 20/22 (90.9%) | 0 | 22 s |
| d08 | 0.3% | 96.0% | 53/54 (98.1%) | 20/24 (83.3%) | 0 | 49 s |
| d09 | 1.4% | 90.9% | 40/42 (95.2%) | 17/20 (85.0%) | 0 | 49 s |
| d10 | 1.4% | 94.1% | 29/31 (93.5%) | 14/16 (87.5%) | 0 | 20 s |
| d11 | 1.1% | 94.1% | 21/22 (95.5%) | 13/16 (81.2%) | 0 | 47 s |
| d12 | 0.3% | 94.4% | 32/33 (97.0%) | 15/17 (88.2%) | 0 | 20 s |
| d13 | 0.8% | 93.3% | 65/65 (100.0%) | 25/28 (89.3%) | 0 | 92 s |
| d14 | 0.0% | 100.0% | 42/42 (100.0%) | 19/21 (90.5%) | 0 | 22 s |
| d15 | 0.4% | 92.9% | 17/18 (94.4%) | 10/13 (76.9%) | 0 | 18 s |
| d16 | 0.2% | 93.3% | 22/23 (95.7%) | 12/14 (85.7%) | 0 | 44 s |
| d17 | 0.2% | 87.0% | - | 8/10 (80.0%) | 0 | 42 s |
| d18 | 0.9% | 94.1% | 49/51 (96.1%) | 16/16 (100.0%) | 0 | 48 s |
| d19 | 1.6% | 94.1% | 47/48 (97.9%) | 15/16 (93.8%) | 0 | 21 s |
| d20 | 0.2% | 87.2% | 48/49 (98.0%) | 10/17 (58.8%) | 0 | 50 s |
| d21 | 19.2% | 94.1% | 38/39 (97.4%) | 12/15 (80.0%) | 0 | 40 s |
| d22 | 0.0% | 100.0% | 45/45 (100.0%) | 19/20 (95.0%) | 0 | 40 s |
| d23 | 0.5% | 95.8% | 55/56 (98.2%) | 19/23 (82.6%) | 0 | 87 s |
| d24 | 0.0% | 100.0% | 39/39 (100.0%) | 18/18 (100.0%) | 0 | 45 s |
| d25 | 25.3% | 53.0% | 308/312 (98.7%) | 80/88 (90.9%) | 0 | 299 s |

Every line, layout difference and missing value is in the JSON report.
