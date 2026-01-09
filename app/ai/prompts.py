PROMPTS = {
    "ask": {
        "short": """
Answer briefly in {language}.
Give a direct explanation.

Question:
{input}
""",

        "long": """
Explain in detail in {language}.
Use simple words.
Give examples if needed.

Question:
{input}
""",

        "exam": """
Answer like an exam-ready explanation in {language}.
Be precise, structured, and formal.

Question:
{input}
"""
    },

    "algorithm": {
        "standard": """
Write a clear step-by-step algorithm in {language}.

Problem:
{input}
"""
    },

    "fix": {
        "standard": """
The following code has issues.

1. Explain the error in {language}
2. Provide corrected code
3. Brief explanation of fix

Code:
{input}
"""
    }
}