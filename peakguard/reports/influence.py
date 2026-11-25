"""[10] 두 번째 화면 — 피크 시각 실측 영향도 (예측이 아니라 실측만 사용)

연결 ─ 화면: ② '평소 대비 증가 Top 10' · '전체 및 채널별 영향도' 그래프
       API : GET /api/rise · /api/influence
       DB  : measurement·channel (읽기)

'평소' = 그 시각과 같은 요일·같은 15분 칸의 직전 4주 실측 평균 (96개 채널이 모두 들어온 주만, 수집 중단 주는 빠짐)

  rise_top(t)        '피크 시각 평소 대비 증가 Top 10' — 채널별 (그 시각 실측 − 평소)
  influence(t, 전체) 건물 합계 실측이 평소보다 높아진 것을 채널별로 나눔
                     평소 건물 합계 + Σ 채널별 증가 = 그 시각 건물 합계 실측  (정확히 맞아떨어짐)
  influence(t, 채널) 그 채널 실측을 시간 기준으로 나눔
                     최근 4주 같은 시각 평균(모든 요일) → + 요일 영향 → + 최근 1주 변화 → + 당일 추가 사용 = 그 시각 실측
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from ..config import FLOORS
from ..database.connection import get_engine

WEEKS = 4


def _at(engine, times: list[pd.Timestamp]) -> pd.DataFrame:
    """시각 목록 × 채널 실측표."""
    q = text(f"""SELECT measured_at, channel_id, demand_kw FROM measurement
                 WHERE measured_at IN ({",".join(f":t{i}" for i in range(len(times)))})""")
    with engine.connect() as con:
        m = pd.read_sql(q, con, params={f"t{i}": f"{t:%Y-%m-%d %H:%M:%S}" for i, t in enumerate(times)})
    m["measured_at"] = pd.to_datetime(m.measured_at)
    return m.pivot_table(index="measured_at", columns="channel_id", values="demand_kw", aggfunc="first")


def _channels(engine) -> pd.DataFrame:
    with engine.connect() as con:
        return pd.read_sql(text("SELECT channel_id, meter_id FROM channel"), con).set_index("channel_id")


def _label(cid: str, meter: str) -> tuple[str, str]:
    from ..api.alerts import label
    return label(cid), ("호실" if meter in FLOORS else "공용")


def usual(t, engine=None) -> dict:
    """t 시각 실측과 평소(같은 요일·시각 직전 4주 평균), 쓴 주 목록."""
    engine = engine or get_engine()
    t = pd.Timestamp(t)
    ch = _channels(engine)
    weeks = [t - pd.Timedelta(days=7 * k) for k in range(1, WEEKS + 1)]
    w = _at(engine, [t] + weeks)
    if t not in w.index or w.loc[t].notna().sum() < len(ch):
        raise ValueError(f"{t:%Y-%m-%d %H:%M} 에 96개 채널 실측이 모두 있지 않습니다.")
    full = [x for x in weeks if x in w.index and w.loc[x].notna().sum() >= len(ch)]
    if not full:
        raise ValueError(f"{t:%Y-%m-%d %H:%M} 의 직전 4주 같은 요일·시각 실측이 없어 '평소'를 계산할 수 없습니다.")
    return {"t": t, "actual": w.loc[t].reindex(ch.index).fillna(0.0), "usual": w.loc[full].mean().reindex(ch.index).fillna(0.0),
            "weeks": full, "ch": ch}


def rise_top(t, n: int = 10, engine=None) -> dict:
    u = usual(t, engine)
    a, b, ch = u["actual"], u["usual"], u["ch"]
    d = (a - b).sort_values(ascending=False)
    rows = []
    for k, (cid, v) in enumerate(d.head(n).items(), 1):
        name, kind = _label(cid, ch.loc[cid, "meter_id"])
        rows.append({"rank": k, "channel": cid.replace("-CH", "-"), "name": name, "kind": kind, "kw": round(float(a[cid]), 3),
                     "usual_kw": round(float(b[cid]), 3), "rise_kw": round(float(v), 3),
                     "rise_pct": round(float(v / b[cid] * 100), 0) if b[cid] > 0.05 else None})
    return {"time": f"{u['t']:%Y-%m-%d %H:%M}", "total_kw": round(float(a.sum()), 2), "usual_total_kw": round(float(b.sum()), 2),
            "rise_total_kw": round(float(a.sum() - b.sum()), 2), "weeks": [f"{x:%m-%d}" for x in u["weeks"]], "rows": rows}


def influence(t, series: str = "TOTAL", top: int = 8, engine=None) -> dict:
    """폭포 그래프용: 시작값(평소) + 항목들 = 끝값(그 시각 실측)."""
    engine = engine or get_engine()
    u = usual(t, engine)
    a, b, ch = u["actual"], u["usual"], u["ch"]
    wk = ", ".join(f"{x:%m-%d}" for x in u["weeks"])
    if series.upper() == "TOTAL":
        d = (a - b)
        order = d.reindex(d.abs().sort_values(ascending=False).index)
        items = []
        for cid, v in order.head(top).items():
            name, kind = _label(cid, ch.loc[cid, "meter_id"])
            items.append({"name": f"{name}" + ("" if kind == "호실" else " (공용)"), "kw": round(float(v), 3)})
        rest = float(d.sum() - order.head(top).sum())
        items.append({"name": "그 밖의 채널", "kw": round(rest, 3)})
        return {"time": f"{u['t']:%Y-%m-%d %H:%M}", "label": "건물 전체", "mode": "채널별",
                "base_name": "평소 같은 시각 건물 합계", "base_kw": round(float(b.sum()), 3),
                "end_name": "그 시각 실측", "actual_kw": round(float(a.sum()), 3), "items": items,
                "note": f"평소 = 같은 요일·시각 직전 4주 실측 평균 ({wk}). 막대 = 채널별 (실측 − 평소), 모두 더하면 실측."}
    cid = series if "-CH" in series else series.replace("-", "-CH", 1)
    if cid not in ch.index:
        raise ValueError(f"{series} 는 없는 채널입니다.")
    t0 = u["t"]
    # 시간 기준 나눔: 최근 4주 같은 시각(모든 요일) → 같은 요일 → 전주 → 당일
    days = [t0 - pd.Timedelta(days=k) for k in range(1, 29)]
    allw = _at(engine, days)
    b0 = float(allw[cid].dropna().mean()) if cid in allw and allw[cid].notna().any() else float(b[cid])
    b1 = float(b[cid])
    last = t0 - pd.Timedelta(days=7)
    b2 = float(allw.loc[last, cid]) if last in allw.index and cid in allw and pd.notna(allw.loc[last, cid]) else b1
    av = float(a[cid])
    name, kind = _label(cid, ch.loc[cid, "meter_id"])
    return {"time": f"{t0:%Y-%m-%d %H:%M}", "label": name, "mode": "시간 기준",
            "base_name": "최근 4주 같은 시각 평균", "base_kw": round(b0, 3),
            "end_name": "그 시각 실측", "actual_kw": round(av, 3),
            "items": [{"name": "요일 영향 (같은 요일 평균과의 차이)", "kw": round(b1 - b0, 3)},
                      {"name": "최근 1주 변화 (전주 같은 시각)", "kw": round(b2 - b1, 3)},
                      {"name": "당일 추가 사용 (전주 대비)", "kw": round(av - b2, 3)}],
            "note": f"최근 4주 같은 시각 평균(모든 요일)에서 시작해 요일·최근 1주·당일 차이를 더하면 그 시각 실측 {av:.2f} kW. 같은 요일 평균 = {wk}."}
