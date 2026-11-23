"""피크 기준점 고르기 — 첫 번째 화면 상태 띠 오른쪽 '피크 기준' 콤보박스

관리자가 아래 다섯 가지 중 하나를 피크 기준으로 고릅니다. 모두 건물 합계(96개 채널 단순합) 15분 수요전력의
95번째 백분위수(P95)이며, 기간만 다릅니다. 예측하는 날(기준일) '이전' 실측만 씁니다(미래 값 사용 안 함).

  recent30   최근 30일          기준일 직전 30일 (기본값, 세 번째 화면 성능평가도 이 기준)
  prev_month 전월               기준일이 속한 달의 바로 앞 달 1일~말일
  this_month 당월               기준일이 속한 달 1일 ~ 기준일 전날
  summer     7~9월              가장 최근 여름(7·8·9월) — 기준일이 그 안이면 기준일 전날까지
  winter     12~2월             가장 최근 겨울(12·1·2월) — 기준일이 그 안이면 기준일 전날까지
  (한전 요금적용전력이 여름·겨울·당월 최대수요전력을 보는 것과 맞추려고 여름·겨울을 따로 둠)

자료가 7일치(당월은 1일치)보다 적으면 그 기준은 '자료 부족'으로 표시하고 고를 수 없게 합니다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import DAY, PEAK_PERCENTILE, PEAK_WINDOW_DAYS

KEYS = ["recent30", "prev_month", "this_month", "summer", "winter"]
NAMES = {"recent30": "최근 30일", "prev_month": "전월", "this_month": "당월", "summer": "7~9월", "winter": "12~2월"}


def _range(key: str, day: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """기준 기간 [시작, 끝) — 끝은 기준일을 넘지 않음."""
    m1 = day.replace(day=1)
    if key == "recent30":
        return day - pd.Timedelta(days=PEAK_WINDOW_DAYS), day
    if key == "prev_month":
        return m1 - pd.DateOffset(months=1), m1
    if key == "this_month":
        return m1, day
    if key == "summer":
        y = day.year if day >= pd.Timestamp(day.year, 7, 1) else day.year - 1
        return pd.Timestamp(y, 7, 1), min(pd.Timestamp(y, 10, 1), day)
    # winter: 12~2월
    y = day.year if day.month <= 2 else (day.year + 1 if day.month == 12 else day.year)
    return pd.Timestamp(y - 1, 12, 1), min(pd.Timestamp(y, 3, 1), day)


def options(total: pd.Series, day) -> dict:
    """total = 건물 합계 15분 수요전력 (96개 채널이 모두 들어온 칸만, 아니면 NaN). day = 기준일."""
    day = pd.Timestamp(day).normalize()
    out = {}
    for k in KEYS:
        a, b = _range(k, day)
        w = total[(total.index >= a) & (total.index < b)].dropna()
        need = DAY if k == "this_month" else 7 * DAY
        ok = len(w) >= need
        last = b - pd.Timedelta(days=1)
        out[k] = {"key": k, "name": NAMES[k], "kw": round(float(np.percentile(w, PEAK_PERCENTILE)), 2) if ok else None,
                  "period": f"{a:%Y-%m-%d} ~ {last:%Y-%m-%d}" if b > a else "—", "days": round(len(w) / DAY, 1),
                  "note": None if ok else ("자료 없음 (2026-07-01부터 수집)" if len(w) == 0 else f"자료 부족 ({len(w) / DAY:.1f}일치)")}
    return out
