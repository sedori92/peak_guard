"""[5] API — 두 번째 화면 '피크이력 및 영향도 분석'

  GET /api/report/peaks?period=day|week|month|year&date=   상단 '최고관리자 보고서' (실측만, 피크 변동기록 + 기여도 Top 10)
  GET /api/report/xlsx?period=&date=                       같은 내용 스프레드시트(.xlsx) 내려받기
  GET /api/snapshot?time=YYYY-MM-DD HH:MM                  하단 '층별 호실 배치도' — 그 시각 채널별 실측과 비중
  GET /api/factors?time=&series=TOTAL|M008-04              하단 '전체 및 채널별 영향요인' — 그 시각을 1시간 전에 예측할 때
                                                           각 입력값이 예측을 얼마나 올리고 내렸는지 (LightGBM, SHAP)
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


@router.get("/factors")
def factors(time: str = Query(..., description="피크 시각 YYYY-MM-DD HH:MM"),
            series: str = Query("TOTAL", description="TOTAL(건물 전체) 또는 채널 (예: M008-04)")):
    """그 시각을 1시간 전에 예측할 때 입력값별 기여도(kW). LightGBM 기준."""
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
