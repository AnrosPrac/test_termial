"""
Plagiarism Detection System - Main Orchestrator (UPDATED with AI)
Now includes AI-powered semantic analysis for better accuracy
"""

from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import asyncio
from enum import Enum


class SimilarityLevel(Enum):
    """Similarity classification levels"""
    CLEAN = "clean"           # 0-30%
    SUSPICIOUS = "suspicious" # 30-60%
    HIGH = "high"            # 60-100%


class FlagColor(Enum):
    """Visual flag indicators"""
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass
class DetectionResult:
    """Result from a single detection layer"""
    layer_name: str
    similarity_score: float  # 0.0 to 1.0
    confidence: float        # 0.0 to 1.0
    details: Dict
    execution_time: float


@dataclass
class PlagiarismReport:
    """Complete plagiarism analysis report"""
    submission1_id: str
    submission2_id: str
    overall_similarity: float
    similarity_level: SimilarityLevel
    flag_color: FlagColor
    layer_results: List[DetectionResult]
    is_likely_ai_generated: bool
    ai_probability: float
    recommendations: List[str]
    confidence: float
    processing_time: float
    # New fields
    is_natural_similarity: bool = False
    ai_reasoning: str = ""


class PlagiarismDetector:
    """
    Main plagiarism detection orchestrator
    NOW WITH AI-POWERED SEMANTIC ANALYSIS
    """
    
    # Updated weights for MANUAL comparison (with AI)
    WEIGHTS_MANUAL = {
        'ai_semantic': 0.50,   # AI gets 50% weight for manual comparisons
        'ast': 0.20,           # AST reduced to 20%
        'token': 0.15,         # Token reduced to 15%
        'control_flow': 0.15,  # Control flow reduced to 15%
    }
    
    # Original weights for BATCH comparison (without AI to save costs)
    WEIGHTS_BATCH = {
        'ast': 0.35,
        'token': 0.30,
        'control_flow': 0.35,
    }
    
    THRESHOLDS = {
        'clean': 0.30,
        'suspicious': 0.60
    }
    
    def __init__(self, use_ai: bool = True):
        """
        Initialize all detection layers
        
        Args:
            use_ai: Whether to use AI semantic analysis (default: True for manual, False for batch)
        """
        from app.plagiarism.ast_analyzer import ASTAnalyzer
        from app.plagiarism.token_fingerprinter import TokenFingerprinter
        from app.plagiarism.control_flow import ControlFlowAnalyzer
        from app.plagiarism.ai_detector import AIDetector
        
        self.ast_analyzer = ASTAnalyzer()
        self.token_fingerprinter = TokenFingerprinter()
        self.control_flow_analyzer = ControlFlowAnalyzer()
        self.ai_detector = AIDetector()
        
        self.use_ai = use_ai
        if use_ai:
            from app.plagiarism.ai_semnatic_analyzer import AISemanticAnalyzer
            self.ai_semantic = AISemanticAnalyzer()
    
    async def compare_submissions(
        self,
        code1: str,
        code2: str,
        language: str,
        submission1_id: str,
        submission2_id: str,
        problem_context: str = None,
        use_ai_semantic: bool = None  # Override instance setting
    ) -> PlagiarismReport:
        """
        Compare two code submissions
        
        Args:
            use_ai_semantic: Override to force AI usage (None = use instance setting)
            problem_context: Optional problem description for better AI analysis
        """
        import time
        start_time = time.time()
        
        # Validate language
        if language.lower() not in ['c', 'cpp', 'python']:
            raise ValueError(f"Unsupported language: {language}")
        
        # Determine if we should use AI
        use_ai = self.use_ai if use_ai_semantic is None else use_ai_semantic
        
        # Run detection layers
        if use_ai:
            # WITH AI: Run all layers including semantic analysis
            layer_results = await asyncio.gather(
                self._run_ai_semantic_analysis(code1, code2, language, problem_context),
                self._run_ast_analysis(code1, code2, language),
                self._run_token_analysis(code1, code2, language),
                self._run_control_flow_analysis(code1, code2, language),
                return_exceptions=True
            )
            weights = self.WEIGHTS_MANUAL
        else:
            # WITHOUT AI: Skip semantic analysis (for batch operations)
            layer_results = await asyncio.gather(
                self._run_ast_analysis(code1, code2, language),
                self._run_token_analysis(code1, code2, language),
                self._run_control_flow_analysis(code1, code2, language),
                return_exceptions=True
            )
            weights = self.WEIGHTS_BATCH
        
        # Run AI detection on individual submissions (lightweight)
        ai_result1 = await self._run_ai_detection(code1, language)
        ai_result2 = await self._run_ai_detection(code2, language)
        
        # Filter out exceptions
        valid_results = [r for r in layer_results if not isinstance(r, Exception)]
        
        # Extract AI semantic analysis if present
        is_natural_similarity = False
        ai_reasoning = ""
        if use_ai and valid_results:
            for result in valid_results:
                if result.layer_name == "AI Semantic Analysis":
                    is_natural_similarity = result.details.get('is_natural_similarity', False)
                    ai_reasoning = result.details.get('reasoning', '')
                    break
        
        # Calculate weighted similarity
        overall_similarity = self._calculate_weighted_score(valid_results, weights)
        
        # AI override: If AI says it's natural similarity, reduce final score
        if is_natural_similarity and overall_similarity > 0.3:
            print(f"🤖 AI detected natural similarity, adjusting score from {overall_similarity:.2%}")
            overall_similarity = overall_similarity * 0.7  # Reduce by 30%
        
        # Determine similarity level and flag
        similarity_level = self._classify_similarity(overall_similarity)
        flag_color = self._assign_flag_color(similarity_level)
        
        # Check if either submission is likely AI-generated
        is_likely_ai = ai_result1.similarity_score > 0.7 or ai_result2.similarity_score > 0.7
        ai_probability = max(ai_result1.similarity_score, ai_result2.similarity_score)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            overall_similarity,
            is_likely_ai,
            valid_results,
            is_natural_similarity,
            ai_reasoning
        )
        
        # Calculate confidence
        confidence = self._calculate_confidence(valid_results)
        
        processing_time = time.time() - start_time
        
        return PlagiarismReport(
            submission1_id=submission1_id,
            submission2_id=submission2_id,
            overall_similarity=overall_similarity,
            similarity_level=similarity_level,
            flag_color=flag_color,
            layer_results=valid_results,
            is_likely_ai_generated=is_likely_ai,
            ai_probability=ai_probability,
            recommendations=recommendations,
            confidence=confidence,
            processing_time=processing_time,
            is_natural_similarity=is_natural_similarity,
            ai_reasoning=ai_reasoning
        )
    
    async def _run_ai_semantic_analysis(
        self,
        code1: str,
        code2: str,
        language: str,
        problem_context: str = None
    ) -> DetectionResult:
        """Run AI-powered semantic comparison"""
        import time
        start = time.time()
        
        try:
            similarity, details = await self.ai_semantic.compare(
                code1, code2, language, problem_context
            )
            
            return DetectionResult(
                layer_name="AI Semantic Analysis",
                similarity_score=similarity,
                confidence=details.get('confidence', 0.85),
                details=details,
                execution_time=time.time() - start
            )
        except Exception as e:
            print(f"⚠️ AI Semantic Analysis failed: {e}")
            return DetectionResult(
                layer_name="AI Semantic Analysis",
                similarity_score=0.5,  # Neutral fallback
                confidence=0.0,
                details={"error": str(e)},
                execution_time=time.time() - start
            )
    
    async def _run_ast_analysis(self, code1: str, code2: str, language: str) -> DetectionResult:
        """Run Abstract Syntax Tree comparison"""
        import time
        start = time.time()
        
        try:
            similarity, details = await self.ast_analyzer.compare(code1, code2, language)
            return DetectionResult(
                layer_name="AST Analysis",
                similarity_score=similarity,
                confidence=0.95,
                details=details,
                execution_time=time.time() - start
            )
        except Exception as e:
            return DetectionResult(
                layer_name="AST Analysis",
                similarity_score=0.0,
                confidence=0.0,
                details={"error": str(e)},
                execution_time=time.time() - start
            )
    
    async def _run_token_analysis(self, code1: str, code2: str, language: str) -> DetectionResult:
        """Run token fingerprinting comparison"""
        import time
        start = time.time()
        
        try:
            similarity, details = await self.token_fingerprinter.compare(code1, code2, language)
            return DetectionResult(
                layer_name="Token Fingerprinting",
                similarity_score=similarity,
                confidence=0.90,
                details=details,
                execution_time=time.time() - start
            )
        except Exception as e:
            return DetectionResult(
                layer_name="Token Fingerprinting",
                similarity_score=0.0,
                confidence=0.0,
                details={"error": str(e)},
                execution_time=time.time() - start
            )
    
    async def _run_control_flow_analysis(self, code1: str, code2: str, language: str) -> DetectionResult:
        """Run control flow graph comparison"""
        import time
        start = time.time()
        
        try:
            similarity, details = await self.control_flow_analyzer.compare(code1, code2, language)
            return DetectionResult(
                layer_name="Control Flow Analysis",
                similarity_score=similarity,
                confidence=0.85,
                details=details,
                execution_time=time.time() - start
            )
        except Exception as e:
            return DetectionResult(
                layer_name="Control Flow Analysis",
                similarity_score=0.0,
                confidence=0.0,
                details={"error": str(e)},
                execution_time=time.time() - start
            )
    
    async def _run_ai_detection(self, code: str, language: str) -> DetectionResult:
        """Detect if code is AI-generated"""
        import time
        start = time.time()
        
        try:
            ai_probability, details = await self.ai_detector.analyze(code, language)
            return DetectionResult(
                layer_name="AI Detection",
                similarity_score=ai_probability,
                confidence=0.75,
                details=details,
                execution_time=time.time() - start
            )
        except Exception as e:
            return DetectionResult(
                layer_name="AI Detection",
                similarity_score=0.0,
                confidence=0.0,
                details={"error": str(e)},
                execution_time=time.time() - start
            )
    
    def _calculate_weighted_score(self, results: List[DetectionResult], weights: Dict) -> float:
        """Calculate weighted similarity score"""
        total_score = 0.0
        total_weight = 0.0
        
        LAYER_WEIGHT_MAP = {
            "AI Semantic Analysis": "ai_semantic",
            "AST Analysis": "ast",
            "Token Fingerprinting": "token",
            "Control Flow Analysis": "control_flow",
        }
        
        for result in results:
            if result.layer_name == "AI Detection":
                continue
            
            weight_key = LAYER_WEIGHT_MAP.get(result.layer_name)
            if weight_key is None or weight_key not in weights:
                continue
            
            weight = weights[weight_key]
            effective_weight = weight * result.confidence
            
            total_score += result.similarity_score * effective_weight
            total_weight += effective_weight
            
            print(f"  {result.layer_name}: {result.similarity_score:.2%} × {weight} × {result.confidence} = {result.similarity_score * effective_weight:.4f}")
        
        if total_weight == 0:
            return 0.0
        
        final_score = total_score / total_weight
        print(f"📊 Overall: {total_score:.4f} / {total_weight:.4f} = {final_score:.2%}")
        
        return final_score
    
    def _classify_similarity(self, score: float) -> SimilarityLevel:
        """Classify similarity score into levels"""
        if score < self.THRESHOLDS['clean']:
            return SimilarityLevel.CLEAN
        elif score < self.THRESHOLDS['suspicious']:
            return SimilarityLevel.SUSPICIOUS
        else:
            return SimilarityLevel.HIGH
    
    def _assign_flag_color(self, level: SimilarityLevel) -> FlagColor:
        """Assign visual flag color"""
        mapping = {
            SimilarityLevel.CLEAN: FlagColor.GREEN,
            SimilarityLevel.SUSPICIOUS: FlagColor.YELLOW,
            SimilarityLevel.HIGH: FlagColor.RED
        }
        return mapping[level]
    
    def _generate_recommendations(
        self,
        similarity: float,
        is_ai: bool,
        results: List[DetectionResult],
        is_natural: bool = False,
        ai_reasoning: str = ""
    ) -> List[str]:
        """Generate action recommendations"""
        recommendations = []
        
        # AI override message
        if is_natural and similarity < 0.60:
            recommendations.append(
                f"🤖 AI ANALYSIS: Natural similarity detected. {ai_reasoning}"
            )
        
        if similarity >= 0.80:
            recommendations.append(
                "CRITICAL: Extremely high similarity detected. Manual review strongly recommended."
            )
        elif similarity >= 0.60:
            recommendations.append(
                "WARNING: High similarity detected. Review for potential plagiarism."
            )
        elif similarity >= 0.30:
            if is_natural:
                recommendations.append(
                    "CAUTION: Moderate similarity, but AI detected natural patterns. Likely acceptable."
                )
            else:
                recommendations.append(
                    "CAUTION: Moderate similarity. May be acceptable for similar problems."
                )
        else:
            recommendations.append(
                "PASS: Low similarity. Code appears original."
            )
        
        if is_ai:
            recommendations.append(
                "AI DETECTION: Code shows patterns consistent with AI generation. Verify student understanding."
            )
        
        return recommendations
    
    def _calculate_confidence(self, results: List[DetectionResult]) -> float:
        """Calculate overall confidence"""
        if not results:
            return 0.0
        
        confidences = [r.confidence for r in results if r.confidence > 0]
        if not confidences:
            return 0.0
        
        return sum(confidences) / len(confidences)


class BatchDetector:
    """Utility for comparing multiple submissions (WITHOUT AI to save costs)"""
    
    def __init__(self):
        self.detector = PlagiarismDetector(use_ai=False)  # Disable AI for batch
    
    async def compare_all_pairs(
        self,
        submissions: List[Tuple[str, str, str]],
        progress_callback: Optional[callable] = None
    ) -> List[PlagiarismReport]:
        """Compare all pairs WITHOUT AI semantic analysis"""
        reports = []
        total_pairs = len(submissions) * (len(submissions) - 1) // 2
        completed = 0
        
        for i in range(len(submissions)):
            for j in range(i + 1, len(submissions)):
                sub1_id, code1, lang1 = submissions[i]
                sub2_id, code2, lang2 = submissions[j]
                
                if lang1 != lang2:
                    continue
                
                report = await self.detector.compare_submissions(
                    code1, code2, lang1, sub1_id, sub2_id,
                    use_ai_semantic=False  # Force disable AI
                )
                
                reports.append(report)
                completed += 1
                
                if progress_callback:
                    progress_callback(completed, total_pairs)
        
        return reports