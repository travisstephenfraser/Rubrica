"""
Shared Scoring Module
=====================
Single source of truth for accuracy-critical scoring pipeline logic shared
between production grading (grader.py) and audit grading (audit_grader.py).

Two-phase design:
  1. consolidate_and_clean(data) — sub-part merging + feedback sanitization
  2. finalize_scores(data)       — recalc + hard cap + round + letter grade

Production inserts its own steps (feedback specificity, contradiction detection,
handwriting flags) between phases. Audit calls them back-to-back.

Dependencies: re (stdlib only). Zero external imports — no import cycles.
"""

import re

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Deliberation language pattern — strips AI thinking-out-loud from feedback.
_DELIBERATION = re.compile(
    r'\b(wait|actually|hmm|let me re-?(?:read|check|count|examine)|'
    r'on second thought|re-?reading|I (?:think|miscounted|need to)|'
    r'looking (?:again|more carefully)|hold on|scratch that|'
    r'no,|correction:|upon (?:closer|further))\b',
    re.IGNORECASE,
)

# Sub-part suffix pattern — matches Q3a, Q3(a), Q3 (a), Q3-a, Q3_a, Q3.a
_SUB_PART_SUFFIX = re.compile(r'[\s\-_\.]?\(?[a-zA-Z]\)?$')


# ---------------------------------------------------------------------------
# Standalone utilities
# ---------------------------------------------------------------------------

def letter_grade(pct: float) -> str:
    """Convert percentage to letter grade. Thresholds: 90 A, 80 B, 70 C, 60 D."""
    if pct >= 90: return "A"
    if pct >= 80: return "B"
    if pct >= 70: return "C"
    if pct >= 60: return "D"
    return "F"


def clean_feedback(text: str) -> str:
    """Strip deliberation language and normalize dashes in feedback text."""
    if not text:
        return text
    # Replace em/en dashes with regular dashes (AI tell)
    text = text.replace("\u2014", "-").replace("\u2013", "-")
    # Split on sentence boundaries, keep only clean sentences
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    clean = [s for s in sentences if not _DELIBERATION.search(s)]
    result = " ".join(clean).strip()
    # If everything was stripped, keep last sentence as fallback (the final answer)
    return result if result else sentences[-1].strip()


# ---------------------------------------------------------------------------
# Pipeline Phase 1: consolidate_and_clean
# ---------------------------------------------------------------------------

def consolidate_and_clean(data: dict) -> None:
    """Phase 1: Sub-part consolidation + feedback sanitization.

    Mutates *data* in place:
    1. Merges sub-part rows (Q3a + Q3b -> Q3) by summing points, concatenating feedback.
    2. Applies clean_feedback() to all per-question + overall feedback.
    """
    # --- Sub-part consolidation ---
    if data.get("scores"):
        merged = {}   # parent_key -> {max, earned, feedbacks}
        order  = []   # preserve first-seen order
        for s in data["scores"]:
            raw = str(s.get("question", "")).strip()
            # Strip trailing sub-part suffixes: Q3a / Q3(a) / Q3 (a) / Q3-a / Q3_a / Q3.a
            parent = _SUB_PART_SUFFIX.sub('', raw).strip()
            if not parent:
                parent = raw
            if parent not in merged:
                merged[parent] = {"max_points": 0, "earned_points": 0, "feedbacks": []}
                order.append(parent)
            merged[parent]["max_points"]    += float(s.get("max_points",    0))
            merged[parent]["earned_points"] += float(s.get("earned_points", 0))
            fb = s.get("feedback", "").strip()
            if fb:
                # Prefix feedback with sub-part label when consolidating
                label = raw if raw != parent else ""
                merged[parent]["feedbacks"].append(f"{label}: {fb}" if label else fb)
        data["scores"] = [
            {
                "question":      k,
                "max_points":    v["max_points"],
                "earned_points": v["earned_points"],
                "feedback":      " | ".join(v["feedbacks"]),
            }
            for k, v in [(k, merged[k]) for k in order]
        ]

    # --- Feedback sanitization ---
    if data.get("scores"):
        for s in data["scores"]:
            if s.get("feedback"):
                s["feedback"] = clean_feedback(s["feedback"])
    if data.get("overall_feedback"):
        data["overall_feedback"] = clean_feedback(data["overall_feedback"])


# ---------------------------------------------------------------------------
# Pipeline Phase 2: finalize_scores
# ---------------------------------------------------------------------------

def finalize_scores(data: dict) -> None:
    """Phase 2: Score recalculation + hard cap + final rounding + letter grade.

    Mutates *data* in place. Must run AFTER any production-only steps that modify
    scores (feedback specificity, contradiction resolution).

    Order is critical (fixes audit ordering bug):
      1. Recalculate total_earned from per-question sums (full precision)
      2. Hard cap: earned <= possible
      3. Final rounding: per-question + total to 2dp
      4. Letter grade from rounded, capped values
    """
    # 1. Score recalculation — derive total_earned from individual scores.
    # Claude's summary total sometimes diverges from per-question scores due to
    # rounding (e.g. 18 x 3.33 = 59.94 instead of 60). Keep total_possible as
    # Claude reported it, but derive earned by subtracting actual missed points.
    if data.get("scores"):
        sum_possible = sum(float(s.get("max_points",    0)) for s in data["scores"])
        sum_earned   = sum(float(s.get("earned_points", 0)) for s in data["scores"])
        missed = sum_possible - sum_earned
        reported_possible = float(data.get("total_possible", sum_possible))
        data["total_earned"] = max(reported_possible - missed, 0)

    # 2. Hard cap: earned can never exceed possible
    if data.get("total_possible", 0) > 0:
        data["total_earned"] = min(data["total_earned"], data["total_possible"])

    # 3. Final rounding (once, after all math)
    if "total_earned" in data:
        data["total_earned"] = round(data["total_earned"], 2)
    if data.get("scores"):
        for s in data["scores"]:
            s["max_points"]    = round(s["max_points"], 2)
            s["earned_points"] = round(s["earned_points"], 2)

    # 4. Letter grade from rounded, capped values
    if data.get("total_possible", 0) > 0:
        pct = data["total_earned"] / data["total_possible"] * 100
        data["letter_grade"] = letter_grade(pct)
