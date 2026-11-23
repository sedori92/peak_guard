"""[3] 예측모델 — XGBoost (eXtreme Gradient Boosting)

화면 '예측모델 선택' 콤보버튼의 'XGBoost' 항목입니다.
  - LightGBM 과 같은 계열(나무를 하나씩 더해 가며 앞 나무의 오차를 고치는 방식)
  - LightGBM·RandomForest 와 똑같은 입력 특징(features.py)과 같은 학습기간을 씁니다.
  - 건물 직접 모델 1개 + 채널 공동 모델 1개 (선행시간 15·30·45·60분은 '선행시간' 특징으로 한 모델에서 처리)
하이퍼파라미터를 바꿔 실험할 때는 PARAMS 만 수정하세요.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import SEED
from .features import FEAT_COLS

PARAMS = dict(learning_rate=0.05, max_depth=7, min_child_weight=20, subsample=0.8, colsample_bytree=0.8,
              tree_method="hist", random_state=SEED, n_jobs=-1)
N_TREES = {"TOTAL": 800, "CHANNEL": 600}


def fit(df: pd.DataFrame, channel_scope: bool):
    """학습: 건물 직접(channel_scope=False) 또는 채널 공동(True) 모델."""
    from xgboost import XGBRegressor
    m = XGBRegressor(n_estimators=N_TREES["CHANNEL" if channel_scope else "TOTAL"], **PARAMS)
    m.fit(df[FEAT_COLS], df["y"])
    return m


def predict(m, df: pd.DataFrame) -> np.ndarray:
    """예측 (음수는 0으로)."""
    return np.clip(m.predict(df[FEAT_COLS]), 0, None)
