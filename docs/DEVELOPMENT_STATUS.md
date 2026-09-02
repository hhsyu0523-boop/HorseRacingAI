# HorseRacingAI Development Status

Updated: 2026-09-03 JST

## Goal

Build a practical JRA prediction system whose first priority is prediction accuracy, then race selection and profitability. Monthly break-even must include both betting stake and the recurring system cost.

## GitHub-first rules

1. `main` is the reproducible baseline.
2. Source code, configuration, evaluation summaries, experiment definitions, and decision logs are committed to GitHub.
3. Local JRA-VAN raw/history databases are NOT committed.
4. Serialized model binaries are NOT committed; their training configuration and evaluation summary must be committed.
5. A PRE prediction, once fixed for a race/day, must not be silently overwritten. Any future live/odds revision must be stored as a separate snapshot.
6. Result data must never enter PRE features. Prediction and result-validation phases stay separated.
7. Model changes are adopted only after a same-holdout comparison against the current baseline.

## Existing implementation confirmed in repository

- JV-Link connection and current race loading
- Historical result collection: `fetch-history`
- SQLite repository layer
- Feature generation
- LightGBM winner/place models
- XGBoost winner/place models
- Ensemble prediction
- Rolling backtest

## Restart point

The previous development track was expanding history from roughly one year to a five-year dataset and comparing training windows. The local database is excluded from Git, so its actual current collection progress must be measured on the development PC before retraining.

## Immediate plan

### Stage A — Inventory and reproducibility

- Record local DB coverage: minimum date, maximum date, race count, runner count.
- Confirm whether the requested five-year interval is complete.
- Record current feature count and current baseline model metrics.
- Preserve these results under `outputs/baseline/`.

### Stage B — Training-window comparison

Use the same chronological holdout for all candidates.

- 1-year training window
- 3-year training window
- 5-year training window

Primary prediction KPIs:

- winner Top1 hit rate
- winner Top3 capture rate
- actual top-3 horses captured by AI Top3
- exact predicted 1-2-3 ranking rate where applicable

Profitability/odds are evaluated only after prediction strength is compared.

### Stage C — Stable model decision

Adopt a candidate only if it improves prediction quality on the fixed holdout without leakage. Otherwise keep the prior stable model.

### Stage D — Operational prediction

After the ranking model is fixed:

- immutable PRE prediction snapshot
- later odds/market snapshot kept separately
- race participation decision
- result retrieval and validation
- stake / return / ROI calculation

## Current status

`GITHUB_BASELINE_READY`: repository structure and development rules established.

Next required measurement: local JRA-VAN SQLite history coverage and current baseline metrics.
