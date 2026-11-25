"""[5] API — 첫 번째 화면 '예측 및 경보' (시연 재생)

연결 ─ 화면: ① 예측 및 경보 — 재생 기준일·모델 콤보, 그래프, Top5, 가능성 막대, 계측 상태 줄 (screen1_predict.js)
       API : GET /api/days · /api/replay · /api/channel · /api/forecast · /api/eval · /api/meter_status
       DB  : st_forecast·st_eval·st_peak_eval·st_explain·measurement·weather_15m·peak_threshold (읽기)

    GET /api/days                         재생 기준일(시험기간 날짜)·방식·예측모델 목록
    GET /api/replay?date=&model=          하루치 화면 데이터: 건물 합계 실측·다음 1시간 예측·피크 기준선,
                                          호실 실측·예측, 호실 피크 기준(직전 30일 P95)
    GET /api/channel?date=&model=&channel= 채널별 피크 선 (채널 실측·15분×4 예측·채널 피크 기준)
    GET /api/forecast?issued_at=&model=   특정 발행 시각의 15분×4 예측
    GET /api/eval                         모델 성능 (전체·채널, 선행시간별) + 피크 감지
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from ..config import (DEFAULT_DATE, DEFAULT_METHOD, DEFAULT_MODEL, DISPLAY, FAMILIES, FLOORS, METHOD, METHODS, PEAK_PERCENTILE,
                      PEAK_WINDOW_DAYS, SUM_BASE)
from .common import WEEK_KO, check_model, has_table, info, num, q, room_key, to_local

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/days")
def days():
    d = q("""SELECT substr(issued_at,1,10) AS date, COUNT(DISTINCT issued_at) AS n
             FROM st_forecast WHERE model_name='LightGBM' AND series_id='TOTAL' GROUP BY 1 ORDER BY 1""")
    # 시험기간 모든 날 (첫날 10-20은 15:15부터, 마지막 날 11-02는 16:15까지 — 수집 재개·종료 시각)
    have = set(info()["models"])
    fams = [{"family": f[0], "name": f[1], "direct": f[2], "sum": f[3]} for f in FAMILIES if f[2] in have and f[3] in have]
    return {"days": d.to_dict("records"), "models": [{"id": m, "name": DISPLAY.get(m, m)} for m in info()["models"]],
            "methods": [{"id": k, "name": v} for k, v in METHODS.items()], "families": fams,
            "default_model": DEFAULT_MODEL, "default_method": DEFAULT_METHOD, "default_date": DEFAULT_DATE,
            "test_start": info()["test_start"], "test_end": info()["test_end"]}


@router.get("/meter_status")
def meter_status(date: str | None = Query(None, description="YYYY-MM-DD (비우면 가장 최근 실측까지 24시간)")):
    """계측 상태: 측정 자료만으로 채널별 끊김·빠진 칸·음수·누적 감소·누적·수요 불일치·값 고정 점검."""
    from ..eda.meter_status import status
    return status(date)


@router.get("/eval")
def evaluation():
    ev = q("SELECT * FROM st_eval ORDER BY split, scope, model_name, horizon_min")
    pk = q("SELECT * FROM st_peak_eval ORDER BY split, model_name")
    return {"metrics": ev.to_dict("records"), "peak_detection": pk.to_dict("records"),
            "test_start": info()["test_start"], "test_end": info()["test_end"],
            "splits": info().get("splits"),
            "note": "split=val 검증·test 시험. MAPE 는 실제값이 전체 1kW·채널 0.1kW 미만인 칸 제외. "
                    "공통 표본(모든 모델이 예측한 칸)만 비교. bias=평균(예측−실제), miscls=오분류율."}


@router.get("/forecast")
def forecast(issued_at: str = Query(..., description="예: 2026-10-28 14:00:00"), model: str = "LightGBM"):
    check_model(model)
    d = q("SELECT series_id, horizon_min, predicted_kw FROM st_forecast WHERE model_name=:m AND issued_at=:t",
          m=model, t=issued_at)
    if d.empty:
        raise HTTPException(404, f"{issued_at} 발행 예측이 없습니다.")
    t0 = pd.Timestamp(issued_at)
    out = {}
    for sid, g in d.groupby("series_id"):
        out[sid] = [{"target_time": str(t0 + pd.Timedelta(minutes=int(r.horizon_min))), "horizon_min": int(r.horizon_min),
                     "predicted_kw": num(r.predicted_kw)} for r in g.sort_values("horizon_min").itertuples()]
    return {"model": model, "issued_at": issued_at, "unit": "kW", "series": out}


@router.get("/replay")
def replay(date: str = Query(..., description="YYYY-MM-DD"), model: str = "LightGBM"):
    """화면(web/dashboard.html)이 쓰는 하루치 재생 데이터."""
    check_model(model)
    inf = info()
    rooms = inf["rooms"]
    inactive = set(inf["inactive"])
    t0, t1 = f"{date} 00:00:00", f"{date} 23:45:00"

    fc = q("""SELECT series_id, issued_at, horizon_min, predicted_kw FROM st_forecast
              WHERE model_name=:m AND issued_at BETWEEN :a AND :b""", m=model, a=t0, b=t1)
    if fc.empty:
        raise HTTPException(404, f"{date} {model} 예측이 없습니다. (/api/days 참고)")
    if model in SUM_BASE:      # 채널합산 모델: 호실 예측 = 원래 모델의 채널 예측
        fc = pd.concat([fc, q("""SELECT series_id, issued_at, horizon_min, predicted_kw FROM st_forecast
              WHERE model_name=:m AND series_id<>'TOTAL' AND issued_at BETWEEN :a AND :b""",
                                m=SUM_BASE[model], a=t0, b=t1)])
    fc["issued_at"] = pd.to_datetime(fc.issued_at).dt.strftime("%H:%M")
    tot = fc[fc.series_id == "TOTAL"].pivot_table(index="issued_at", columns="horizon_min", values="predicted_kw")
    room_fc = fc[fc.series_id != "TOTAL"].set_index(["issued_at", "series_id", "horizon_min"]).predicted_kw

    m = q("""SELECT m.channel_id, c.meter_id, m.measured_at, m.demand_kw, m.power_kw
             FROM measurement m JOIN channel c ON c.channel_id = m.channel_id
             WHERE m.measured_at BETWEEN :a AND :b""", a=t0, b=t1)
    m["measured_at"] = pd.to_datetime(m.measured_at)
    if getattr(m.measured_at.dt, "tz", None) is not None:
        m["measured_at"] = m.measured_at.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    m["t"] = m.measured_at.dt.strftime("%H:%M")
    n_ch = int(q("SELECT COUNT(*) AS n FROM channel").n[0])
    agg = m.groupby("t").agg(y=("demand_kw", "sum"), p=("power_kw", "sum"), n=("demand_kw", "size"))
    agg.loc[agg.n < n_ch, ["y", "p"]] = float("nan")              # 일부 채널 미수신이면 합산 표시 안 함
    common = m[~m.meter_id.isin(FLOORS)].groupby("t").demand_kw.sum()
    room_now = m[m.meter_id.isin(FLOORS)].pivot_table(index="t", columns="channel_id", values="demand_kw")

    w = q("SELECT measured_at, temperature_c FROM weather_15m WHERE measured_at BETWEEN :a AND :b", a=t0, b=t1) \
        if has_table("weather_15m") else pd.DataFrame(columns=["measured_at", "temperature_c"])
    temp = dict(zip(pd.to_datetime(w.measured_at).dt.strftime("%H:%M"), w.temperature_c))
    th = q("SELECT threshold_kw FROM peak_threshold WHERE date=:d", d=date)
    thr = float(th.threshold_kw[0]) if len(th) else None

    ex = q("SELECT issued_at, feature, contribution_kw FROM st_explain WHERE issued_at BETWEEN :a AND :b", a=t0, b=t1)
    ex["issued_at"] = pd.to_datetime(ex.issued_at).dt.strftime("%H:%M")
    lg = q("""SELECT issued_at, predicted_kw FROM st_forecast WHERE model_name='LightGBM' AND series_id='TOTAL'
              AND horizon_min=60 AND issued_at BETWEEN :a AND :b""", a=t0, b=t1)
    lg_pred = dict(zip(pd.to_datetime(lg.issued_at).dt.strftime("%H:%M"), lg.predicted_kw))

    # 호실별 피크 기준 = 그날 직전 30일 그 호실 15분 수요전력 P95 ('호실별 피크 예측 가능성'의 분모)
    w0 = (pd.Timestamp(date) - pd.Timedelta(days=PEAK_WINDOW_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    hist = q("""SELECT m.channel_id, m.demand_kw FROM measurement m JOIN channel c ON c.channel_id=m.channel_id
                WHERE c.meter_id IN ('M002','M004','M006','M008') AND m.measured_at >= :a AND m.measured_at < :b""",
             a=w0, b=t0)
    room_thr = {room_key(c): (num(float(np.percentile(g.demand_kw, PEAK_PERCENTILE)), 3) if len(g) >= 7 * 96 else None)
                for c, g in hist.groupby("channel_id")}

    frames = []
    for t in tot.index:
        pred = [tot.loc[t].get(h) for h in (15, 30, 45, 60)]
        if t not in agg.index or any(v is None or math.isnan(v) for v in pred) or math.isnan(agg.loc[t, "y"]):
            continue
        k = int(max(range(4), key=lambda i: pred[i]))
        rooms_now, proom = {}, {}
        for cid in rooms:
            now = room_now.loc[t, cid] if (t in room_now.index and cid in room_now.columns) else float("nan")
            now = 0.0 if math.isnan(now) else float(now)
            rooms_now[room_key(cid)] = round(now, 3)
            if cid in inactive:
                pv = 0.0
            else:
                pv = room_fc.get((t, cid, 15 * (k + 1)), now)
            proom[room_key(cid)] = round(float(pv), 3)
        e = ex[ex.issued_at == t].copy()
        e = e.reindex(e.contribution_kw.abs().sort_values(ascending=False).index).head(7)
        frames.append({
            "t": t, "y": num(agg.loc[t, "y"], 2), "p": num(agg.loc[t, "p"], 2),
            "temp": num(temp.get(t), 1), "pred": [num(v, 2) for v in pred], "thr": num(thr, 2),
            "rooms": rooms_now, "common": num(common.get(t, 0.0), 2), "proom": proom,
            # 점검 우선 호실(예상 피크 시각 예측 kW 상위 8개)의 15·30·45·60분 예측 → 하단 그래프 점 4개
            "rp": {room_key(c): [round(float(room_fc.get((t, c, 15 * (h + 1)), rooms_now[room_key(c)])), 3) for h in range(4)]
                   for c in sorted((c for c in rooms if c not in inactive), key=lambda c: -proom[room_key(c)])[:8]},
            "shap": [[r.feature, num(r.contribution_kw, 2)] for r in e.itertuples()],
            "shap_base": num(inf["shap_base"], 2), "shap_pred": num(lg_pred.get(t), 2),
        })
    if not frames:
        raise HTTPException(404, f"{date} 은 실측 또는 예측이 비어 있어 재생할 수 없습니다.")

    perf, peak = performance(inf["models"])

    d = pd.Timestamp(date)
    return {"day": f"{date}({WEEK_KO[d.dayofweek]})", "date": date, "model": DISPLAY.get(model, model), "model_id": model,
            "method": METHOD.get(model), "method_name": METHODS.get(METHOD.get(model), "기준"), "room_thr": room_thr,
            "frames": frames, "imp": inf["importance"], "perf": perf, "peak": peak, "floors": FLOORS,
            "test_start": inf["test_start"], "test_end": inf["test_end"], "splits": inf.get("splits"),
            "thr_method": "직전 30일 전체 합산 15분 수요전력 P95",
            "thr_options": thr_options(date), "peak_now": peak_now(date)}


def peak_now(date: str) -> dict:
    """상태 띠 맨 앞 '현재 피크' (한전 방식 최대수요전력, models/thresholds.py)."""
    from ..live.predictor import grid
    from ..models.thresholds import current_peak
    from .common import engine
    g = grid(engine)
    return current_peak(pd.Series(g.y[0], index=g.times), date)


def thr_options(date: str) -> dict:
    """상태 띠 '피크 기준' 콤보박스: 최근 30일·전월·당월·7~9월·12~2월 P95 (models/thresholds.py)."""
    from ..live.predictor import grid
    from ..models.thresholds import options
    from .common import engine
    g = grid(engine)
    return options(pd.Series(g.y[0], index=g.times), date)




def performance(models: list[str]) -> tuple[dict, dict]:
    """건물 전체 성능(시험·검증)과 피크 오분류표. 키는 화면 표시 이름."""
    ev = q("SELECT * FROM st_eval WHERE scope='TOTAL'")
    te, va = ev[ev.split == "test"], ev[ev.split == "val"]
    perf = {}
    for name in models:
        e = te[te.model_name == name].set_index("horizon_min")
        if 0 not in e.index:
            continue
        v = va[(va.model_name == name) & (va.horizon_min == 0)]
        perf[DISPLAY.get(name, name)] = {"id": name, "method": METHOD.get(name, ""),
                                         "mae": num(e.loc[0, "mae"]), "rmse": num(e.loc[0, "rmse"]),
                                         "mape": num(e.loc[0, "mape"]), "bias": num(e.loc[0, "bias"]),
                                         "val_mae": num(v.mae.iloc[0]) if len(v) else None,
                                         "by_h": [num(e.loc[h, "mae"]) for h in (15, 30, 45, 60)], "n": int(e.loc[0, "n"])}
    pk = q("SELECT * FROM st_peak_eval")
    peak = {}
    for r in pk.itertuples():
        d = peak.setdefault(DISPLAY.get(r.model_name, r.model_name), {})
        d[r.split] = {"precision": num(r.precision), "recall": num(r.recall), "f1": num(r.f1), "tp": int(r.tp),
                      "fp": int(r.fp), "fn": int(r.fn), "tn": int(r.tn), "n": int(r.n), "miscls": num(r.miscls, 4)}
    for d in peak.values():              # 화면 기본값 = 시험기간
        d.update(d.get("test", {}))
    return perf, peak


def _channel_id(channel: str) -> str:
    """'M004-03' 또는 'M004-CH03' → 'M004-CH03'"""
    c = channel.strip().upper()
    if "-CH" not in c and "-" in c:
        m, n = c.split("-", 1)
        c = f"{m}-CH{int(n):02d}"
    return c


@router.get("/channel")
def channel_line(date: str = Query(..., description="YYYY-MM-DD"), channel: str = Query(..., description="예: M004-02"),
                 model: str = DEFAULT_MODEL):
    """채널별 피크 선: 그날 채널 실측(15분), 발행 시각별 15분×4 예측, 채널 피크 기준(직전 30일 P95)."""
    check_model(model)
    model_fc = SUM_BASE.get(model, model)        # 채널합산 모델의 채널 예측 = 원래 모델의 채널 예측
    cid = _channel_id(channel)
    ch = q("SELECT channel_id, meter_id, is_active, room_name FROM channel WHERE channel_id=:c", c=cid)
    if ch.empty:
        raise HTTPException(404, f"채널 {channel} 이 없습니다.")
    t0, t1 = f"{date} 00:00:00", f"{date} 23:45:00"
    d0 = pd.Timestamp(date)
    w0 = (d0 - pd.Timedelta(days=PEAK_WINDOW_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    hist = q("SELECT demand_kw FROM measurement WHERE channel_id=:c AND measured_at >= :a AND measured_at < :b",
             c=cid, a=w0, b=t0)
    thr = float(np.percentile(hist.demand_kw, PEAK_PERCENTILE)) if len(hist) >= 7 * 96 else None

    m = q("SELECT measured_at, demand_kw FROM measurement WHERE channel_id=:c AND measured_at BETWEEN :a AND :b",
          c=cid, a=t0, b=t1)
    m["t"] = to_local(m.measured_at).dt.strftime("%H:%M")
    actual = [{"t": r.t, "y": num(r.demand_kw, 3)} for r in m.sort_values("t").itertuples()]

    fc = q("""SELECT issued_at, horizon_min, predicted_kw FROM st_forecast
              WHERE model_name=:m AND series_id=:c AND issued_at BETWEEN :a AND :b""", m=model_fc, c=cid, a=t0, b=t1)
    fc["t"] = to_local(fc.issued_at).dt.strftime("%H:%M")
    pred = {t: [num(v, 3) for v in g.sort_values("horizon_min").predicted_kw] for t, g in fc.groupby("t")}
    note = None
    if not bool(ch.is_active[0]):
        note = "전 기간 0kW 채널이라 예측 대상에서 제외했습니다."
    elif not pred:
        note = "이 채널은 예측 저장 대상(호실 채널)이 아니거나 해당 날짜 예측이 없습니다."
    peak = max(actual, key=lambda r: r["y"] or -1) if actual else None
    meter = str(ch.meter_id[0])
    return {"channel": cid, "label": f"{FLOORS.get(meter, '?')}층 {cid[-2:]}번" if meter in FLOORS else cid,
            "room_name": ch.room_name[0], "date": date, "model": DISPLAY.get(model, model), "unit": "kW",
            "thr": num(thr, 3), "thr_method": f"직전 {PEAK_WINDOW_DAYS}일 이 채널 15분 수요전력 P{PEAK_PERCENTILE}",
            "actual": actual, "pred": pred, "actual_peak": peak, "note": note}
