"""[5] API — 화면 '예측 및 경보' > '경보발령 선택' 상자 뒤에서 일하는 부분

  GET    /api/contacts                 입주자 연락처 목록 (호실별)
  POST   /api/contacts                 연락처 추가   {channel_id, name, email, phone, memo}
  PUT    /api/contacts/{contact_id}    연락처 수정
  DELETE /api/contacts/{contact_id}    연락처 삭제
  GET    /api/alerts/template          기본 안내문구
  POST   /api/alerts                   경보 발령 (대상 호실·안내문구) → 경보 발령 기록 장부에 저장, 메일 서버 설정 시 메일 발송
  GET    /api/alerts                   최근 발령 기록

메일 발송: .env 에 SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM 을 넣었을 때만 실제로 보냅니다.
설정이 없으면 '기록만' 남깁니다. 문자(SMS)는 업체 연동 자리만 있습니다(send_sms).
입주자 연락처는 개인정보이므로 DB 파일에만 저장하고 깃허브에는 올리지 않습니다.
"""
from __future__ import annotations

import os
import re
import smtplib
from email.message import EmailMessage

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..config import COMMON_METERS, FLOORS
from ..database.tables_ops import alert_log, tenant_contact
from .common import engine, q

router = APIRouter(prefix="/api", tags=["alerts"])

DEFAULT_MESSAGE = ("앞으로 한 시간 안에 전력 피크기준을 넘을 것으로 예상됩니다. "
                   "불필요한 전열기구 나 전등 등 스위치를 차단하여 주시기 바랍니다")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Contact(BaseModel):
    channel_id: str = Field(..., description="호실 채널 (예: M008-CH04 또는 M008-04)")
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    memo: str | None = None


class Alert(BaseModel):
    channels: list[str] = Field(default_factory=list, description="발령 대상 호실 채널")
    include_common: bool = Field(False, description="공용부분 포함 (최후 수단)")
    message: str = DEFAULT_MESSAGE
    messages: dict[str, str] = Field(default_factory=dict, description="호실별로 다르게 쓴 안내문구 (선택)")
    ref_time: str | None = None
    mode: str = "시연"
    model_name: str | None = None
    predicted_max_kw: float | None = None
    threshold_kw: float | None = None


def _cid(c: str) -> str:
    """'M008-04' → 'M008-CH04' (DB 표기)"""
    c = c.strip().upper()
    if "-CH" not in c and "-" in c:
        m, n = c.split("-", 1)
        c = f"{m}-CH{int(n):02d}"
    return c


def label(cid: str) -> str:
    meter, n = cid.split("-")[0], cid[-2:]
    if meter in FLOORS:
        return f"{FLOORS[meter]}층 {n}번"
    return f"{COMMON_METERS.get(meter, '?')}층 공용 {n}번"


def _check(c: Contact) -> dict:
    cid = _cid(c.channel_id)
    if q("SELECT 1 AS x FROM channel WHERE channel_id=:c", c=cid).empty:
        raise HTTPException(400, f"없는 채널입니다: {c.channel_id}")
    if c.email and not EMAIL_RE.match(c.email.strip()):
        raise HTTPException(400, "메일 주소 형식이 올바르지 않습니다.")
    if c.phone and not re.fullmatch(r"[0-9\-+ ]{8,20}", c.phone.strip()):
        raise HTTPException(400, "전화번호는 숫자와 - 만 써 주세요.")
    return {"channel_id": cid, "name": (c.name or "").strip() or None, "email": (c.email or "").strip() or None,
            "phone": (c.phone or "").strip() or None, "memo": (c.memo or "").strip() or None}


@router.get("/contacts")
def list_contacts():
    d = q("SELECT * FROM tenant_contact ORDER BY channel_id, contact_id")
    d["label"] = [label(c) for c in d.channel_id]
    d["channel"] = d.channel_id.str.replace("-CH", "-")
    return d.astype(object).where(d.notna(), None).to_dict("records")


@router.post("/contacts")
def add_contact(c: Contact):
    row = _check(c) | {"created_at": pd.Timestamp.now().to_pydatetime(), "updated_at": pd.Timestamp.now().to_pydatetime()}
    with engine.begin() as con:
        r = con.execute(tenant_contact.insert(), [row])
    return {"contact_id": r.inserted_primary_key[0] if r.inserted_primary_key else None, **row}


@router.put("/contacts/{contact_id}")
def edit_contact(contact_id: int, c: Contact):
    row = _check(c) | {"updated_at": pd.Timestamp.now().to_pydatetime()}
    with engine.begin() as con:
        n = con.execute(tenant_contact.update().where(tenant_contact.c.contact_id == contact_id).values(**row)).rowcount
    if not n:
        raise HTTPException(404, "연락처가 없습니다.")
    return {"contact_id": contact_id, **row}


@router.delete("/contacts/{contact_id}")
def delete_contact(contact_id: int):
    with engine.begin() as con:
        n = con.execute(tenant_contact.delete().where(tenant_contact.c.contact_id == contact_id)).rowcount
    if not n:
        raise HTTPException(404, "연락처가 없습니다.")
    return {"deleted": contact_id}


@router.get("/alerts/template")
def template():
    return {"message": DEFAULT_MESSAGE}


def send_email(to: str, subject: str, body: str) -> bool:
    """메일 서버가 설정돼 있을 때만 실제 발송."""
    host = os.getenv("SMTP_HOST")
    if not host:
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "")), to
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=10) as s:
        s.starttls()
        if os.getenv("SMTP_USER"):
            s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
        s.send_message(msg)
    return True


def send_sms(phone: str, body: str) -> bool:  # noqa: ARG001
    """문자 발송 업체 연동 자리 (현재 미연동 → 보내지 않음)."""
    return False


@router.post("/alerts")
def issue_alert(a: Alert):
    chans = [_cid(c) for c in a.channels]
    if not chans and not a.include_common:
        raise HTTPException(400, "발령 대상 호실을 하나 이상 고르세요.")
    contacts = q("SELECT * FROM tenant_contact")
    targets = contacts[contacts.channel_id.isin(chans)]
    sent, failed = 0, 0
    for r in targets.itertuples():
        body = a.messages.get(r.channel_id.replace("-CH", "-"), a.messages.get(r.channel_id, a.message))
        if r.email:
            try:
                sent += int(send_email(r.email, f"[피크가드] {label(r.channel_id)} 전력 피크 예상 안내", body))
            except Exception:
                failed += 1
        if r.phone:
            send_sms(r.phone, body)
    status = "기록만 (메일 서버 미설정)" if not os.getenv("SMTP_HOST") else ("메일 발송" if not failed else f"일부 실패 {failed}건")
    row = {"created_at": pd.Timestamp.now().to_pydatetime(), "ref_time": a.ref_time, "mode": a.mode,
           "model_name": a.model_name, "predicted_max_kw": a.predicted_max_kw, "threshold_kw": a.threshold_kw,
           "channels": ",".join(chans), "include_common": a.include_common,
           "message": a.message if not a.messages else a.message + " | 호실별 문구 " + str(len(a.messages)) + "건",
           "recipients": int(len(targets)), "email_sent": sent, "status": status}
    with engine.begin() as con:
        r = con.execute(alert_log.insert(), [row])
    return {"alert_id": r.inserted_primary_key[0] if r.inserted_primary_key else None, **row,
            "targets": [label(c) for c in chans], "no_contact": [label(c) for c in chans if c not in set(targets.channel_id)]}


@router.get("/alerts")
def recent_alerts(limit: int = 20):
    d = q(f"SELECT * FROM alert_log ORDER BY alert_id DESC LIMIT {int(limit)}")
    d["targets"] = [", ".join(label(c) for c in (s or "").split(",") if c) for s in d.channels]
    return d.astype(object).where(d.notna(), None).to_dict("records")
