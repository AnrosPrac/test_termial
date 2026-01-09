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
    }
}