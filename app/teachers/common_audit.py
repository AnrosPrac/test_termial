from datetime import datetime
from app.teachers.teacher_permissions import db, TeacherContext
from app.teachers.teacher_models import AuditLog

async def log_audit(
    teacher: TeacherContext,
    action: str,
    target_type: str,
    target_id: str,
    metadata: dict = None
):
    """
    Log all destructive or important teacher actions for auditability
    
    Args:
        teacher: TeacherContext with user details
        action: Action performed (e.g., 'create_assignment', 'delete_classroom')
        target_type: Resource type (e.g., 'classroom', 'assignment', 'submission')
        target_id: ID of the resource
        metadata: Additional context (optional)
    """
    audit_log = AuditLog(
        actor_user_id=teacher.user_id,
        actor_sidhi_id=teacher.sidhi_id,
        role="teacher",
        action=action,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata or {},
        timestamp=datetime.utcnow()
    )
    
    await db.audit_logs.insert_one(audit_log.dict())

async def get_audit_trail(target_type: str = None, target_id: str = None, limit: int = 100):
    """
    Retrieve audit logs with optional filters
    
    Args:
        target_type: Filter by resource type
        target_id: Filter by specific resource ID
        limit: Max records to return
        
    Returns:
        List of audit log entries
    """
    query = {}
    
    if target_type:
        query["target_type"] = target_type
    
    if target_id:
        query["target_id"] = target_id
    
    cursor = db.audit_logs.find(query).sort("timestamp", -1).limit(limit)
    logs = await cursor.to_list(length=limit)
    
    for log in logs:
        log.pop("_id", None)
    
    return logs