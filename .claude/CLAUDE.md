# Rubrica — Exam Grader

Flask + SQLite + Claude Sonnet app that grades handwritten exams against rubrics. Single-file architecture (`grader.py`, ~2,050 lines). All data stays local except anonymized exam pages sent to Claude for grading.

Two pillars govern all changes: **privacy** (student PII never leaves the machine) and **accuracy** (grading logic is protected by boundary re-grading, feedback specificity enforcement, and validated against ETS thresholds via independent dual-model audit (Gemini cross-family default, Opus fallback)). Accuracy includes score fairness: never introduce rounding or normalization that systematically costs students points. See `.claude/rules/` for details.

## Key Files Beyond grader.py

- `audit_grader.py` — dual-model audit (Gemini/Opus); re-grades samples as reference scorer. Flask-callable via `start_audit()` + `get_audit_status()`, also runs standalone from CLI
- `generate_audit_report.py` — produces PDF validation report from audit results
- `.claude/agents/testing` — testing agent (`/testing`); blind validation analyst that reads audit JSON and evaluates inter-rater reliability, bias, and feedback quality using external psychometric standards. Has no knowledge of grading pipeline internals by design.

## Audit & Bundle Reports

- When generating the audit report or bundle, load the **most recent audit files** until reaching **>= 30 comparisons** (the minimum for reliable QWK/ICC). Do not load all historical audit data.
- QWK and ICC metrics **must appear** in both the audit report and the bundle whenever n >= 30. If they're missing, the data loading is wrong.
- **No hardcoded adjusted agreement figures** (e.g. "97% adj. for auditor error"). Those require manual validation of specific mismatches and must not auto-print on future reports.

## Git & Deployment

- **Always ask before pushing to remote.** Never `git push` without explicit approval, even after committing.
- **Proprietary files are gitignored**, not tracked. The audit/insights pipeline, rubric-builder, and testing agent stay local. `grader.py` has import guards so the app runs without them. See `.gitignore` for the full list.

## Server

- **Never start, stop, or restart the Flask server.** Travis manages the server process manually. If a change requires a restart, say so and wait.
