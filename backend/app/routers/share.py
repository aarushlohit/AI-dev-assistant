from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import SharedSnippet
from ..schemas import ShareCreateRequest, ShareCreateResponse, ShareRecord

router = APIRouter(tags=["Share"])

SHARE_TTL = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(UTC)


def _to_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _cleanup_expired_shares(db: Session) -> None:
    cutoff = _now() - SHARE_TTL
    db.execute(delete(SharedSnippet).where(SharedSnippet.created_at < cutoff))
    db.commit()


def _is_expired(record: SharedSnippet) -> bool:
    return _to_utc(record.created_at) < _now() - SHARE_TTL


def _serialize_result(result: object) -> str:
    return json.dumps(result, ensure_ascii=False)


def _deserialize_result(result_json: str) -> object:
    try:
        return json.loads(result_json)
    except json.JSONDecodeError:
        return result_json


@router.post("/", response_model=ShareCreateResponse)
def create_share(payload: ShareCreateRequest, db: Session = Depends(get_db)):
    _cleanup_expired_shares(db)

    token = ""
    for _ in range(5):
        candidate = secrets.token_urlsafe(6)
        exists = db.execute(select(SharedSnippet).where(SharedSnippet.token == candidate)).scalar_one_or_none()
        if exists is None:
            token = candidate
            break

    if not token:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not create share token")

    record = SharedSnippet(
        token=token,
        code=payload.code,
        result_json=_serialize_result(payload.result),
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return ShareCreateResponse(id=record.token)


@router.get("/{token}", response_model=ShareRecord)
def get_share(token: str, db: Session = Depends(get_db)):
    record = db.execute(select(SharedSnippet).where(SharedSnippet.token == token)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared result not found")

    if _is_expired(record):
        db.execute(delete(SharedSnippet).where(SharedSnippet.token == token))
        db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared result has expired")

    return ShareRecord(
        id=record.token,
        code=record.code,
        result=_deserialize_result(record.result_json),
        created_at=record.created_at.isoformat(),
    )
