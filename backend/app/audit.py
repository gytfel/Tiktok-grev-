from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent, User


def record(
    db: Session,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            actor_name=actor.username if actor else "system",
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            details=details or {},
        )
    )
