# Website integration contract

`recommendation.py` is the boundary between the decision algorithm and the
website. The website should not reimplement validation, scoring, or budget
optimization.

## Website inputs

The interactive form needs only:

- budget;
- strategy: `balanced`, `safety`, `traffic`, or `equity`;
- region: statewide, one county FIPS code, or one PennDOT district.

The NBI, county, and model-prediction file paths are server configuration, not
form inputs. The optional high-risk threshold changes summary reporting only;
it does not change which bridges the optimizer selects. The algorithm applies
a default 25% priority-protection cap internally, so it is not another required
website input.

## Python entry point

```python
from algorithm.recommendation import generate_recommendation

result = generate_recommendation(
    "website/Data/PA 2025.csv",
    "algorithm/data/model_predictions.csv",
    "algorithm/data/pa_counties.csv",
    budget=25_000_000,
    strategy="balanced",
    county_fips="003",
)
```

The returned dictionary is strict JSON: missing values are `null`, never
`NaN`. Its `schema_version` lets the website detect future contract changes.

## Response sections

- `request`: normalized user choices;
- `warnings`: coverage, cleaning, and development-data warnings;
- `data_validation`: input coverage and model metadata;
- `scoring`: strategy, weights, and score range;
- `summary`: budget use and funded/unfunded statistics;
- `selected_bridges`: funded portfolio in display order;
- `high_priority_unfunded`: top unfunded bridges for transparency;
- `selected_bridge_ids`: compact list for highlighting map features.

Every bridge record includes its map coordinates, descriptions, statewide and
regional ranks, predicted cost, all decision indicators, component scores,
top reasons, whether it was priority-protected, and a selection explanation.

## Later web API layer

The current group website is static HTML and JavaScript. It cannot safely run
the Python optimizer in the browser. The next integration step should add a
small Python API endpoint such as `POST /api/recommend`, with the four form
choices in the request body. The endpoint calls `generate_recommendation` and
returns its dictionary. The existing JavaScript can then use `fetch()` to:

1. submit the form;
2. render summary cards and the ranked table;
3. highlight selected bridge IDs on the existing map;
4. show each bridge's explanation.

The API should validate requests and return a clear 4xx error for invalid
budgets or filters. Production deployment details depend on where the group's
website will be hosted, so they are intentionally kept outside this framework-
independent algorithm module.

## Minimal FastAPI example

The website teammate can put the following in a server file such as `api.py`.
FastAPI is an optional web-layer dependency and is intentionally not required
by the algorithm package itself.

```python
import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from algorithm.data_pipeline import DataValidationError
from algorithm.recommendation import generate_recommendation

app = FastAPI()

NBI_PATH = os.getenv("BEN2_NBI", "website/Data/PA 2025.csv")
PREDICTIONS_PATH = os.getenv(
    "BEN2_PREDICTIONS",
    "website/Data/combined_model_predictions.csv",
)
COUNTIES_PATH = os.getenv("BEN2_COUNTIES", "algorithm/data/pa_counties.csv")


class RecommendRequest(BaseModel):
    budget: float = Field(gt=0)
    strategy: Literal["balanced", "safety", "traffic", "equity"] = "balanced"
    county_fips: str | None = None
    district: int | None = None


@app.post("/api/recommend")
def recommend(request: RecommendRequest):
    try:
        return generate_recommendation(
            NBI_PATH,
            PREDICTIONS_PATH,
            COUNTIES_PATH,
            budget=request.budget,
            strategy=request.strategy,
            county_fips=request.county_fips,
            penndot_district=request.district,
        )
    except (DataValidationError, OSError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
```

Install and run the optional web layer from the repository root:

```bash
python -m pip install -r algorithm/requirements.txt fastapi uvicorn
uvicorn api:app --reload
```

The browser can then call it with:

```javascript
const response = await fetch("/api/recommend", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    budget: 25000000,
    strategy: "balanced",
    county_fips: null,
    district: null,
  }),
});

if (!response.ok) throw new Error(await response.text());
const plan = await response.json();

renderSummary(plan.summary);
renderBridgeTable(plan.selected_bridges);
highlightMapBridges(plan.selected_bridge_ids);
```

The packaged production-demo input is
`website/Data/combined_model_predictions.csv`. It contains 20,191 complete
risk-and-cost records. `BEN2_PREDICTIONS` can still point to a different combined
file for later model versions. The 12-row mock remains available only for tests.
