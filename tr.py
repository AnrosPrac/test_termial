"""
Lumetrix — Merge + Transform to Platform Schema
=================================================
Takes TWO separate files:
  1. question_bank.json  — questions extracted from PDF
  2. test_cases.json     — test cases (Q1: { public[], private[] })

Merges them and outputs final platform_questions.json

USAGE:
    python merge_and_transform.py

No external libraries needed.
"""

import json
import uuid
import datetime
import os
import re

# ── CONFIG — update these ──────────────────────────────────
QUESTIONS_FILE   = "question_bank.json"     # from extract_questions.py
TEST_CASES_FILE  = "test_cases.json"        # your test cases file
OUTPUT_FILE      = "platform_questions.json"
COURSE_ID        = "COURSE_PYTHON_2026"     # your actual course ID
DEFAULT_LANGUAGE = "python"
DEFAULT_TYPE     = "coding"
TIME_LIMIT       = 2.0                      # seconds
MEMORY_LIMIT     = 256                      # MB
# ──────────────────────────────────────────────────────────

POINTS_MAP = {
    "easy":     100,
    "moderate": 250,
    "medium":   250,
    "hard":     500,
    "capstone": 1000
}


def generate_question_id():
    return "Q_" + uuid.uuid4().hex[:8].upper()


def normalize_difficulty(raw):
    mapping = {
        "easy":     "easy",
        "moderate": "medium",
        "medium":   "medium",
        "hard":     "hard",
        "capstone": "hard"
    }
    return mapping.get(str(raw).lower().strip(), "easy")


def extract_module_id(key, entry):
    if "module" in entry and entry["module"]:
        return f"MODULE_{entry['module']}"
    match = re.match(r"M(\d+)-Q\d+", str(key))
    if match:
        return f"MODULE_{match.group(1)}"
    if "capstone" in str(key).lower():
        return "MODULE_CAPSTONE"
    return None


def build_test_cases(tc_entry):
    """Convert public[] + private[] into unified test_cases[]."""
    test_cases = []
    for tc in tc_entry.get("public", []):
        test_cases.append({
            "input":     tc.get("input", ""),
            "output":    tc.get("output", ""),
            "is_sample": True,
            "weight":    1.0
        })
    for tc in tc_entry.get("private", []):
        test_cases.append({
            "input":     tc.get("input", ""),
            "output":    tc.get("output", ""),
            "is_sample": False,
            "weight":    1.0
        })
    return test_cases


def normalize_tc_key(key):
    """
    Normalize test case keys to match question keys.
    Handles formats like:
      'Q1', 'Q01', 'M1-Q1', 'q1' etc.
    Returns a list of possible match candidates.
    """
    key = key.strip()
    candidates = [key, key.upper(), key.lower()]

    # If key is like 'Q1' or 'Q01', also try 'M1-Q1', 'M01-Q1' etc.
    simple_match = re.match(r"[Qq](\d+)$", key)
    if simple_match:
        num = simple_match.group(1)
        candidates += [
            f"Q{num}",
            f"Q{int(num):02d}",
        ]

    return candidates


def match_test_cases(q_key, tc_data):
    """
    Try to find a matching test case entry for a given question key.
    Supports flexible key matching between the two files.
    """
    # Direct match first
    if q_key in tc_data:
        return tc_data[q_key]

    # Try stripping module prefix: 'M1-Q5' -> 'Q5'
    stripped = re.sub(r"M\d+-", "", q_key)
    if stripped in tc_data:
        return tc_data[stripped]

    # Try zero-padded: 'Q5' -> 'Q05'
    padded = re.sub(r"Q(\d+)$", lambda m: f"Q{int(m.group(1)):02d}", stripped)
    if padded in tc_data:
        return tc_data[padded]

    return None


def load_questions(path):
    """Load questions file — supports both array and dict formats."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # If it's the full meta+questions format from extract_questions.py
    if isinstance(data, dict) and "questions" in data:
        return data["questions"]

    # If it's a flat dict like { "Q1": {...}, "Q2": {...} }
    if isinstance(data, dict):
        result = []
        for k, v in data.items():
            if isinstance(v, dict):
                v["_key"] = k
                result.append(v)
        return result

    # If it's already a list
    if isinstance(data, list):
        return data

    return []


def load_test_cases(path):
    """Load test cases file — flat dict format { Q1: {...}, Q2: {...} }"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def merge_and_transform(questions_path, tc_path, output_path):
    # ── Load files ──
    if not os.path.exists(questions_path):
        print(f"❌ Questions file not found: {questions_path}")
        return
    if not os.path.exists(tc_path):
        print(f"❌ Test cases file not found: {tc_path}")
        return

    questions = load_questions(questions_path)
    tc_data   = load_test_cases(tc_path)

    print(f"✅ Loaded {len(questions)} questions from {questions_path}")
    print(f"✅ Loaded {len(tc_data)} test case entries from {tc_path}")

    platform_questions = []
    matched   = 0
    unmatched = 0
    created_at = datetime.datetime.utcnow().isoformat() + "Z"

    for entry in questions:
        # Determine key for this question
        q_key = (
            entry.get("id") or
            entry.get("_key") or
            entry.get("question_id") or
            f"Q{entry.get('question_number', '')}"
        )

        # Get description
        description = (
            entry.get("description") or
            entry.get("question") or
            entry.get("title") or
            ""
        )

        # Get title
        title = entry.get("title") or description[:60].rstrip() + ("..." if len(description) > 60 else "")

        # Difficulty
        raw_diff   = entry.get("difficulty", "Easy")
        difficulty = normalize_difficulty(raw_diff)
        points     = POINTS_MAP.get(raw_diff.lower().strip(), 100)

        # Module
        module_id = extract_module_id(q_key, entry)

        # Match test cases
        tc_entry = match_test_cases(q_key, tc_data)
        if tc_entry:
            test_cases = build_test_cases(tc_entry)
            matched += 1
        else:
            # No test cases found — leave empty with a warning
            test_cases = []
            unmatched += 1
            print(f"  ⚠️  No test cases found for: {q_key}")

        platform_q = {
            "question_id":  generate_question_id(),
            "course_id":    COURSE_ID,
            "module_id":    module_id,
            "title":        title,
            "description":  description,
            "difficulty":   difficulty,
            "language":     DEFAULT_LANGUAGE,
            "problem_type": DEFAULT_TYPE,
            "test_cases":   test_cases,
            "starter_code": "# Write your solution here\n",
            "time_limit":   TIME_LIMIT,
            "memory_limit": MEMORY_LIMIT,
            "points":       points,
            "is_active":    True,
            "created_at":   created_at
        }

        platform_questions.append(platform_q)

    # Save
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(platform_questions, f, indent=2, ensure_ascii=False)

    print(f"\n🎉 Done!")
    print(f"   ✅ Total questions transformed : {len(platform_questions)}")
    print(f"   🔗 Test cases matched          : {matched}")
    print(f"   ⚠️  No test cases found for    : {unmatched}")
    print(f"   💾 Output saved to             : {output_path}")


if __name__ == "__main__":
    merge_and_transform(QUESTIONS_FILE, TEST_CASES_FILE, OUTPUT_FILE)