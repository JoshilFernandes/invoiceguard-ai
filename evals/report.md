# InvoiceGuard eval report

Extractor: `rules` · 10 cases · 0.28 s

| Metric | Value | Gate |
|---|---|---|
| routing_accuracy | 1.0 | ≥ 0.9 |
| field_accuracy | 1.0 | ≥ 0.95 |
| flag_recall | 1.0 | ≥ 0.9 |
| unsafe_auto_approvals | 0 | ≤ 0 |
| straight_through_rate | 0.3 | — |
| audit_chain_valid | True | true |

| Case | Expected | Got | Pass | Notes |
|---|---|---|---|---|
| 01_clean_office_supplies.pdf | auto_approve | auto_approve | ✅ | — |
| 02_xrechnung_cloud_hosting.xml | auto_approve | auto_approve | ✅ | — |
| 03_vat_miscalculated.pdf | human_review | human_review | ✅ | — |
| 04_bank_change_fraud.eml | human_review | human_review | ✅ | — |
| 05_duplicate_invoice.pdf | reject | reject | ✅ | — |
| 06_price_and_quantity_variance.pdf | human_review | human_review | ✅ | — |
| 07_prompt_injection.pdf | human_review | human_review | ✅ | — |
| 08_unknown_vendor_no_po.pdf | human_review | human_review | ✅ | — |
| 09_delivery_note.pdf | reject | reject | ✅ | — |
| 10_zugferd_cii_toner.xml | auto_approve | auto_approve | ✅ | — |

**Result: PASS**
