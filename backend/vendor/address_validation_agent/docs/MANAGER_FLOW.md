# Manager Explanation — Smarty + Melissa Agent Strategy

## Objective
Build a provider-agnostic CASS validation agent for FTTH address validation. The agent will extract address data from KMZ/KML/CSV until the data extraction agent is ready, validate the same canonical records using both Smarty and Melissa, compare the results, calculate confidence, and produce a trusted address layer for downstream agents.

## Why one common pipeline
We should not create two separate pipelines. Extraction, parsing, normalization, deduplication, scoring, exception routing, and output are common. Only provider-specific adapters differ.

## Flow
1. Input files: KMZ/KML/CSV.
2. Temporary extraction: extract address, city, state, ZIP, lat/lon, network node, terminal ID.
3. Canonicalization: normalize address into a common schema.
4. Smarty adapter: validate address using Smarty.
5. Melissa adapter: validate the same address using Melissa.
6. Normalize provider outputs into one internal schema.
7. Compare DPV, ZIP+4, vacancy, record type, geocode precision, and unit handling.
8. Choose best provider result or route to manual review if conflict is high.
9. Generate confidence score and structure hint.
10. Export final CSV/JSON/Excel for downstream parcel matching, structure classification, Street View validation, and network design.

## Why Smarty and Melissa
Both are CASS/USPS validation providers but can return different results for rural addresses, secondary/unit addresses, vacancy, rooftop precision, and DPV signals. The POC benchmarks them on the same FTTH data to identify the best provider or hybrid strategy.

## POC Deliverable
Smarty vs Melissa comparison report for 20–50 addresses from the provided FTTH dataset, with selected provider, confidence score, and exception reason.
