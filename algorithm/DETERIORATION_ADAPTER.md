# Deterioration model output adapter

The deterioration model and the current NBI file can contain different bridge
populations. `deterioration_adapter.py` aligns them without inventing model
scores for bridges the model did not evaluate.

## Matching policy

- A bridge enters `usable_deterioration_predictions.csv` only when its cleaned
  bridge ID exists in both the full model rankings and the current NBI input.
- A model row whose ID is absent from the current NBI is written to
  `unknown_model_records.csv` with an explicit exclusion reason.
- A current NBI bridge with no model row is written to
  `unmodeled_nbi_bridges.csv`. It receives no imputed risk score and remains
  ineligible for automatic scoring and budget allocation.
- `deterioration_coverage_report.json` records all counts, coverage, score
  bounds, and warnings.

This separation prevents missingness concentrated in one structure type, such
as culverts, from being disguised as average or low deterioration risk.

## Score interpretation

`MODEL_DETERIORATION_RISK_SCORE` is renamed to
`deterioration_risk_score`. It is treated as a normalized ranking score in the
0–1 range. It must not be described as a calibrated probability until the
model team documents the target event, prediction horizon, and calibration.

## Run the adapter

```bash
python algorithm/deterioration_adapter.py \
  --nbi "website/Data/PA 2025.csv" \
  --rankings "/path/to/next_inspection_bridge_risk_rankings.csv" \
  --output-dir /tmp/ben2bridges_deterioration
```

The Top-100 file is not an input because it duplicates the first 100 rows of
the full rankings file.
