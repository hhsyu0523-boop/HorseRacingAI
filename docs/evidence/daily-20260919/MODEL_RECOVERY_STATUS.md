# Model recovery checkpoint

Saved on 2026-09-20. Additional searches and training are stopped at the user's request.

## Findings

All four required files remain **NOT FOUND within the searched scope**:
`winner_model.pkl`, `place_model.pkl`, `winner_xgb.pkl`, `place_xgb.pkl`.

Searches already completed:
- Local Git branches and reachable commit/object history for the four filenames: no matches.
- Remote branch listing: main and codex/productionize-daily-20260919 only; both tips were locally available.
- Filename search under C:/Users/HidekazuHasegawa, including the HorseRacingAI repository, its dedicated worktree and AppData/Local automation worktrees. Excluded .git, node_modules and unrelated BoatRaceAI/BOATRACE_AI trees. Three OS/temp directories remained access-denied; this is not a claim of exhaustive disk coverage.
- The root models directory contains best_model.pkl, reduced_model.pkl, win_model.pkl and top3_model.pkl. These were only hashed, not loaded or adopted as substitutes. Their compatibility remains unverified.

## Regeneration feasibility: conditional, not validated

scripts/train_model.py produces the required two LightGBM filenames; scripts/train_xgboost.py produces the required two XGBoost filenames. Installed imports succeeded (LightGBM 4.7.0, XGBoost 3.2.0, NumPy 2.4.6). The read-only audit found matching 37-feature column sets between feature_history and the PRE feature schema. This is not a semantic, inference or model compatibility test.

The audited database contains 236442 rows / 17319 races, dated 2021-08-21 through 2026-08-16, in both history tables. Rows on or after 2026-09-19: zero. The recorded holdout window (2025-08-24 through 2026-08-16) has 46949 rows / 3414 races, matching the existing report's counts. Exact row identities/content and historical feature provenance have not been verified.

The existing trainers read all feature_history rows without an explicit cutoff and derive their validation split from available dates. Their validation set also drives early stopping; category maps are constructed from all loaded rows. Therefore the current audit does not certify leakage-safe regeneration or an independent holdout test. Do not invoke training directly on the operational DB or treat new validation metrics as proof of non-regression.

Next minimum work, only when resumed: prepare an isolated, fixed input snapshot excluding 2026-09-19 and later; verify fixed holdout identities and feature provenance; keep holdout out of fitting/category construction/early stopping; then produce isolated candidates through the existing trainer logic and evaluate compatibility and the unchanged holdout before any adoption. The recorded winner baseline is Top1 866/3414 (25.37%) and winner-in-Top3 1972/3414 (57.76%). No non-regression result exists yet.

## Preservation and safety

- Phase A implementation and 23 passing tests / live official result acquisition for two races remain saved in commit 9114e03 and Draft PR #3.
- This checkpoint adds only the read-only audit script, its aggregate JSON output and this note. Operational DB access uses SQLite mode=ro; temporary schema creation uses :memory:. No model unpickling, training, prediction or result labels used for learning.
- No existing tracked Phase A files changed. No Stable/main checkout, commit, reset or push was performed. Local main reflog's latest recorded change is 2026-07-26; local main and origin/main have different pre-existing tips.
- Model adoption and Phase A live acceptance remain BLOCKED. The 2026-09-19 PRE deadline was already missed; no retrospective PRE was created. Phases 4-6 remain not started.
- The audit process is no longer running. No further search or training was started for this checkpoint.
