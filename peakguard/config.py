"""[1] 환경설정 — 경로·DB 주소·공통 상수를 한곳에서 관리

다른 모듈은 모두 여기 값을 가져다 씁니다. 경로나 기준을 바꿀 때는 이 파일만 고치면 됩니다.
DB 주소는 .env 파일의 DATABASE_URL 로 바꿀 수 있습니다 (없으면 SQLite 파일 peakguard.db).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent       # 저장소 최상위 폴더
load_dotenv(ROOT / ".env")

# ---- 경로 ---------------------------------------------------------------
DATA_RAW = ROOT / "data" / "raw"            # 기업 제공 전력 CSV 8개
DATA_WEATHER = ROOT / "data" / "weather"    # 기상청 ASOS 분자료 CSV
DATA_CALENDAR = ROOT / "data" / "calendar"  # 공휴일 달력 (collect/calendar_kr.py 가 생성)
DATA_REVIEWS = ROOT / "data" / "reviews"    # 앱 리뷰 (collect/reviews.py 가 생성)
MODEL_DIR = ROOT / "models"                 # 학습된 모델 파일
REPORT_DIR = ROOT / "reports"               # 평가 그래프·보고서 출력
WEB_DIR = ROOT / "web"                      # 관리자 화면 (Front-end)

# ---- DB ----------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{ROOT / 'peakguard.db'}"

# ---- 시간 단위 -----------------------------------------------------------
TZ = "Asia/Seoul"
DAY = 96                  # 하루 15분 칸 수
WEEK = 7 * DAY
H = 4                     # 예측 구간 수 (15·30·45·60분)

# ---- 건물 구성 ------------------------------------------------------------
ROOM_METERS = ("M002", "M004", "M006", "M008")             # 호실별 계량기 (나머지는 공용)
FLOORS = {"M002": 1, "M004": 2, "M006": 3, "M008": 4}      # 호실 계량기 → 층
COMMON_METERS = {"M001": 1, "M003": 2, "M005": 3, "M007": 4}

# ---- 예측·평가 ------------------------------------------------------------
# 기간 나누기 (시계열 순서대로)
#   학습 ~9/14 · 검증 9/15~10/6 · (10/6 15시~10/20 15시 통신 교체로 수집 중단) · 시험 10/20 15:15~11/2 16:30
TRAIN_END = "2026-09-15 00:00"    # 이 시각 전까지의 정답만 학습에 사용
VAL_START, VAL_END = "2026-09-15 00:00", "2026-10-06 23:45"
TEST_START, TEST_END = "2026-10-20 15:15", "2026-11-02 16:30"   # 발행 시각 기준 (공통 표본 1,155 발행)
SEED = 42
PEAK_WINDOW_DAYS = 30             # 피크 기준 = 직전 30일
ALERT_RATIO = 0.95                # 위험 판정: 다음 1시간 예측이 피크 기준의 95% 이상이면 '위험', 아니면 '정상' (2단계)
PEAK_PERCENTILE = 95              #           의 95백분위수(P95)
MAPE_MIN_TOTAL_KW = 1.0           # MAPE 계산 제외 기준 (저사용량)
MAPE_MIN_CHANNEL_KW = 0.1

BASE_DAY, BASE_LAST = "전일 동일시각", "최근값 유지"      # 최근값 유지는 v0.2 화면에서 쓰지 않음

# ---- 화면 왼쪽 위 콤보버튼: 예측 방식 × 예측모델 ----------------------------
#   채널별 합산 후 예측 = 96개 채널을 같은 시각끼리 먼저 더한 '건물 합계' 한 줄을 모델이 바로 예측 (기업 권장)
#   채널별 예측 후 합산 = 활성 채널 61개를 각각 예측한 뒤 같은 시각끼리 더해 건물 합계를 만듦
METHODS = {"direct": "채널별 합산 후 예측", "sum": "채널별 예측 후 합산"}
FAMILIES = [  # (모델 이름, 화면 이름, 합산 후 예측 결과 이름, 예측 후 합산 결과 이름)
    ("LightGBM", "LightGBM", "LightGBM", "LGBM_SUM"),
    ("XGBoost", "XGBoost", "XGBoost", "XGB_SUM"),
    ("RandomForest", "Random Forest", "RandomForest", "RF_SUM"),
    ("BiLSTM", "Bi-LSTM", "BiLSTM", "LSTM_SUM"),
]
SUM_BASE = {f[3]: f[2] for f in FAMILIES}          # 예측 후 합산 결과 → 채널 예측을 만든 모델
MODELS = [BASE_DAY] + [f[2] for f in FAMILIES] + [f[3] for f in FAMILIES]
DISPLAY = {BASE_DAY: BASE_DAY, BASE_LAST: BASE_LAST}
DISPLAY |= {f[2]: f[1] for f in FAMILIES} | {f[3]: f[1] for f in FAMILIES}
METHOD = {BASE_DAY: "기준"} | {f[2]: "direct" for f in FAMILIES} | {f[3]: "sum" for f in FAMILIES}
DEFAULT_METHOD = "direct"
DEFAULT_MODEL = "BiLSTM"                # 화면 첫 값: 채널별 합산 후 예측 · Bi-LSTM · 시험기간 마지막 날
DEFAULT_DATE = "2026-11-02"
