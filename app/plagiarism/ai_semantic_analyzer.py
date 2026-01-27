"""
AI Semantic Analyzer
Uses your existing Gemini infrastructure for intelligent code comparison

This analyzer understands:
- Natural similarities in boilerplate code
- Problem-solving approach differences
- Algorithmic logic vs syntax similarities
"""

from typing import Dict, Tuple, Optional
import re


class AISemanticAnalyzer:
    """
    AI-powered semantic code comparison using Gemini
    Integrates with your existing gemini_core infrastructure
    """
    
    def __init__(self):
        # Import your existing Gemini helper
        from app.ai.gemini_core import run_gemini
        self.run_gemini = run_gemini
    
    async def compare(
        self,
        code1: str,
        code2: str,
        language: str,
        problem_context: Optional[str] = None
    ) -> Tuple[float, Dict]:
        """
        Compare two code submissions using AI semantic analysis
        
        Args:
            code1: First code submission
            code2: Second code submission
            language: Programming language
            problem_context: Optional problem description for context
        
        Returns:
            (similarity_score, details_dict)
        """
        try:
            # Build prompt
            prompt = self._build_comparison_prompt(
                code1, code2, language, problem_context
            )
            
            # Use your existing Gemini runner
            response_text = self.run_gemini(prompt)
            
            # Parse response
            similarity_score, reasoning, is_natural = self._parse_response(response_text)
            
            details = {
                "ai_analysis": response_text,
                "reasoning": reasoning,
                "is_natural_similarity": is_natural,
                "model": "gemini-2.5-flash-lite"
            }
            
            return similarity_score, details
            
        except Exception as e:
            print(f"AI Semantic Analysis error: {e}")
            # Return neutral score on error
            return 0.0, {
                "error": str(e),
                "is_natural_similarity": False,
                "reasoning": f"Analysis failed: {str(e)}"
            }
    
    def _build_comparison_prompt(
        self,
        code1: str,
        code2: str,
        language: str,
        problem_context: Optional[str] = None
    ) -> str:
        """Build the comparison prompt for Gemini"""
        
        context_section = ""
        if problem_context:
            context_section = f"""
PROBLEM CONTEXT:
{problem_context}

"""
        
        prompt = f"""You are an expert code plagiarism detector. Your task is to analyze two code submissions and determine if they are plagiarized or just naturally similar due to the problem constraints.

{context_section}LANGUAGE: {language}

CODE SUBMISSION 1:
```{language}
{code1}
```

CODE SUBMISSION 2:
```{language}
{code2}
```

ANALYSIS INSTRUCTIONS:

1. **Ignore Natural Boilerplate**: Things like `print()`, `def main()`, `for i in range()`, `if __name__ == "__main__"`, standard imports, etc. are NOT plagiarism - they're natural for the problem.

2. **Focus on Algorithmic Logic**: Look for:
   - Unique variable naming patterns
   - Identical algorithmic approaches when multiple approaches exist
   - Similar code organization/structure beyond what the problem requires
   - Identical helper functions or unusual implementations
   - Same edge case handling (when not obvious)

3. **Consider Problem Constraints**: If the problem has specific requirements that force similar code, that's NOT plagiarism.

4. **Detect True Plagiarism**: Look for:
   - Identical logic with only variable names changed
   - Same uncommon approaches/algorithms
   - Identical comments or unusual code patterns
   - Copy-paste with minor modifications

OUTPUT FORMAT (required):
SIMILARITY_SCORE: [0.0 to 1.0]
IS_NATURAL: [YES or NO]
REASONING: [Your detailed explanation in 2-3 sentences]

Example outputs:
- If different algorithms: "SIMILARITY_SCORE: 0.15\nIS_NATURAL: YES\nREASONING: Both use standard input/output and loops, but implement different algorithms. Code 1 uses sorting while Code 2 uses a hash map approach. Natural similarity only."

- If same algorithm but different style: "SIMILARITY_SCORE: 0.40\nIS_NATURAL: YES\nREASONING: Both use similar two-pointer algorithm, which is a common approach for this problem. However, variable names and loop structures differ significantly. This appears to be convergent thinking rather than copying."

- If plagiarism: "SIMILARITY_SCORE: 0.85\nIS_NATURAL: NO\nREASONING: Identical algorithmic approach with only variable names changed. Same unusual helper function structure and identical edge case handling. Strong evidence of plagiarism."

Now analyze these two submissions:"""
        
        return prompt
    
    def _parse_response(self, response: str) -> Tuple[float, str, bool]:
        """
        Parse Gemini's response to extract similarity score and reasoning
        
        Returns:
            (similarity_score, reasoning, is_natural)
        """
        # Extract similarity score
        score_match = re.search(r'SIMILARITY_SCORE:\s*([0-9.]+)', response, re.IGNORECASE)
        similarity_score = float(score_match.group(1)) if score_match else 0.5
        
        # Clamp to valid range
        similarity_score = max(0.0, min(1.0, similarity_score))
        
        # Extract natural flag
        natural_match = re.search(r'IS_NATURAL:\s*(YES|NO)', response, re.IGNORECASE)
        is_natural = natural_match.group(1).upper() == "YES" if natural_match else False
        
        # Extract reasoning
        reasoning_match = re.search(r'REASONING:\s*(.+?)(?:\n\n|$)', response, re.IGNORECASE | re.DOTALL)
        reasoning = reasoning_match.group(1).strip() if reasoning_match else "No detailed reasoning provided."
        
        return similarity_score, reasoning, is_natural