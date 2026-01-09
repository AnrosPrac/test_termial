from app.ai.prompts import PROMPTS
from app.ai.gemini_core import run_gemini
import json
from pathlib import Path
# Assuming you place the flowchart script in the same directory
from flowchart_engine_v1 import generate_flowchart_from_json_and_save

def normalize_language(lang: str) -> str:
    return "Tanglish (Tamil + English mix)" if lang == "tanglish" else "English"

def execute_ai(mode: str, version: str, language: str, input_text: str):
    if mode not in PROMPTS:
        raise ValueError("Unsupported mode")
    if version not in PROMPTS[mode]:
        raise ValueError("Unsupported version")

    prompt_template = PROMPTS[mode][version]
    prompt = prompt_template.format(
        language=normalize_language(language),
        input=input_text
    )

    raw_output = run_gemini(prompt)

    # SPECIAL CASE: Flowchart Generation
    if mode == "fc":
        try:
            # Clean the output in case Gemini adds markdown fences
            clean_json = raw_output.replace("```json", "").replace("```", "").strip()
            flow_data = json.loads(clean_json)
            
            temp_img_path = Path("/tmp/flow_output.png")
            success = generate_flowchart_from_json_and_save(flow_data, temp_img_path)
            
            if success:
                return temp_img_path # Return the path to the image
        except Exception as e:
            print(f"Flowchart processing error: {e}")
            return None

    return raw_output