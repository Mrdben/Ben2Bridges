# Cost model output adapter

The full cost catalog contains one conditional whole-project scenario for each
available bridge component. These rows are not paid component line items and
must not be summed.

`cost_adapter.py` derives one conservative bridge-level planning cost by taking
the maximum conditional whole-project estimate among the available deck,
superstructure, and substructure scenarios. The lower and upper 80% interval,
high-cost probability, and source component are taken from that same selected
scenario.

The resulting field is labeled with:

```text
cost_method = max_conditional_component_scenario
cost_is_derived = true
```

This is a transparent policy fallback because actual component-level
deterioration probabilities are unavailable. It is not the sum of component
costs and is not an observed paid project cost.

## Run

```bash
python algorithm/cost_adapter.py \
  --nbi "website/Data/PA 2025.csv" \
  --risk algorithm/generated/deterioration/usable_deterioration_predictions.csv \
  --catalog "/path/to/all_latest_bridges_part_wise_cost_catalog.csv" \
  --cost-reference-year 2025 \
  --output-dir algorithm/generated/combined
```

The adapter outputs the combined risk-and-cost predictions, unknown catalog
bridges, bridges with risk but no applicable cost, and a JSON coverage report.
