"""[3] 예측모델 — 기준모델 2종 (모델이 이보다 나아야 의미가 있음, FR-AI-003)

  - 전일 동일시각 : 어제 같은 시각 값을 그대로 사용
  - 최근값 유지   : 발행 시점의 최신 수요전력을 1시간 동안 유지
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import BASE_DAY, BASE_LAST

COLUMN = {BASE_DAY: "lag_day", BASE_LAST: "y0"}


def predict(df: pd.DataFrame, name: str) -> np.ndarray:
    """features() 결과 → 정규화된 예측값 (scale 을 곱하면 kW)."""
    return df[COLUMN[name]].to_numpy()
