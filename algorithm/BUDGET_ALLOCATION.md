# Budget Allocation Method

## Decision

For each eligible bridge `i`, define:

```text
x_i = 1 if the bridge is selected for repair
x_i = 0 otherwise
```

## Objective

```text
maximize sum(priority_score_i * x_i)
```

## Budget Constraint

```text
sum(predicted_cost_i * x_i) <= user_budget
```

All predicted costs and the user budget must use the same `cost_unit`.

## Why This Is Not Simple Ranking

A single expensive, high-ranked bridge can produce less total priority benefit
than several moderately ranked bridges that fit together under the same
budget. The implementation therefore solves the binary portfolio problem with
SciPy's exact mixed-integer optimizer rather than selecting bridges from the
top of the ranking until money runs out.

## Geography

Scores are normalized statewide before geographic filtering. Allocation can
then be run for:

- all eligible Pennsylvania bridges;
- one three-digit county FIPS code; or
- one PennDOT district.

County and district filters are mutually exclusive in the initial interface.

## Outputs

Every candidate bridge receives:

- `selected_for_repair`;
- `funding_status` (`Selected` or `Unfunded`); and
- `funded_rank` for selected bridges.

The summary reports:

- selected and unfunded counts;
- predicted budget used, remaining budget, and utilization;
- total and mean selected priority score;
- high-risk bridges selected and left unfunded;
- Poor-condition bridges selected;
- ADT represented by selected bridges; and
- selected detour totals and missing-detour count.

The default high-risk reporting threshold is a deterioration probability of
`0.70`. It is a reporting definition only and does not change the optimizer's
objective.

## Interpretation

The result is the highest-scoring portfolio under the chosen score profile,
budget, geographic scope, model estimates, and project assumptions. It is not
an objectively best engineering repair plan. Predicted costs remain estimates,
and the prototype does not model project dependencies, construction schedules,
crew capacity, or engineering feasibility.
