"""[3] 예측모델 — DB → 15분 격자(시계열 × 시각) 만들기와 피크 기준 계산

연결 ─ 화면: — (모든 예측·분석이 쓰는 15분 격자, 음수 정제)
       API : —
       DB  : measurement·weather_15m·holiday·channel (읽기)

  - 대상 시계열: 'TOTAL'(활성 채널 합산) + 활성 채널 61개
  - y : 정답용 원본 (결측은 NaN 그대로)
  - yi: 입력용 (1시간 이하 끊김만 선형 보간)
  - p : 현재전력 순간값 (입력용)
  - temp·hum : 기상청 ASOS 광주 기온·습도 15분 값 (1시간 이하 끊김만 보간)
  - tanom : 기온 평소 대비 = 그 시각 기온 − 직전 7일 평균 기온 (예측 입력용)
      기온 그대로를 넣으면 여름에 학습하고 가을에 시험할 때 '계절'을 기온으로 착각해 오차가 커짐(실험: LightGBM 시험 MAE 0.691 → 0.731).
      평소 대비 차이로 바꾸면 계절과 상관없이 '평소보다 덥다/춥다'만 남아 오차가 줄어듦(0.691 → 0.678). 습도는 넣으면 나빠져 분석에만 씀.

현재전력 음수 처리 (기업 추가 답변 5번)
  - 음수는 계측 오류로 보고 결측(NaN)으로 바꿈 (0이나 절댓값으로 바꾸지 않음)
  - 1시간(4칸) 이하로 짧게 끊긴 곳만 앞뒤 정상값으로 보간
  - 그보다 긴 연속 음수·결측 구간은 보간하지 않고 p_bad 로 표시 → 학습에서 제외 (train.py)
  - 처리 전후 건수·비율은 prep 에 기록 → st_meta 'info' → 세 번째 화면·README
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
    p_bad: np.ndarray = None   # (S, T) 현재전력이 긴 구간 결측(음수 포함)이라 학습에서 뺄 칸
    temp: np.ndarray = None    # (T,) 기온 ℃
    hum: np.ndarray = None     # (T,) 습도 %
    prep: dict = None          # 전처리 기록 (음수 건수·비율 등)
    tanom: np.ndarray = None   # (T,) 기온 평소 대비 (직전 7일 평균과 차이)
    use_weather: bool = False  # 예측 입력에 기상을 쓸지 (config.USE_WEATHER). 기상 값 자체는 분석용으로 항상 읽음


def load_grid(engine, use_weather: bool | None = None) -> Grid:
    """use_weather: 예측 입력에 기상을 쓸지 (None 이면 config.USE_WEATHER). 기온·습도는 분석용으로 항상 읽음."""
    from ..config import USE_WEATHER
    use_weather = USE_WEATHER if use_weather is None else use_weather
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
    n_rows, n_neg = int(len(m)), int((m.power_kw < 0).sum())
    m.loc[m.power_kw < 0, "power_kw"] = np.nan                       # 현재전력 음수 → 결측 (기업 안내)
    pw = m.pivot_table(index="measured_at", columns="channel_id", values="power_kw").reindex(times)
    dem, pw = dem.reindex(columns=active), pw.reindex(columns=active)   # 현재전력이 전부 음수인 채널도 열 유지
    total = dem.sum(axis=1, min_count=len(active))
    total_p = pw.sum(axis=1, min_count=len(active))
    y = np.vstack([total.to_numpy(), dem.to_numpy().T])
    p = np.vstack([total_p.to_numpy(), pw.to_numpy().T])
    yi = pd.DataFrame(y.T).interpolate(limit=4, limit_area="inside").to_numpy().T
    # 채널별로 짧은 끊김(1시간 이하)만 보간한 뒤 건물 합계를 다시 냄
    pw_i = pw.interpolate(limit=4, limit_area="inside")
    gap = pw.isna() & pw_i.isna() & dem.notna()                       # 실측(수요전력)은 있는데 현재전력은 긴 결측
    # 건물 합계 순간값: 긴 결측 채널은 그 칸의 수요전력을 대신 씀 (기업 안내: 수요전력이 정상이면 우선 활용)
    total_pi = pw_i.where(~gap, dem).sum(axis=1, min_count=len(active))
    pi = np.vstack([total_pi.to_numpy(), pw_i.to_numpy().T])
    p_bad = np.vstack([np.zeros(len(times), bool), gap.to_numpy().T])   # 학습 제외는 그 채널 자신의 칸만
    pi = np.where(np.isnan(pi), yi, pi)                     # 예측할 때만: 순간값이 없으면 수요전력으로 대신 (학습은 p_bad 로 제외)
    short = int((pw.isna() & pw_i.notna()).to_numpy().sum())
    prep = {"rows": n_rows, "power_negative": n_neg, "power_negative_pct": round(n_neg / n_rows * 100, 2) if n_rows else 0,
            "short_interpolated_cells": short, "long_gap_cells": int(gap.to_numpy().sum()),
            "excluded_issue_slots_total": int(p_bad[0].sum())}
    temp = hum = np.full(len(times), np.nan)
    if True:
        with engine.connect() as con:
            w = pd.read_sql(text("SELECT measured_at, temperature_c, humidity_pct FROM weather_15m"), con)
        if len(w):
            w["measured_at"] = pd.to_datetime(w.measured_at)
            w = w.drop_duplicates("measured_at").set_index("measured_at").reindex(times).interpolate(limit=4, limit_area="inside")
            temp, hum = w.temperature_c.to_numpy(), w.humidity_pct.to_numpy()
    prep["weather_slots"] = int((~np.isnan(temp)).sum())
    tanom = (pd.Series(temp) - pd.Series(temp).rolling(7 * DAY, min_periods=DAY).mean().shift(1)).to_numpy()
    kr = holidays.KR(years=sorted({t.year for t in times}))
    hol = np.array([d in kr for d in times.date])
    off = hol | (times.dayofweek >= 5)
    rooms_all = ch.loc[ch.meter_id.isin(ROOM_METERS), "channel_id"].sort_values().tolist()
    return Grid(times, ["TOTAL"] + active, y, yi, pi, off, hol, rooms_all,
                set(ch.loc[~ch.is_active.astype(bool), "channel_id"]), p_bad, temp, hum, prep, tanom, use_weather)


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
