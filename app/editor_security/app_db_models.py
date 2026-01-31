# app/db/models.py
"""
MongoDB document models using mongoengine
"""

from mongoengine import (
    Document,
    StringField,
    DateTimeField,
    IntField,
    BooleanField,
    DictField,
    ListField,
    EmbeddedDocument,
    EmbeddedDocumentField,
    ReferenceField,
    PULL,
)
from datetime import datetime, timedelta
import hashlib


class SessionMetadata(EmbeddedDocument):
    """Metadata for a session"""
    start_time = DateTimeField(default=datetime.utcnow)
    last_activity = DateTimeField(default=datetime.utcnow)
    tab_switch_count = IntField(default=0)
    focus_loss_count = IntField(default=0)


class IntegrityChecks(EmbeddedDocument):
    """Integrity check records"""
    paste_attempts = IntField(default=0)
    copy_attempts = IntField(default=0)
    cut_attempts = IntField(default=0)
    suspicious_activity = BooleanField(default=False)
    violation_count = IntField(default=0)
    locked_until = DateTimeField(null=True)


class CodeCheckpoint(EmbeddedDocument):
    """Saved code checkpoint"""
    language = StringField(required=True)
    code = StringField(required=True)
    code_hash = StringField(required=True)
    created_at = DateTimeField(default=datetime.utcnow)


class EditorSession(Document):
    """Editor session document"""
    session_id = StringField(required=True, unique=True)
    user_id = StringField(required=True)
    question_id = StringField(required=True)
    session_token = StringField(required=True, unique=True)
    
    status = StringField(choices=['ACTIVE', 'LOCKED', 'EXPIRED', 'COMPLETED'], default='ACTIVE')
    
    created_at = DateTimeField(default=datetime.utcnow)
    expires_at = DateTimeField(required=True)
    locked_until = DateTimeField(null=True)
    
    metadata = EmbeddedDocumentField(SessionMetadata, default=SessionMetadata)
    integrity_checks = EmbeddedDocumentField(IntegrityChecks, default=IntegrityChecks)
    
    # Code checkpoints per language
    checkpoints = ListField(EmbeddedDocumentField(CodeCheckpoint), default=[])
    
    course_id = StringField(null=True)
    
    meta = {
        'collection': 'editor_sessions',
        'indexes': [
            'session_id',
            'user_id',
            'question_id',
            'session_token',
            'status',
            'expires_at',
        ]
    }
    
    def is_valid(self) -> bool:
        """Check if session is still valid"""
        if self.status == 'EXPIRED':
            return False
        if self.status == 'LOCKED':
            return False
        if datetime.utcnow() > self.expires_at:
            self.status = 'EXPIRED'
            self.save()
            return False
        return True
    
    def lock_session(self, duration_seconds: int = 30):
        """Lock session for specified duration"""
        self.locked_until = datetime.utcnow() + timedelta(seconds=duration_seconds)
        self.integrity_checks.locked_until = self.locked_until
        self.integrity_checks.violation_count += 1
        
        if self.integrity_checks.violation_count >= 3:
            self.status = 'LOCKED'
        
        self.save()
    
    def add_checkpoint(self, language: str, code: str):
        """Add or update code checkpoint for language"""
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        
        # Remove existing checkpoint for this language
        self.checkpoints = [cp for cp in self.checkpoints if cp.language != language]
        
        # Add new checkpoint
        checkpoint = CodeCheckpoint(
            language=language,
            code=code,
            code_hash=code_hash
        )
        self.checkpoints.append(checkpoint)
        self.save()
    
    def get_checkpoint(self, language: str) -> CodeCheckpoint:
        """Get checkpoint for specific language"""
        for cp in self.checkpoints:
            if cp.language == language:
                return cp
        return None


class SecurityEvent(Document):
    """Security event log"""
    session_id = StringField(required=True)
    user_id = StringField(required=True)
    question_id = StringField(required=True)
    
    event_type = StringField(required=True)
    severity = StringField(
        choices=['low', 'medium', 'high', 'critical'],
        default='low'
    )
    
    timestamp = DateTimeField(default=datetime.utcnow)
    metadata = DictField(default={})
    
    resolved = BooleanField(default=False)
    
    meta = {
        'collection': 'security_events',
        'indexes': [
            'session_id',
            'user_id',
            'event_type',
            'severity',
            'timestamp',
            ('user_id', 'timestamp'),
            ('session_id', 'timestamp'),
        ]
    }


class CodeCheckpointLog(Document):
    """Log of all code checkpoints"""
    session_id = StringField(required=True)
    user_id = StringField(required=True)
    question_id = StringField(required=True)
    
    language = StringField(required=True)
    code = StringField(required=True)
    code_hash = StringField(required=True)
    
    created_at = DateTimeField(default=datetime.utcnow)
    
    meta = {
        'collection': 'code_checkpoint_logs',
        'indexes': [
            'session_id',
            'user_id',
            ('user_id', 'question_id'),
        ]
    }


class SubmissionIntegrity(Document):
    """Integrity analysis for each submission"""
    submission_id = StringField(required=True, unique=True)
    session_id = StringField(required=True)
    user_id = StringField(required=True)
    question_id = StringField(required=True)
    
    # Integrity status
    integrity_status = StringField(
        choices=['CLEAN', 'SUSPICIOUS', 'COMPROMISED'],
        default='CLEAN'
    )
    suspicion_score = IntField(default=0)  # 0-100
    
    # Behavioral metrics
    paste_attempts = IntField(default=0)
    copy_attempts = IntField(default=0)
    cut_attempts = IntField(default=0)
    keystroke_anomalies = IntField(default=0)
    
    violation_count = IntField(default=0)
    edit_time_ms = IntField(default=0)
    
    # Analysis details
    analysis_details = DictField(default={})
    score_breakdown = DictField(default={})
    
    # Flags
    flagged_for_review = BooleanField(default=False)
    reviewer_notes = StringField(null=True)
    reviewed_at = DateTimeField(null=True)
    
    created_at = DateTimeField(default=datetime.utcnow)
    
    meta = {
        'collection': 'submission_integrity',
        'indexes': [
            'submission_id',
            'session_id',
            'user_id',
            'integrity_status',
            'flagged_for_review',
            'created_at',
        ]
    }
