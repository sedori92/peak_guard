@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 피크가드 진행 기록 (닫으려면 Ctrl+C 또는 창 닫기)
docker compose logs -f --tail 50
