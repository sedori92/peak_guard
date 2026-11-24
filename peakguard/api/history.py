"""[5] API — 두 번째 화면 '피크이력 및 영향도 분석'

  GET /api/report/peaks?period=day|week|month|year&date=   상단 '최고관리자 보고서' (실측만, 피크 변동기록 + 기여도 Top 10)
  GET /api/report/xlsx?period=&date=                       같은 내용 스프레드시트(.xlsx) 내려받기
  GET /api/snapshot?time=YYYY-MM-DD HH:MM                  하단 '층별 호실 배치도' — 그 시각 채널별 실측과 비중
  GET /api/rise?time=                                      상단 '피크 시각 평소 대비 증가 Top 10' (실측 − 평소)
  GET /api/influence?time=&series=TOTAL|M008-04            하단 '전체 및 채널별 영향도' — 그 시각 실측이 평소보다 높아진 몫 (실측만)
  GET /api/factors?time=&series=                           (참고용) 1시간 전 LightGBM 예측의 SHAP — 화면에서는 쓰지 않음
"""
from __future__ import annotations

from urllib.parse import quote

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from ..config import FLOORS, H
from ..models.features import FEAT_COLS, FEATURES, features
from .alerts import _cid, label
from .common import engine, num, q

router = APIRouter(prefix="/api", tags=["history"])
PERIOD = "^(day|week|month|year)$"


@router.get("/report/peaks")
def report_peaks(period: str = Query("week", pattern=PERIOD), date: str = Query(..., description="YYYY-MM-DD"),
                 series: bool = False):
    """최고관리자 보고서 미리보기 (실측만 사용)."""
    from ..reports.executive import build
    r = build(period, date)
    if not series:
        r.pop("series")
    return r


@router.get("/report/xlsx")
def report_xlsx(period: str = Query("week", pattern=PERIOD), date: str = Query(..., description="YYYY-MM-DD")):
    """최고관리자 보고서 스프레드시트."""
    from ..reports.executive import build, filename, to_xlsx
    r = build(period, date)
    if not r["records"]:
        raise HTTPException(404, f"{r['start']}~{r['end']} 기간에 96개 채널이 모두 수신된 데이터가 없습니다.")
    return Response(to_xlsx(r), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename(r))}"})


@router.get("/report/preview")
def report_preview(period: str = Query("week", pattern=PERIOD), date: str = Query(..., description="YYYY-MM-DD"),
                   max_rows: int = 300):
    """[스프레드시트 보기] 버튼: 내려받을 .xlsx 를 그대로 읽어 시트별 표로 돌려줌 (화면 미리보기·출력용)."""
    import io

    from openpyxl import load_workbook

    from ..reports.executive import build, filename, to_xlsx
    r = build(period, date)
    if not r["records"]:
        raise HTTPException(404, f"{r['start']}~{r['end']} 기간에 96개 채널이 모두 수신된 데이터가 없습니다.")
    wb = load_workbook(io.BytesIO(to_xlsx(r)), data_only=True)
    sheets = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            if all(v is None for v in row):
                continue
            rows.append(["" if v is None else (round(v, 2) if isinstance(v, float) else str(v)) for v in row])
        sheets.append({"name": ws.title, "rows": rows[:max_rows], "total_rows": len(rows)})
    return {"title": f"피크가드 {r['title']} 피크 보고서", "period": f"{r['start']} ~ {r['end']}",
            "filename": filename(r), "sheets": sheets}


@router.get("/printer")
def printer():
    """[출력] 버튼: 이 서버 컴퓨터에 연결된 프린터 확인 (Windows: Get-Printer, macOS·리눅스: lpstat)."""
    import platform
    import shutil
    import subprocess
    names, default = [], None
    try:
        if platform.system() == "Windows":
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "Get-Printer | Where-Object {$_.PrinterStatus -ne 'Offline'} | Select-Object -ExpandProperty Name"],
                                 capture_output=True, text=True, timeout=8).stdout
            names = [x.strip() for x in out.splitlines() if x.strip() and "PDF" not in x and "XPS" not in x and "OneNote" not in x and "Fax" not in x]
        elif shutil.which("lpstat"):
            out = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, timeout=8).stdout
            names = [ln.split()[1] for ln in out.splitlines() if ln.startswith("printer ") and "disabled" not in ln]
            d = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, timeout=8).stdout
            default = d.split(":")[-1].strip() if ":" in d else None
    except Exception:            # 확인 실패 = 연결 안 됨으로 처리
        names = []
    return {"available": bool(names), "printers": names, "default": default}


@router.get("/snapshot")
def snapshot(time: str = Query(..., description="YYYY-MM-DD HH:MM")):
    """그 15분 칸의 96개 채널 실측과 건물·호실 대비 비중 (배치도용)."""
    t = pd.Timestamp(time)
    d = q("""SELECT m.channel_id, c.meter_id, m.demand_kw FROM measurement m JOIN channel c ON c.channel_id=m.channel_id
             WHERE m.measured_at = :t""", t=f"{t:%Y-%m-%d %H:%M:%S}")
    if d.empty:
        raise HTTPException(404, f"{time} 실측이 없습니다.")
    tot = float(d.demand_kw.sum())
    rooms = d[d.meter_id.isin(FLOORS)]
    rsum = float(rooms.demand_kw.sum())
    out = []
    for r in d.sort_values("demand_kw", ascending=False).itertuples():
        room = r.meter_id in FLOORS
        out.append({"channel": r.channel_id.replace("-CH", "-"), "label": label(r.channel_id),
                    "kind": "호실" if room else "공용", "floor": FLOORS.get(r.meter_id) or int(label(r.channel_id)[0]),
                    "kw": num(r.demand_kw, 3), "share_building": num(r.demand_kw / tot * 100 if tot else None, 1),
                    "share_rooms": num(r.demand_kw / rsum * 100 if room and rsum else None, 1)})
    return {"time": f"{t:%Y-%m-%d %H:%M}", "total_kw": num(tot, 2), "rooms_kw": num(rsum, 2),
            "common_kw": num(tot - rsum, 2), "n_channels": int(len(d)), "channels": out}


@router.get("/rise")
def rise(time: str = Query(..., description="피크 시각 YYYY-MM-DD HH:MM")):
    """'피크 시각 평소 대비 증가 Top 10' — 채널별 (그 시각 실측 − 같은 요일·시각 직전 4주 평균). 실측만."""
    from ..reports.influence import rise_top
    try:
        return rise_top(time)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/influence")
def influence(time: str = Query(..., description="피크 시각 YYYY-MM-DD HH:MM"),
              series: str = Query("TOTAL", description="TOTAL(건물 전체) 또는 채널 (예: M008-04)")):
    """'전체 및 채널별 영향도' — 그 시각 실측이 평소보다 높아진 것을 나눠 보여 줌 (폭포 그래프, 실측만).
    전체: 평소 건물 합계 + 채널별 증가 = 실측 / 채널: 최근 4주 같은 시각 평균 + 요일·최근 1주·당일 차이 = 실측"""
    from ..reports.influence import influence as calc
    try:
        return calc(time, series)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/factors", include_in_schema=True)
def factors(time: str = Query(..., description="피크 시각 YYYY-MM-DD HH:MM"),
            series: str = Query("TOTAL", description="TOTAL(건물 전체) 또는 채널 (예: M008-04)")):
    """(참고용, 화면에서는 쓰지 않음) 그 시각을 1시간 전에 예측할 때 입력값별 기여(kW). LightGBM SHAP."""
    from ..live.predictor import _load, grid
    g = grid(engine)
    t = pd.Timestamp(time)
    j = int(np.searchsorted(g.times, t))
    if j >= len(g.times) or g.times[j] != t:
        raise HTTPException(404, f"{time} 은 15분 격자에 없습니다.")
    sid = "TOTAL" if series.upper() == "TOTAL" else _cid(series)
    if sid not in g.series:
        raise HTTPException(404, f"{series} 는 예측 대상 채널이 아닙니다 (전 기간 0kW 채널은 제외).")
    s = g.series.index(sid)
    m = _load("LightGBM")
    issue = j - H                                            # 1시간 전 발행
    if issue < 0 or np.isnan(g.y[0][issue]):
        raise HTTPException(404, f"{g.times[max(issue, 0)]:%m-%d %H:%M} 실측이 없어(수집 중단 구간) 1시간 전 예측을 할 수 없었던 시각입니다.")
    x = features(g, s, np.array([issue]), m["scale"][s])
    x = x[x["h"] == H]
    model = m["TOTAL"] if s == 0 else m["CHANNEL"]
    contrib = model.predict(x[FEAT_COLS], pred_contrib=True)[0] * m["scale"][s]
    pred = float(contrib.sum())
    items = sorted(((FEATURES[c], float(v)) for c, v in zip(FEAT_COLS, contrib[:-1]) if c not in ("series", "scale")),
                   key=lambda kv: -abs(kv[1]))
    other = float(contrib[:-1].sum()) - sum(v for _, v in items)
    actual = g.y[s][j]
    return {"time": f"{t:%Y-%m-%d %H:%M}", "issued_at": f"{g.times[issue]:%Y-%m-%d %H:%M}", "series": sid.replace("-CH", "-"),
            "label": "건물 전체" if s == 0 else label(sid), "model": "LightGBM",
            "base_kw": num(float(contrib[-1]), 3), "pred_kw": num(max(pred, 0.0), 3),
            "actual_kw": None if np.isnan(actual) else num(float(actual), 3),
            "items": [{"name": n, "kw": num(v, 3)} for n, v in items] + ([{"name": "채널 특성", "kw": num(other, 3)}] if abs(other) > 1e-4 else [])}
