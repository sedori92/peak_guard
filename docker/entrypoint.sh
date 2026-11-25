#!/bin/sh
# 피크가드 상자가 켜질 때 하는 일 (관리자는 신경 쓰지 않아도 됨)
#   1) DB 가 없으면 data/ 폴더의 CSV 로 DB 구축        (python run.py 2, 약 1분)
#   2) 학습된 모델이 없으면 학습 + 기상 비교 학습       (python run.py 3, 처음 한 번 약 20~30분)
#   3) 서버 시작 → 브라우저에서 http://localhost:8000
#   DB 는 state/, 모델은 models/ 폴더에 남으므로 다음부터는 1·2 를 건너뛰고 바로 서버가 뜸
set -e
cd /app
mkdir -p /app/state /app/models /app/reports /app/data/raw /app/data/weather /app/data/incoming

n=$(ls /app/data/raw/*.csv 2>/dev/null | wc -l)
if [ ! -f /app/state/.db_ready ]; then
  if [ "$n" -lt 1 ]; then
    echo "[피크가드] data/raw 폴더에 기업 전력 CSV 가 없습니다. 파일을 넣고 다시 시작하세요."
    exit 1
  fi
  echo "[피크가드] 1/3 DB 만들기 (전력 CSV ${n}개)"
  python run.py 2
  touch /app/state/.db_ready
fi
if [ ! -f /app/models/st_BiLSTM.pt ]; then
  echo "[피크가드] 2/3 예측모델 학습 — 처음 한 번만, 약 20~30분 걸립니다"
  python run.py 3
  echo "[피크가드] 2/3 기상 포함/미포함 비교 학습 (세 번째 화면 '기상 비교')"
  python -m peakguard.models.train --compare || echo "[피크가드] 기상 비교 학습 실패 — 그 화면만 비어 보이고 나머지는 정상"
fi
echo "[피크가드] 3/3 서버 시작 — 브라우저에서 http://localhost:8000"
exec python -m uvicorn peakguard.api.app:app --host 0.0.0.0 --port 8000
