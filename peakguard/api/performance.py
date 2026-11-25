"""[5] API — 세 번째 화면 '모델성능 및 오경보율 비교'

연결 ─ 화면: ③ 모델성능 및 오경보율 비교 전체 (screen3_perf.js)
       API : GET /api/performance?thr=
       DB  : st_eval·st_peak_eval·st_forecast·st_meta (읽기)

  GET /api/performance?thr=recent30|prev_month|this_month|summer|winter

피크 기준점(첫 화면 '피크 기준' 콤보박스와 같은 다섯 가지)에 따라 오분류표를 다시 판정합니다
(evaluation/peak_by_threshold.py). 예측 오차 표는 기준과 상관없이 같습니다.

화면 위쪽 '채널별 합산 후 예측'(기업 권장), 아래쪽 '채널별 예측 후 합산' 두 묶음을 돌려줍니다.
두 묶음 모두 같은 시험기간·같은 예측 시점(공통 표본)에서 건물 합계 실측과 비교한 결과입니다.
  - 모델 성능 표 : MAE·RMSE·MAPE·편향, 15·30·45·60분 MAE, 전일 동일시각(기업 기준모델) 대비 개선율, 검증 MAE
  - 피크 경보 오분류표 : 맞힘·놓침·헛경보·정상, 오분류율, 재현율(실제 위험 중 맞힌 비율), 정밀도
  - '경보 없음' 줄 : 경보를 한 번도 내지 않았다고 가정한 비교용 줄 (오분류율만 보면 안 되는 이유를 보여 줌)
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..config import BASE_DAY, DISPLAY, FAMILIES, METHODS
from .common import info, num, q

router = APIRouter(prefix="/api", tags=["performance"])


def _row(ev, pk, mid: str) -> dict | None:
    te = ev[(ev.model_name == mid) & (ev.split == "test")].set_index("horizon_min")
    if 0 not in te.index:
        return None
    va = ev[(ev.model_name == mid) & (ev.split == "val") & (ev.horizon_min == 0)]
    p = pk[(pk.model_name == mid) & (pk.split == "test")]
    p = p.iloc[0] if len(p) else None
    return {"id": mid, "name": DISPLAY.get(mid, mid), "mae": num(te.loc[0, "mae"]), "rmse": num(te.loc[0, "rmse"]),
            "mape": num(te.loc[0, "mape"]), "bias": num(te.loc[0, "bias"]),
            "val_mae": num(va.mae.iloc[0]) if len(va) else None,
            "by_h": [num(te.loc[h, "mae"]) for h in (15, 30, 45, 60)],
            "peak": None if p is None else {"tp": int(p.tp), "fn": int(p.fn), "fp": int(p.fp), "tn": int(p.tn), "n": int(p.n),
                                           "miscls": num(p.miscls, 4), "recall": num(p.recall, 4),
                                           "precision": num(p.precision, 4)}}


@router.get("/performance")
def performance(thr: str = Query("recent30", pattern="^(recent30|prev_month|this_month|summer|winter)$")):
    from ..evaluation.peak_by_threshold import compute
    from ..models.thresholds import KEYS, NAMES
    inf = info()
    pt = compute()
    ev = q("SELECT * FROM st_eval WHERE scope='TOTAL'")
    pk = q("SELECT * FROM st_peak_eval")
    base = _row(ev, pk, BASE_DAY)
    sel = pt[thr]
    def _pk(p):
        return {"tp": p["tp"], "fn": p["fn"], "fp": p["fp"], "tn": p["tn"], "n": p["n"], "miscls": num(p["miscls"], 4),
                "recall": num(p["recall"], 4) if p["tp"] + p["fn"] else None,
                "precision": num(p["precision"], 4) if p["tp"] + p["fp"] else None}
    vals = [v for v in sel["days"].values() if v is not None]
    out = {"test": [inf["test_start"], inf["test_end"]], "splits": inf.get("splits"), "sections": [],
           "thr": {"key": thr, "name": NAMES[thr], "n": sel["n"], "actual_risk": sel["actual_risk"],
                   "kw_min": min(vals) if vals else None, "kw_max": max(vals) if vals else None},
           "thr_options": [{"key": k, "name": NAMES[k], "n": pt[k]["n"], "actual_risk": pt[k]["actual_risk"]} for k in KEYS]}
    for key in ("direct", "sum"):                                # 화면 위: 합산 후 예측(기업 권장), 아래: 예측 후 합산
        rows = [base] + [_row(ev, pk, f[3] if key == "sum" else f[2]) for f in FAMILIES]
        rows = [r for r in rows if r]
        for r in rows:
            r["gain"] = None if r["id"] == BASE_DAY else num((base["mae"] - r["mae"]) / base["mae"] * 100, 1)
        for r in rows:                                       # 고른 피크 기준으로 다시 판정
            r["peak"] = _pk(sel["rows"][r["id"]]) if r["id"] in sel["rows"] else None
        bp = base["peak"]
        none_row = {"name": "경보 없음 (비교용)", "tp": 0, "fn": bp["tp"] + bp["fn"], "fp": 0, "tn": bp["fp"] + bp["tn"],
                    "n": bp["n"], "recall": 0.0, "precision": None,
                    "miscls": num((bp["tp"] + bp["fn"]) / bp["n"], 4) if bp["n"] else None}
        cand = [r for r in rows if r["id"] != BASE_DAY and r["peak"]]
        cand_r = [r for r in cand if r["peak"]["recall"] is not None]
        best_recall = max(cand_r, key=lambda r: (r["peak"]["recall"], -r["peak"]["fp"]))["id"] if cand_r else None
        best_mae = min(cand, key=lambda r: r["mae"])["id"] if cand else None
        out["sections"].append({"key": key, "title": METHODS[key], "rows": rows, "no_alarm": none_row,
                                "best_recall": best_recall, "best_mae": best_mae,
                                "actual_risk": bp["tp"] + bp["fn"]})
    # 추가 평가: 발생시각 오차 · 조건별 정확도 · 기상 포함/미포함 · 최적 모델 선정 (evaluation/extra.py)
    from ..evaluation import extra
    out["timing"] = extra.timing({k: v for k, v in sel["days"].items() if v is not None})
    out["by_condition"] = extra.by_condition()
    out["by_day"] = extra.by_day()
    out["prep"] = inf.get("prep")
    allrows = {r["id"]: r for sec in out["sections"] for r in sec["rows"]}
    out["weather"] = extra.weather_compare(allrows) if thr == "recent30" else extra.weather_compare(
        {k: {**v, "peak": None} for k, v in allrows.items()})
    out["selection"] = extra.selection(out["sections"])
    return out
