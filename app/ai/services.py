from app.ai.prompts import PROMPTS
from app.ai.gemini_core import run_gemini

def normalize_language(lang: str) -> str:
    return "Tanglish (Tamil + English mix)" if lang == "tanglish" else "English"

def execute_ai(
    mode: str,
    version: str,
    language: str,
    input_text: str
) -> str:

    if mode not in PROMPTS:
        raise ValueError("Unsupported mode")

    if version not in PROMPTS[mode]:
        raise ValueError("Unsupported version")

    prompt_template = PROMPTS[mode][version]

    prompt = prompt_template.format(
        language=normalize_language(language),
        input=input_text
    )

    return run_gemini(prompt)