# app/services/integrity_service.py
"""
Code integrity analysis service
"""

from typing import Dict, Any, Optional
from app.editor_security.app_db_models import SubmissionIntegrity, CodeCheckpointLog
from app.editor_security.app_models_security import IntegrityStatus


class IntegrityAnalyzerService:
    """Analyze code submission integrity"""
    
    # Scoring weights
    PASTE_POINTS = 20
    COPY_POINTS = 10
    CUT_POINTS = 5
    VIOLATION_POINTS = 15
    
    SUSPICIOUS_THRESHOLD = 40
    COMPROMISED_THRESHOLD = 70
    
    def analyze_submission(
        self,
        session_id: str,
        user_id: str,
        question_id: str,
        code: str,
        language: str,
        metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Analyze code submission for integrity
        
        Args:
            session_id: Session ID
            user_id: User ID
            question_id: Question ID
            code: Source code
            language: Programming language
            metadata: Behavioral metrics from frontend
        
        Returns:
            Integrity analysis result
        """
        suspicion_score = 0
        score_breakdown = {}
        
        # ============ Analysis Factors ============
        
        # Factor 1: Paste attempts (MAJOR RED FLAG)
        paste_attempts = metadata.get('pasteAttempts', 0)
        if paste_attempts > 0:
            paste_score = min(paste_attempts * self.PASTE_POINTS, 60)
            suspicion_score += paste_score
            score_breakdown['paste_attempts'] = paste_score
        
        # Factor 2: Copy attempts
        copy_attempts = metadata.get('copyAttempts', 0)
        if copy_attempts > 0:
            copy_score = min(copy_attempts * self.COPY_POINTS, 30)
            suspicion_score += copy_score
            score_breakdown['copy_attempts'] = copy_score
        
        # Factor 3: Cut attempts
        cut_attempts = metadata.get('cutAttempts', 0)
        if cut_attempts > 0:
            cut_score = cut_attempts * self.CUT_POINTS
            suspicion_score += cut_score
            score_breakdown['cut_attempts'] = cut_score
        
        # Factor 4: Previous violations
        violation_count = metadata.get('violationCount', 0)
        if violation_count > 0:
            violation_score = violation_count * self.VIOLATION_POINTS
            suspicion_score += violation_score
            score_breakdown['violations'] = violation_score
        
        # Factor 5: Edit time vs code length (keystroke analysis)
        code_length = len(code)
        edit_time_ms = metadata.get('editTime', 1)
        edit_time_seconds = max(edit_time_ms / 1000, 1)
        
        chars_per_second = code_length / edit_time_seconds
        
        if chars_per_second > 100:
            # More than 100 chars/second = suspicious
            speed_score = min((chars_per_second - 100) / 10, 30)
            suspicion_score += speed_score
            score_breakdown['edit_speed'] = speed_score
        
        # ============ Determine Status ============
        
        if suspicion_score >= self.COMPROMISED_THRESHOLD:
            status = IntegrityStatus.COMPROMISED
        elif suspicion_score >= self.SUSPICIOUS_THRESHOLD:
            status = IntegrityStatus.SUSPICIOUS
        else:
            status = IntegrityStatus.CLEAN
        
        # Cap score at 100
        suspicion_score = min(int(suspicion_score), 100)
        
        # Get previous checkpoint for potential rollback
        previous_checkpoint = CodeCheckpointLog.objects(
            session_id=session_id,
            language=language
        ).order_by('-created_at').first()
        
        previous_code = previous_checkpoint.code if previous_checkpoint else None
        rollback_reason = None
        should_rollback = False
        
        if status == IntegrityStatus.COMPROMISED:
            should_rollback = True
            rollback_reason = self._generate_rollback_reason(metadata)
        
        # Create analysis record
        analysis = SubmissionIntegrity(
            session_id=session_id,
            user_id=user_id,
            question_id=question_id,
            integrity_status=status.value,
            suspicion_score=suspicion_score,
            paste_attempts=paste_attempts,
            copy_attempts=copy_attempts,
            cut_attempts=cut_attempts,
            violation_count=violation_count,
            edit_time_ms=edit_time_ms,
            score_breakdown=score_breakdown,
            flagged_for_review=status != IntegrityStatus.CLEAN,
            analysis_details={
                'chars_per_second': round(chars_per_second, 2),
                'factors_analyzed': [
                    'paste_attempts',
                    'copy_attempts',
                    'cut_attempts',
                    'violations',
                    'edit_speed'
                ]
            }
        )
        analysis.save()
        
        return {
            'status': status.value,
            'suspicion_score': suspicion_score,
            'paste_attempts': paste_attempts,
            'copy_attempts': copy_attempts,
            'cut_attempts': cut_attempts,
            'violation_count': violation_count,
            'edit_time_ms': edit_time_ms,
            'should_rollback': should_rollback,
            'rollback_reason': rollback_reason,
            'previous_code': previous_code,
            'analysis_id': str(analysis.id)
        }
    
    def get_submission_analysis(self, submission_id: str) -> Optional[Dict[str, Any]]:
        """
        Get integrity analysis for a submission
        
        Args:
            submission_id: Submission ID
        
        Returns:
            Analysis record or None
        """
        analysis = SubmissionIntegrity.objects(submission_id=submission_id).first()
        if not analysis:
            return None
        
        return {
            'submission_id': submission_id,
            'integrity_status': analysis.integrity_status,
            'suspicion_score': analysis.suspicion_score,
            'paste_attempts': analysis.paste_attempts,
            'copy_attempts': analysis.copy_attempts,
            'cut_attempts': analysis.cut_attempts,
            'flagged_for_review': analysis.flagged_for_review,
            'reviewed_at': analysis.reviewed_at,
            'reviewer_notes': analysis.reviewer_notes,
            'analysis_details': analysis.analysis_details
        }
    
    def get_flagged_submissions(
        self,
        user_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Get flagged submissions for review
        
        Args:
            user_id: Filter by user (optional)
            skip: Number of records to skip
            limit: Max records to return
        
        Returns:
            Flagged submissions
        """
        query = SubmissionIntegrity.objects(flagged_for_review=True)
        
        if user_id:
            query = query(user_id=user_id)
        
        total = query.count()
        
        submissions = query.skip(skip).limit(limit).order_by('-created_at')
        
        return {
            'total': total,
            'skip': skip,
            'limit': limit,
            'submissions': [
                {
                    'submission_id': str(s.submission_id),
                    'user_id': s.user_id,
                    'integrity_status': s.integrity_status,
                    'suspicion_score': s.suspicion_score,
                    'created_at': s.created_at,
                    'reviewed': s.reviewed_at is not None
                }
                for s in submissions
            ]
        }
    
    # ============ Private Methods ============
    
    def _generate_rollback_reason(self, metadata: Dict[str, Any]) -> str:
        """Generate human-readable rollback reason"""
        reasons = []
        
        if metadata.get('pasteAttempts', 0) > 0:
            reasons.append(f"{metadata['pasteAttempts']} paste attempt(s)")
        
        if metadata.get('copyAttempts', 0) > 0:
            reasons.append(f"{metadata['copyAttempts']} copy attempt(s)")
        
        if metadata.get('violationCount', 0) > 0:
            reasons.append(f"{metadata['violationCount']} previous violation(s)")
        
        edit_time = metadata.get('editTime', 0)
        code_length = metadata.get('codeLength', 0)
        if edit_time > 0 and code_length > 0:
            chars_per_sec = (code_length / edit_time) * 1000
            if chars_per_sec > 100:
                reasons.append(f"Unnatural keystroke rate ({chars_per_sec:.0f} chars/sec)")
        
        return "Suspicious activity: " + ", ".join(reasons) if reasons else "Suspicious activity detected"
