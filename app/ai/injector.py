import re
from app.ai.services import execute_ai

def process_injection_to_memory(text_content: str):
    lines = [line.strip() for line in text_content.split('\n') if line.strip()]
    generated_files = {}

    for i, line in enumerate(lines):
        if "|" in line:
            filename, prompt = line.split("|", 1)
            filename = filename.strip()
        else:
            filename = f"task_{i+1}.c"
            prompt = line.strip()

        raw_code = execute_ai(
            mode="write",
            version="standard",
            language="english",
            input_text=prompt
        )

        # Clean backticks
        clean_code = re.sub(r"```[a-zA-Z]*\n|```", "", raw_code).strip()
        generated_files[filename] = clean_code

    return generated_files