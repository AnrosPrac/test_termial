import re
from app.ai.services import execute_ai

def process_cells_generation(text_content: str):
    lines = [line.strip() for line in text_content.split('\n') if line.strip()]
    tasks = []

    for i, line in enumerate(lines, 1):
        # Handle the "filename | question" format
        if "|" in line:
            filename, question = line.split("|", 1)
            filename = filename.strip()
            question = question.strip()
        else:
            filename = f"task_{i}.py"
            question = line.strip()

        # Request ONLY the code from Gemini
        raw_code = execute_ai(
            mode="write", 
            version="standard", 
            language="english", 
            input_text=question
        )

        # Clean markdown fences (important for .ipynb execution)
        clean_code = re.sub(r"```[a-zA-Z]*\n|```", "", raw_code).strip()
        
        tasks.append({
            "filename": filename,
            "question": question,
            "code": clean_code
        })

    return tasks