"""[5] API 서버 — FastAPI 앱 (조회 API + 관리자 화면 + CSV 업로드 + 보고서)

    python run.py 5                         # http://localhost:8000  (API 문서: /docs)
    uvicorn peakguard.api.app:app --port 8000

라우터별 파일
    api/dashboard.py  화면 조회 API (/api/days, /api/replay, /api/channel, /api/forecast, /api/eval)
    api/upload.py     [11] CSV 업로드 (/api/upload)
    아래 /api/report  [10] 주간·월간 보고서 텍스트
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ..config import WEB_DIR
from . import dashboard, upload

app = FastAPI(title="PeakGuard API", version="2.0",
              description="피크가드 — 15분 수요전력 다음 1시간 예측 조회·업로드·보고서 API")
app.include_router(dashboard.router)
app.include_router(upload.router)
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
