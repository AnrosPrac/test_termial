import re
import json
from app.ai.services import execute_ai

def process_injection_to_memory(text_content: str):
    # Ask Gemini for the plan
    plan_raw = execute_ai(
        mode="plan",
        version="standard",
        language="english",
        input_text=text_content
    )
    
    try:
        # CTO FIX: More robust regex to find the JSON block
        match = re.search(r"(\{.*\})", plan_raw, re.DOTALL)
        if not match:
            raise ValueError("No JSON found in AI response")
            
        plan_data = json.loads(match.group(1))
        # Ensure we get a list even if the key is missing
        file_plan = plan_data.get("files", []) 
        
    except Exception as e:
        print(f"[!] Planning failed, falling back to line-by-line: {e}")
        # Fallback to your original logic if AI fails to plan
        lines = [l.strip() for l in text_content.split('\n') if l.strip()]
        file_plan = [{"filename": f"task_{i+1}.c", "prompt": l} for i, l in enumerate(lines)]

    generated_files = {}
    for item in file_plan:
        filename = item.get("filename", "untitled.c")
        prompt = item.get("prompt", "")

        raw_code = execute_ai(mode="write", version="standard", language="english", input_text=prompt)
        # Your exact cleaning logic
        clean_code = re.sub(r"```[a-zA-Z]*\n|```", "", raw_code).strip()
        generated_files[filename] = clean_code

    return generated_files