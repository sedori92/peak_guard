@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 피크가드 서버를 끕니다. (DB·모델은 그대로 남아 다음 시작 때 바로 켜집니다)
docker compose stop
pause
