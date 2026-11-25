"""[4] 세 번째 화면 추가 평가 — 시험기간 저장 예측(st_forecast)과 실측으로 계산

연결 ─ 화면: ③ '1. 선정 결론' · '3. 정확도 상세'(발생시각 오차·조건별·기간별) · '4. 기상 비교', ② '4. 기상·이상 패턴'의 기상 데이터 검토 상자
       API : GET /api/performance · /api/patterns(weather_review)
       DB  : st_forecast·st_eval·weather_15m (읽기)

  timing(thr)   예측시각과 실제 발생시각의 오차 (기업 1차 답변 D2)
                  사건 = 실측 건물 합계가 피크 기준 이상으로 올라간 연속 구간, 시작 시각 T
                  사전 경보 = T 1시간 전~15분 전에 낸 예측 중 '위험'(기준 이상)이 하나라도 있으면
                  선행시간 = T − 처음 위험을 낸 예측 시점,  시각 오차 = 그 예측이 말한 최초 위험 시각 − T
  by_condition  조건별 예측 정확도 MAE (평일 / 주말·공휴일 / 근무시간 09~18시 / 그 밖 시간) (과제 원문 세부 7)
  weather       기상(기온·습도) 포함 / 미포함 성능 비교 (models/compare_no_weather.json)
  selection     최적 모델 선정 결론 (기업 목표: 기준모델 대비 MAE 10% 이상 개선 → 그중 피크를 덜 놓치는 모델)
"""
from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from ..config import ALERT_RATIO, BASE_DAY, DISPLAY, FAMILIES, H, MODEL_DIR, METHODS, MODELS


@lru_cache(maxsize=1)
def _load():
    from ..api.common import engine, q
    from ..live.predictor import grid
    g = grid(engine)
    fc = q("SELECT model_name, issued_at, horizon_min, predicted_kw FROM st_forecast WHERE series_id='TOTAL'")
    fc["issued_at"] = pd.to_datetime(fc.issued_at)
    P = {m: d.pivot(index="issued_at", columns="horizon_min", values="predicted_kw") for m, d in fc.groupby("model_name")}
    P = {m: P[m] for m in MODELS if m in P}              # 화면 순서: 기준모델 → 합산 후 예측 4종 → 예측 후 합산 4종
    iss = pd.DatetimeIndex(sorted(set.intersection(*[set(p.dropna().index) for p in P.values()])))
    tot = pd.Series(g.y[0], index=g.times)
    A = np.stack([tot.reindex(iss + pd.Timedelta(minutes=15 * h)).to_numpy() for h in range(1, H + 1)], 1)
    off = pd.Series(g.offday, index=g.times)
    return g, P, iss, tot, A, off


def timing(day_thr: dict) -> dict:
    g, P, iss, tot, A, off = _load()
    t0, t1 = iss.min() + pd.Timedelta(hours=1), iss.max() + pd.Timedelta(hours=1)
    s = tot[(tot.index >= t0) & (tot.index <= t1)]
    th = pd.Series([day_thr.get(f"{x:%Y-%m-%d}") for x in s.index], index=s.index, dtype=float)
    over = (s >= th) & th.notna()
    starts = s.index[over & ~over.shift(1, fill_value=False)]
    out = {}
    for m, p in P.items():
        lead, err = [], []
        for T in starts:
            thr = day_thr.get(f"{T:%Y-%m-%d}")
            hit = None
            for k in range(4, 0, -1):                      # T-60 → T-15 순서로 처음 경보를 낸 예측 찾기
                ti = T - pd.Timedelta(minutes=15 * k)
                if ti not in p.index:
                    continue
                v = p.loc[ti].to_numpy()
                first = next((h for h in range(H) if v[h] >= thr * ALERT_RATIO), None)
                if first is not None:
                    hit = (k * 15, (ti + pd.Timedelta(minutes=15 * (first + 1)) - T) / pd.Timedelta("1min"))
                    break
            if hit:
                lead.append(hit[0])
                err.append(hit[1])
        n = len(starts)
        out[m] = {"events": n, "warned": len(lead), "warned_pct": round(len(lead) / n * 100, 0) if n else None,
                  "lead_min": round(float(np.mean(lead)), 0) if lead else None,
                  "abs_err_min": round(float(np.mean(np.abs(err))), 0) if err else None,
                  "early_pct": round(float(np.mean(np.array(err) < 0) * 100), 0) if err else None,
                  "within15_pct": round(float(np.mean(np.abs(err) <= 15) * 100), 0) if err else None}
    return {"events": int(len(starts)), "rows": out,
            "rule": "사건 = 실측 건물 합계가 그날 피크 기준 이상으로 올라간 연속 구간(시작 시각 T). 사전 경보 = T 1시간~15분 전 예측 중 "
                    "'위험'(기준 이상 예측)이 있었는지. 선행시간 = T − 처음 경보 시점, 시각 오차 = 경보가 말한 최초 위험 시각 − T."}


@lru_cache(maxsize=1)
def by_condition() -> dict:
    g, P, iss, tot, A, off = _load()
    flat = pd.DatetimeIndex(np.concatenate([iss + pd.Timedelta(minutes=15 * h) for h in range(1, H + 1)]))
    shape = (H, len(iss))
    is_off = off.reindex(flat).fillna(False).to_numpy().astype(bool).reshape(shape).T
    hour = flat.hour.to_numpy().reshape(shape).T
    conds = {"평일": ~is_off, "주말·공휴일": is_off, "근무시간 09~18시": (hour >= 9) & (hour < 18), "그 밖 시간": (hour < 9) | (hour >= 18)}
    ok = ~np.isnan(A)
    out = {}
    for m, p in P.items():
        Pm = p.reindex(iss)[[15 * h for h in range(1, H + 1)]].to_numpy()
        out[m] = {c: round(float(np.nanmean(np.abs(Pm - A)[mask & ok])), 3) for c, mask in conds.items()}
    n = {c: int((mask & ok).sum()) for c, mask in conds.items()}
    return {"conditions": list(conds), "n": n, "rows": out}


@lru_cache(maxsize=1)
def by_day() -> dict:
    """기간별 정확도 (과제 원문 세부 7): 시험기간 예측 대상 날짜별 MAE + 평일/주말·공휴일 평균."""
    g, P, iss, tot, A, off = _load()
    flat = pd.DatetimeIndex(np.concatenate([iss + pd.Timedelta(minutes=15 * h) for h in range(1, H + 1)]))
    shape = (H, len(iss))
    day = flat.normalize().to_numpy().reshape(shape).T
    ok = ~np.isnan(A)
    days = sorted(set(day[ok].tolist()))
    is_off = {d: bool(off.reindex([pd.Timestamp(d)]).fillna(False).iloc[0]) or pd.Timestamp(d).dayofweek >= 5 for d in days}
    rows = {}
    for m, p in P.items():
        Pm = p.reindex(iss)[[15 * h for h in range(1, H + 1)]].to_numpy()
        E = np.abs(Pm - A)
        dd = [round(float(np.mean(E[(day == d) & ok])), 3) if ((day == d) & ok).any() else None for d in days]
        wk = (np.isin(day, [d for d in days if not is_off[d]])) & ok
        of = (np.isin(day, [d for d in days if is_off[d]])) & ok
        rows[m] = {"daily": dd, "weekday": round(float(np.mean(E[wk])), 3) if wk.any() else None,
                   "offday": round(float(np.mean(E[of])), 3) if of.any() else None}
    best = [min((r["daily"][i] for r in rows.values() if r["daily"][i] is not None), default=None) for i in range(len(days))]
    n = [int(((day == d) & ok).sum()) for d in days]
    WD = "월화수목금토일"
    return {"days": [{"date": f"{pd.Timestamp(d):%Y-%m-%d}", "wd": WD[pd.Timestamp(d).dayofweek] + ("·휴" if is_off[d] and pd.Timestamp(d).dayofweek < 5 else ""),
                      "n": k} for d, k in zip(days, n)],
            "rows": rows, "best": best,
            "note": f"시험기간 공통 표본(모든 모델이 예측한 칸)을 예측 대상 날짜별로 나눈 건물 합계 MAE. "
                    f"비교 칸은 {pd.Timestamp(flat[ok.T.ravel()].min()):%m-%d %H:%M} ~ {pd.Timestamp(flat[ok.T.ravel()].max()):%m-%d %H:%M} "
                    "(수집 중단 뒤 첫날은 전일 동일시각 기준모델에 필요한 전날 자료가 없어 빠짐, 첫날·마지막 날은 일부 시간만). "
                    "굵은 주황 = 그날 가장 정확한 모델. 칸 수: " + ", ".join(f"{pd.Timestamp(d):%m-%d} {k}" for d, k in zip(days, n))}


def weather_compare(perf_rows: dict) -> dict | None:
    """현재 모델(config.USE_WEATHER)과 반대 조건 비교 학습(models/compare_weather.json)을 나란히 놓음."""
    from ..config import USE_WEATHER
    f = MODEL_DIR / "compare_weather.json"
    if not f.exists():
        return None
    cmp = json.loads(f.read_text(encoding="utf-8"))
    other, cw = cmp["models"], bool(cmp["weather"])
    rows = []
    for mid, r in perf_rows.items():
        if mid not in other or mid == BASE_DAY:
            continue
        cur = {"mae": r["mae"], "val_mae": r.get("val_mae"), "tp": r["peak"]["tp"] if r.get("peak") else None,
               "fp": r["peak"]["fp"] if r.get("peak") else None, "fn": r["peak"]["fn"] if r.get("peak") else None,
               "recall": r["peak"]["recall"] if r.get("peak") else None}
        o = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in other[mid].items()}
        w, nw = (o, cur) if cw else (cur, o)
        rows.append({"id": mid, "name": DISPLAY.get(mid, mid),
                     "mae_w": w["mae"], "mae_nw": nw["mae"], "val_w": w["val_mae"], "val_nw": nw["val_mae"],
                     "recall_w": w["recall"], "recall_nw": nw["recall"], "tp_w": w["tp"], "tp_nw": nw["tp"],
                     "fp_w": w["fp"], "fp_nw": nw["fp"]})
    better = sum(1 for r in rows if r["val_w"] is not None and r["val_nw"] is not None and r["val_w"] < r["val_nw"])
    return {"rows": rows, "use_weather": USE_WEATHER, "val_better_with": better, "n": len(rows),
            "note": "기상 포함 = 예측 시점까지 관측된 기온의 '평소(최근 7일 평균) 대비'와 '1시간 변화'를 입력에 넣어 학습. "
                    "미포함 = 같은 설정에서 기상만 뺀 학습. 같은 학습·검증·시험기간, 피크 기준 최근 30일.",
            "decision": (f"채택 판단은 검증기간 MAE로: 기상 포함이 더 좋은 모델 {better}/{len(rows)}개 → "
                         + ("예측 입력에 기상 사용" if USE_WEATHER else "예측 입력에서 기상 미채택 (수집·업데이트·분석은 계속, 겨울 자료로 재검증)"))}


def selection(sections: list[dict]) -> dict:
    """선정 규칙 (기업 1차 답변 D1·D2)
      ① 기업 목표: 기준모델(전일 동일시각) 대비 MAE 10% 이상 개선한 모델만 후보
      ② 피크를 가장 적게 놓치는(재현율 높은) 모델 — 피크가드의 목적은 피크를 놓치지 않는 것
      ③ 같으면 헛경보가 적은 모델, 그다음 MAE 가 작은 모델 (F1 = 재현율·정밀도 조화평균은 참고로 표시)
    """
    cand = []
    for s in sections:
        for r in s["rows"]:
            k = r.get("peak")
            if r["id"] == BASE_DAY or not k or r["gain"] is None or r["gain"] < 10 or k["recall"] is None:
                continue
            prec = k["precision"] or 0.0
            f1 = 2 * prec * k["recall"] / (prec + k["recall"]) if prec + k["recall"] else 0.0
            cand.append({**r, "method": s["key"], "method_name": s["title"], "f1": round(f1, 3)})
    if not cand:
        return {"text": "선정할 수 있는 모델이 없습니다 (기준모델 대비 10% 개선 + 재현율 계산 가능한 모델 없음)."}
    cand.sort(key=lambda r: (-r["peak"]["recall"], r["peak"]["fp"], r["mae"]))   # 피크를 놓치지 않는 것이 먼저
    best = cand[0]
    direct = [c for c in cand if c["method"] == "direct"]
    db = direct[0] if direct else None
    pk = best["peak"]
    hi_rec = max(cand, key=lambda r: (r["peak"]["recall"], -r["peak"]["fp"]))
    lines = [f"선정: {best['method_name']} · {best['name']}",
             f"근거 ① 기업 목표: 기준모델(전일 동일시각) 대비 MAE {best['gain']}% 개선 (목표 10% 이상) — MAE {best['mae']} kW",
             f"근거 ② 피크 경보: 실제 위험 {pk['tp'] + pk['fn']}건 중 {pk['tp']}건 사전 감지(재현율 {round(pk['recall'] * 100)}%, 놓침 {pk['fn']}건) — 후보 중 가장 적게 놓침, "
             f"헛경보 {pk['fp']}건(정밀도 {round((pk['precision'] or 0) * 100)}%, F1 {best['f1']})",
             "근거 ③ 피크가드의 목적은 피크를 놓치지 않는 것이므로 재현율을 먼저, 그다음 헛경보·MAE 순으로 비교"]
    if db and db is not best:
        dk = db["peak"]
        lines.append(f"참고: 기업 권장 방식(채널별 합산 후 예측) 안에서는 {db['name']} — 재현율 {round(dk['recall'] * 100)}%, 헛경보 {dk['fp']}건, F1 {db['f1']}, MAE {db['mae']}")
    return {"best": best["id"], "best_method": best["method"], "lines": lines,
            "ranking": [{"name": f"{c['method_name']} · {c['name']}", "recall": c["peak"]["recall"], "precision": c["peak"]["precision"],
                         "fp": c["peak"]["fp"], "f1": c["f1"], "mae": c["mae"], "gain": c["gain"]} for c in cand[:6]]}


def weather_summary() -> dict:
    """두 번째 화면 '기상 데이터 검토' 칸: 연계 상태 + 모델 비교 결론 (세 번째 화면 '4. 기상 비교' 요약)."""
    from ..api.common import q
    from ..config import USE_WEATHER
    w = q("SELECT MIN(measured_at) a, MAX(measured_at) b, COUNT(temperature_c) n FROM weather_15m")
    out = {"from": str(w.a[0])[:16] if len(w) else None, "to": str(w.b[0])[:16] if len(w) else None,
           "slots": int(w.n[0]) if len(w) else 0, "use_weather": USE_WEATHER}
    f = MODEL_DIR / "compare_weather.json"
    if f.exists():
        ev = q("SELECT model_name, mae FROM st_eval WHERE split='val' AND scope='TOTAL' AND horizon_min=0").set_index("model_name").mae
        cmp = json.loads(f.read_text(encoding="utf-8"))
        rows = []
        for m, o in cmp["models"].items():
            if m == BASE_DAY or m not in ev.index:
                continue
            cur = float(ev[m])
            w_, nw_ = (o["val_mae"], cur) if cmp["weather"] else (cur, o["val_mae"])
            rows.append((m, w_, nw_))
        out["better_with"] = sum(1 for _, a, b in rows if a < b)
        out["n"] = len(rows)
    return out
