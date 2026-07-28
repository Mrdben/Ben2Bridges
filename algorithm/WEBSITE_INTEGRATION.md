# Website integration contract

`recommendation.py` is the boundary between the decision algorithm and the
website. The website should not reimplement validation, scoring, or budget
optimization.

## Website inputs

The interactive form needs only:

- budget;
- strategy: `balanced`, `safety`, or `traffic`;
- region: statewide, one county FIPS code, or one PennDOT district.

The NBI, county, and model-prediction file paths are server configuration, not
form inputs. The optional high-risk threshold changes summary reporting only;
it does not change which bridges the optimizer selects.

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
top reasons, and a selection explanation.

## Later web API layer

The current group website is static HTML and JavaScript. It cannot safely run
the Python optimizer in the browser. The next integration step should add a
small Python API endpoint such as `POST /api/recommend`, with the three form
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
