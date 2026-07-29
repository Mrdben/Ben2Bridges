# Website Integration Handoff for an AI Coding Agent

## Instruction to the teammate

Give your coding AI access to this repository and send it this message:

```text
Read algorithm/AI_WEBSITE_INTEGRATION_HANDOFF.md completely. Inspect the
existing website stack, then implement the website integration described in
that file. Reuse the existing Python recommendation engine; do not rewrite its
scoring or budget-allocation logic. Run the stated acceptance checks before
finishing.
```

## Goal

Connect the existing Ben2Bridges website to the completed Python recommendation
engine. A website user should enter a budget, strategy, and optional region;
the page should return a recommended bridge-repair portfolio, summary cards, a
ranked table, explanations, and map highlighting.

## Existing algorithm: reuse, do not duplicate

The single supported Python entry point is:

```python
from algorithm.recommendation import generate_recommendation
```

Do not reproduce the scoring formula, calibrated weights, priority protection,
or MILP optimization in JavaScript or in a second backend module. The entry
point already performs:

1. NBI/model data validation and joining;
2. statewide normalization and bridge scoring;
3. geographic filtering;
4. 25% strict priority-prefix protection;
5. exact MILP optimization of the residual budget;
6. explanation and strict-JSON generation.

The production Balanced weights are already configured as:

```text
deterioration 45%, current condition 25%, traffic 25%, detour 5%
```

## Required data paths

Configure these server-side; never ask the browser to supply file paths:

```text
NBI:         website/Data/PA 2025.csv
Predictions: algorithm/generated/combined/combined_model_predictions.csv
Counties:    algorithm/data/pa_counties.csv
```

Important: `algorithm/generated/` is intentionally ignored by Git. The full
combined model CSV must be transferred separately to the machine running the
API or generated locally with the adapters described in `algorithm/README.md`.

For UI development only, this committed mock file can be used:

```text
algorithm/data/mock_model_predictions.csv
```

It covers only 12 bridges and must not be used for the final full-state demo.

Prefer environment variables for deployment:

```text
BEN2_NBI
BEN2_PREDICTIONS
BEN2_COUNTIES
```

## API to implement

First inspect the current website and reuse its existing backend framework if
one exists. If it is static-only, add a small Python backend such as FastAPI.

Create:

```text
POST /api/recommend
Content-Type: application/json
```

Request body:

```json
{
  "budget": 25000000,
  "strategy": "balanced",
  "county_fips": null,
  "district": null
}
```

Rules:

- `budget` must be positive;
- `strategy` is `balanced`, `safety`, or `traffic`;
- `county_fips` and `district` are optional but mutually exclusive;
- bridge IDs and county FIPS codes must remain strings so leading zeros are
  preserved;
- return a clear HTTP 400 response for invalid input;
- if frontend and backend use different origins, configure CORS only for the
  required development/production origins.

The handler should call:

```python
result = generate_recommendation(
    nbi_path,
    predictions_path,
    counties_path,
    budget=request.budget,
    strategy=request.strategy,
    county_fips=request.county_fips,
    penndot_district=request.district,
)
```

A full optional FastAPI example is available in
`algorithm/WEBSITE_INTEGRATION.md`.

## Frontend behavior to implement

Submit the form with `fetch("/api/recommend", ...)`. While optimization is
running, disable repeat submission and show a loading state. Then use:

- `summary` for budget used, remaining budget, selected count, high-risk count,
  Poor-condition count, and priority-protection statistics;
- `selected_bridges` for the ranked recommendation table and bridge details;
- `selected_bridge_ids` to highlight recommended bridges on the map;
- `high_priority_unfunded` for a transparency panel;
- `scoring.weights` and `scoring.weight_status` for methodology display;
- `warnings` for visible model/data limitations.

Each bridge record already includes coordinates, map/description fields,
priority ranks, predicted cost, score components, selection reasons, and the
`priority_protected` flag.

Do not make the browser compare alternative portfolios or rerun the scoring
logic. Display the recommendation returned by the API.

## Minimal browser request

```javascript
const response = await fetch("/api/recommend", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    budget: Number(budgetInput.value),
    strategy: strategyInput.value,
    county_fips: selectedCounty || null,
    district: selectedDistrict ? Number(selectedDistrict) : null,
  }),
});

if (!response.ok) {
  throw new Error(await response.text());
}

const plan = await response.json();
renderSummary(plan.summary);
renderBridgeTable(plan.selected_bridges);
highlightMapBridges(plan.selected_bridge_ids);
```

Adapt the three render functions to the existing website components and map
implementation rather than replacing the site's visual design.

## Local verification commands

From the repository root:

```bash
python -m pip install -r algorithm/requirements.txt
python -m pytest algorithm/tests -q
```

If FastAPI is selected for a static-only website:

```bash
python -m pip install fastapi uvicorn
uvicorn api:app --reload
```

Then send a real request to `POST /api/recommend` and inspect the response.

## Acceptance checklist

The integration is complete only when all items pass:

- existing algorithm tests still pass;
- the website never reimplements the algorithm;
- a statewide Balanced request returns `status: "ok"`;
- `scoring.weights` is `0.45/0.25/0.25/0.05`;
- `scoring.weight_status` is `official_informed_calibrated`;
- `summary.total_predicted_cost <= summary.budget`;
- `summary.priority_protection_fraction` is `0.25` by default;
- selected bridge IDs remain zero-padded strings;
- county and district filtering work and cannot be submitted together;
- loading, empty, and error states are visible;
- selected bridges appear in the table and are highlighted on the map;
- warnings from the API are shown rather than hidden;
- the full combined prediction CSV, not the 12-row mock, is used for the final
  demo.

## Related files

- `algorithm/recommendation.py`: website-facing Python entry point;
- `algorithm/WEBSITE_INTEGRATION.md`: response contract and FastAPI example;
- `algorithm/DATA_CONTRACT.md`: input columns and validation rules;
- `algorithm/SCORING_METHOD.md`: score construction;
- `algorithm/BUDGET_ALLOCATION.md`: priority-protected hybrid allocation;
- `algorithm/README.md`: adapters, commands, and complete algorithm overview.
