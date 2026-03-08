# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Travis Fraser
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
    r'no,|correction:|correcting:|upon (?:closer|further)|'
    r'total\s*[:=]\s*\d|awarding\s+\d|this\s+earns|'
    r'the (?:header|earned) score.*(?:incorrect|wrong)|note:\s*the)\b',
    re.IGNORECASE,
)

# Scoring-language sentences — pure grading verdicts with no substantive feedback.
# "Full credit." / "Full credit awarded for part (b)." / "Full credit for part (a)."
# Must NOT match substantive uses like "Full credit requires both forms."
_SCORING_SENTENCE = re.compile(
    r'^(?:Full|Partial|No)\s+credit'
    r'(?:\s+(?:awarded|given|earned))?'
    r'(?:\s+for\s+(?:part\s+)?\(?\w\)?)?'
    r'\s*\.?$'
    r'|^(?:Both|All)\s+parts?\s+(?:earn|receive|get)\s+(?:full|partial)\s+credit\s*\.?$',
    re.IGNORECASE,
)

# Sub-part suffix pattern — matches Q3a, Q3(a), Q3 (a), Q3-a, Q3_a, Q3.a
_SUB_PART_SUFFIX = re.compile(r'[\s\-_\.]?\(?[a-zA-Z]\)?$')

# Inline point annotations — stripped from feedback after scores are finalized.
# Matches: (1 pt), (1.5/1.5), (0/2), - 1.5 pts, earns 1 pt, full credit: 1.5 pts
_POINTS_INLINE = re.compile(
    r'\s*\(\d+\.?\d*\s*/\s*\d+\.?\d*\s*(?:pts?|points?)?\)'    # (1.5/1.5), (0/2), (1.5/1.5 pts)
    r'|\s*\(\d+\.?\d*\s+(?:pts?|points?)\s*(?:not\s+earned)?\)' # (1 pt), (0.5 pt not earned)
    r'|\s*[-]\s+\d+\.?\d*\s*(?:/\s*\d+\.?\d*\s*)?(?:pts?|points?)\.?'  # - 1.5 pts
    r'|\b[Aa]ward\s+\d+\.?\d*\s*/\s*\d+\.?\d*'                  # Award 0.75/1.5
    r'|\bearns?\s+(?:full\s+)?(?:credit\s+)?\(?\d+\.?\d*(?:\s*/\s*\d+\.?\d*)?\s*(?:pts?|points?)?\)?'
    r'|\bfull\s+credit\s*[:]\s*\d+\.?\d*\s*(?:pts?|points?)?'
    r'|\s*\(\d+\.?\d*\s+points?\s+possible\)'                     # (5 points possible)
    r'|\b\d*\.?\d+\s+out\s+of\s+\d+\.?\d*\s*(?:pts?|points?)?\.?' # 1 out of 2 points.
    r'|\bout\s+of\s+\d+\.?\d*\s*(?:pts?|points?)?\.?'             # out of 2 points.
    r'|\bScore\s*:\s*\d+\.?\d*\s*/\s*\d+\.?\d*\.?'               # Score: 0/3.33.
    r'|,?\s*earning\s+(?:full|partial)\s+credit\b[^.]*'           # , earning full credit despite...
    r'|,?\s*earning\s+\d+\.?\d*\s*(?:pts?|points?)\.?'            # , earning 1 pt.
    r'|,?\s*earning\s+(?:full|partial)\s+\d+\.?\d*\s*(?:pts?|points?)\.?'  # , earning full 2 points.
    r'|\b[Oo]ne\s+point\s+awarded\.?'                             # One point awarded.
    r'|,?\s*warranting\s+\d+\.?\d*\s*(?:pts?|points?)\b[^.]*'     # , warranting 2 pts per the rubric.
    r'|\bThis\s+yields\s+\d+\.?\d*\s*(?:pts?|points?)\b[^.]*'     # This yields 1 point for part (b)...
    r'|\b[Cc]redit\s+awarded\s+at\s+the\s+\d+\S*\s*(?:tier\s+)?(?:for\s+)?'  # Credit awarded at the 1-pt tier for
    , re.IGNORECASE,
)


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


def strip_point_annotations(text: str) -> str:
    """Remove inline point tallies from feedback so stale numbers can't contradict scores."""
    if not text:
        return text
    cleaned = _POINTS_INLINE.sub('', text)
    # Collapse double spaces, orphaned punctuation artifacts
    cleaned = re.sub(r'\s{2,}', ' ', cleaned)
    cleaned = re.sub(r'\s*-\s*\.', '.', cleaned)
    cleaned = re.sub(r'\.\s*\.', '.', cleaned)       # ". ." -> "."
    cleaned = re.sub(r';\s*\.', '.', cleaned)         # "; ." -> "."
    return cleaned.strip()


def clean_feedback(text: str) -> str:
    """Strip deliberation language, point annotations, and normalize dashes in feedback text."""
    if not text:
        return text
    # Replace em/en dashes with regular dashes (AI tell)
    text = text.replace("\u2014", "-").replace("\u2013", "-")
    # Split on sentence boundaries, keep only clean sentences
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    clean = [s for s in sentences
             if not _DELIBERATION.search(s) and not _SCORING_SENTENCE.match(s.strip())]
    result = " ".join(clean).strip()
    # If everything was stripped, keep last sentence as fallback (the final answer)
    result = result if result else sentences[-1].strip()
    # Strip inline point annotations (stale tallies from model deliberation)
    return strip_point_annotations(result)


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
                merged[parent] = {"max_points": 0, "earned_points": 0, "feedbacks": [], "page": s.get("page")}
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
                **({"page": v["page"]} if v.get("page") is not None else {}),
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
