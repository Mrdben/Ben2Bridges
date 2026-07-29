# Priority Scoring Method

## Purpose

The priority score compares the relative maintenance importance of individual
bridges. It does not decide which combination fits within a budget; predicted
cost remains separate for the later portfolio-optimization step.

## Formula

For bridge `i`:

```text
PriorityScore_i = 100 * (
    w_deterioration * D_i
  + w_condition     * C_i
  + w_traffic       * T_i
  + w_detour        * R_i
)
```

Every component is between 0 and 1, every weight is nonnegative, and the four
weights sum to 1.

## Components

### Deterioration score

```text
D_i = deterioration_risk_score_i
```

The current model output is a normalized 0-1 risk score. It is not described as
a calibrated probability because that interpretation has not been validated.
The score is used directly and is not renormalized.

### Current-condition score

```text
C_i = (9 - LOWEST_RATING_i) / 9
```

A lower FHWA rating therefore produces a higher maintenance-urgency score.
Current condition has a lower weight because it may also be an input to the
deterioration model. Its explicit role here represents present urgency rather
than relative future decline risk.

### Traffic score

ADT is transformed with `log(1 + ADT)` to compress its highly skewed range. It
is then converted to a 0-1 percentile score across all eligible statewide
bridges supplied to the scoring function. Ties receive their average rank.

### Detour score

Valid detour kilometers are converted to a 0-1 percentile score across all
eligible statewide bridges supplied to the scoring function. A missing or
invalid detour receives the neutral score `0.5` and an imputation flag; it is
neither rewarded nor penalized solely because data are unavailable.

## Policy Profiles

| Strategy | Deterioration | Condition | Traffic | Detour |
|---|---:|---:|---:|---:|
| Balanced | 45% | 25% | 25% | 5% |
| Safety | 55% | 25% | 10% | 10% |
| Traffic | 25% | 10% | 50% | 15% |

Balanced is the official-informed robust compromise selected from 76 bounded
candidates. Candidate performance was compared at the top 5%, 10%, and 20% of
the statewide ranking using partial PennDOT importance alignment, model
high-risk coverage, Poor-condition coverage, and perturbation stability. The
selection rule maximized the weakest relative performance across those four
targets; it did not fit incomplete historical repair records as ground truth.

Safety and Traffic remain transparent provisional policy assumptions. The
website can expose all three named profiles without requiring users to enter
four coefficients.

The Balanced weights are not government-approved or universally optimal. They
are best described as `official-informed calibrated` under the project's data,
search bounds, and stated objectives. Full methodology and limitations are in
`WEIGHT_CALIBRATION.md`.

## Ranking

Bridges are ranked by descending priority score. Exact score ties are resolved
deterministically by:

1. higher deterioration risk score;
2. worse current condition;
3. lower predicted cost;
4. bridge ID.

The cost tie-breaker does not change the priority score. Portfolio selection
will use cost explicitly in the separate budget constraint.

## Normalization Scope

Score the complete statewide eligible table before applying county or PennDOT
district filters. This keeps a bridge's normalized score stable when a user
changes the geographic view. The current 12-row mock prediction file is only a
functional demonstration; meaningful statewide normalization requires the
model team's full prediction file.

## Continuing Validation

Continue to evaluate the profiles across multiple budgets and regions. Report
portfolio overlap, high-risk bridges funded, current-condition distribution,
represented ADT, detour impact, and budget utilization. Documentation must call
the selected default weights robust policy weights, not universally optimal
weights.
