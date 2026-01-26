"""
Plagiarism Detection API Router (UPDATED with AI support)
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, validator
from typing import List, Optional
from datetime import datetime

from app.ai.client_bound_guard import verify_client_bound_request
from app.plagiarism.plagiarism_main import PlagiarismDetector, BatchDetector 

router = APIRouter(tags=["Plagiarism Detection"])


# ==================== MODELS ====================

class CodeComparisonRequest(BaseModel):
    """Compare two code submissions with AI"""
    code1: str
    code2: str
    language: str
    submission1_id: Optional[str] = "code1"
    submission2_id: Optional[str] = "code2"
    problem_context: Optional[str] = None  # NEW: Problem description for better AI analysis
    use_ai: Optional[bool] = True  # NEW: Enable/disable AI semantic analysis
    
    @validator('language')
    def validate_language(cls, v):
        if v.lower() not in ['c', 'cpp', 'python']:
            raise ValueError('Language must be c, cpp, or python')
        return v.lower()
    
    @validator('code1', 'code2')
    def validate_code(cls, v):
        if len(v.strip()) == 0:
            raise ValueError('Code cannot be empty')
        if len(v.encode('utf-8')) > 500 * 1024:  # 500KB
            raise ValueError('Code too large (max 500KB)')
        return v


class PlagiarismReportResponse(BaseModel):
    """Enhanced response with AI reasoning"""
    submission1_id: str
    submission2_id: str
    overall_similarity: float
    similarity_level: str
    flag_color: str
    is_likely_ai_generated: bool
    ai_probability: float
    recommendations: List[str]
    confidence: float
    processing_time: float
    layer_results: List[dict]
    # NEW fields
    is_natural_similarity: Optional[bool] = False
    ai_reasoning: Optional[str] = ""


# ==================== ENDPOINTS ====================

@router.post("/compare", response_model=PlagiarismReportResponse)
async def compare_code_submissions(
    request: CodeComparisonRequest,
    user: dict = Depends(verify_client_bound_request)
):
    """
    Compare two code submissions for plagiarism WITH AI SEMANTIC ANALYSIS
    
    Now includes:
    - AI-powered semantic comparison (50% weight)
    - Intelligent boilerplate filtering
    - Natural similarity detection
    - Problem context awareness
    
    The AI understands that similarities in basic constructs (print, def, for, if)
    are natural and not plagiarism. It focuses on unique algorithmic logic.
    """
    try:
        detector = PlagiarismDetector(use_ai=request.use_ai)
        
        report = await detector.compare_submissions(
            code1=request.code1,
            code2=request.code2,
            language=request.language,
            submission1_id=request.submission1_id,
            submission2_id=request.submission2_id,
            problem_context=request.problem_context,
            use_ai_semantic=request.use_ai
        )
        
        # Convert layer results to dict
        layer_dicts = []
        for layer in report.layer_results:
            layer_dicts.append({
                "layer_name": layer.layer_name,
                "similarity_score": round(layer.similarity_score, 4),
                "confidence": round(layer.confidence, 4),
                "execution_time": round(layer.execution_time, 4),
                "details": layer.details
            })
        
        return PlagiarismReportResponse(
            submission1_id=report.submission1_id,
            submission2_id=report.submission2_id,
            overall_similarity=round(report.overall_similarity, 4),
            similarity_level=report.similarity_level.value,
            flag_color=report.flag_color.value,
            is_likely_ai_generated=report.is_likely_ai_generated,
            ai_probability=round(report.ai_probability, 4),
            recommendations=report.recommendations,
            confidence=round(report.confidence, 4),
            processing_time=round(report.processing_time, 4),
            layer_results=layer_dicts,
            is_natural_similarity=report.is_natural_similarity,
            ai_reasoning=report.ai_reasoning
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch-analyze/{assignment_id}")
async def batch_analyze_assignment(
    assignment_id: str,
    question_number: int,
    background_tasks: BackgroundTasks,
    user: dict = Depends(verify_client_bound_request),
    recompute: bool = False
):
    """
    Batch analyze assignment submissions WITHOUT AI (to save costs)
    
    For batch operations, we skip AI semantic analysis to avoid
    excessive API costs. AI is only used for manual teacher-initiated
    comparisons via /compare endpoint.
    """
    # ... rest of the batch code stays the same ...
    # (BatchDetector already has use_ai=False by default)
    pass


# ... rest of the router stays the same ...