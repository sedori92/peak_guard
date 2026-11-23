"""[5] API 서버 — FastAPI 앱 (조회 API + 관리자 화면 + CSV 업로드 + 보고서)

    python run.py 5                         # http://localhost:8000  (API 문서: /docs)
    uvicorn peakguard.api.app:app --port 8000

화면별 파일 (v0.2)
  첫 번째 화면 '예측 및 경보'
    api/dashboard.py    시연 재생 (/api/days, /api/replay, /api/channel, /api/forecast, /api/eval)
    api/live.py         [실시간 연결] 버튼 (/api/live/sync, /api/live/predict, /api/live/status)
    api/alerts.py       '경보발령 선택' 상자 (/api/contacts, /api/alerts)
  두 번째 화면 '피크이력 및 영향도 분석'
    api/history.py      최고관리자 보고서·배치도·영향요인 (/api/report/peaks, /api/report/xlsx, /api/snapshot, /api/factors)
  세 번째 화면 '모델성능 및 오경보율 비교'
    api/performance.py  (/api/performance)
  공통
    api/upload.py       [11] CSV 업로드 (/api/upload)
    아래 /api/report     [10] 주간·월간 보고서 텍스트
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ..config import WEB_DIR
from ..database.connection import create_schema
from .common import engine
from . import alerts, dashboard, history, live, performance, upload

app = FastAPI(title="PeakGuard API", version="2.0",
              description="피크가드 — 15분 수요전력 다음 1시간 예측 조회·업로드·보고서 API")
app.include_router(dashboard.router)
app.include_router(upload.router)
for r in (live.router, alerts.router, history.router, performance.router):
    app.include_router(r)

# 서버를 켤 때 운영용 장부(입주자 연락처·경보 발령 기록·실시간 예측 기록)가 없으면 만듭니다 (있으면 그대로 둠).
create_schema(engine)


app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/report", response_class=PlainTextResponse, tags=["report"])
def report(period: str = Query("week", pattern="^(week|month)$"), date: str = Query(..., description="YYYY-MM-DD")):
    from ..reports.periodic import build
    try:
        txt, _ = build(period, date)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return txt


@app.get("/api/health", tags=["report"])
def health():
    from .common import q
    n = int(q("SELECT COUNT(*) AS n FROM measurement").n[0])
    return {"status": "ok", "measurements": n}
