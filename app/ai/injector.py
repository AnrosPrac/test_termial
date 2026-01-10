import os
import re
from pathlib import Path
from app.ai.services import execute_ai

def process_injection(text_content: str, folder_name: str, base_path: str = "./output") -> str:
    folder_path = Path(base_path) / folder_name
    folder_path.mkdir(parents=True, exist_ok=True)

    lines = [line.strip() for line in text_content.split('\n') if line.strip()]
    
    results = []
    for i, line in enumerate(lines):
        if "|" in line:
            filename, prompt = line.split("|", 1)
        else:
            filename = f"task_{i+1}.c"
            prompt = line

        raw_code = execute_ai(
            mode="write",
            version="standard",
            language="english",
            input_text=prompt
        )

        clean_code = re.sub(r"```(?:\w+)?\n?|```", "", raw_code).strip()

        file_path = folder_path / filename.strip()
        file_path.write_text(clean_code)
        results.append(str(file_path))

    return str(folder_path)