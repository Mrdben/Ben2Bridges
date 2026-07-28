# Ben2Bridges Data Contract

## Purpose

This document defines how the deterioration model, cost model, NBI data, and
decision algorithm exchange bridge-level data. The algorithm must be able to
join every prediction to the correct bridge without changing bridge IDs or
guessing measurement units.

## Authoritative NBI Input

The current inference input is:

`website/Data/PA 2025.csv`

It contains 23,314 Pennsylvania bridge records. The authoritative join key is
`STRUCTURE_NUMBER_008`.

The file has been checked against the official FHWA Pennsylvania 2025 NBI
download. The project copy has some numeric formatting differences, such as
`001` becoming `1` and `0` becoming `0.0`, but all 23,314 rows and all 123
columns are semantically identical after parsing.

When reading the NBI file:

1. Read `STRUCTURE_NUMBER_008` as text, never as an integer.
2. Remove leading and trailing whitespace.
3. Preserve all leading zeroes.
4. Do not replace the cleaned bridge ID with a row number.

Example:

```text
Raw NBI value:     "        1PA0099"
Cleaned bridge_id: "1PA0099"
```

## Preferred Model Delivery

The preferred handoff is one combined file named `model_predictions.csv` with
one row per bridge:

| Column | Type | Required | Rules |
|---|---|---:|---|
| `bridge_id` | string | yes | Cleaned `STRUCTURE_NUMBER_008`; non-empty and unique |
| `deterioration_probability` | float | yes | Finite value from 0 through 1, inclusive |
| `predicted_cost` | float | yes | Finite value greater than 0 |
| `cost_unit` | string | yes | One consistent unit for the entire file, normally `USD` |
| `prediction_horizon_years` | integer | yes | Positive and consistent with the deterioration target |
| `model_version` | string | yes | Non-empty identifier such as `v1` or a dated version |

CSV header:

```csv
bridge_id,deterioration_probability,predicted_cost,cost_unit,prediction_horizon_years,model_version
```

The model output should not repeat all NBI columns. The algorithm will join the
predictions back to the authoritative NBI input using `bridge_id`.

## Alternative Two-File Delivery

If the two models cannot produce one combined file, the algorithm can accept:

`deterioration_predictions.csv`

```csv
bridge_id,deterioration_probability,prediction_horizon_years,model_version
```

`cost_predictions.csv`

```csv
bridge_id,predicted_cost,cost_unit,model_version
```

Both files must follow the same bridge-ID cleaning rules. The algorithm will
perform a one-to-one join and report any bridge that appears in only one file.

## Cost Requirement

The budget and every `predicted_cost` must use the same unit. If the model
outputs USD, the website budget must also be entered in USD. If the project uses
relative cost units instead, both the website and all documentation must label
the budget as relative cost units rather than dollars.

The project must describe predicted costs as estimates, not exact engineering
repair costs.

## Validation Before Scoring

The algorithm must validate the prediction file before calculating any scores:

1. Required columns exist.
2. `bridge_id` values are non-empty and unique after whitespace is removed.
3. Every prediction ID exists in the authoritative NBI file.
4. `deterioration_probability` is finite and in `[0, 1]`.
5. `predicted_cost` is finite and greater than zero.
6. One consistent `cost_unit` is used.
7. `prediction_horizon_years` is a positive integer.
8. Prediction coverage is reported as a count and percentage of NBI bridges.

A bridge without both required predictions is ineligible for automatic budget
selection. It must be reported as missing model data rather than silently
deleted or assigned an invented value.

## NBI Fields Used by the Decision Algorithm

The initial decision algorithm is expected to use these fields:

| Decision concept | NBI field |
|---|---|
| Bridge ID | `STRUCTURE_NUMBER_008` |
| PennDOT planning district | Derived from `COUNTY_CODE_003` using `data/pa_counties.csv` |
| County | `COUNTY_CODE_003`, joined to `data/pa_counties.csv` |
| Current condition | `LOWEST_RATING`, `BRIDGE_CONDITION` |
| Component condition details | `DECK_COND_058`, `SUPERSTRUCTURE_COND_059`, `SUBSTRUCTURE_COND_060`, `CULVERT_COND_062` |
| Condition category | `BRIDGE_CONDITION` |
| Traffic volume | `ADT_029` |
| Traffic observation year | `YEAR_ADT_030` |
| Truck percentage | `PERCENT_ADT_TRUCK_109` |
| Detour impact | `DETOUR_KILOS_019` |
| Bridge age | `YEAR_BUILT_027` |
| Structure size | `STRUCTURE_LEN_MT_049`, `DECK_AREA` |
| Map location | `LAT_016`, `LONG_017` |

### Current-condition rule

`LOWEST_RATING` is the FHWA download field containing the lowest applicable
rating among Deck (Item 58), Superstructure (Item 59), Substructure (Item 60),
and Culvert (Item 62). It is the primary current-condition value for scoring.
The component fields are retained for explanations.

Condition code `N` means not applicable. It is not zero and must not be treated
as the worst condition. In particular, Items 58, 59, and 60 are normally `N`
for culverts, while Item 62 contains the applicable culvert condition.

FHWA condition categories are:

| Lowest applicable rating | Category |
|---:|---|
| 7-9 | Good |
| 5-6 | Fair |
| 0-4 | Poor |

Current condition will remain an explicit but lower-weight decision indicator.
This represents present maintenance urgency, while the deterioration model
represents future decline probability.

### Detour rule

`DETOUR_KILOS_019` is the additional bypass/detour length in whole kilometers.
FHWA defines `0` as an available ground-level bypass, `1` for a usable adjacent
twin bridge in the specified case, and `199` for 199 kilometers or more.

The official Pennsylvania 2025 file contains one value of `999`, although the
FHWA Item 19 guide does not define `999` as valid. The algorithm must treat this
one value as invalid/missing, attach a data-quality warning, and must not award
it the maximum detour-impact score.

### Geography rule

`COUNTY_CODE_003` is a three-digit county FIPS code. It must be zero-padded and
joined to `data/pa_counties.csv` for a county name and PennDOT district.

The raw `HIGHWAY_DISTRICT_002` field is the submitting highway agency's
district. A small number of federally submitted bridges use codes outside the
PennDOT district system. For consistent website filtering, `penndot_district`
will therefore be derived from county instead of copied directly from Item 2.

Other known source-data issues will be handled during preprocessing rather
than by the model-output contract. For example,
`CRITICAL_FACILITY_006B` is empty in the current Pennsylvania file. There are
also 21 records with `YEAR_ADT_030` equal to `0`; their traffic volume remains
available, but the traffic observation year is treated as missing and flagged.

## Development Mock Data

`data/mock_model_predictions.csv` contains synthetic probabilities and costs
attached to real NBI bridge IDs. Its only purpose is to let the algorithm be
developed before the model team delivers predictions. None of its prediction
values may be presented as model findings or used in the final evaluation.
