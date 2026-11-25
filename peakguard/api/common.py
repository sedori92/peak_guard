"""[5] API 서버 — 공통 도구 (DB 조회, 실행 정보, 값 정리)

연결 ─ 화면: — (API 파일들이 함께 쓰는 도구)
       API : — 
       DB  : 모든 표 읽기(q), st_meta(info)
"""
from __future__ import annotations

import json
import math
from functools import lru_cache

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import text

from ..config import DISPLAY, FLOORS  # noqa: F401  (다른 라우터에서 함께 사용)
from ..database.connection import get_engine

engine = get_engine()
WEEK_KO = "월화수목금토일"


def q(sql: str, **params) -> pd.DataFrame:
    with engine.connect() as con:
        return pd.read_sql(text(sql), con, params=params)


@lru_cache(maxsize=1)
def info() -> dict:
    d = q("SELECT value FROM st_meta WHERE key='info'")
    if d.empty:
        raise HTTPException(503, "예측 결과가 없습니다. 먼저 python -m peakguard.shortterm 을 실행하세요.")
    return json.loads(d.value[0])


def room_key(cid: str) -> str:          # 'M004-CH03' → 'M004-03' (화면 표기)
    return cid.replace("-CH", "-")


def num(v, d=3):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), d)


def check_model(model: str) -> str:
    if model not in info()["models"]:
        raise HTTPException(400, f"알 수 없는 모델: {model} (가능: {', '.join(info()['models'])})")
    return model


def has_table(name: str) -> bool:
    with engine.connect() as con:
        return name in engine.dialect.get_table_names(con)


def to_local(s: pd.Series) -> pd.Series:
    """DB 시각 → 한국시간(시간대 표시 없음)."""
    s = pd.to_datetime(s)
    if getattr(s.dt, "tz", None) is not None:
        s = s.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    return s
