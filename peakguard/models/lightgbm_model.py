"""[3] 예측모델 — LightGBM

  - 전체 합산 모델 1개 + 채널 공동 모델 1개 (선행 구간 h 를 특징으로 넣어 4구간을 한 모델로 예측)
  - explain(): 1시간 뒤 예측의 SHAP 기여도(kW) — 화면 '영향요인' 탭에 사용
하이퍼파라미터를 바꿔 실험할 때는 PARAMS 만 수정하세요.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import SEED
from .features import FEAT_COLS

PARAMS = dict(learning_rate=0.05, num_leaves=63, min_child_samples=40, subsample=0.8, subsample_freq=1,
              colsample_bytree=0.8, random_state=SEED, verbose=-1)
N_TREES = {"TOTAL": 800, "CHANNEL": 600}
PARAMS_TOTAL = {**PARAMS, "num_leaves": 15}          # 건물 직접 모델: 검증기간 설정값 비교로 정함 (docs/TUNING.md)


def fit(df: pd.DataFrame, channel_scope: bool):
    import lightgbm as lgb
    m = lgb.LGBMRegressor(n_estimators=N_TREES["CHANNEL" if channel_scope else "TOTAL"], **(PARAMS if channel_scope else PARAMS_TOTAL))
    m.fit(df[FEAT_COLS], df["y"])
    return m


def predict(m, df: pd.DataFrame) -> np.ndarray:
    return np.clip(m.predict(df[FEAT_COLS]), 0, None)


def explain(m, df: pd.DataFrame, scale: float) -> np.ndarray:
    """SHAP 기여도 (행 × (특징 + 기준값)), kW 단위."""
    return m.predict(df[FEAT_COLS], pred_contrib=True) * scale
