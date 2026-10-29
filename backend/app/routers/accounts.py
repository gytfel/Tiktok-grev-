import csv
import io
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.accounts_service import check_account, disconnect_account
from app.db import get_db
from app.deps import current_user, get_account_check_enqueuer, get_tiktok, require_writer
from app.models import Account, AccountGroup, JobStatus, PublicationJob, User, utcnow
from app.schemas import (
    AccountBulkAction,
    AccountCreate,
    AccountOut,
    AccountUpdate,
    GroupIn,
    GroupOut,
    ImportResult,
)
from app.tiktok import TikTokClient

router = APIRouter(prefix="/api", tags=["accounts"])


def _get_account(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Аккаунт не найден")
    return account


def _get_group(db: Session, group_id: int | None) -> AccountGroup:
    group = db.get(AccountGroup, group_id) if group_id else None
    if group is None:
        raise HTTPException(404, "Группа не найдена")
    return group


def _has_pending_jobs(db: Session, account_ids: list[int]) -> bool:
    return bool(
        db.scalar(
            select(func.count())
            .select_from(PublicationJob)
            .where(
                PublicationJob.account_id.in_(account_ids),
                PublicationJob.status.in_(JobStatus.PENDING),
            )
        )
    )


# --- группы -------------------------------------------------------------------------------


@router.get("/groups", response_model=list[GroupOut])
def list_groups(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return list(db.scalars(select(AccountGroup).order_by(AccountGroup.name)))


@router.post("/groups", response_model=GroupOut)
def create_group(data: GroupIn, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    name = data.name.strip()
    if db.execute(select(AccountGroup).where(AccountGroup.name == name)).scalar_one_or_none():
        raise HTTPException(409, "Группа с таким названием уже есть")
    group = AccountGroup(name=name)
    db.add(group)
    db.flush()
    audit.record(db, user, "group.created", "group", group.id, {"name": name})
    db.commit()
    return group


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    group = _get_group(db, group_id)
    audit.record(db, user, "group.deleted", "group", group.id, {"name": group.name})
    db.delete(group)
    db.commit()
    return {"ok": True}


# --- аккаунты -----------------------------------------------------------------------------


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return list(db.scalars(select(Account).order_by(Account.id)))


@router.post("/accounts", response_model=AccountOut)
def create_account(data: AccountCreate, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    account = Account(label=data.label.strip(), notes=data.notes)
    if data.group_ids:
        account.groups = list(db.scalars(select(AccountGroup).where(AccountGroup.id.in_(data.group_ids))))
    db.add(account)
    db.flush()
    audit.record(db, user, "account.created", "account", account.id, {"label": account.label})
    db.commit()
    return account


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(
    account_id: int,
    data: AccountUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_writer),
):
    account = _get_account(db, account_id)
    changes = data.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(account, key, value.strip() if key == "label" else value)
    audit.record(db, user, "account.updated", "account", account.id, {"fields": sorted(changes)})
    db.commit()
    return account


@router.post("/accounts/{account_id}/check", response_model=AccountOut)
def check_one(
    account_id: int,
    db: Session = Depends(get_db),
    tt: TikTokClient = Depends(get_tiktok),
    _: User = Depends(require_writer),
):
    account = _get_account(db, account_id)
    check_account(db, account, tt, utcnow())
    db.refresh(account)
    return account


@router.post("/accounts/{account_id}/disconnect", response_model=AccountOut)
def disconnect(
    account_id: int,
    db: Session = Depends(get_db),
    tt: TikTokClient = Depends(get_tiktok),
    user: User = Depends(require_writer),
):
    account = _get_account(db, account_id)
    if _has_pending_jobs(db, [account.id]):
        raise HTTPException(409, "У аккаунта есть незавершённые публикации")
    disconnect_account(db, account, tt)
    audit.record(db, user, "account.disconnected", "account", account.id)
    db.commit()
    return account


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: int,
    db: Session = Depends(get_db),
    tt: TikTokClient = Depends(get_tiktok),
    user: User = Depends(require_writer),
):
    account = _get_account(db, account_id)
    if _has_pending_jobs(db, [account.id]):
        raise HTTPException(409, "У аккаунта есть незавершённые публикации")
    disconnect_account(db, account, tt)
    audit.record(db, user, "account.deleted", "account", account.id, {"label": account.label})
    db.delete(account)
    db.commit()
    return {"ok": True}


@router.post("/accounts/bulk")
def bulk_action(
    data: AccountBulkAction,
    db: Session = Depends(get_db),
    tt: TikTokClient = Depends(get_tiktok),
    enqueue: Callable[[list[int]], None] = Depends(get_account_check_enqueuer),
    user: User = Depends(require_writer),
):
    accounts = list(db.scalars(select(Account).where(Account.id.in_(data.account_ids))))
    ids = [a.id for a in accounts]
    if data.action in ("add_to_group", "remove_from_group"):
        group = _get_group(db, data.group_id)
        for account in accounts:
            if data.action == "add_to_group" and group not in account.groups:
                account.groups.append(group)
            elif data.action == "remove_from_group" and group in account.groups:
                account.groups.remove(group)
        audit.record(db, user, f"accounts.{data.action}", "group", group.id, {"account_ids": ids})
    elif data.action == "check":
        enqueue(ids)
    elif data.action == "delete":
        if _has_pending_jobs(db, ids):
            raise HTTPException(409, "У части аккаунтов есть незавершённые публикации")
        for account in accounts:
            disconnect_account(db, account, tt)
            db.delete(account)
        audit.record(db, user, "accounts.deleted", "account", None, {"account_ids": ids})
    db.commit()
    return {"ok": True, "affected": len(ids)}


def _decode_csv(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(422, "Не удалось прочитать файл: сохраните CSV в UTF-8")


@router.post("/accounts/import", response_model=ImportResult)
def import_csv(file: UploadFile, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    """CSV с колонками label (обязательно), groups (через |), notes."""
    text = _decode_csv(file.file.read(5 * 1024 * 1024 + 1)[: 5 * 1024 * 1024])
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else "", delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    fields = {(f or "").strip().lower() for f in reader.fieldnames or []}
    if "label" not in fields:
        raise HTTPException(422, "В файле нет колонки label")

    existing_labels = set(db.scalars(select(Account.label)))
    groups = {g.name: g for g in db.scalars(select(AccountGroup))}
    created, skipped, errors = 0, 0, []
    for line_no, row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        label = row.get("label", "")
        if not label:
            errors.append(f"Строка {line_no}: пустой label")
            continue
        if len(label) > 128:
            errors.append(f"Строка {line_no}: label длиннее 128 символов")
            continue
        if label in existing_labels:
            skipped += 1
            continue
        account = Account(label=label, notes=row.get("notes", ""))
        for name in filter(None, (g.strip() for g in row.get("groups", "").split("|"))):
            if name not in groups:
                groups[name] = AccountGroup(name=name[:100])
                db.add(groups[name])
            account.groups.append(groups[name])
        db.add(account)
        existing_labels.add(label)
        created += 1
    audit.record(db, user, "accounts.imported", "account", None, {"created": created, "skipped": skipped})
    db.commit()
    return ImportResult(created=created, skipped=skipped, errors=errors[:50])
