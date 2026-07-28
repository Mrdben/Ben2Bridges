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
D_i = deterioration_probability_i
```

The model probability is already on a 0-1 scale and is not renormalized.

### Current-condition score

```text
C_i = (9 - LOWEST_RATING_i) / 9
```

A lower FHWA rating therefore produces a higher maintenance-urgency score.
Current condition has a lower weight because it may also be an input to the
deterioration model. Its explicit role here represents present urgency rather
than future decline probability.

### Traffic score

ADT is transformed with `log(1 + ADT)` to compress its highly skewed range. It
is then converted to a 0-1 percentile score across all eligible statewide
bridges supplied to the scoring function. Ties receive their average rank.

### Detour score

Valid detour kilometers are converted to a 0-1 percentile score across all
eligible statewide bridges supplied to the scoring function. A missing or
invalid detour receives the neutral score `0.5` and an imputation flag; it is
neither rewarded nor penalized solely because data are unavailable.

## Provisional Policy Profiles

| Strategy | Deterioration | Condition | Traffic | Detour |
|---|---:|---:|---:|---:|
| Balanced | 40% | 15% | 30% | 15% |
| Safety | 55% | 25% | 10% | 10% |
| Traffic | 25% | 10% | 50% | 15% |

These are transparent starting assumptions, not objectively optimal weights.
The website can expose the named profiles without requiring users to enter four
coefficients.

## Ranking

Bridges are ranked by descending priority score. Exact score ties are resolved
deterministically by:

1. higher deterioration probability;
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

## Required Later Validation

After budget optimization is implemented, evaluate candidate weights across
multiple budgets and regions. Report portfolio overlap, high-risk bridges
funded, current-condition distribution, represented ADT, detour impact, and
budget utilization. Final documentation should call the selected default
weights robust policy weights, not universally optimal weights.
