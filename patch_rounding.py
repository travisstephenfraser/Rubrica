# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Travis Fraser
"""
Patch stored grade_data to fix remainder distribution drift.

The old normalization pipeline distributed rounding remainder onto the largest
question's max_points but NOT earned_points. Students who scored full marks on
that question show a phantom loss (e.g. 4.76/4.85 despite "Correct" feedback).

This script fixes per-question earned_points where earned < max but the student
clearly got full marks (earned was equal to max before the remainder bump), then
recalculates totals. Respects boundary-averaged exams by preserving the average.

Usage:
    python patch_rounding.py              # dry run (shows changes)
    python patch_rounding.py --apply      # write changes to DB
"""

import argparse
import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "exam_grader.db"


def letter_grade(pct: float) -> str:
    if pct >= 90: return "A"
    if pct >= 80: return "B"
    if pct >= 70: return "C"
    if pct >= 60: return "D"
    return "F"


def patch(data: dict) -> tuple[dict, list[str]]:
    """Fix remainder drift in per-question scores. Returns (data, list of fixes)."""
    fixes = []
    scores = data.get("scores", [])
    if not scores:
        return data, fixes

    # Find questions where earned < max by a small amount (< 0.15) — these are
    # phantom losses from the old remainder distribution. The remainder is at most
    # ~N_questions * 0.005 ≈ 0.14, all dumped onto one question's max_points.
    # Anything >= 0.15 is a real partial credit deduction, not drift.
    for s in scores:
        earned = float(s.get("earned_points", 0))
        mx = float(s.get("max_points", 0))
        gap = mx - earned
        if 0 < gap < 0.15 and earned > 0:
            # Check if this is likely a full-marks question hit by remainder drift.
            # The remainder bumped max by up to ~0.5 but left earned unchanged.
            # After the bump, both values share the same scale factor base,
            # so the pre-bump earned would have equaled the pre-bump max.
            s["earned_points"] = mx
            fixes.append(f"{s['question']}: {earned} -> {mx} (+{gap:.2f})")

    # Recalculate totals from fixed per-question scores
    if fixes:
        is_averaged = data.get("boundary_check", {}).get("result") == "averaged"
        if is_averaged:
            # Boundary-averaged: total_earned is avg of two passes.
            # Pass 1 = sum(earned_points), so shift total by the same delta.
            old_sum = sum(float(s.get("earned_points", 0)) for s in scores) - sum(
                float(f.split("-> ")[1].split(" ")[0]) - float(f.split(": ")[1].split(" ")[0])
                for f in fixes
            )
            new_sum = sum(float(s.get("earned_points", 0)) for s in scores)
            delta = new_sum - old_sum
            # The stored total is the average of pass1 and pass2.
            # Pass1 had the same remainder bug, so shift the average by delta/2
            # (pass2 scores aren't stored, so we assume it had similar drift).
            data["total_earned"] = round(data["total_earned"] + delta, 2)
        else:
            sum_possible = sum(float(s.get("max_points", 0)) for s in scores)
            sum_earned = sum(float(s.get("earned_points", 0)) for s in scores)
            missed = sum_possible - sum_earned
            reported_possible = float(data.get("total_possible", sum_possible))
            data["total_earned"] = round(max(reported_possible - missed, 0), 2)

        # Hard cap
        if data.get("total_possible", 0) > 0:
            data["total_earned"] = min(data["total_earned"], data["total_possible"])

        # Recalculate letter grade
        if data.get("total_possible", 0) > 0:
            pct = data["total_earned"] / data["total_possible"] * 100
            data["letter_grade"] = letter_grade(pct)

    return data, fixes


def main():
    parser = argparse.ArgumentParser(description="Patch stored grade_data remainder drift")
    parser.add_argument("--apply", action="store_true", help="Write changes to DB (default: dry run)")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    exams = conn.execute(
        "SELECT anon_id, version, grade_data FROM exams WHERE grade_data IS NOT NULL"
    ).fetchall()

    changed = 0
    unchanged = 0
    grade_changes = []

    for exam in exams:
        data = json.loads(exam["grade_data"])
        old_earned = data.get("total_earned")
        old_grade = data.get("letter_grade")

        patched, fixes = patch(data)

        new_earned = patched.get("total_earned")
        new_grade = patched.get("letter_grade")

        if fixes:
            changed += 1
            diff = (new_earned or 0) - (old_earned or 0)
            print(f"  {exam['anon_id']:12s}  {old_earned} -> {new_earned} ({diff:+.2f})  {old_grade} -> {new_grade}")
            for f in fixes:
                print(f"    {f}")

            if old_grade != new_grade:
                grade_changes.append((exam["anon_id"], old_grade, new_grade))

            if args.apply:
                conn.execute(
                    "UPDATE exams SET grade_data=? WHERE anon_id=?",
                    (json.dumps(patched, ensure_ascii=False), exam["anon_id"])
                )
        else:
            unchanged += 1

    if args.apply:
        conn.commit()

    conn.close()

    print(f"\n{'APPLIED' if args.apply else 'DRY RUN'}: {changed} patched, {unchanged} unchanged")
    if grade_changes:
        print(f"\nLetter grade changes ({len(grade_changes)}):")
        for anon_id, old, new in grade_changes:
            print(f"  {anon_id}: {old} -> {new}")
    elif changed:
        print("No letter grade changes.")


if __name__ == "__main__":
    main()
