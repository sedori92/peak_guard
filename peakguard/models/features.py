"""[3] 예측모델 — 트리 모델(LightGBM·RandomForest)용 입력 특징

발행 시각 t 에 알 수 있는 값만 사용 (미래 실측 사용 금지, 요구사항 NFR-RP-001).
한 행 = (시계열, 발행 시각, 선행 구간 h). 값은 시계열별 scale 로 나눠 정규화.
특징을 추가·변경하면 FEATURES 에 이름(화면 표시용)도 함께 넣으세요.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import DAY, H, WEEK
from .grid import Grid, take as _take

# 특징 이름 → 화면(영향요인) 표시 이름
FEATURES = {
    "y0": "현재 수요전력", "p0": "현재전력 합계(순간값)", "y1": "15분 전 수요", "y2": "30분 전 수요",
    "y3": "45분 전 수요", "y4": "1시간 전 같은 위치", "y8": "2시간 전", "d1": "15분 변화량",
    "max1h": "최근 1시간 최대", "mean4h": "최근 4시간 평균", "lag_day": "전일 같은 시각",
    "lag_week": "전주 같은 시각", "slot": "예측 시각(시간대)", "dow": "요일", "offday": "주말·공휴일",
    "h": "선행시간", "series": "채널", "scale": "채널 규모",
}
FEAT_COLS = list(FEATURES)


def features(g: Grid, s: int, issues: np.ndarray, scale: float) -> pd.DataFrame:
    """시계열 s, 발행 시각 인덱스 issues → (발행 × 4구간) 행. 값은 scale 로 정규화."""
    yi, p, y = g.yi[s] / scale, g.p[s] / scale, g.y[s] / scale
    rows = []
    base = {
        "y0": _take(yi, issues), "p0": _take(p, issues),
        "y1": _take(yi, issues - 1), "y2": _take(yi, issues - 2), "y3": _take(yi, issues - 3),
        "y4": _take(yi, issues - 4), "y8": _take(yi, issues - 8),
    }
    base["d1"] = base["y0"] - base["y1"]
    base["max1h"] = np.nanmax(np.vstack([base[k] for k in ("y0", "y1", "y2", "y3")]), axis=0)
    base["mean4h"] = np.nanmean(np.vstack([_take(yi, issues - k) for k in range(16)]), axis=0)
    for h in range(1, H + 1):
        tgt = issues + h
        f = dict(base)
        f.update({
            "lag_day": _take(yi, tgt - DAY), "lag_week": _take(yi, tgt - WEEK),
            "slot": tgt % DAY, "dow": _take(g.times.dayofweek.to_numpy().astype(float), tgt),
            "offday": _take(g.offday.astype(float), tgt), "h": np.full(len(issues), h),
            "series": np.full(len(issues), s), "scale": np.full(len(issues), scale),
            "issue": issues, "y": _take(y, tgt),
        })
        rows.append(pd.DataFrame(f))
    return pd.concat(rows, ignore_index=True)
