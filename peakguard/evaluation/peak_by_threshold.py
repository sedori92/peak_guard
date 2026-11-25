"""[4] 세 번째 화면 '모델성능 및 오경보율 비교' — 피크 기준점별 오분류표

연결 ─ 화면: ③ 맨 위 '피크 기준' 콤보 — 기준별 오분류표 다시 판정
       API : GET /api/performance?thr=
       DB  : st_forecast (읽기)

첫 화면 '피크 기준' 콤보박스의 다섯 가지 기준(최근 30일·전월·당월·7~9월·12~2월 95번째 백분위수)마다
시험기간 예측을 다시 판정합니다. 예측값은 학습 때 DB에 저장한 시험기간 예측(st_forecast)을 그대로 씁니다.

판정 방법 (학습 때 st_peak_eval 과 같음):
  - 발행 시각마다 다음 1시간 4칸 실측 중 하나라도 그 날 기준 이상 → '실제 위험'
  - 같은 4칸 예측 중 하나라도 기준 이상 → '예측 위험'
  - 모든 모델이 예측했고 실측 4칸이 다 있는 발행(공통 표본)만 비교. 그 날 기준을 계산할 자료가 없으면 제외
예측 오차(MAE 등)는 기준과 상관없으므로 바뀌지 않습니다.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from ..config import H, MODELS
from ..models.thresholds import KEYS, options
from .metrics import peak_detection


@lru_cache(maxsize=1)
def compute() -> dict:
    """{기준 key: {"options": 날짜별 기준, "rows": {모델: 오분류표}, "actual_risk": 건수, "n": 발행 수}}"""
    from ..api.common import engine, q
    from ..live.predictor import grid
    g = grid(engine)
    fc = q("SELECT model_name, issued_at, horizon_min, predicted_kw FROM st_forecast WHERE series_id='TOTAL'")
    fc["issued_at"] = pd.to_datetime(fc.issued_at)
    models = [m for m in MODELS if m in set(fc.model_name)]
    P = {m: d.pivot(index="issued_at", columns="horizon_min", values="predicted_kw") for m, d in fc.groupby("model_name")}
    iss = sorted(set.intersection(*[set(P[m].dropna().index) for m in models]))
    iss = pd.DatetimeIndex(iss)
    tot = pd.Series(g.y[0], index=g.times)
    A = np.stack([tot.reindex(iss + pd.Timedelta(minutes=15 * h)).to_numpy() for h in range(1, H + 1)], 1)
    days = sorted({d.date().isoformat() for d in iss})
    opt = {d: options(tot, d) for d in days}
    out = {}
    for k in KEYS:
        thr = np.array([np.nan if opt[d.date().isoformat()][k]["kw"] is None else opt[d.date().isoformat()][k]["kw"] for d in iss])
        ok = ~np.isnan(A).any(1) & ~np.isnan(thr)
        rows = {m: peak_detection(A, P[m].reindex(iss)[[15 * h for h in range(1, H + 1)]].to_numpy(), thr, ok) for m in models}
        pa = (A >= np.nan_to_num(thr, nan=np.inf)[:, None]).any(1)[ok]
        out[k] = {"rows": rows, "actual_risk": int(pa.sum()), "n": int(ok.sum()),
                  "days": {d: opt[d][k]["kw"] for d in days}}
    return out
