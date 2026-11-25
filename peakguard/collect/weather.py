"""데이터 수집·전처리 — 기상청 ASOS 광주(156) 분자료 → 15분 집계 → DB(weather_15m)

연결 ─ 화면: ② '4. 기상·이상 패턴'의 기상요인 관계 · ③ '4. 기상 비교'의 자료
       API : POST /api/upload(기상 CSV) · POST /api/live/sync
       DB  : weather_15m (쓰기)

사용법
    python run.py 2                       # DB 구축 때 함께 적재
    python -m peakguard.collect.weather --weather-dir data/weather

집계 규칙 (데이터명세서 D02와 동일)
  - 15분 구간 (t-15분, t] 의 1분 관측을 모아 평균
  - 기온·습도·풍속은 15개 관측이 모두 있을 때만 평균을 냄 (부족하면 빈칸, 보간·0 대체 없음)
  - 예측모델 입력(예측 시점 기온·습도·기온 1시간 변화)과 사용패턴 분석(기상 상관)에 사용

업데이트 (전력 데이터 [실시간 연결]과 같은 방식, 가짜 값은 만들지 않음)
  1) 기상 데이터 폴더 data/incoming/weather/*.csv  — 기상자료개방포털에서 내려받은 ASOS 분자료 CSV
     (또는 화면 [실시간 연결] → [파일 끌어다놓기]에 같은 CSV를 올리면 자동으로 기상으로 인식)
  2) 기상 API (선택) — .env 의 WEATHER_API_URL 에 기상청 API허브 ASOS 분자료 주소를 넣으면 사용
     주소 안의 {start} {end} (YYYYMMDDHHMM) {key}(WEATHER_API_KEY) 자리를 채워 호출하고, 같은 열 이름의 CSV를 받음
  - 새 분자료가 들어오면 그 구간의 15분 값만 다시 계산해 바꿔 넣음 (이전 값 중복 없음)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from ..config import DATA_WEATHER
from ..database.connection import create_schema, get_engine

COLS = {"일시": "observed_at", "기온(°C)": "temperature_c", "습도(%)": "humidity_pct", "풍속(m/s)": "wind_speed_m_s"}


def read_asos_bytes(raw: bytes) -> pd.DataFrame:
    """ASOS 분자료 CSV 한 개(바이트) → observed_at·기온·습도·풍속."""
    import io
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            d = pd.read_csv(io.BytesIO(raw), encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("CSV 인코딩을 읽을 수 없습니다.")
    miss = [k for k in ("일시", "기온(°C)") if k not in d.columns]
    if miss:
        raise ValueError(f"기상 CSV 에 {miss} 열이 없습니다 (기상청 ASOS 분자료 형식이어야 함).")
    for k in COLS:
        if k not in d.columns:
            d[k] = float("nan")
    d = d.rename(columns=COLS)[list(COLS.values())]
    d["observed_at"] = pd.to_datetime(d["observed_at"])
    return d.drop_duplicates("observed_at").sort_values("observed_at")


def is_weather_csv(raw: bytes) -> bool:
    head = raw[:600]
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            t = head.decode(enc, errors="ignore")
        except Exception:
            continue
        if "일시" in t and "기온" in t:
            return True
    return False


def last_weather(engine) -> str | None:
    with engine.connect() as con:
        v = con.execute(text("SELECT MAX(measured_at) FROM weather_15m WHERE temperature_c IS NOT NULL")).scalar()
    return None if v is None else str(v)[:16]


def upsert(engine, minutes: pd.DataFrame, source: str = "") -> dict:
    """분자료 → 15분 집계 → 그 구간만 지우고 다시 넣기 (같은 시각 중복 없음)."""
    if minutes.empty:
        return {"source": source, "rows": 0, "slots": 0}
    agg = aggregate(minutes)
    a, b = agg.measured_at.min(), agg.measured_at.max()
    with engine.connect() as con:
        have = pd.read_sql(text("SELECT measured_at, valid_minutes FROM weather_15m WHERE measured_at >= :a AND measured_at <= :b"),
                           con, params={"a": f"{a:%Y-%m-%d %H:%M:%S}", "b": f"{b:%Y-%m-%d %H:%M:%S}"})
    have = dict(zip(pd.to_datetime(have.measured_at), have.valid_minutes))
    # 이미 있는 칸을 관측 수가 더 적은(구간 끝이 잘린) 값으로 덮지 않음
    agg = agg[[not (t in have and (have[t] or 0) > n) for t, n in zip(agg.measured_at, agg.valid_minutes)]]
    if agg.empty:
        return {"source": source, "rows": int(len(minutes)), "slots": 0}
    with engine.begin() as con:
        for t in agg.measured_at:
            if t in have:
                con.execute(text("DELETE FROM weather_15m WHERE measured_at = :t"), {"t": f"{t:%Y-%m-%d %H:%M:%S}"})
    if engine.dialect.name == "sqlite":
        agg["measured_at"] = agg["measured_at"].dt.strftime("%Y-%m-%d %H:%M:%S")
    agg.to_sql("weather_15m", engine, if_exists="append", index=False, chunksize=20_000)
    return {"source": source, "rows": int(len(minutes)), "slots": int(len(agg)), "from": f"{a:%Y-%m-%d %H:%M}", "to": f"{b:%Y-%m-%d %H:%M}"}


def fetch_api(after: str | None) -> pd.DataFrame | None:
    """.env WEATHER_API_URL 이 있으면 마지막 기상 시각 이후 분자료를 받아 옴 (없으면 None)."""
    import os
    url = os.getenv("WEATHER_API_URL")
    if not url:
        return None
    import urllib.request
    start = (pd.Timestamp(after) if after else pd.Timestamp.now() - pd.Timedelta(days=1)) - pd.Timedelta(minutes=15)
    end = pd.Timestamp.now()
    full = url.format(start=f"{start:%Y%m%d%H%M}", end=f"{end:%Y%m%d%H%M}", key=os.getenv("WEATHER_API_KEY", ""))
    with urllib.request.urlopen(full, timeout=30) as r:
        return read_asos_bytes(r.read())


def update(engine) -> dict:
    """[실시간 연결]과 함께 부르는 기상 업데이트: 폴더 CSV → 기상 API(설정 시). 결과 요약."""
    from ..config import ROOT
    import shutil
    folder = ROOT / "data" / "incoming" / "weather"
    folder.mkdir(parents=True, exist_ok=True)
    before = last_weather(engine)
    done, notes = [], []
    for f in sorted(folder.glob("*.csv")):
        try:
            done.append(upsert(engine, read_asos_bytes(f.read_bytes()), f.name))
            (folder / "done").mkdir(exist_ok=True)
            shutil.move(str(f), str(folder / "done" / f.name))
        except ValueError as e:
            notes.append(f"{f.name}: {e}")
    try:
        d = fetch_api(before)
        if d is None:
            notes.append("기상 API 연결은 설정되지 않음 (.env 의 WEATHER_API_URL)")
        else:
            done.append(upsert(engine, d, "기상 API"))
    except Exception as e:                                   # 연결 실패해도 예측은 계속 (기상 없이)
        notes.append(f"기상 API 연결 실패: {e.__class__.__name__}")
    return {"before_last": before, "after_last": last_weather(engine), "files": done, "notes": notes,
            "folder": "data/incoming/weather"}


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
