# Ben2Bridges Decision Algorithm

This directory contains the bridge-scoring and budget-allocation work for the
Ben2Bridges project.

Current files:

- `AI_WEBSITE_INTEGRATION_HANDOFF.md`: one-file implementation brief that a
  website teammate can give directly to an AI coding agent.
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
- `scoring.py`: calculates normalized component scores, applies a named policy
  profile, and produces a bridge ranking. Balanced uses calibrated
  official-informed weights; Safety and Traffic remain provisional.
- `SCORING_METHOD.md`: formula, normalization, policy profiles, and limitations.
- `tests/test_scoring.py`: automated scoring and ranking tests.
- `budget_allocation.py`: priority-protected budget selection with exact MILP
  optimization of the residual budget.
- `BUDGET_ALLOCATION.md`: optimization objective, constraints, outputs, and
  limitations.
- `tests/test_budget_allocation.py`: allocation and geographic-filter tests.
- `recommendation.py`: runs validation, statewide scoring, geographic
  filtering, exact budget allocation, and explanation generation in one call.
- `WEBSITE_INTEGRATION.md`: stable JSON contract and plan for connecting the
  algorithm to the group's website.
- `tests/test_recommendation.py`: end-to-end recommendation response tests.
- `deterioration_adapter.py`: aligns the model team's full deterioration
  rankings to the current NBI bridge population without imputing missing model
  scores.
- `DETERIORATION_ADAPTER.md`: matching, exclusion, and score-interpretation
  policy for the deterioration model handoff.
- `tests/test_deterioration_adapter.py`: adapter validation and coverage tests.
- `cost_adapter.py`: converts the cost team's conditional whole-project
  scenario catalog into one conservative, explicitly derived cost per bridge
  and combines it with usable deterioration scores.
- `COST_ADAPTER.md`: cost interpretation, fallback method, and limitations.
- `tests/test_cost_adapter.py`: cost-adapter validation and matching tests.
- `evaluate_algorithm.py`: fixed-weight sensitivity evaluation across multiple
  budgets and strategies.
- `EVALUATION_RESULTS.md`: results and interpretation from the real model-output
  evaluation.
- `calibration_data.py`: combines official NBI history, a partial PennDOT
  importance reconstruction, and PennEnviroScreen audit fields.
- `calibrate_weights.py`: searches bounded Balanced weights and compares exact
  budget portfolios.
- `WEIGHT_CALIBRATION.md`: calibration evidence, selection rule, results, and
  limitations.

## Build the algorithm input table

When the deterioration model arrives as a full risk-ranking export, validate
and align it before combining it with the cost model:

```bash
python algorithm/deterioration_adapter.py \
  --nbi "website/Data/PA 2025.csv" \
  --rankings "/path/to/next_inspection_bridge_risk_rankings.csv" \
  --output-dir /tmp/ben2bridges_deterioration
```

Unmodeled bridges are reported separately and receive no invented risk score.
The resulting usable risk-score file is an intermediate input; it still needs
to be joined with the cost-model output before the unified recommendation
engine can run.

Create the combined prediction file from the current cost catalog:

```bash
python algorithm/cost_adapter.py \
  --nbi "website/Data/PA 2025.csv" \
  --risk algorithm/generated/deterioration/usable_deterioration_predictions.csv \
  --catalog "/path/to/all_latest_bridges_part_wise_cost_catalog.csv" \
  --cost-reference-year 2025 \
  --prediction-horizon next_inspection \
  --output-dir algorithm/generated/combined
```

The catalog rows are alternative conditional whole-project scenarios, so the
adapter does not add component costs. It temporarily uses the largest scenario
per bridge and records the derivation method and source component. Generated
artifacts are ignored by Git because the source model outputs are delivered
separately.

From the repository root:

```bash
python algorithm/data_pipeline.py \
  --nbi "website/Data/PA 2025.csv" \
  --predictions website/Data/combined_model_predictions.csv \
  --counties algorithm/data/pa_counties.csv \
  --output /tmp/ben2bridges_algorithm_input.csv
```

The command prints a JSON validation report. The packaged combined file contains
20,191 unique bridge predictions with derived 2025 planning costs and covers
86.6046% of the 2025 Pennsylvania NBI inventory.

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

Available strategies are `balanced`, `safety`, `traffic`, and `equity`. Balanced
uses the official-informed calibrated weights `45/25/25/5`. Safety, Traffic, and
Equity/social impact remain provisional policy profiles. Equity uses detour burden
as a community-access proxy and does not include demographic data. None is a
government-approved or universally optimal funding policy.

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

Only one geographic filter may be supplied at a time. By default, no more than
25% of the budget protects a strict top-ranked prefix; the remaining budget
uses exact 0-1 mixed-integer optimization. Advanced CLI runs can override the
default with `--priority-protection-fraction`.

## Generate one website-ready recommendation

The unified command runs all three stages and writes strict JSON:

```bash
python algorithm/recommendation.py \
  --nbi "website/Data/PA 2025.csv" \
  --predictions website/Data/combined_model_predictions.csv \
  --counties algorithm/data/pa_counties.csv \
  --budget 10000000 \
  --strategy balanced \
  --output /tmp/ben2bridges_recommendation.json
```

Optional `--county-fips` and `--district` filters are mutually exclusive. The
packaged risk scores and derived costs remain provisional planning inputs and
must be labeled as such in the website and presentation.
