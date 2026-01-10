import re
import json
from app.ai.services import execute_ai

def process_injection_to_memory(text_content: str):
    plan_raw = execute_ai(
        mode="plan",
        version="standard",
        language="english",
        input_text=text_content
    )
    
    file_plan = []
    
    try:
        match = re.search(r"(\{.*\})", plan_raw, re.DOTALL)
        if not match:
            raise ValueError("No JSON found")
            
        plan_data = json.loads(match.group(1))
        
        if isinstance(plan_data, dict):
            file_plan = plan_data.get("files", [])
        elif isinstance(plan_data, list):
            file_plan = plan_data
            
    except Exception as e:
        print(f"[!] Planning failed, falling back: {e}")
        lines = [l.strip() for l in text_content.split('\n') if l.strip()]
        file_plan = [{"filename": f"task_{i+1}.c", "prompt": l} for i, l in enumerate(lines)]

    generated_files = {}
    for item in file_plan:
        if not isinstance(item, dict): continue
        
        filename = item.get("filename", f"file_{len(generated_files)+1}.c")
        prompt = item.get("prompt", "")

        if not prompt: continue

        raw_code = execute_ai(mode="write", version="standard", language="english", input_text=prompt)
        clean_code = re.sub(r"```[a-zA-Z]*\n|```", "", raw_code).strip()
        generated_files[filename] = clean_code

    return generated_files