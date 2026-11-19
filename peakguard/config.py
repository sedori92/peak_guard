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
TEST_START = "2026-09-28"         # 이 날부터 평가(이전은 학습)
SEED = 42
PEAK_WINDOW_DAYS = 30             # 피크 기준 = 직전 30일
PEAK_PERCENTILE = 95              #           의 95백분위수(P95)
MAPE_MIN_TOTAL_KW = 1.0           # MAPE 계산 제외 기준 (저사용량)
MAPE_MIN_CHANNEL_KW = 0.1

BASE_DAY, BASE_LAST = "전일 동일시각", "최근값 유지"
MODELS = [BASE_DAY, BASE_LAST, "LightGBM", "RandomForest", "BiLSTM"]
DISPLAY = {BASE_DAY: BASE_DAY, BASE_LAST: BASE_LAST, "LightGBM": "LightGBM",
           "RandomForest": "Random Forest", "BiLSTM": "Bi-LSTM"}
DEFAULT_MODEL = "LightGBM"
