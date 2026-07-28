# Ben2Bridges Decision Algorithm

This directory contains the bridge-scoring and budget-allocation work for the
Ben2Bridges project.

Current files:

- `DATA_CONTRACT.md`: interface between the NBI input, prediction models, and
  decision algorithm.
- `OFFICIAL_DATA_SOURCES.md`: official FHWA, Census, and PennDOT provenance and
  source-specific cleaning decisions.
- `data/pa_counties.csv`: Pennsylvania county FIPS names and PennDOT planning
  districts.
- `data/model_predictions_template.csv`: header-only template for the model
  team's final combined prediction file.
- `data/mock_model_predictions.csv`: synthetic predictions attached to real
  bridge IDs for development only. These are not model results.
- `data_pipeline.py`: validates and joins NBI, prediction, and county data into
  the bridge-level table consumed by later scoring code.
- `tests/test_data_pipeline.py`: automated validation tests.
- `scoring.py`: calculates normalized component scores, applies a provisional
  policy profile, and produces a bridge ranking.
- `SCORING_METHOD.md`: formula, normalization, policy profiles, and limitations.
- `tests/test_scoring.py`: automated scoring and ranking tests.
- `budget_allocation.py`: exact budget-constrained bridge portfolio selection.
- `BUDGET_ALLOCATION.md`: optimization objective, constraints, outputs, and
  limitations.
- `tests/test_budget_allocation.py`: allocation and geographic-filter tests.
- `recommendation.py`: runs validation, statewide scoring, geographic
  filtering, exact budget allocation, and explanation generation in one call.
- `WEBSITE_INTEGRATION.md`: stable JSON contract and plan for connecting the
  algorithm to the group's website.
- `tests/test_recommendation.py`: end-to-end recommendation response tests.

## Build the algorithm input table

From the repository root:

```bash
python algorithm/data_pipeline.py \
  --nbi "website/Data/PA 2025.csv" \
  --predictions algorithm/data/mock_model_predictions.csv \
  --counties algorithm/data/pa_counties.csv \
  --output /tmp/ben2bridges_algorithm_input.csv
```

The command prints a JSON validation report. The current mock file covers only
12 real bridge IDs, so it is suitable for development but not final analysis.

Run the tests with:

```bash
python -m unittest discover -s algorithm/tests -v
```

## Score and rank eligible bridges

After building the validated input CSV, run:

```bash
python algorithm/scoring.py \
  --input /tmp/ben2bridges_algorithm_input.csv \
  --strategy balanced \
  --output /tmp/ben2bridges_scored.csv
```

Available provisional strategies are `balanced`, `safety`, and `traffic`.
Their weights are starting policy assumptions, not statistically proven
optima. They must be reviewed through sensitivity analysis after budget
optimization is available.

## Allocate a budget

```bash
python algorithm/budget_allocation.py \
  --input /tmp/ben2bridges_scored.csv \
  --budget 10000000 \
  --output /tmp/ben2bridges_allocation.csv
```

Optional geographic filters:

```text
--county-fips 001
--district 8
```

Only one geographic filter may be supplied at a time. The command uses exact
0-1 mixed-integer optimization, not a greedy top-down ranking.

## Generate one website-ready recommendation

The unified command runs all three stages and writes strict JSON:

```bash
python algorithm/recommendation.py \
  --nbi "website/Data/PA 2025.csv" \
  --predictions algorithm/data/mock_model_predictions.csv \
  --counties algorithm/data/pa_counties.csv \
  --budget 10000000 \
  --strategy balanced \
  --output /tmp/ben2bridges_recommendation.json
```

Optional `--county-fips` and `--district` filters are mutually exclusive. The
mock predictions trigger a development-data warning in the JSON response.
