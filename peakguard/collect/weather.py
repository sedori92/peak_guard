"""데이터 수집·전처리 — 기상청 ASOS 광주(156) 분자료 → 15분 집계 → DB(weather_15m)

사용법
    python run.py 2                       # DB 구축 때 함께 적재
    python -m peakguard.collect.weather --weather-dir data/weather

집계 규칙 (데이터명세서 D02와 동일)
  - 15분 구간 (t-15분, t] 의 1분 관측을 모아 평균
  - 기온·습도·풍속은 15개 관측이 모두 있을 때만 평균을 냄 (부족하면 빈칸, 보간·0 대체 없음)
  - 현재 예측 모델 입력에는 쓰지 않고, 화면 표시·탐색 분석용으로 보관
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from ..config import DATA_WEATHER
from ..database.connection import create_schema, get_engine

COLS = {"일시": "observed_at", "기온(°C)": "temperature_c", "습도(%)": "humidity_pct", "풍속(m/s)": "wind_speed_m_s"}


def read_asos(folder: Path) -> pd.DataFrame:
    frames = []
    for f in sorted(folder.glob("*ASOS*.csv")):
        for enc in ("cp949", "utf-8-sig"):
            try:
                d = pd.read_csv(f, encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        frames.append(d.rename(columns=COLS)[list(COLS.values())])
        print(f"  읽음 {f.name}: {len(d):,}행")
    d = pd.concat(frames, ignore_index=True)
    d["observed_at"] = pd.to_datetime(d["observed_at"])
    return d.drop_duplicates("observed_at").sort_values("observed_at")


def aggregate(d: pd.DataFrame) -> pd.DataFrame:
    # (t-15, t] 구간 → 끝 시각 t 로 묶기: 00:01~00:15 → 00:15
    d = d.copy()
    d["measured_at"] = d["observed_at"].dt.ceil("15min")
    g = d.groupby("measured_at")
    out = pd.DataFrame({"valid_minutes": g.size()})
    for c in ("temperature_c", "humidity_pct", "wind_speed_m_s"):
        cnt = g[c].count()
        out[c] = g[c].mean().where(cnt >= 15).round(2)
    return out.reset_index()


def load(weather_dir: Path = DATA_WEATHER, db_url: str | None = None) -> None:
    engine = get_engine(db_url)
    create_schema(engine)
    if not list(Path(weather_dir).glob("*ASOS*.csv")):
        print(f"  [건너뜀] {weather_dir} 에 ASOS CSV 가 없습니다 (화면 기온은 빈칸으로 표시)")
        return
    print("1) ASOS 분자료 읽기")
    agg = aggregate(read_asos(weather_dir))
    with engine.begin() as con:
        con.execute(text("DELETE FROM weather_15m"))
    if engine.dialect.name == "sqlite":
        agg["measured_at"] = agg["measured_at"].dt.strftime("%Y-%m-%d %H:%M:%S")
    agg.to_sql("weather_15m", engine, if_exists="append", index=False, chunksize=20_000)
    print(f"완료: weather_15m {len(agg):,}행 (기온 결측 {agg.temperature_c.isna().sum()}행)")


def main() -> None:
    ap = argparse.ArgumentParser(description="ASOS 기상 분자료를 15분으로 집계해 DB에 적재")
    ap.add_argument("--weather-dir", default=DATA_WEATHER, type=Path)
    ap.add_argument("--db-url", default=None)
    a = ap.parse_args()
    load(a.weather_dir, a.db_url)


if __name__ == "__main__":
    main()
