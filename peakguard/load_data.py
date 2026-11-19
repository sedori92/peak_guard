"""원본 CSV → DB 적재 (ETL: Extract·Transform·Load)

사용법
    python -m peakguard.load_data --data-dir data/raw          # .env 의 DATABASE_URL(없으면 SQLite)
    python -m peakguard.load_data --data-dir data/raw --reset  # 기존 측정값 지우고 다시 적재

전처리 규칙
  1. 측정 시간을 15분 단위로 맞춤(16:29:59.999 → 16:30:00). 1분 이상 어긋난 초기 시험 데이터는 제외
  2. 같은 채널·같은 시각 중복은 마지막 값만 남김
  3. 전 기간 수요전력이 0인 채널은 is_active = FALSE (예측 대상에서 제외)
"""
from __future__ import annotations

import argparse
import io
import re
from pathlib import Path

import pandas as pd
from sqlalchemy import delete, text

from .db import channel, create_schema, get_engine, measurement, meter

COLS = {
    "전력량계 ID": "meter_id",
    "채널 번호": "channel_no",
    "측정 시간": "raw_time",
    "현재 전력(kW)": "power_kw",
    "누적 사용량(kWh)": "energy_kwh",
    "수요 전력(kW)": "demand_kw",
}
TZ = "Asia/Seoul"


def read_raw(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames, meters = [], []
    for f in sorted(data_dir.glob("*.csv")):
        d = pd.read_csv(f, encoding="utf-8-sig").rename(columns=COLS)
        m = re.search(r"(\d+)층_(공용|호실별)", f.name)
        if not m:
            print(f"[건너뜀] 파일명에서 층/구역을 읽을 수 없음: {f.name}")
            continue
        for mid in d["meter_id"].unique():
            meters.append({"meter_id": mid, "floor": int(m.group(1)),
                           "area_type": m.group(2), "source_file": f.name})
        frames.append(d)
        print(f"  읽음 {f.name}: {len(d):,}건")
    return pd.concat(frames, ignore_index=True), pd.DataFrame(meters).drop_duplicates("meter_id")


def clean(df: pd.DataFrame) -> pd.DataFrame:
    ts = pd.to_datetime(df["raw_time"].str[:23])          # '+0900' 앞까지 = 한국시간
    slot = ts.dt.round("15min")
    ok = (ts - slot).abs() <= pd.Timedelta("1min")
    print(f"  15분 격자에서 벗어난 행 제외: {(~ok).sum():,}건")
    df = df.loc[ok].copy()
    df["measured_at"] = slot[ok]
    df["channel_id"] = df["meter_id"] + "-CH" + df["channel_no"].astype(int).astype(str).str.zfill(2)
    before = len(df)
    df = df.drop_duplicates(["channel_id", "measured_at"], keep="last")
    print(f"  중복 제거: {before - len(df):,}건")
    df["demand_kw"] = df["demand_kw"].clip(lower=0)
    df["energy_kwh"] = df["energy_kwh"].clip(lower=0)
    return df


def _copy_postgres(engine, df: pd.DataFrame) -> None:
    """PostgreSQL(Supabase)은 COPY 로 빠르게 적재 (100만 건 수십 초)."""
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False)
    buf.seek(0)
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.copy_expert(
                "COPY measurement (channel_id, measured_at, power_kw, energy_kwh, demand_kw) "
                "FROM STDIN WITH (FORMAT csv)", buf)
        raw.commit()
    finally:
        raw.close()


def load(data_dir: Path, reset: bool = False, db_url: str | None = None) -> None:
    engine = get_engine(db_url)
    print(f"DB: {engine.url.render_as_string(hide_password=True)}")
    create_schema(engine)

    print("1) CSV 읽기")
    raw, meters = read_raw(data_dir)
    print("2) 전처리")
    df = clean(raw)

    chans = (df.groupby(["channel_id", "meter_id", "channel_no"], as_index=False)["demand_kw"].max()
             .assign(is_active=lambda x: x["demand_kw"] > 0).drop(columns="demand_kw"))
    print(f"  채널 {len(chans)}개 (활성 {chans.is_active.sum()}개, 전 기간 0kW {(~chans.is_active).sum()}개)")

    is_pg = engine.dialect.name == "postgresql"
    meas = df[["channel_id", "measured_at", "power_kw", "energy_kwh", "demand_kw"]].copy()
    if is_pg:
        meas["measured_at"] = meas["measured_at"].dt.tz_localize(TZ)
    else:
        meas["measured_at"] = meas["measured_at"].dt.strftime("%Y-%m-%d %H:%M:%S")

    print("3) DB 적재")
    with engine.begin() as con:
        if reset:
            for t in ("forecast_slot", "peak_forecast", "model_run"):
                con.execute(text(f"DELETE FROM {t}"))
            con.execute(delete(measurement))
            con.execute(delete(channel))
            con.execute(delete(meter))
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
        print(f"  measurement 에 이미 {n_exist:,}건이 있어 측정값 적재는 건너뜀 (--reset 으로 재적재)")
        return

    if is_pg:
        _copy_postgres(engine, meas)
    else:
        meas.to_sql("measurement", engine, if_exists="append", index=False, chunksize=50_000)
    with engine.connect() as con:
        n = con.execute(text("SELECT COUNT(*) FROM measurement")).scalar()
    print(f"완료: measurement {n:,}건")


def main() -> None:
    p = argparse.ArgumentParser(description="원본 CSV를 DB에 적재")
    p.add_argument("--data-dir", default="data/raw", type=Path)
    p.add_argument("--reset", action="store_true", help="기존 데이터 삭제 후 재적재")
    p.add_argument("--db-url", default=None, help="DB 주소 (기본: .env 의 DATABASE_URL)")
    a = p.parse_args()
    load(a.data_dir, a.reset, a.db_url)


if __name__ == "__main__":
    main()
