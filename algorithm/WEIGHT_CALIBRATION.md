# Balanced Weight Calibration

## Result

The production Balanced profile is:

| Indicator | Weight |
|---|---:|
| Deterioration risk | 45% |
| Current condition | 25% |
| Traffic | 25% |
| Detour | 5% |

Its status is `official_informed_calibrated`. This means it is a transparent,
evidence-informed compromise for this prototype—not a PennDOT-approved or
universally optimal policy.

## Evidence

The calibration table covers 20,191 eligible bridges. Of those, 19,785 have a
complete 2022–2025 NBI condition trajectory and 20,166 match a Pennsylvania
Environmental Justice block group.

The search uses:

- current model deterioration risk scores;
- current NBI condition, traffic, and detour fields;
- a partial reconstruction of PennDOT importance:
  `sqrt(deck area * AADT) * detour factor * truck factor`;
- FHWA/PennDOT Poor-condition and high-risk coverage targets;
- deterministic small-input perturbations for ranking stability.

The PennDOT reconstruction is partial because internal scour,
fracture-critical, and flood-history factors are unavailable. Historical
improvement labels are incomplete and are audit-only. PennEnviroScreen is also
audit-only; it does not prescribe a bridge score weight.

## Search and Selection

Seventy-six weight combinations were tested in five-percentage-point steps:

- deterioration: 30–55%;
- condition: 10–25%;
- traffic: 15–40%;
- detour: 5–20%.

Candidates were evaluated at the top 5%, 10%, and 20% of the statewide ranking.
The selection rule maximized the minimum relative performance across:

1. partial PennDOT importance overlap;
2. model high-risk recall;
3. Poor-condition recall;
4. perturbation ranking stability.

This maximin rule avoids inventing another subjective weighted average of the
evaluation criteria.

## Baseline Comparison

Compared with the former `40/15/30/15` Balanced profile:

| Ranking metric | Former | Calibrated |
|---|---:|---:|
| Official-importance overlap | 35.40% | 31.79% |
| High-risk recall | 42.27% | 46.97% |
| Poor-condition recall | 6.75% | 11.09% |
| Perturbation stability | 83.15% | 78.69% |
| Historical weak-label recall (audit) | 14.23% | 16.15% |

At a statewide $25M budget, the calibrated profile selected 3,837 bridges,
including 323 high-risk and 247 Poor-condition bridges. The former profile
selected 3,810 bridges, including 315 high-risk and 187 Poor-condition bridges.

## Portfolio Limitation and Mitigation

Testing found that a pure knapsack could prefer many inexpensive projects and
omit every highest-ranked bridge. The production allocator now protects a
strict top-ranked prefix using at most 25% of the budget, then applies exact
MILP optimization to the remaining budget. This mitigation is separate from
scoring-weight calibration and represents a transparent prototype policy—not
a PennDOT funding mandate.
