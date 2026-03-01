# Accuracy

Grading correctness is the core product promise. Protect these mechanisms:

- **`temperature=0.0`** for deterministic Claude responses. Do not raise it.
- **Sub-part consolidation** (Q3a + Q3b -> Q3) runs in Python after the API response. Changing the merge logic or regex patterns affects every grade. Test thoroughly.
- **Feedback sanitizer (`_clean_feedback()`)** strips AI deliberation language ("wait, actually") and replaces em/en dashes with regular dashes. Changes here affect student-facing output.
- **JSON retry** (max 2 attempts) catches malformed Claude responses. Do not remove this safety net.
- **Score recalculation** normalizes totals when rubric points don't divide evenly. Rounding changes cascade to letter grades.
- **Letter grade thresholds**: 90+ A, 80+ B, 70+ C, 60+ D, <60 F. These are set in `letter_grade()`.
- **2x contrast enhancement** on exam images helps Claude read faint pencil. Changing the factor affects grading quality.
- **Boundary re-grade** (`_boundary_regrade()`) automatically re-grades exams scoring within +/-1.5% of letter grade thresholds (90/80/70/60). If two passes disagree on the letter grade, scores are averaged. The `boundary_check` field in grade_data records both passes for audit trail. Do not remove or widen the margin without validation data.
- **Feedback specificity enforcement** detects vague feedback (<8 words or matching `_VAGUE_FEEDBACK` patterns like "Good work", "Incorrect") and re-prompts via a text-only API call for rubric-anchored feedback. This runs inside `_grade_exam()` after the deliberation sanitizer. The refinement is text-only (no images) to keep token cost low.
- When modifying any scoring or feedback logic, verify the full chain: API response -> JSON parse -> sub-part consolidation -> feedback cleaning -> feedback specificity check -> score normalization -> letter grade -> boundary re-grade.
