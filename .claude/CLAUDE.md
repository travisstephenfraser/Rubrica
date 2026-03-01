# Rubrica — Exam Grader

Flask + SQLite + Claude Sonnet app that grades handwritten exams against rubrics. Single-file architecture (`grader.py`, ~1,850 lines). All data stays local except anonymized exam pages sent to Claude for grading.

Two pillars govern all changes: **privacy** (student PII never leaves the machine) and **accuracy** (grading logic is protected). See `.claude/rules/` for details.
