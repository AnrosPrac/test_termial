from gemini_core import run_gemini

if __name__ == "__main__":
    result = run_gemini(
        mode="ask",
        language="english",
        input_text="Explain pointers in C"
    )
    print(result)