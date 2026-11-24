"""[4] 세 번째 화면 추가 평가 — 시험기간 저장 예측(st_forecast)과 실측으로 계산

  timing(thr)   예측시각과 실제 발생시각의 오차 (기업 1차 답변 D2)
                  사건 = 실측 건물 합계가 피크 기준 이상으로 올라간 연속 구간, 시작 시각 T
                  사전 경보 = T 1시간 전~15분 전에 낸 예측 중 '위험'(기준의 95% 이상)이 하나라도 있으면
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

from ..config import ALERT_RATIO, BASE_DAY, DISPLAY, FAMILIES, H, MODEL_DIR, METHODS


@lru_cache(maxsize=1)
def _load():
    from ..api.common import engine, q
    from ..live.predictor import grid
    g = grid(engine)
    fc = q("SELECT model_name, issued_at, horizon_min, predicted_kw FROM st_forecast WHERE series_id='TOTAL'")
    fc["issued_at"] = pd.to_datetime(fc.issued_at)
    P = {m: d.pivot(index="issued_at", columns="horizon_min", values="predicted_kw") for m, d in fc.groupby("model_name")}
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
                    f"'위험'(기준의 {int(ALERT_RATIO * 100)}% 이상)이 있었는지. 선행시간 = T − 처음 경보 시점, 시각 오차 = 경보가 말한 최초 위험 시각 − T."}


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


def weather_compare(perf_rows: dict) -> dict | None:
    f = MODEL_DIR / "compare_no_weather.json"
    if not f.exists():
        return None
    nw = json.loads(f.read_text(encoding="utf-8"))
    rows = []
    for mid, r in perf_rows.items():
        if mid not in nw or mid == BASE_DAY:
            continue
        rows.append({"id": mid, "name": DISPLAY.get(mid, mid), "mae_w": r["mae"], "mae_nw": round(nw[mid]["mae"], 3),
                     "recall_w": r["peak"]["recall"] if r.get("peak") else None, "recall_nw": round(nw[mid]["recall"], 4),
                     "tp_w": r["peak"]["tp"] if r.get("peak") else None, "tp_nw": nw[mid]["tp"],
                     "fp_w": r["peak"]["fp"] if r.get("peak") else None, "fp_nw": nw[mid]["fp"]})
    return {"rows": rows, "note": "기상 포함 = 예측 시점까지 관측된 기온의 '평소(최근 7일 평균) 대비'와 '1시간 변화'를 입력으로 넣은 현재 모델. "
                                  "미포함 = 같은 설정에서 기상만 뺀 비교 학습 (최근 30일 기준, 같은 시험기간)."}


def selection(sections: list[dict]) -> dict:
    """선정 규칙 (기업 1차 답변 D1·D2)
      ① 기업 목표: 기준모델(전일 동일시각) 대비 MAE 10% 이상 개선한 모델만 후보
      ② 피크 경보 성능: F1 = 재현율(놓치지 않음)과 정밀도(헛경보가 적음)의 조화평균이 가장 높은 모델
         - 위험을 기준의 95%부터 알리므로 후보 모델의 재현율이 모두 높아(85~97%) 차이는 주로 헛경보에서 남
      ③ 같으면 MAE 가 작은 모델
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
    cand.sort(key=lambda r: (-r["f1"], r["mae"]))
    best = cand[0]
    direct = [c for c in cand if c["method"] == "direct"]
    db = direct[0] if direct else None
    pk = best["peak"]
    hi_rec = max(cand, key=lambda r: (r["peak"]["recall"], -r["peak"]["fp"]))
    lines = [f"선정: {best['method_name']} · {best['name']}",
             f"근거 ① 기업 목표: 기준모델(전일 동일시각) 대비 MAE {best['gain']}% 개선 (목표 10% 이상) — MAE {best['mae']} kW",
             f"근거 ② 피크 경보: 실제 위험 {pk['tp'] + pk['fn']}건 중 {pk['tp']}건 사전 감지(재현율 {round(pk['recall'] * 100)}%), 헛경보 {pk['fp']}건(정밀도 {round((pk['precision'] or 0) * 100)}%) "
             f"— 재현율과 정밀도를 함께 본 F1 {best['f1']}로 후보 중 가장 높음",
             f"근거 ③ 위험을 기준의 95%부터 알려 후보 모델 모두 재현율이 높음. 재현율만 보면 {hi_rec['method_name']} · {hi_rec['name']}"
             f"({round(hi_rec['peak']['recall'] * 100)}%)가 가장 높지만 헛경보가 {hi_rec['peak']['fp']}건으로 많아, 관리자가 실제로 쓰기에는 헛경보가 적은 모델이 유리"]
    if db and db is not best:
        dk = db["peak"]
        lines.append(f"참고: 기업 권장 방식(채널별 합산 후 예측) 안에서는 {db['name']} — 재현율 {round(dk['recall'] * 100)}%, 헛경보 {dk['fp']}건, F1 {db['f1']}, MAE {db['mae']}")
    return {"best": best["id"], "best_method": best["method"], "lines": lines,
            "ranking": [{"name": f"{c['method_name']} · {c['name']}", "recall": c["peak"]["recall"], "precision": c["peak"]["precision"],
                         "fp": c["peak"]["fp"], "f1": c["f1"], "mae": c["mae"], "gain": c["gain"]} for c in cand[:6]]}
