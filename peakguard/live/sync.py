"""실시간 연결 ① 빠진 구간 채우기 — 마지막 실측 이후 값만 중복 없이 DB(측정값 장부)에 넣습니다.

데이터를 가져오는 곳 (있는 것만 사용, 가짜 데이터는 절대 만들지 않음):
  1) 새 데이터 폴더  data/incoming/*.csv
       기업에서 받은 CSV(기업 제공 원본과 같은 형식)를 이 폴더에 넣어 두면 버튼을 누를 때 읽어 들입니다.
       읽은 파일은 data/incoming/done/ 으로 옮겨 다시 읽지 않습니다.
  2) 기업 DB 직접 연결 (선택)
       .env 에 LIVE_DB_URL(예: mysql+pymysql://아이디:비밀번호@주소/DB이름)과
       LIVE_DB_QUERY(마지막 시각 이후 행을 돌려주는 SELECT 문, :after 자리표시 사용)를 넣으면 사용합니다.
       기업이 실시간 연결을 허용하지 않은 현재는 비워 둡니다.

같은 (채널, 시각) 값은 한 번만 저장됩니다. 같은 파일을 여러 번 넣어도 겹치지 않습니다.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

from ..config import ROOT

INCOMING = ROOT / "data" / "incoming"        # 새 데이터 폴더
CSV_COLS = ["전력량계 ID", "채널 번호", "측정 시간", "현재 전력(kW)", "누적 사용량(kWh)", "수요 전력(kW)"]


def last_measured(engine) -> str | None:
    """측정값 장부에서 가장 최근 시각."""
    with engine.connect() as con:
        v = con.execute(text("SELECT MAX(measured_at) FROM measurement")).scalar()
    return None if v is None else str(v)[:16]


def sync(engine) -> dict:
    """버튼 [실시간 연결]의 1단계. 결과 요약을 돌려줍니다."""
    from ..api.upload import ingest
    before = last_measured(engine)
    files, inserted, notes = [], 0, []
    # 1) 새 데이터 폴더
    INCOMING.mkdir(parents=True, exist_ok=True)
    done = INCOMING / "done"
    for f in sorted(INCOMING.glob("*.csv")):
        r = ingest(f.read_bytes(), f.name)
        inserted += r.get("inserted", 0)
        files.append({"file": f.name, "inserted": r.get("inserted", 0), "duplicates": r.get("duplicates", 0)})
        done.mkdir(exist_ok=True)
        shutil.move(str(f), str(done / f.name))
    # 2) 기업 DB 직접 연결 (설정했을 때만)
    url, query = os.getenv("LIVE_DB_URL"), os.getenv("LIVE_DB_QUERY")
    if url and query:
        try:
            src = create_engine(url)
            with src.connect() as con:
                d = pd.read_sql(text(query), con, params={"after": before})
            if len(d):
                d.columns = CSV_COLS[:len(d.columns)]
                r = ingest(d.to_csv(index=False).encode("utf-8-sig"), "기업DB")
                inserted += r.get("inserted", 0)
                files.append({"file": "기업DB", "inserted": r.get("inserted", 0), "duplicates": r.get("duplicates", 0)})
        except Exception as e:                                  # 연결 실패해도 예측은 계속
            notes.append(f"기업 DB 연결 실패: {e.__class__.__name__}")
    else:
        notes.append("기업 DB 직접 연결은 설정되지 않음 (.env 의 LIVE_DB_URL)")
    after = last_measured(engine)
    now = pd.Timestamp.now().floor("15min")
    gap = int((now - pd.Timestamp(after)) / pd.Timedelta("15min")) if after else None
    if inserted == 0:
        notes.append(f"{before} 이후 새 데이터 없음 — 마지막 실측 기준으로 예측합니다")
    return {"before_last": before, "after_last": after, "inserted": inserted, "files": files,
            "missing_slots_to_now": gap, "now": f"{now:%Y-%m-%d %H:%M}", "notes": notes,
            "incoming_folder": str(INCOMING.relative_to(ROOT))}
