# app/services/session_service.py
"""
Session management service for editor security
"""

from datetime import datetime, timedelta
import jwt
import os
from typing import Optional, Dict, Any
import uuid

from app.db.models import EditorSession, CodeCheckpointLog, SecurityEvent
from app.models.security import (
    SessionStatus,
    EventType,
    EventSeverity,
)


class SessionService:
    """Handle all session-related operations"""
    
    def __init__(self):
        self.jwt_secret = os.getenv("JWT_SECRET", "your-secret-key-change-in-prod")
        self.jwt_algorithm = "HS256"
        self.session_timeout_minutes = 15
    
    def create_session(self, user_id: str, question_id: str, course_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a new editor session
        
        Args:
            user_id: User ID
            question_id: Question ID
            course_id: Optional course ID
        
        Returns:
            Session token and metadata
        """
        session_id = str(uuid.uuid4())
        expires_at = datetime.utcnow() + timedelta(minutes=self.session_timeout_minutes)
        
        # Create JWT token
        payload = {
            'session_id': session_id,
            'user_id': user_id,
            'question_id': question_id,
            'exp': expires_at,
            'type': 'editor_session'
        }
        
        session_token = jwt.encode(payload, self.jwt_secret, algorithm=self.jwt_algorithm)
        
        # Save to database
        session = EditorSession(
            session_id=session_id,
            user_id=user_id,
            question_id=question_id,
            session_token=session_token,
            expires_at=expires_at,
            course_id=course_id
        )
        session.save()
        
        return {
            'session_id': session_id,
            'session_token': session_token,
            'expires_at': expires_at,
            'question_id': question_id
        }
    
    def validate_session(self, session_id: str, session_token: str) -> Optional[EditorSession]:
        """
        Validate and retrieve a session
        
        Args:
            session_id: Session ID
            session_token: JWT token
        
        Returns:
            EditorSession if valid, None otherwise
        """
        try:
            # Validate JWT
            payload = jwt.decode(session_token, self.jwt_secret, algorithms=[self.jwt_algorithm])
            
            # Get session from database
            session = EditorSession.objects(session_id=session_id, session_token=session_token).first()
            
            if not session:
                return None
            
            if not session.is_valid():
                return None
            
            # Update last activity
            session.metadata.last_activity = datetime.utcnow()
            session.save()
            
            return session
            
        except jwt.InvalidTokenError:
            return None
        except Exception as e:
            print(f"Error validating session: {e}")
            return None
    
    def record_security_event(
        self,
        session_id: str,
        user_id: str,
        question_id: str,
        event_type: EventType,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Record a security event
        
        Args:
            session_id: Session ID
            user_id: User ID
            question_id: Question ID
            event_type: Type of security event
            metadata: Event metadata
        
        Returns:
            Event record
        """
        severity = self._calculate_event_severity(event_type, metadata)
        
        event = SecurityEvent(
            session_id=session_id,
            user_id=user_id,
            question_id=question_id,
            event_type=event_type.value,
            severity=severity.value,
            metadata=metadata or {}
        )
        event.save()
        
        # Handle critical events
        if severity == EventSeverity.CRITICAL:
            self._handle_critical_event(session_id)
        
        return {
            'event_id': str(event.id),
            'session_id': session_id,
            'recorded_at': event.timestamp,
            'severity': severity.value
        }
    
    def record_batch_events(
        self,
        session_id: str,
        user_id: str,
        question_id: str,
        events: list
    ) -> Dict[str, Any]:
        """
        Record multiple security events in batch
        
        Args:
            session_id: Session ID
            user_id: User ID
            question_id: Question ID
            events: List of events
        
        Returns:
            Batch recording summary
        """
        critical_count = 0
        event_docs = []
        
        for event_data in events:
            event_type = EventType(event_data['event_type'])
            severity = self._calculate_event_severity(event_type, event_data.get('metadata'))
            
            event = SecurityEvent(
                session_id=session_id,
                user_id=user_id,
                question_id=question_id,
                event_type=event_data['event_type'],
                severity=severity.value,
                metadata=event_data.get('metadata', {})
            )
            event_docs.append(event)
            
            if severity == EventSeverity.CRITICAL:
                critical_count += 1
        
        # Bulk insert
        SecurityEvent.objects.insert(event_docs)
        
        # Update session metadata
        session = EditorSession.objects(session_id=session_id).first()
        if session:
            if critical_count > 0:
                session.lock_session()
            session.metadata.last_activity = datetime.utcnow()
            session.save()
        
        return {
            'total_events': len(events),
            'critical_events': critical_count,
            'action_required': critical_count > 0,
            'action_type': 'LOCK_SESSION' if critical_count > 0 else None,
            'message': f'{critical_count} critical events detected' if critical_count > 0 else 'Events recorded'
        }
    
    def save_code_checkpoint(
        self,
        session_id: str,
        user_id: str,
        question_id: str,
        language: str,
        code: str
    ) -> Dict[str, Any]:
        """
        Save a code checkpoint
        
        Args:
            session_id: Session ID
            user_id: User ID
            question_id: Question ID
            language: Programming language
            code: Source code
        
        Returns:
            Checkpoint record
        """
        # Save to session
        session = EditorSession.objects(session_id=session_id).first()
        if session:
            session.add_checkpoint(language, code)
        
        # Also log to checkpoint history
        checkpoint = CodeCheckpointLog(
            session_id=session_id,
            user_id=user_id,
            question_id=question_id,
            language=language,
            code=code,
            code_hash=self._hash_code(code)
        )
        checkpoint.save()
        
        return {
            'checkpoint_id': str(checkpoint.id),
            'language': language,
            'code_hash': checkpoint.code_hash,
            'created_at': checkpoint.created_at
        }
    
    def get_last_checkpoint(
        self,
        session_id: str,
        language: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get the last checkpoint for a language
        
        Args:
            session_id: Session ID
            language: Programming language
        
        Returns:
            Checkpoint data or None
        """
        session = EditorSession.objects(session_id=session_id).first()
        if not session:
            return None
        
        checkpoint = session.get_checkpoint(language)
        if not checkpoint:
            return None
        
        return {
            'language': checkpoint.language,
            'code': checkpoint.code,
            'code_hash': checkpoint.code_hash,
            'created_at': checkpoint.created_at
        }
    
    def lock_session(self, session_id: str, duration_seconds: int = 30):
        """
        Lock a session due to violations
        
        Args:
            session_id: Session ID
            duration_seconds: Lock duration
        """
        session = EditorSession.objects(session_id=session_id).first()
        if session:
            session.lock_session(duration_seconds)
    
    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get session information
        
        Args:
            session_id: Session ID
        
        Returns:
            Session info or None
        """
        session = EditorSession.objects(session_id=session_id).first()
        if not session:
            return None
        
        return {
            'session_id': session.session_id,
            'user_id': session.user_id,
            'question_id': session.question_id,
            'status': session.status,
            'created_at': session.created_at,
            'last_activity': session.metadata.last_activity,
            'expires_at': session.expires_at,
            'violation_count': session.integrity_checks.violation_count,
            'locked_until': session.locked_until
        }
    
    # ============ Private Methods ============
    
    def _calculate_event_severity(
        self,
        event_type: EventType,
        metadata: Optional[Dict[str, Any]] = None
    ) -> EventSeverity:
        """Calculate severity of an event"""
        critical_events = [
            EventType.SUSPICIOUS_ACTIVITY_DETECTED,
            EventType.CODE_ROLLBACK
        ]
        
        if event_type in critical_events:
            return EventSeverity.CRITICAL
        
        high_severity_events = [
            EventType.PASTE_ATTEMPT,
            EventType.COPY_ATTEMPT,
            EventType.HOTKEY_PASTE_ATTEMPT
        ]
        
        if event_type in high_severity_events:
            if metadata and metadata.get('attempt_count', 0) > 3:
                return EventSeverity.HIGH
            return EventSeverity.MEDIUM
        
        return EventSeverity.LOW
    
    def _handle_critical_event(self, session_id: str):
        """Handle critical security events"""
        session = EditorSession.objects(session_id=session_id).first()
        if session:
            session.lock_session(30)
    
    @staticmethod
    def _hash_code(code: str) -> str:
        """Hash code for integrity checking"""
        import hashlib
        return hashlib.sha256(code.encode()).hexdigest()
