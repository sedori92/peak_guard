"""트리 모델(LightGBM·RandomForest)용 특징(feature) 만들기.

예측 시점: '전날 자정' (하루 전 예측, day-ahead). 그래서 특징은 모두 '예측일 이전' 값만 사용.
한 행 = (시계열, 예측일, 15분 칸)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .dataset import SLOTS, DayData

N_HIST = 3  # 같은 유형의 최근 며칠을 볼지


def same_type_history(dd: DayData, n: int = N_HIST) -> np.ndarray:
    """(일, n) : 각 날짜 직전의 같은 유형(평일/휴일) 날짜 인덱스 n개 (가까운 순, 없으면 -1)."""
    out = np.full((len(dd.days), n), -1)
    hist = {True: [], False: []}
    for d, t in enumerate(dd.offday):
        h = hist[bool(t)][-n:][::-1]
        out[d, :len(h)] = h
        hist[bool(t)].append(d)
    return out


def build(dd: DayData, series_idx: list[int], day_idx: list[int], with_target: bool = True) -> pd.DataFrame:
    """특징 행렬 생성. 값은 시계열별 scale 로 정규화되어 있음."""
    sth = same_type_history(dd)
    k = np.arange(SLOTS)
    rows = []
    for s in series_idx:
        xs = dd.x[s] / dd.scale[s]
        ys = dd.y[s] / dd.scale[s]
        for d in day_idx:
            if d < 7:
                continue
            p1, p7 = xs[d - 1], xs[d - 7]
            hist = [xs[j] for j in sth[d] if j >= 0] or [p7]
            st1 = hist[0]
            stm = np.mean(hist, axis=0)
            f = {
                "series": np.full(SLOTS, s),
                "scale": np.full(SLOTS, dd.scale[s]),
                "slot": k,
                "hour": k // 4,
                "dow": np.full(SLOTS, dd.days[d].dayofweek),
                "offday": np.full(SLOTS, int(dd.offday[d])),
                "holiday": np.full(SLOTS, int(dd.holiday[d])),
                "prev_offday": np.full(SLOTS, int(dd.offday[d - 1])),
                "lag_1d": p1,
                "lag_1d_max": np.full(SLOTS, p1.max()),
                "lag_1d_mean": np.full(SLOTS, p1.mean()),
                "lag_1d_last": np.full(SLOTS, p1[-1]),
                "lag_7d": p7,
                "lag_7d_max": np.full(SLOTS, p7.max()),
                "same_type_1": st1,
                "same_type_1_max": np.full(SLOTS, st1.max()),
                "same_type_mean": stm,
                "same_type_mean_max": np.full(SLOTS, stm.max()),
                "same_type_slot_max": np.max(hist, axis=0),
                "day": np.full(SLOTS, d),
            }
            if with_target:
                f["y"] = ys[d]
            rows.append(pd.DataFrame(f))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


FEATURES = ["series", "scale", "dow", "offday", "holiday", "prev_offday", "lag_1d_max", "lag_1d_mean",
                  "lag_1d_last", "lag_7d_max", "st1_max", "st_mean_max", "st_max_max", "st_min_max",
                  "recent14_max", "recent14_mean_max", "st1_mean"]

FEATURES = ["series", "scale", "slot", "hour", "dow", "offday", "holiday", "prev_offday",
            "lag_1d", "lag_1d_max", "lag_1d_mean", "lag_1d_last", "lag_7d", "lag_7d_max",
            "same_type_1", "same_type_1_max", "same_type_mean", "same_type_mean_max",
            "same_type_slot_max"]
