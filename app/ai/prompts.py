PROMPTS = {
    "ask": {
        "standard": "Answer the following question briefly and clearly in {language}: {input}"
    },
    "write": {
        "standard": "Write only the code for the following request in {language}. Do not include any explanations, markdown formatting, or '```c' tags: {input}"
    },
    "fix": {
        "standard": "Fix the following code. Return ONLY the corrected code. Do not include explanations or markdown tags: {input}"
    },
    "algo": {
        "standard": "Provide a step-by-step logical algorithm for the following: {input}",
        "from_code": "Analyze this code and provide a step-by-step algorithm: {input}"
    },
    "fc": {
        "standard": """
        Output ONLY valid JSON. No markdown tags.
        Schema: {"flows": [{"name": "Flowchart", "steps": [{"type": "start"}, {"type": "process", "text": "..."}, {"type": "decision", "cond": "...", "yes": [], "no": []}, {"type": "end"}]}]}
        Convert this code to the JSON schema: {input}
        """
    },
    "json":{
        "standard": "Output ONLY valid JSON. No markdown tags. No unwanted talk or text only what we ask as a json no other single LETTER OR "
    },
    "explain":{
        "from_code":"understand and explain this code {input} in tanglish , what are the types of explanation we need is 1.Keyword explanation 2.unused syntax or cofusing part explanation 3.if memory included about memory 4.logic of the code written 5.how the students must explain it to the teacher 6.is there any imrpovements needed"
    },
    "diff":{
        "standard":"Explain the difference between the two code snippet {input1} and {input2} with following concpts . the bigO , which is fast which is slow,which will be better for a larger system ,  which is best practice , what all are the risks in both files and what are the improvements required "
},"trace": {
    "from_code": """
    Analyze this code {input}. Simulate its execution step-by-step.
    Output ONLY a JSON object with a list called 'frames'.
    Each 'frame' must contain:
    - 'line_no': The current line executing.
    - 'vars': Current local variables.
    - 'stack': Current function call stack.
    - 'heap': Any pointers or dynamic memory.
    - 'explanation': What is happening in this specific step.
    Ensure it traces enough steps to show the logic clearly.
    """
},
"game_syntax":{"standard": "Generate a JSON object for a 'spot the bug' game. Language: {language}. Output must be valid JSON with keys: 'code' (a 10-line snippet), 'buggy_line' (the line number with the error), 'error_type' (e.g., missing semicolon, case sensitivity), and 'explanation'. Ensure the bug is subtle."},
"game_logic": {
    "standard": """
    Output ONLY a raw JSON object for a logic quiz. No markdown, no prose.
    Level: {level}. Mixed operators: &&, ||, !, ==, !=, ^.
    Schema: {"expr": "string", "answer": boolean}
    """
},
"game_regex": {
    "standard": "Generate a JSON object for a regex matching game. Include 'pattern' (a valid regex) and 'options' (a list of 4 strings where only one matches the pattern). Schema: {'pattern': '...', 'options': ['...', '...', '...', '...'], 'correct_index': int}"
},"plan": {
    "standard": """
    Analyze these requirements and create a file structure. 
    Assign continuous filenames (file1.c, file2.c, etc., or appropriate extensions like .py, .h).
    
    CRITICAL: Output ONLY a raw JSON object. No markdown tags, no backticks, no preamble.
    
    Schema:
    {"files": [{"filename": "file1.c", "prompt": "instructions"}]}
    
    User Input: {input}
    """
}}