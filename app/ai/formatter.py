from app.ai.services import execute_ai

def process_formatting(text_content: str):
    formatting_prompt = f"""
    Act as a code lab assistant. Reorganize the provided text into a structured list of tasks.
    Each task must be on a new line following this exact format:
    qN.c | [Clear coding task description]
    
    Replace 'N' with the task number starting from 1. 
    If a task is already clear, keep it. If it is conversational, convert it into a direct coding prompt.
    Return ONLY the list.
    
    Text to format:
    {text_content}
    """

    raw_output = execute_ai(
        mode="write",
        version="standard",
        language="english",
        input_text=formatting_prompt
    )
    
    return raw_output