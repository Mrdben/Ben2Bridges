# Budget Allocation Method

## Decision

For each eligible bridge `i`, define:

```text
x_i = 1 if the bridge is selected for repair
x_i = 0 otherwise
```

## Priority-Protection Stage

The default allocator first protects a strict top-ranked prefix using no more
than 25% of the available budget:

```text
priority_protection_cap = 0.25 * user_budget
```

Starting at rank 1, bridges are protected in order while their cumulative cost
fits inside the cap. If the next bridge does not fit, prefix construction
stops; a cheaper lower-ranked bridge cannot jump over it. If the highest-ranked
bridge alone exceeds the cap, no bridge is forced by this stage.

The 25% default prevents thousands of very inexpensive, moderate-score projects
from excluding every highest-priority bridge while leaving at least 75% of the
budget for portfolio efficiency. It is an internal default, so the website does
not need another required user input. Advanced callers can set
`priority_protection_fraction` between 0 and 1; setting it to 0 reproduces the
unprotected knapsack behavior.

## Residual Objective

After subtracting protected project costs, the remaining budget is optimized:

```text
maximize sum(priority_score_i * x_i)
```

## Budget Constraint

```text
sum(predicted_cost_i * x_i) <= user_budget
```

All predicted costs and the user budget must use the same `cost_unit`.

Protected bridges are fixed at `x_i = 1`; the objective above applies to the
remaining candidates.

## Why This Is a Hybrid

A pure top-down ranking can waste remaining budget, while a pure sum-of-scores
knapsack can favor thousands of extremely inexpensive projects and omit every
top-ranked bridge. The implementation therefore combines a bounded strict
priority prefix with SciPy's exact mixed-integer optimizer for the residual
budget.

The solver operates on scaled floating-point costs. If its numerical
feasibility tolerance produces a portfolio even slightly above the original
budget, the implementation automatically reruns with a sub-part-per-million
safety reserve and reports `optimal:milp_with_feasibility_buffer`. An
over-budget portfolio is never returned.

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
- `priority_protected`;
- `funding_status` (`Selected` or `Unfunded`); and
- `funded_rank` for selected bridges.

The summary reports:

- selected and unfunded counts;
- predicted budget used, remaining budget, and utilization;
- total and mean selected priority score;
- configured priority-protection fraction, protected bridge count, protected
  budget used, and protected budget percentage;
- high-risk bridges selected and left unfunded;
- Poor-condition bridges selected;
- ADT represented by selected bridges; and
- selected detour totals and missing-detour count.

The default high-risk reporting threshold is a deterioration risk score of
`0.70`. It is a project reporting definition, not a calibrated failure
probability, and does not change the optimizer's objective.

## Interpretation

The result is a priority-protected hybrid portfolio under the chosen score
profile, budget, geographic scope, model estimates, and project assumptions.
The residual selection is exactly optimal after the protected prefix is fixed;
the complete hybrid is intentionally not the unconstrained maximum-total-score
knapsack. It is not an objectively best engineering repair plan. Predicted
costs remain estimates, and the prototype does not model project dependencies,
construction schedules, crew capacity, or engineering feasibility.
