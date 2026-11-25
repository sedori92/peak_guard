"""[2] DB 구축 — 테이블 생성 + 전력 CSV 적재

연결 ─ 화면: —
       API : —
       DB  : measurement·channel·meter (쓰기, python run.py 2)

    python run.py 2                 # 테이블 생성 + 전력 + 기상 적재 (권장)
    python -m peakguard.database.load_power --reset

전처리 규칙은 collect/power_csv.py 에 있습니다.
전 기간 수요전력이 0인 채널은 is_active = FALSE (예측 대상에서 제외).
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

from sqlalchemy import delete, text

from ..collect.power_csv import clean, read_folder, to_db_rows
from ..config import DATA_RAW
from .connection import channel, create_schema, get_engine, measurement, meter


def _copy_postgres(engine, df) -> None:
    """PostgreSQL 은 COPY 로 빠르게 적재."""
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False)
    buf.seek(0)
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.copy_expert("COPY measurement (channel_id, measured_at, power_kw, energy_kwh, demand_kw) "
                            "FROM STDIN WITH (FORMAT csv)", buf)
        raw.commit()
    finally:
        raw.close()


def load(data_dir: Path = DATA_RAW, reset: bool = False, db_url: str | None = None) -> None:
    engine = get_engine(db_url)
    print(f"DB: {engine.url.render_as_string(hide_password=True)}")
    create_schema(engine)

    print("1) 전력 CSV 읽기")
    raw, meters = read_folder(data_dir)
    print("2) 전처리")
    df, _ = clean(raw)
    chans = (df.groupby(["channel_id", "meter_id", "channel_no"], as_index=False)["demand_kw"].max()
             .assign(is_active=lambda x: x["demand_kw"] > 0).drop(columns="demand_kw"))
    print(f"  채널 {len(chans)}개 (활성 {chans.is_active.sum()}개, 전 기간 0kW {(~chans.is_active).sum()}개)")

    print("3) DB 적재")
    with engine.begin() as con:
        if reset:
            for t in ("forecast_slot", "peak_forecast", "model_run"):
                con.execute(text(f"DELETE FROM {t}"))
            con.execute(delete(measurement)); con.execute(delete(channel)); con.execute(delete(meter))
        existing = {r[0] for r in con.execute(text("SELECT meter_id FROM meter"))}
        new_m = meters[~meters.meter_id.isin(existing)]
        if len(new_m):
            con.execute(meter.insert(), new_m.to_dict("records"))
        existing = {r[0] for r in con.execute(text("SELECT channel_id FROM channel"))}
        new_c = chans[~chans.channel_id.isin(existing)].copy()
        new_c["channel_no"] = new_c["channel_no"].astype(int)
        new_c["is_active"] = new_c["is_active"].astype(bool)
        if len(new_c):
            con.execute(channel.insert(), new_c.to_dict("records"))
        n_exist = con.execute(text("SELECT COUNT(*) FROM measurement")).scalar()
    if n_exist and not reset:
        print(f"  measurement 에 이미 {n_exist:,}건이 있어 건너뜀 (--reset 으로 재적재, 추가분은 run.py 11 업로드)")
        return
    meas = to_db_rows(df, engine.dialect.name)
    if engine.dialect.name == "postgresql":
        _copy_postgres(engine, meas)
    else:
        meas.to_sql("measurement", engine, if_exists="append", index=False, chunksize=50_000)
    with engine.connect() as con:
        n = con.execute(text("SELECT COUNT(*) FROM measurement")).scalar()
    print(f"완료: measurement {n:,}건")


def main() -> None:
    p = argparse.ArgumentParser(description="전력 CSV를 DB에 적재")
    p.add_argument("--data-dir", default=DATA_RAW, type=Path)
    p.add_argument("--reset", action="store_true", help="기존 데이터 삭제 후 재적재")
    p.add_argument("--db-url", default=None)
    a = p.parse_args()
    load(a.data_dir, a.reset, a.db_url)


if __name__ == "__main__":
    main()
