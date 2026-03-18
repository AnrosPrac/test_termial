"""
Lumetrix Question Bank — PDF to JSON Extractor
================================================
REQUIREMENTS:
    pip install pymupdf

USAGE:
    python extract_questions.py

Place this script in the same folder as your PDF file.
Update PDF_PATH below if needed.
"""

import json
import re
import fitz  # pymupdf

# ── CONFIG ──────────────────────────────────────────────
PDF_PATH = "xy.pdf"
OUTPUT_PATH = "question_bank.json"
# ────────────────────────────────────────────────────────

MODULE_PATTERN = re.compile(r"Module\s+(\d+)[:\s—–-]+(.+)")
SECTION_PATTERN = re.compile(r"Section\s+([AB])\s*[—–-]", re.IGNORECASE)
DIFFICULTY_PATTERN = re.compile(r"(Easy|Moderate|Hard|Capstone)\s*\(Section\s*([AB])\)", re.IGNORECASE)
QUESTION_PATTERN = re.compile(r"^Q(\d+)\.\s*\[(Easy|Moderate|Hard|Capstone)\]\s*(.+)", re.DOTALL)
CAPSTONE_PATTERN = re.compile(r"^Capstone\s+(\d+)[:\s—–-]+(.+?)(?=Capstone\s+\d+|$)", re.DOTALL)


def extract_text_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        pages.append(page.get_text())
    return pages


def parse_questions(pages):
    full_text = "\n".join(pages)
    lines = full_text.split("\n")

    questions = []
    current_module_num = None
    current_module_name = ""
    current_section = "A"

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Detect module
        mod_match = MODULE_PATTERN.match(line)
        if mod_match:
            current_module_num = int(mod_match.group(1))
            current_module_name = mod_match.group(2).strip()
            i += 1
            continue

        # Detect section
        sec_match = SECTION_PATTERN.search(line)
        if sec_match:
            current_section = sec_match.group(1).upper()
            i += 1
            continue

        # Detect difficulty + section combo header (e.g. "Easy (Section A)")
        diff_match = DIFFICULTY_PATTERN.match(line)
        if diff_match:
            current_section = diff_match.group(2).upper()
            i += 1
            continue

        # Detect question
        q_match = QUESTION_PATTERN.match(line)
        if q_match:
            q_num = int(q_match.group(1))
            difficulty = q_match.group(2).capitalize()
            q_text = q_match.group(3).strip()

            # Collect multi-line question text
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                # Stop if next question or section header starts
                if QUESTION_PATTERN.match(next_line) or SECTION_PATTERN.search(next_line) or MODULE_PATTERN.match(next_line):
                    break
                if next_line:
                    q_text += " " + next_line
                j += 1

            q_text = re.sub(r"\s+", " ", q_text).strip()

            questions.append({
                "id": f"M{current_module_num}-Q{q_num}",
                "question_number": q_num,
                "module": current_module_num,
                "module_name": current_module_name,
                "section": current_section,
                "difficulty": difficulty,
                "question": q_text,
                "test_cases": {
                    "public": [],
                    "private": []
                }
            })
            i = j
            continue

        i += 1

    return questions


def parse_capstones(pages):
    full_text = "\n".join(pages)
    capstones = []

    # Find capstone section
    capstone_section_match = re.search(r"CAPSTONE CHALLENGES(.+?)(?=Total Questions:|\Z)", full_text, re.DOTALL)
    if not capstone_section_match:
        return capstones

    capstone_text = capstone_section_match.group(1)

    # Split by each capstone
    entries = re.split(r"(Capstone\s+\d+[:\s—–-]+)", capstone_text)

    current_title = ""
    for part in entries:
        title_match = re.match(r"Capstone\s+(\d+)[:\s—–-]+", part)
        if title_match:
            current_title = part.strip()
        elif current_title:
            num_match = re.search(r"Capstone\s+(\d+)", current_title)
            cap_num = int(num_match.group(1)) if num_match else 0
            description = re.sub(r"\s+", " ", part).strip()

            # Extract module references
            mod_ref_match = re.search(r"Modules?[:\s]+([0-9,\s]+)", description)
            modules_used = []
            if mod_ref_match:
                modules_used = [int(m.strip()) for m in mod_ref_match.group(1).split(",") if m.strip().isdigit()]

            # Extract capstone name from first line of description
            first_line = description.split(".")[0].strip()

            capstones.append({
                "id": f"CAPSTONE-{cap_num}",
                "capstone_number": cap_num,
                "module": 0,
                "module_name": "Capstone Challenges",
                "section": "Capstone",
                "difficulty": "Capstone",
                "title": first_line,
                "modules_integrated": modules_used,
                "question": description,
                "test_cases": {
                    "public": [],
                    "private": []
                }
            })
            current_title = ""

    return capstones


def build_json(questions, capstones):
    all_items = questions + capstones

    output = {
        "meta": {
            "title": "Lumetrix | Sidhilynx Python Programming Question Bank",
            "edition": "1.0",
            "academic_year": "2025-26",
            "total_questions": len(questions),
            "total_capstones": len(capstones),
            "total_items": len(all_items),
            "modules": 10,
            "sections": ["A", "B"],
            "difficulty_levels": ["Easy", "Moderate", "Hard", "Capstone"],
            "test_case_structure": {
                "public_count": 7,
                "private_count": 8,
                "note": "Test cases to be filled per question"
            }
        },
        "modules_index": {
            "1": "Computer Fundamentals & Problem Solving",
            "2": "Python Basics & Data Representation",
            "3": "Operators and Expressions",
            "4": "Conditional Control Statements",
            "5": "Repetitive Control Statements",
            "6": "Functions and Recursion",
            "7": "Strings and Tuples",
            "8": "Lists and List Comprehensions",
            "9": "Sets, Dictionaries & Functional Programming",
            "10": "Object-Oriented Programming (OOP)"
        },
        "questions": all_items
    }

    return output


def main():
    print(f"📄 Opening PDF: {PDF_PATH}")
    pages = extract_text_from_pdf(PDF_PATH)
    print(f"✅ Extracted {len(pages)} pages")

    print("🔍 Parsing questions...")
    questions = parse_questions(pages)
    print(f"✅ Found {len(questions)} questions")

    print("🏆 Parsing capstones...")
    capstones = parse_capstones(pages)
    print(f"✅ Found {len(capstones)} capstones")

    print("🏗️  Building JSON structure...")
    output = build_json(questions, capstones)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n🎉 Done! JSON saved to: {OUTPUT_PATH}")
    print(f"📊 Summary:")
    print(f"   Total questions : {output['meta']['total_questions']}")
    print(f"   Total capstones : {output['meta']['total_capstones']}")
    print(f"   Total items     : {output['meta']['total_items']}")


if __name__ == "__main__":
    main()