"""데이터 수집·전처리 — 기업 제공 전력 CSV 읽기와 정제

연결 ─ 화면: — (DB 구축·파일 끌어다놓기·실시간 연결이 함께 쓰는 정제 규칙)
       API : POST /api/upload 가 사용
       DB  : measurement (쓰기)

DB 적재(database/load_power.py)와 CSV 업로드 API(api/upload.py)가 같은 규칙을 쓰도록 여기 모아 둡니다.

전처리 규칙
  1. 측정 시간을 15분 단위로 맞춤 (16:29:59.999 → 16:30:00). 1분 이상 어긋난 행은 제외
  2. 같은 채널·같은 시각 중복은 마지막 값만 남김
  3. 수요전력·누적사용량 음수는 0으로 (현재전력은 계측 오차 그대로 보존)
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd

COLS = {
    "전력량계 ID": "meter_id",
    "채널 번호": "channel_no",
    "측정 시간": "raw_time",
    "현재 전력(kW)": "power_kw",
    "누적 사용량(kWh)": "energy_kwh",
    "수요 전력(kW)": "demand_kw",
}
FILE_PATTERN = re.compile(r"(\d+)층_(공용|호실별)")


def read_csv(src) -> pd.DataFrame:
    """파일 경로 또는 바이트를 읽어 영문 컬럼명으로 바꾼다. 필수 컬럼이 없으면 ValueError."""
    if isinstance(src, (bytes, bytearray)):
        src = io.BytesIO(src)
    d = pd.read_csv(src, encoding="utf-8-sig")
    missing = [k for k in COLS if k not in d.columns]
    if missing:
        raise ValueError(f"필수 컬럼 없음: {', '.join(missing)}")
    return d.rename(columns=COLS)[list(COLS.values())]


def meter_info(file_name: str, meter_ids) -> list[dict]:
    """파일명('생활지원로봇센터 2층_호실별.csv')에서 층·구역을 읽어 계량기 정보를 만든다."""
    m = FILE_PATTERN.search(file_name)
    if not m:
        return []
    return [{"meter_id": mid, "floor": int(m.group(1)), "area_type": m.group(2), "source_file": file_name}
            for mid in meter_ids]


def read_folder(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames, meters = [], []
    for f in sorted(Path(data_dir).glob("*.csv")):
        d = read_csv(f)
        info = meter_info(f.name, d["meter_id"].unique())
        if not info:
            print(f"  [건너뜀] 파일명에서 층/구역을 읽을 수 없음: {f.name}")
            continue
        meters += info
        frames.append(d)
        print(f"  읽음 {f.name}: {len(d):,}건")
    return pd.concat(frames, ignore_index=True), pd.DataFrame(meters).drop_duplicates("meter_id")


def clean(df: pd.DataFrame, verbose: bool = True) -> tuple[pd.DataFrame, dict]:
    """정제 결과와 제외 건수 통계를 돌려준다."""
    stats = {"rows_read": len(df)}
    ts = pd.to_datetime(df["raw_time"].astype(str).str[:23], errors="coerce")   # '+0900' 앞까지 = 한국시간
    bad_time = ts.isna()
    for c in ("channel_no", "power_kw", "energy_kwh", "demand_kw"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    bad_val = df[["channel_no", "power_kw", "energy_kwh", "demand_kw"]].isna().any(axis=1)
    slot = ts.dt.round("15min")
    off_grid = (ts - slot).abs() > pd.Timedelta("1min")
    ok = ~bad_time & ~bad_val & ~off_grid
    stats.update(bad_time=int(bad_time.sum()), bad_value=int((bad_val & ~bad_time).sum()),
                 off_grid=int((off_grid & ~bad_time & ~bad_val).sum()))
    df = df.loc[ok].copy()
    df["measured_at"] = slot[ok]
    df["channel_id"] = df["meter_id"] + "-CH" + df["channel_no"].astype(int).astype(str).str.zfill(2)
    before = len(df)
    df = df.drop_duplicates(["channel_id", "measured_at"], keep="last")
    stats["duplicates_in_file"] = before - len(df)
    df["demand_kw"] = df["demand_kw"].clip(lower=0)
    df["energy_kwh"] = df["energy_kwh"].clip(lower=0)
    if verbose:
        print(f"  시각 오류 {stats['bad_time']} · 값 오류 {stats['bad_value']} · 15분 격자 이탈 {stats['off_grid']} · "
              f"파일 내 중복 {stats['duplicates_in_file']}건 제외")
    return df, stats


def to_db_rows(df: pd.DataFrame, dialect: str, tz: str = "Asia/Seoul") -> pd.DataFrame:
    """measurement 테이블 형식으로 변환 (SQLite 는 문자열 시각, PostgreSQL 은 시간대 포함)."""
    meas = df[["channel_id", "measured_at", "power_kw", "energy_kwh", "demand_kw"]].copy()
    if dialect == "postgresql":
        meas["measured_at"] = meas["measured_at"].dt.tz_localize(tz)
    else:
        meas["measured_at"] = meas["measured_at"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return meas
