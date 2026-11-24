"""[11] CSV → API 붙여 보내기 — 새 전력 CSV 를 업로드해 DB에 추가

    POST /api/upload   (form-data: file=<CSV>, dry_run=true|false)

- 원본 CSV 형식(전력량계 ID, 채널 번호, 측정 시간, 현재 전력, 누적 사용량, 수요 전력) 그대로 받습니다.
- 정제 규칙은 DB 구축 때와 같습니다 (collect/power_csv.py).
- 이미 있는 (채널, 시각) 은 건너뛰므로 같은 파일을 여러 번 보내도 중복 저장되지 않습니다.
- 등록되지 않은 계량기·채널 행은 거부하고 사유를 돌려줍니다.
- 업로드 후 예측을 새로 만들려면 run.py 3 을 다시 실행하세요.
"""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from sqlalchemy import text

from ..collect.power_csv import clean, read_csv, to_db_rows
from ..database.tables_forecast import upload_log
from .common import engine

router = APIRouter(prefix="/api", tags=["upload"])


def ingest(raw: bytes, file_name: str, dry_run: bool = False) -> dict:
    try:
        df = read_csv(raw)
    except Exception as e:  # 형식 오류
        raise HTTPException(400, f"CSV 형식 오류: {e}")
    df, stats = clean(df, verbose=False)
    with engine.connect() as con:
        known = {r[0] for r in con.execute(text("SELECT channel_id FROM channel"))}
    unknown = df[~df.channel_id.isin(known)]
    df = df[df.channel_id.isin(known)]
    rows = to_db_rows(df, engine.dialect.name)
    result = {"file": file_name, **stats, "unknown_channel": int(len(unknown)),
              "unknown_channels": sorted(unknown.channel_id.unique().tolist())[:20],
              "valid_rows": int(len(rows)),
              "period": [str(df.measured_at.min()), str(df.measured_at.max())] if len(df) else None}
    if dry_run or rows.empty:
        result.update(inserted=0, duplicates=0, dry_run=dry_run)
        return result
    with engine.begin() as con:
        before = con.execute(text("SELECT COUNT(*) FROM measurement")).scalar()
        cols = "(channel_id, measured_at, power_kw, energy_kwh, demand_kw)"
        vals = "(:channel_id, :measured_at, :power_kw, :energy_kwh, :demand_kw)"
        if engine.dialect.name == "postgresql":
            stmt = text(f"INSERT INTO measurement {cols} VALUES {vals} ON CONFLICT DO NOTHING")
        else:   # SQLite: 기존 행과 같은 문자열 시각 형식으로 저장
            stmt = text(f"INSERT OR IGNORE INTO measurement {cols} VALUES {vals}")
        con.execute(stmt, rows.to_dict("records"))
        after = con.execute(text("SELECT COUNT(*) FROM measurement")).scalar()
        inserted = int(after - before)
        rejected = stats["bad_time"] + stats["bad_value"] + stats["off_grid"] + len(unknown)
        con.execute(upload_log.insert(), [dict(file_name=file_name, uploaded_at=pd.Timestamp.now().to_pydatetime(),
                                               rows_read=stats["rows_read"], inserted=inserted,
                                               duplicates=len(rows) - inserted, rejected=int(rejected))])
    result.update(inserted=inserted, duplicates=int(len(rows) - inserted), dry_run=False,
                  next_step="새 데이터로 예측을 갱신하려면 python run.py 3 실행")
    return result


@router.post("/upload")
async def upload(file: UploadFile = File(...), dry_run: bool = False):
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "CSV 파일만 받습니다.")
    raw = await file.read()
    from ..collect.weather import is_weather_csv, read_asos_bytes, upsert
    if is_weather_csv(raw):                                   # 기상청 ASOS 분자료 CSV → 기상 업데이트
        try:
            r = upsert(engine, read_asos_bytes(raw), file.filename)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"kind": "기상", "file": file.filename, "inserted": r["slots"], "duplicates": 0,
                "note": f"기상 15분 값 {r['slots']}칸 갱신 ({r.get('from', '')} ~ {r.get('to', '')})"}
    return {"kind": "전력", **ingest(raw, file.filename, dry_run)}
