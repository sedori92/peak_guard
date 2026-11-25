#!/bin/sh
# 맥·리눅스용: 터미널에서  sh 피크가드_시작.sh
cd "$(dirname "$0")"
command -v docker >/dev/null 2>&1 && docker version >/dev/null 2>&1 || { echo "[확인] Docker Desktop 을 설치하고 켠 뒤 다시 실행하세요: https://www.docker.com/products/docker-desktop"; exit 1; }
mkdir -p data/raw data/weather data/incoming
ls data/raw/*.csv >/dev/null 2>&1 || { echo "[확인] data/raw 폴더에 기업 전력 CSV 8개를 넣은 뒤 다시 실행하세요."; exit 1; }
echo "서버 상자를 만들고 켭니다. 처음 한 번은 30분~1시간 걸립니다."
docker compose up -d --build || exit 1
echo "준비될 때까지 기다리는 중... (진행 상황: docker compose logs -f)"
until curl -s -o /dev/null http://localhost:8000/api/health; do
  docker ps --filter name=peakguard --filter status=running -q | grep -q . || { echo "[오류] 서버가 멈췄습니다:"; docker logs --tail 30 peakguard; exit 1; }
  sleep 10
done
echo "준비 완료 — http://localhost:8000"
(open http://localhost:8000 2>/dev/null || xdg-open http://localhost:8000 2>/dev/null) &
