"""[3] 예측모델 — RandomForest

연결 ─ 화면: ① 예측모델 콤보 'Random Forest', ③ Random Forest 줄
       API : —
       DB  : — (models/st_RandomForest.joblib 파일)

  - LightGBM 과 같은 특징 사용. 결측은 -1 로 채움
  - 채널 공동 모델은 학습 행이 200만 개가 넘어 40만 개를 표본 추출해 학습 (속도·용량)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import SEED
from .features import FEAT_COLS

MAX_ROWS = 400_000


def fit(df: pd.DataFrame, channel_scope: bool):
    from sklearn.ensemble import RandomForestRegressor
    if channel_scope and len(df) > MAX_ROWS:
        df = df.sample(MAX_ROWS, random_state=SEED)
    # 건물 직접 모델 설정값은 검증기간 비교로 정함 (docs/TUNING.md), 채널 공동 모델은 기존 값
    m = RandomForestRegressor(n_estimators=150, min_samples_leaf=10 if channel_scope else 20,
                              max_features=0.6 if channel_scope else 0.33, n_jobs=-1, random_state=SEED)
    m.fit(df[FEAT_COLS].fillna(-1), df["y"])
    return m


def predict(m, df: pd.DataFrame) -> np.ndarray:
    return np.clip(m.predict(df[FEAT_COLS].fillna(-1)), 0, None)
