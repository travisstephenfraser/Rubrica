# Rubrica — Exam Grader

Flask + SQLite + Claude Sonnet app that grades handwritten exams against rubrics. Single-file architecture (`grader.py` + shared `scoring.py`). All data stays local except anonymized exam pages sent to Claude for grading.

Two pillars govern all changes: **privacy** (student PII never leaves the machine) and **accuracy** (grading logic is protected by boundary re-grading, feedback specificity enforcement, and deterministic post-processing). Accuracy includes score fairness: never introduce rounding or normalization that systematically costs students points. See `.claude/rules/` for details.

## Files

- `grader.py` — Flask app: all routes, grading pipeline, DB access
- `scoring.py` — shared scoring module: sub-part consolidation, feedback sanitization, score finalization, letter grade assignment
- `templates/` — Jinja2 + Bootstrap 5 templates, dark mode via `data-bs-theme`
- `requirements.txt` — runtime dependencies
- `patch_rounding.py` — one-time DB migration that fixes a historical remainder-distribution bug on stored grade_data; run once with `--apply` if upgrading from a version earlier than the fix

## Optional modules (import-guarded)

`grader.py` uses `try/except ImportError` guards so the app runs without these. When they are present locally, extra features appear; when absent, the UI hides the corresponding surfaces via `has_audit` / `has_builder` template flags.

- Audit pipeline (dual-model re-grade, QWK/ICC reporting, PDF validation report)
- Rubric builder (structured tier extraction, cross-version mapping)
- Gmail OAuth sender (per-student PDF email distribution)

These are not part of the open-source distribution today. The core grading pipeline is fully functional without them.

## Git & Deployment

- **Always ask before pushing to remote.** Never `git push` without explicit approval, even after committing.
- Student data stays gitignored. Databases, exam PDFs, `.env` files never go to the repo.

## Server

- **Never start, stop, or restart the Flask server.** The user manages the server process manually. If a change requires a restart, say so and wait.
