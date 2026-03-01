# Rubrica — Exam Grader

Flask + SQLite + Claude Sonnet app that grades handwritten exams against rubrics. Single-file architecture (`grader.py`, ~2,050 lines). All data stays local except anonymized exam pages sent to Claude for grading.

Two pillars govern all changes: **privacy** (student PII never leaves the machine) and **accuracy** (grading logic is protected by boundary re-grading, feedback specificity enforcement, and validated against ETS thresholds via independent Opus 4.6 audit). Accuracy includes score fairness: never introduce rounding or normalization that systematically costs students points. See `.claude/rules/` for details.

## Key Files Beyond grader.py

- `audit_grader.py` — standalone audit script; re-grades samples with Opus 4.6 as reference scorer
- `generate_audit_report.py` — produces PDF validation report from audit results
- `.claude/agents/testing` — testing agent (`/testing`); blind validation analyst that reads audit JSON and evaluates inter-rater reliability, bias, and feedback quality using external psychometric standards. Has no knowledge of grading pipeline internals by design.
