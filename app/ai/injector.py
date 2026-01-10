import re
import json
from app.ai.services import execute_ai

def process_injection_to_memory(text_content: str):
    # STAGE 1: AI PLANNING
    # We use a new 'plan' mode to let Gemini decide the file structure
    plan_raw = execute_ai(
        mode="plan",
        version="standard",
        language="english",
        input_text=text_content
    )
    
    # Robust JSON extraction to handle any extra text Gemini might return
    try:
        plan_match = re.search(r'\{.*\}', plan_raw, re.DOTALL)
        if plan_match:
            plan_data = json.loads(plan_match.group(0))
            file_plan = plan_data.get("files", [])
        else:
            raise ValueError("No JSON found")
    except Exception:
        # Fallback: If AI planning fails, use your original line-by-line logic
        lines = [line.strip() for line in text_content.split('\n') if line.strip()]
        file_plan = []
        for i, line in enumerate(lines):
            if "|" in line:
                fn, p = line.split("|", 1)
                file_plan.append({"filename": fn.strip(), "prompt": p.strip()})
            else:
                file_plan.append({"filename": f"task_{i+1}.c", "prompt": line.strip()})

    generated_files = {}

    # STAGE 2: GENERATION
    for item in file_plan:
        filename = item["filename"]
        prompt = item["prompt"]

        raw_code = execute_ai(
            mode="write",
            version="standard",
            language="english",
            input_text=prompt
        )

        # Clean backticks (keeping your exact regex)
        clean_code = re.sub(r"```[a-zA-Z]*\n|```", "", raw_code).strip()
        generated_files[filename] = clean_code

    return generated_files