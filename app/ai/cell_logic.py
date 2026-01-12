import re
from app.ai.services import execute_ai

def process_cells_generation(text_content: str):
    lines = [line.strip() for line in text_content.split('\n') if line.strip()]
    tasks = []

    for i, line in enumerate(lines, 1):
        if "|" in line:
            filename, question = line.split("|", 1)
        else:
            filename, question = f"task_{i}.py", line

        raw_response = execute_ai(mode="cells", version="standard", language="english", input_text=question)

        # Extraction logic for the new prompt format
        code_part = re.search(r"\[CODE\](.*?)\[OUTPUT\]", raw_response, re.DOTALL)
        output_part = re.search(r"\[OUTPUT\](.*)", raw_response, re.DOTALL)

        clean_code = code_part.group(1).strip() if code_part else raw_response
        # Remove any lingering backticks
        clean_code = re.sub(r"```[a-zA-Z]*\n|```", "", clean_code).strip()
        
        simulated_output = output_part.group(1).strip() if output_part else "No output preview available."

        tasks.append({
            "filename": filename.strip(),
            "question": question.strip(),
            "code": clean_code,
            "output": simulated_output
        })
    return tasks