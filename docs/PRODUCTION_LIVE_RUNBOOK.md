# HorseRacingAI Production Live Runbook

## Scope

This runbook is for the first real-money week. The system must remain prediction-first, then race-selection and profitability. Do not silently replace a fixed PRE prediction with later result data.

## Before each live session

1. Confirm `outputs/predictions/prediction_YYYYMMDD.txt` exists for the target date.
2. Confirm the prediction was generated before the race and is treated as the immutable PRE snapshot.
3. Keep later odds/market revisions as separate snapshots.
4. Do not use result data as a prediction feature.
5. Only bet races that pass the production selection rules already validated by the system.

## First-week operating policy

- Use a small fixed stake per selected race; do not increase stake to recover losses.
- Prefer monthly P/L evaluation over a daily profit quota.
- Record for every selected race: prediction, bet type, stake, odds at purchase, result, payout, net P/L, cumulative stake and cumulative P/L.
- Pure profit = payout - betting stake - allocated monthly running cost.
- Monthly BEP = total betting stake + allocated monthly running cost.
- The first week is a production validation period; do not promote a new model from a single day's result.

## Model governance

The current 5-year holdout baseline is the production reference until a same-holdout candidate demonstrates a genuine improvement. The recorded baseline is winner Top1 25.37% and winner Top3 57.76%; the winner-strengthening candidate was not adopted because it underperformed the baseline.

## Automation health

The weekly automation target is Sunday 20:30 JST. A Sunday without SUCCESS by 23:30 JST is abnormal. The Windows logon trigger is recovery-only and must not rerun a healthy weekly evaluation.

If the PC-local task is missing or stale, run `automation\\REPAIR_AUTORUN.cmd` once on the development PC. The repair script refreshes the runner and scheduled-task definition from `main`, registers `HorseRacingAI-Auto`, starts it, and verifies a fresh local status.
