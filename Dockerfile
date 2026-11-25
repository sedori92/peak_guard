# 피크가드 서버 상자(Docker image) — 관리자 PC·서버에 파이썬을 따로 설치하지 않고 실행
#   만들기·실행: docker compose up -d --build   (윈도우는 '피크가드_시작.bat' 두 번 클릭)
#   안에서 하는 일: docker/entrypoint.sh (처음 한 번 DB 구축·모델 학습 → 서버 시작)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DATABASE_URL=sqlite:////app/state/peakguard.db TZ=Asia/Seoul
WORKDIR /app

# 딥러닝(PyTorch)은 그래픽카드 없는 CPU 판으로 (용량 약 1/5)
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY run.py ./
COPY peakguard ./peakguard
COPY web ./web
COPY db ./db
COPY tests ./tests
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /app/state /app/models /app/reports /app/data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60m CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1
ENTRYPOINT ["/entrypoint.sh"]
