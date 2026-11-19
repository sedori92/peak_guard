"""[5] API 서버 — FastAPI 앱 (조회 API + 관리자 화면 + CSV 업로드 + 보고서)

    python run.py 5                         # http://localhost:8000  (API 문서: /docs)
    uvicorn peakguard.api.app:app --port 8000

라우터별 파일
    api/dashboard.py  화면 조회 API (/api/days, /api/replay, /api/channel, /api/forecast, /api/eval)
    api/upload.py     [11] CSV 업로드 (/api/upload)
    api/compare.py    보조화면 채널합산(Random Forest) 비교 (/api/sumcompare)
    아래 /api/report       [10] 주간·월간 보고서 텍스트
         /api/report/peaks [10] 최고관리자 보고서 미리보기 (일·주·월·연 피크 변동기록 + 기여도 Top10)
         /api/report/xlsx  [10] 같은 내용 스프레드시트 내려받기
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ..config import WEB_DIR
from . import compare, dashboard, upload

app = FastAPI(title="PeakGuard API", version="2.0",
              description="피크가드 — 15분 수요전력 다음 1시간 예측 조회·업로드·보고서 API")
app.include_router(dashboard.router)
app.include_router(upload.router)
app.include_router(compare.router)
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


PERIOD = "^(day|week|month|year)$"


@app.get("/api/report/peaks", tags=["report"])
def report_peaks(period: str = Query("month", pattern=PERIOD), date: str = Query(..., description="YYYY-MM-DD"),
                 series: bool = False):
    """[10] 최고관리자 보고서 미리보기: 피크 변동기록(일·주·월·연) + 피크 시각 기여도 Top10."""
    from ..reports.executive import build
    r = build(period, date)
    if not series:
        r.pop("series")
    return r


@app.get("/api/report/xlsx", tags=["report"])
def report_xlsx(period: str = Query("month", pattern=PERIOD), date: str = Query(..., description="YYYY-MM-DD")):
    """[10] 최고관리자 보고서 스프레드시트(.xlsx) 내려받기."""
    from urllib.parse import quote

    from fastapi.responses import Response

    from ..reports.executive import build, filename, to_xlsx
    r = build(period, date)
    if not r["records"]:
        raise HTTPException(404, f"{r['start']}~{r['end']} 기간에 96개 채널이 모두 수신된 데이터가 없습니다.")
    return Response(to_xlsx(r), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename(r))}"})


@app.get("/api/health", tags=["report"])
def health():
    from .common import q
    n = int(q("SELECT COUNT(*) AS n FROM measurement").n[0])
    return {"status": "ok", "measurements": n}
