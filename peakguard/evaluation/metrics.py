"""[4] 평가 — 오차 지표와 피크 사전 감지 지표 (요구사항 FR-EV-002·003)

  MAE  = Σ|y−ŷ|/n,  RMSE = √(Σ(y−ŷ)²/n),  MAPE = 100×Σ(|y−ŷ|/|y|)/m (저사용량 제외)
  피크 감지: 발행 시점에 '다음 1시간 중 하나라도 기준 이상'을 맞혔는지 (정밀도·재현율·F1)
"""
from __future__ import annotations

import numpy as np


def metrics(a: np.ndarray, p: np.ndarray, low: float) -> dict:
    ok = ~np.isnan(a) & ~np.isnan(p)
    a, p = a[ok], p[ok]
    if len(a) == 0:
        return dict(mae=None, rmse=None, mape=None, n=0, n_mape=0)
    m = a >= low                                       # MAPE 는 저사용량 제외 (FR-EV-003)
    return dict(mae=float(np.mean(np.abs(a - p))), rmse=float(np.sqrt(np.mean((a - p) ** 2))),
                mape=float(np.mean(np.abs(a[m] - p[m]) / a[m]) * 100) if m.any() else None,
                n=int(len(a)), n_mape=int(m.sum()))


def peak_detection(actual: np.ndarray, pred: np.ndarray, thr: np.ndarray, ok: np.ndarray) -> dict:
    """actual·pred: (발행, 4) kW, thr: (발행,) 기준값, ok: 평가에 쓸 발행."""
    pa = (actual >= thr[:, None]).any(1)[ok]
    pp = (pred >= thr[:, None]).any(1)[ok]
    tp, fp, fn = int((pa & pp).sum()), int((~pa & pp).sum()), int((pa & ~pp).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return dict(precision=prec, recall=rec, f1=2 * prec * rec / (prec + rec) if prec + rec else 0.0,
                tp=tp, fp=fp, fn=fn, n=int(ok.sum()))
