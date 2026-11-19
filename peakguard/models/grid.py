"""[3] 예측모델 — DB → 15분 격자(시계열 × 시각) 만들기와 피크 기준 계산

  - 대상 시계열: 'TOTAL'(활성 채널 합산) + 활성 채널 61개
  - y : 정답용 원본 (결측은 NaN 그대로)
  - yi: 입력용 (1시간 이하 끊김만 선형 보간)
  - p : 현재전력 순간값 (입력용)
"""
from __future__ import annotations

from dataclasses import dataclass

import holidays
import numpy as np
import pandas as pd
from sqlalchemy import text

from ..config import DAY, PEAK_PERCENTILE, PEAK_WINDOW_DAYS, ROOM_METERS


@dataclass
class Grid:
    times: pd.DatetimeIndex
    series: list[str]          # ['TOTAL', 활성 채널...]
    y: np.ndarray              # (S, T) 수요전력 원본 (결측 NaN) — 정답
    yi: np.ndarray             # (S, T) 입력용 (1시간 이하 끊김 보간)
    p: np.ndarray              # (S, T) 현재전력 순간값 (입력용)
    offday: np.ndarray         # (T,)
    holiday: np.ndarray        # (T,)
    rooms_all: list[str]       # 화면용 호실 채널 34개 (비활성 포함)
    inactive: set[str]


def load_grid(engine) -> Grid:
    with engine.connect() as con:
        m = pd.read_sql(text("SELECT channel_id, measured_at, power_kw, demand_kw FROM measurement"), con)
        ch = pd.read_sql(text("SELECT channel_id, meter_id, is_active FROM channel"), con)
    m["measured_at"] = pd.to_datetime(m["measured_at"])
    if getattr(m["measured_at"].dt, "tz", None) is not None:
        m["measured_at"] = m["measured_at"].dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    active = ch.loc[ch.is_active.astype(bool), "channel_id"].tolist()
    t0, t1 = m.measured_at.min().normalize(), m.measured_at.max()
    times = pd.date_range(t0, t1, freq="15min")
    dem = m.pivot_table(index="measured_at", columns="channel_id", values="demand_kw").reindex(times)
    pw = m.pivot_table(index="measured_at", columns="channel_id", values="power_kw").reindex(times)
    dem, pw = dem[active], pw[active]
    total = dem.sum(axis=1, min_count=len(active))
    total_p = pw.sum(axis=1, min_count=len(active))
    y = np.vstack([total.to_numpy(), dem.to_numpy().T])
    p = np.vstack([total_p.to_numpy(), pw.to_numpy().T])
    yi = pd.DataFrame(y.T).interpolate(limit=4, limit_area="inside").to_numpy().T
    pi = pd.DataFrame(p.T).interpolate(limit=4, limit_area="inside").to_numpy().T
    pi = np.where(np.isnan(pi), yi, pi)                     # 순간값 없으면 수요전력으로 대체
    kr = holidays.KR(years=sorted({t.year for t in times}))
    hol = np.array([d in kr for d in times.date])
    off = hol | (times.dayofweek >= 5)
    rooms_all = ch.loc[ch.meter_id.isin(ROOM_METERS), "channel_id"].sort_values().tolist()
    return Grid(times, ["TOTAL"] + active, y, yi, pi, off, hol, rooms_all,
                set(ch.loc[~ch.is_active.astype(bool), "channel_id"]))


def thresholds(g: Grid) -> pd.Series:
    """날짜별 피크 기준 = 직전 30일 전체 합산 15분 수요전력의 P95 (7일치 이상 있을 때만)."""
    tot = pd.Series(g.y[0], index=g.times)
    out = {}
    for d in pd.date_range(g.times[0].normalize(), g.times[-1].normalize(), freq="D"):
        w = tot[(tot.index >= d - pd.Timedelta(days=PEAK_WINDOW_DAYS)) & (tot.index < d)].dropna()
        out[d.date().isoformat()] = (float(np.percentile(w, PEAK_PERCENTILE)) if len(w) >= 7 * DAY else np.nan, len(w))
    return pd.DataFrame(out, index=["threshold_kw", "n_samples"]).T


def take(a: np.ndarray, idx: np.ndarray) -> np.ndarray:
    out = np.full(idx.shape, np.nan)
    ok = (idx >= 0) & (idx < len(a))
    out[ok] = a[idx[ok]]
    return out
