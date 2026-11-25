@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 피크가드 시작
echo ==================== 피크가드 시작 ====================
docker version >nul 2>&1
if errorlevel 1 (
  echo [확인] Docker Desktop 이 설치되어 있지 않거나 꺼져 있습니다.
  echo   1^) https://www.docker.com/products/docker-desktop 에서 내려받아 설치
  echo   2^) 시작 메뉴에서 Docker Desktop 을 켜고, 아래쪽에 "Engine running" 이 보이면
  echo      이 파일을 다시 두 번 누르세요.
  pause
  exit /b 1
)
if not exist "data\raw" mkdir "data\raw"
if not exist "data\weather" mkdir "data\weather"
if not exist "data\incoming" mkdir "data\incoming"
dir /b "data\raw\*.csv" >nul 2>&1
if errorlevel 1 (
  echo [확인] data\raw 폴더에 기업 전력 CSV 8개를 넣은 뒤 다시 실행하세요.
  echo        기상청 CSV 는 data\weather 폴더에 넣습니다. 지금 data 폴더를 엽니다.
  start "" explorer "%~dp0data"
  pause
  exit /b 1
)
echo 서버 상자를 만들고 켭니다.
echo 처음 한 번은 프로그램 내려받기와 모델 학습 때문에 30분~1시간 걸립니다. 다음부터는 1분 안에 켜집니다.
docker compose up -d --build
if errorlevel 1 (
  echo [오류] 위 메시지를 캡처해 담당자에게 보내 주세요.
  pause
  exit /b 1
)
echo.
echo 준비될 때까지 기다리는 중... 진행 상황은 '피크가드_기록보기.bat' 로 볼 수 있습니다.
:wait
curl -s -o nul http://localhost:8000/api/health && goto ready
docker ps --filter "name=peakguard" --filter "status=running" -q | findstr . >nul || goto failed
timeout /t 10 /nobreak >nul
goto wait
:ready
echo.
echo 준비 완료 — 브라우저에서 http://localhost:8000 을 엽니다.
start "" http://localhost:8000
timeout /t 5 >nul
exit /b 0
:failed
echo.
echo [오류] 서버가 멈췄습니다. 마지막 기록:
docker logs --tail 30 peakguard
echo 이 화면을 캡처해 담당자에게 보내 주세요.
pause
exit /b 1
