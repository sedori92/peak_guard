"""DB → 예측용 데이터 구조

핵심 아이디어: 각 시계열(전체 합계 'TOTAL' + 활성 채널)을 '일(day) × 96칸(15분)' 행렬로 만든다.
  - 학습 대상 : 하루 96칸이 모두 있는 날만
  - 입력(과거 값): 짧은 결측(1시간 이하)은 보간, 그 외 결측은 '직전 같은 유형의 날' 값으로 대체
"""
from __future__ import annotations

from dataclasses import dataclass, field

import holidays
import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

SLOTS = 96  # 하루 15분 칸 수
TOTAL = "TOTAL"


def _to_local_naive(s: pd.Series) -> pd.Series:
    s = pd.to_datetime(s, utc=False)
    if getattr(s.dt, "tz", None) is not None:
        s = s.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    return s


def read_wide(engine: Engine) -> pd.DataFrame:
    """활성 채널의 수요전력을 (15분 시각 × 채널) 표로 읽고 TOTAL 열을 추가."""
    q = text("""
        SELECT m.channel_id, m.measured_at, m.demand_kw
        FROM measurement m JOIN channel c ON c.channel_id = m.channel_id
        WHERE c.is_active
    """)
    with engine.connect() as con:
        df = pd.read_sql(q, con)
    df["measured_at"] = _to_local_naive(df["measured_at"])
    wide = df.pivot_table(index="measured_at", columns="channel_id", values="demand_kw", aggfunc="last")
    grid = pd.date_range(wide.index.min().normalize(), wide.index.max().normalize() + pd.Timedelta("1D"),
                         freq="15min", inclusive="left")
    wide = wide.reindex(grid)
    wide = wide.interpolate(limit=4, limit_area="inside")          # 1시간 이하 끊김 보간
    total = wide.sum(axis=1, min_count=wide.shape[1])               # 모든 채널이 있을 때만 합계
    wide.insert(0, TOTAL, total)
    return wide


@dataclass
class DayData:
    days: pd.DatetimeIndex                      # 날짜
    series: list[str]                           # ['TOTAL', 'M001-CH02', ...]
    y: np.ndarray                               # (시계열, 일, 96) 원래 값 (결측 NaN)
    x: np.ndarray                               # (시계열, 일, 96) 입력용 보정 값 (결측 없음)
    offday: np.ndarray                          # (일,) 주말·공휴일 여부
    holiday: np.ndarray                         # (일,) 공휴일 여부
    scale: np.ndarray = field(default=None)     # (시계열,) 정규화 기준값

    @property
    def complete(self) -> np.ndarray:           # (시계열, 일) 96칸이 모두 있는 날
        return ~np.isnan(self.y).any(axis=2)

    def prev_same_type(self) -> np.ndarray:
        """각 날짜에 대해 직전 '같은 유형(평일/휴일)' 날의 인덱스 (없으면 -1)."""
        out = np.full(len(self.days), -1)
        last = {True: -1, False: -1}
        for d, t in enumerate(self.offday):
            out[d] = last[bool(t)]
            last[bool(t)] = d
        return out


def to_days(wide: pd.DataFrame, extra_days: int = 1) -> DayData:
    """(시각 × 시계열) 표 → DayData. extra_days 만큼 미래(예측 대상) 날짜를 빈 칸으로 덧붙인다."""
    days = pd.date_range(wide.index.min().normalize(), periods=len(wide) // SLOTS + extra_days, freq="D")
    grid = pd.date_range(days[0], periods=len(days) * SLOTS, freq="15min")
    w = wide.reindex(grid)
    y = w.to_numpy(dtype=float).T.reshape(w.shape[1], len(days), SLOTS)

    kr = holidays.KR(years=sorted({d.year for d in days}))
    hol = np.array([d.date() in kr for d in days])
    off = hol | (days.dayofweek >= 5)
    dd = DayData(days=days, series=list(w.columns), y=y, x=y.copy(), offday=off, holiday=hol)

    # 입력용 결측 보정: 직전 같은 유형 날 값 → 그래도 없으면 시계열 평균
    pst = dd.prev_same_type()
    for d in range(len(days)):
        if pst[d] >= 0:
            m = np.isnan(dd.x[:, d, :])
            dd.x[:, d, :][m] = dd.x[:, pst[d], :][m]
    mean = np.nanmean(dd.x, axis=(1, 2), keepdims=True)
    dd.x = np.where(np.isnan(dd.x), np.broadcast_to(mean, dd.x.shape), dd.x)
    return dd


def set_scale(dd: DayData, train_mask_days: np.ndarray) -> None:
    """채널마다 크기가 다르므로 학습기간 95백분위수로 나눠 정규화 (0 방지)."""
    vals = dd.y[:, train_mask_days, :].reshape(len(dd.series), -1)
    dd.scale = np.maximum(np.nanpercentile(vals, 95, axis=1), 0.05)
