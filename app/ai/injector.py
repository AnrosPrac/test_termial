import re
import json
from app.ai.services import execute_ai

def map_tasks(raw_text: str):
    mapping_prompt = f"Split the following text into a clean JSON list of individual coding tasks. Each item should be a clear, single-sentence prompt. Return ONLY the JSON list: {raw_text}"
    
    raw_plan = execute_ai(
        mode="json", 
        version="standard", 
        language="english", 
        input_text=mapping_prompt
    )
    
    try:
        match = re.search(r"\[.*\]", raw_plan, re.DOTALL)
        return json.loads(match.group(0)) if match else [l.strip() for l in raw_text.split('\n') if l.strip()]
    except:
        return [l.strip() for l in raw_text.split('\n') if l.strip()]

def process_injection_to_memory(text_content: str):
    prompts = map_tasks(text_content)
    
    generated_files = {}
    for i, prompt in enumerate(prompts, 1):
        filename = f"task_{i}.c"
        raw_code = execute_ai(
            mode="write", 
            version="standard", 
            language="english", 
            input_text=prompt
        )
        clean_code = re.sub(r"```[a-zA-Z]*\n|```", "", raw_code).strip()
        generated_files[filename] = clean_code

    return generated_files