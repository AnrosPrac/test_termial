"""
Plagiarism Detection System - Main Orchestrator
Combines multiple detection layers for robust code similarity analysis

Supports: C, C++, Python
Detection Layers: AST, Token Fingerprinting, Control Flow, AI Detection
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


class PlagiarismDetector:
    """
    Main plagiarism detection orchestrator
    Combines multiple detection layers with weighted scoring
    """
    
    # Layer weights (must sum to 1.0)
    WEIGHTS = {
        'ast': 0.35,           # Abstract Syntax Tree
        'token': 0.25,         # Token Fingerprinting
        'control_flow': 0.25,  # Control Flow Graph
        'ai_detection': 0.15   # AI-generated code detection
    }
    
    # Similarity thresholds
    THRESHOLDS = {
        'clean': 0.30,
        'suspicious': 0.60
    }
    
    def __init__(self):
        """Initialize all detection layers"""
        # Import detection modules (will create these next)
        from app.plagiarism.ast_analyzer import ASTAnalyzer
        from app.plagiarism.token_fingerprinter import TokenFingerprinter
        from app.plagiarism.control_flow import ControlFlowAnalyzer
        from app.plagiarism.ai_detector import AIDetector
        
        self.ast_analyzer = ASTAnalyzer()
        self.token_fingerprinter = TokenFingerprinter()
        self.control_flow_analyzer = ControlFlowAnalyzer()
        self.ai_detector = AIDetector()
    
    async def compare_submissions(
        self,
        code1: str,
        code2: str,
        language: str,
        submission1_id: str,
        submission2_id: str
    ) -> PlagiarismReport:
        """
        Compare two code submissions using all detection layers
        
        Args:
            code1: First code submission
            code2: Second code submission
            language: Programming language (c, cpp, python)
            submission1_id: ID of first submission
            submission2_id: ID of second submission
        
        Returns:
            Complete plagiarism report
        """
        import time
        start_time = time.time()
        
        # Validate language
        if language.lower() not in ['c', 'cpp', 'python']:
            raise ValueError(f"Unsupported language: {language}")
        
        # Run all detection layers in parallel
        layer_results = await asyncio.gather(
            self._run_ast_analysis(code1, code2, language),
            self._run_token_analysis(code1, code2, language),
            self._run_control_flow_analysis(code1, code2, language),
            return_exceptions=True
        )
        
        # Run AI detection on both submissions
        ai_result1 = await self._run_ai_detection(code1, language)
        ai_result2 = await self._run_ai_detection(code2, language)
        
        # Filter out any exceptions
        valid_results = [r for r in layer_results if not isinstance(r, Exception)]
        
        # Calculate weighted similarity score
        overall_similarity = self._calculate_weighted_score(valid_results)
        
        # Determine similarity level and flag color
        similarity_level = self._classify_similarity(overall_similarity)
        flag_color = self._assign_flag_color(similarity_level)
        
        # Check if either submission is likely AI-generated
        is_likely_ai = ai_result1.similarity_score > 0.7 or ai_result2.similarity_score > 0.7
        ai_probability = max(ai_result1.similarity_score, ai_result2.similarity_score)
        
        # Add AI detection to results
        valid_results.append(ai_result1)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            overall_similarity,
            is_likely_ai,
            valid_results
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
            processing_time=processing_time
        )
    
    async def _run_ast_analysis(
        self,
        code1: str,
        code2: str,
        language: str
    ) -> DetectionResult:
        """Run Abstract Syntax Tree comparison"""
        import time
        start = time.time()
        
        try:
            similarity, details = await self.ast_analyzer.compare(
                code1, code2, language
            )
            
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
    
    async def _run_token_analysis(
        self,
        code1: str,
        code2: str,
        language: str
    ) -> DetectionResult:
        """Run token fingerprinting comparison"""
        import time
        start = time.time()
        
        try:
            similarity, details = await self.token_fingerprinter.compare(
                code1, code2, language
            )
            
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
    
    async def _run_control_flow_analysis(
        self,
        code1: str,
        code2: str,
        language: str
    ) -> DetectionResult:
        """Run control flow graph comparison"""
        import time
        start = time.time()
        
        try:
            similarity, details = await self.control_flow_analyzer.compare(
                code1, code2, language
            )
            
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
    
    async def _run_ai_detection(
        self,
        code: str,
        language: str
    ) -> DetectionResult:
        """Detect if code is AI-generated"""
        import time
        start = time.time()
        
        try:
            ai_probability, details = await self.ai_detector.analyze(
                code, language
            )
            
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
    
    def _calculate_weighted_score(self, results: List[DetectionResult]) -> float:
        """
        Calculate weighted similarity score from layer results
        
        FIXED: Proper layer name to weight mapping
        """
        total_score = 0.0
        total_weight = 0.0
        
        # Map layer names to weight keys
        LAYER_WEIGHT_MAP = {
            "AST Analysis": "ast",
            "Token Fingerprinting": "token",
            "Control Flow Analysis": "control_flow",
            "AI Detection": "ai_detection"
        }
        
        for result in results:
            # Skip AI detection for overall similarity (it's tracked separately)
            if result.layer_name == "AI Detection":
                continue
            
            # Get weight using proper mapping
            weight_key = LAYER_WEIGHT_MAP.get(result.layer_name)
            
            if weight_key is None:
                print(f"⚠️ WARNING: Unknown layer '{result.layer_name}', skipping")
                continue
            
            weight = self.WEIGHTS.get(weight_key, 0.0)
            
            if weight == 0.0:
                print(f"⚠️ WARNING: Layer '{result.layer_name}' has 0 weight")
                continue
            
            # Weight by confidence
            effective_weight = weight * result.confidence
            
            total_score += result.similarity_score * effective_weight
            total_weight += effective_weight
            
            # Debug logging (remove in production)
            print(f"  {result.layer_name}: {result.similarity_score:.2%} × {weight} × {result.confidence} = {result.similarity_score * effective_weight:.4f}")
        
        if total_weight == 0:
            print("⚠️ ERROR: Total weight is 0!")
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
        """Assign visual flag color based on similarity level"""
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
        results: List[DetectionResult]
    ) -> List[str]:
        """Generate action recommendations based on analysis"""
        recommendations = []
        
        if similarity >= 0.80:
            recommendations.append(
                "CRITICAL: Extremely high similarity detected. "
                "Manual review strongly recommended."
            )
        elif similarity >= 0.60:
            recommendations.append(
                "WARNING: High similarity detected. Review for potential plagiarism."
            )
        elif similarity >= 0.30:
            recommendations.append(
                "CAUTION: Moderate similarity. May be acceptable for similar problems."
            )
        else:
            recommendations.append(
                "PASS: Low similarity. Code appears original."
            )
        
        if is_ai:
            recommendations.append(
                "AI DETECTION: Code shows patterns consistent with AI generation. "
                "Verify student understanding."
            )
        
        # Check for specific patterns
        for result in results:
            if result.layer_name == "AST Analysis" and result.similarity_score > 0.9:
                recommendations.append(
                    "AST: Nearly identical program structure detected."
                )
            
            if result.layer_name == "Token Fingerprinting" and result.similarity_score > 0.9:
                recommendations.append(
                    "TOKEN: Code appears to be copied with minimal changes."
                )
        
        return recommendations
    
    def _calculate_confidence(self, results: List[DetectionResult]) -> float:
        """Calculate overall confidence in the plagiarism detection"""
        if not results:
            return 0.0
        
        # Average confidence across all layers
        confidences = [r.confidence for r in results if r.confidence > 0]
        
        if not confidences:
            return 0.0
        
        return sum(confidences) / len(confidences)


# Batch comparison utilities
class BatchDetector:
    """Utility for comparing multiple submissions efficiently"""
    
    def __init__(self):
        self.detector = PlagiarismDetector()
    
    async def compare_all_pairs(
        self,
        submissions: List[Tuple[str, str, str]],  # (id, code, language)
        progress_callback: Optional[callable] = None
    ) -> List[PlagiarismReport]:
        """
        Compare all pairs of submissions
        
        Args:
            submissions: List of (submission_id, code, language) tuples
            progress_callback: Optional callback for progress updates
        
        Returns:
            List of plagiarism reports for all pairs
        """
        reports = []
        total_pairs = len(submissions) * (len(submissions) - 1) // 2
        completed = 0
        
        for i in range(len(submissions)):
            for j in range(i + 1, len(submissions)):
                sub1_id, code1, lang1 = submissions[i]
                sub2_id, code2, lang2 = submissions[j]
                
                # Skip if different languages
                if lang1 != lang2:
                    continue
                
                report = await self.detector.compare_submissions(
                    code1, code2, lang1, sub1_id, sub2_id
                )
                
                reports.append(report)
                completed += 1
                
                if progress_callback:
                    progress_callback(completed, total_pairs)
        
        return reports
    
    def filter_flagged_reports(
        self,
        reports: List[PlagiarismReport],
        min_similarity: float = 0.60
    ) -> List[PlagiarismReport]:
        """Filter reports to only high-similarity cases"""
        return [
            r for r in reports
            if r.overall_similarity >= min_similarity
        ]
