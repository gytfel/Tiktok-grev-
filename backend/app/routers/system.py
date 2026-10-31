import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app import monitor
from app.config import get_settings
from app.db import get_db
from app.deps import get_probes, require_writer
from app.models import User, utcnow

router = APIRouter(tags=["system"])


@router.get("/api/system/status")
def status(
    db: Session = Depends(get_db),
    probes: monitor.Probes = Depends(get_probes),
    _: User = Depends(require_writer),
) -> dict:
    now = utcnow()
    settings = get_settings()
    return {
        **monitor.collect(db, probes, now).as_dict(now),
        "monitor_interval_s": settings.monitor_interval_s,
        "metrics_enabled": bool(settings.metrics_token),
    }


@router.get("/api/metrics", response_class=PlainTextResponse)
def metrics(
    request: Request, db: Session = Depends(get_db), probes: monitor.Probes = Depends(get_probes)
) -> PlainTextResponse:
    token = get_settings().metrics_token
    if not token:
        raise HTTPException(404)
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(401, "Нужен токен METRICS_TOKEN", headers={"WWW-Authenticate": "Bearer"})
    report = monitor.collect(db, probes, utcnow())
    return PlainTextResponse(monitor.prometheus(report), media_type="text/plain; version=0.0.4")
