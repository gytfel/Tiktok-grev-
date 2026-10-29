from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.db import get_db
from app.deps import current_user, require_writer
from app.media import UploadTooLarge, extension_of, media_path, probe, store_upload, validate
from app.models import Campaign, MediaAsset, User
from app.schemas import MediaOut
from app.tiktok.client import CONTENT_TYPES

router = APIRouter(prefix="/api/media", tags=["media"])


def _to_out(media: MediaAsset, campaign_count: int) -> MediaOut:
    out = MediaOut.model_validate(media)
    out.campaign_count = campaign_count
    return out


def _campaign_count(db: Session, media_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Campaign).where(Campaign.media_asset_id == media_id))


@router.get("", response_model=list[MediaOut])
def list_media(db: Session = Depends(get_db), _: User = Depends(current_user)):
    counts = dict(
        db.execute(select(Campaign.media_asset_id, func.count()).group_by(Campaign.media_asset_id)).all()
    )
    items = db.scalars(select(MediaAsset).order_by(MediaAsset.id.desc()))
    return [_to_out(m, counts.get(m.id, 0)) for m in items]


@router.post("", response_model=MediaOut)
def upload_media(file: UploadFile, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    settings = get_settings()
    filename = file.filename or "video"
    ext = extension_of(filename)
    if ext not in CONTENT_TYPES:
        raise HTTPException(422, "Поддерживаются только MP4, MOV и WebM")
    try:
        storage_key, size, sha256 = store_upload(
            file.file, settings.media_dir, ext, settings.max_upload_bytes
        )
    except UploadTooLarge:
        raise HTTPException(413, "Файл слишком большой") from None
    if size == 0:
        media_path(settings.media_dir, storage_key).unlink(missing_ok=True)
        raise HTTPException(422, "Пустой файл")
    info = probe(media_path(settings.media_dir, storage_key))
    issues = validate(ext, size, info)
    media = MediaAsset(
        original_filename=filename[:255],
        storage_key=storage_key,
        content_type=CONTENT_TYPES[ext],
        sha256=sha256,
        size_bytes=size,
        duration_ms=info.duration_ms if info else None,
        width=info.width if info else None,
        height=info.height if info else None,
        fps=info.fps if info else None,
        video_codec=info.video_codec if info else None,
        validation_status="invalid" if issues else "valid",
        validation_issues=issues,
        uploaded_by=user.id,
    )
    db.add(media)
    db.flush()
    audit.record(
        db,
        user,
        "media.uploaded",
        "media",
        media.id,
        {"filename": media.original_filename, "sha256": sha256, "valid": not issues},
    )
    db.commit()
    return _to_out(media, 0)


@router.get("/{media_id}/file")
def media_file(media_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    media = db.get(MediaAsset, media_id)
    if media is None:
        raise HTTPException(404, "Видео не найдено")
    path = media_path(get_settings().media_dir, media.storage_key)
    if not path.exists():
        raise HTTPException(404, "Файл отсутствует в хранилище")
    return FileResponse(path, media_type=media.content_type)


@router.delete("/{media_id}")
def delete_media(media_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    media = db.get(MediaAsset, media_id)
    if media is None:
        raise HTTPException(404, "Видео не найдено")
    if _campaign_count(db, media.id):
        raise HTTPException(409, "Видео используется в кампаниях")
    audit.record(db, user, "media.deleted", "media", media.id, {"filename": media.original_filename})
    db.delete(media)
    db.commit()
    media_path(get_settings().media_dir, media.storage_key).unlink(missing_ok=True)
    return {"ok": True}
