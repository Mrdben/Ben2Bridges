# Official Data Sources

Verified on 2026-07-28.

## FHWA National Bridge Inventory

- Pennsylvania 2025 download page:
  <https://www.fhwa.dot.gov/BRIDGE/nbi/ascii2025.cfm>
- NBI record format:
  <https://www.fhwa.dot.gov/bridge/nbi/format.cfm>
- Recording and Coding Guide:
  <https://www.fhwa.dot.gov/bridge/mtguide.pdf>
- FHWA condition definitions:
  <https://www.fhwa.dot.gov/bridge/britab.cfm>

The official Pennsylvania 2025 delimited file contains 23,314 bridge records
and 123 columns. A parsed field-by-field comparison found no semantic data
differences between the official file and `website/Data/PA 2025.csv`. The
project copy is retained to avoid an unnecessary replacement.

## U.S. Census Bureau County Names

- 2025 Pennsylvania Counties Gazetteer:
  <https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_gaz_counties_42.txt>
- 2025 Gazetteer landing page:
  <https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2025.html>

The Gazetteer provides Pennsylvania county GEOIDs and official county names.
The three-digit `county_fips` in `data/pa_counties.csv` is the county portion of
the five-digit Pennsylvania GEOID, whose state prefix is `42`.

## Pennsylvania Department of Transportation Districts

- PennDOT Engineering Districts and county lists:
  <https://www.pa.gov/agencies/penndot/regional-offices>

PennDOT's official county lists are used to assign every Pennsylvania county
to one of the 11 engineering districts numbered 1-6 and 8-12. This derived
district is used for planning filters because NBI Item 2 is an agency district
and contains non-PennDOT codes for a small number of federally submitted
bridges.

## Reproducibility Notes

- Model outputs are not downloaded from any external source; they will be
  supplied by the project model team.
- `data/mock_model_predictions.csv` is synthetic development data and is not
  an official source.
- Source units and exceptional codes are handled according to
  `DATA_CONTRACT.md`.
