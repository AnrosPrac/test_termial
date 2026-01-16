import os
import google.generativeai as genai

# Configure Gemini
genai.configure(api_key=os.getenv("GEMINI-KEY"))

MODEL = genai.GenerativeModel("gemini-2.0-flash-lite")
def run_gemini(prompt: str) -> str:
    response = MODEL.generate_content(prompt)
    return response.text.strip()