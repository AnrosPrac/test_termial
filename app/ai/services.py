from app.ai.prompts import PROMPTS
from app.ai.gemini_core import run_gemini
import json
from pathlib import Path
import re
# Assuming you place the flowchart script in the same directory
from app.ai.flowchart_engine_v1 import generate_flowchart_from_json_and_save

def normalize_language(lang: str) -> str:
    return "Tanglish (Tamil + English mix)" if lang == "tanglish" else "English"

def execute_ai(mode: str, version: str, language: str, input_text: str,input2: str = ""):
    if mode not in PROMPTS:
        raise ValueError("Unsupported mode")
    if version not in PROMPTS[mode]:
        raise ValueError("Unsupported version")

    prompt_template = PROMPTS[mode][version]
    if mode == "diff":
        # Ensure we have both inputs for diff, otherwise it will crash
        prompt = prompt_template.format(
            language=normalize_language(language),
            input1=input_text,
            input2=input2 if input2 else ""
        )
    else:
        prompt = prompt_template.format(
            language=normalize_language(language),
            input=input_text
        )

    raw_output = run_gemini(prompt)

    # SPECIAL CASE: Flowchart Generation
    if mode == "fc":
        try:
            # ROBUST CLEANING: Find the first '{' and the last '}'
            # This ignores any "Here is your JSON:" text or ```json tags
            match = re.search(r'\{.*\}', raw_output, re.DOTALL)
            if match:
                clean_json = match.group(0)
                flow_data = json.loads(clean_json)
                
                temp_img_path = Path("/tmp/flow_output.png")
                # Call your generator
                success = generate_flowchart_from_json_and_save(flow_data, temp_img_path)
                
                if success:
                    return temp_img_path
            else:
                print(f"No JSON found in Gemini output: {raw_output}")
                return None
        except Exception as e:
            print(f"Flowchart processing error: {e}")
            return None

    return raw_output