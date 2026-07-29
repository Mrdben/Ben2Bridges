# Real-Output Algorithm Evaluation

> Baseline note: the scenario values below were produced before Balanced was
> calibrated and before priority protection, using the former `40/15/30/15`
> weights and pure knapsack allocation. The calibrated Balanced profile is
> `45/25/25/5`, and the current allocator protects a strict priority prefix;
> the calibration comparison is documented in `WEIGHT_CALIBRATION.md`.

## Scope

The fixed scoring profiles were evaluated on the 20,191 Pennsylvania bridges
that have both a deterioration risk score and a derived bridge-level cost.
No scoring weights were tuned during evaluation.

Twelve statewide scenarios were solved using three strategies (`balanced`,
`safety`, and `traffic`) and four budgets ($5M, $10M, $25M, and $50M).

## Integrity Results

- All 12 scenarios reached an optimal solver status.
- All 12 portfolios stayed within budget.
- No selected portfolio contained duplicate bridge IDs.
- Selected bridge counts increased as budget increased.
- For every strategy, each lower-budget portfolio was fully retained in the
  next higher-budget portfolio in this evaluation.

## $25M Scenario

| Strategy | Bridges selected | High-risk selected | Poor-condition selected | Selected daily traffic |
|---|---:|---:|---:|---:|
| Balanced | 3,810 | 315 | 187 | 11,618,111 |
| Safety | 3,849 | 375 | 264 | 7,095,510 |
| Traffic | 3,622 | 237 | 139 | 16,114,206 |

These results show the expected policy response. The safety profile funds more
high-risk and Poor-condition bridges, while the traffic profile covers more
daily traffic. The balanced profile falls between those goals.

At $25M, the balanced portfolio shares 86.25% of its selected bridges with the
safety portfolio and 84.36% with the traffic portfolio. Safety and traffic
share 70.04% of the safety selection. The strategies therefore change policy
emphasis without producing unrelated or unstable recommendations.

## Budget Sensitivity

All profiles used approximately 100% of each tested budget. Balanced selection
counts increased from 1,104 at $5M to 1,889 at $10M, 3,810 at $25M, and 6,300
at $50M. The lower-budget bridge set was retained completely at each adjacent
budget increase. The safety and traffic profiles showed the same 100% adjacent
retention pattern.

## Numerical Safeguard Found During Testing

Initial stress testing exposed two portfolios that exceeded their budgets by
$0.13 and $33.45 because of the mixed-integer solver's floating-point
feasibility tolerance. The allocator now detects any positive overage and
reruns the optimization with a negligible safety reserve. A regression test
protects this behavior. The scoring formula and weights were not changed.

## Interpretation and Limitations

This evaluation established a pre-calibration baseline and confirmed numerical
integrity. It did not establish statistically optimal weights. Final
stakeholder review would still be needed for real use.

The most important current limitation is model semantics: the deterioration
output is a normalized risk score that has not been confirmed as a calibrated
probability, and bridge costs are conservatively derived as the maximum
conditional component scenario. Results must remain labeled as provisional
planning recommendations rather than engineering funding decisions.

Detailed results are generated in `algorithm/generated/evaluation/`:

- `scenario_summary.csv`
- `strategy_overlap.csv`
- `budget_retention.csv`
- `evaluation_report.json`

## Final Priority-Protected Balanced Validation

After Balanced calibration and the 25% priority-protection mitigation, four
statewide scenarios were rerun on the same 20,191 eligible bridges:

| Budget | Protected prefix | Protected budget | Bridges selected | High-risk | Poor | Budget used |
|---:|---:|---:|---:|---:|---:|---:|
| $5M | 4 | 24.7395% | 893 | 78 | 20 | $4,999,996.59 |
| $10M | 8 | 24.7246% | 1,545 | 145 | 55 | $9,999,996.18 |
| $25M | 36 | 23.2090% | 3,190 | 304 | 181 | $24,999,999.33 |
| $50M | 125 | 24.9984% | 5,279 | 520 | 412 | $49,999,995.23 |

All residual MILP solves reached optimal status, every portfolio stayed within
budget, all selected IDs were unique, and selected counts increased with the
budget. In the $25M scenario, statewide ranks 1 through 36 were protected as a
strict continuous prefix; the former pure-knapsack result selected none of the
top 20.

Final generated results are in
`algorithm/generated/evaluation_priority_protected/`.
