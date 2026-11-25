"""[분석] 두 번째 화면 아래 '사용패턴 분석' — 실측만 사용 (기업 과제 원문 목표 1, 세부 2·4·5)

연결 ─ 화면: ② 아래 '사용패턴 분석' 7칸 (screen2_patterns.js)
       API : GET /api/patterns
       DB  : measurement·weather_15m (읽기)

  GET /api/patterns?range=all|90d|30d&date=YYYY-MM-DD

  heat        요일 × 시간대 평균 수요전력 (건물 합계 kW)                     — 시간대·요일별 사용 특성
  monthly     월별 평균·최대 수요전력, 사용량(kWh)                            — 월별 사용 특성
  peak_hours  날마다 가장 높았던 15분 칸의 시각 분포 (평일)                   — 피크 반복 패턴
  weather     일 최고기온·평균습도와 일 최대 수요전력 (평일) + 상관계수         — 기상요인 관계
  anomalies   평소(같은 요일·같은 시각 평균)보다 크게 벗어난 15분 칸 (z ≥ 3)    — 이상 사용패턴
  pre_peak    위험일(그날 최대가 피크 기준 이상) 피크 전 2시간 평균 곡선과
              피크 1시간 전부터 가장 많이 늘어난 호실                         — 피크 발생 전 사용패턴
  saving      피크 기준 이상(위험)이 자주 나온 시간대와 그때 많이 쓰는 호실 — 절감이 필요한 시간대·대상
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from ..config import ALERT_RATIO, DAY, FLOORS
from ..models.grid import thresholds

WD = "월화수목금토일"
Z = 2.5                      # 이상 사용패턴: 평소(같은 날 유형·같은 시각)에서 표준편차 2.5배 이상


def _label(cid: str) -> str:
    from ..api.alerts import label
    return label(cid)


@lru_cache(maxsize=8)
def analyze(rng: str = "all", date: str | None = None, key: tuple = ()) -> dict:
    from ..api.common import engine
    from ..live.predictor import grid
    g = grid(engine)
    tot_all = pd.Series(g.y[0], index=g.times)
    end = (pd.Timestamp(date) + pd.Timedelta(days=1)) if date else tot_all.dropna().index.max() + pd.Timedelta(minutes=15)
    start = tot_all.dropna().index.min() if rng == "all" else end - pd.Timedelta(days=int(rng.rstrip("d")))
    sel = (g.times >= start) & (g.times < end)
    times = g.times[sel]
    tot = pd.Series(g.y[0][sel], index=times)
    ok = tot.notna()
    thr_day = thresholds(g)["threshold_kw"]
    out = {"range": rng, "start": f"{tot[ok].index.min():%Y-%m-%d}", "end": f"{tot[ok].index.max():%Y-%m-%d}",
           "days": int(tot[ok].index.normalize().nunique()), "slots": int(ok.sum())}
    t = tot[ok]
    # 1) 요일 × 시간대
    hm = t.groupby([t.index.dayofweek, t.index.hour]).mean().unstack()
    out["heat"] = {"rows": [WD[i] for i in hm.index], "cols": list(range(24)),
                   "values": [[None if np.isnan(v) else round(float(v), 2) for v in r] for r in hm.reindex(columns=range(24)).to_numpy()]}
    # 2) 월별
    mo = t.groupby(t.index.to_period("M")).agg(["mean", "max", "count"])
    out["monthly"] = [{"month": str(p), "avg_kw": round(float(r["mean"]), 2), "max_kw": round(float(r["max"]), 2),
                       "energy_kwh": round(float(r["mean"] * r["count"] * 0.25), 0), "days": round(float(r["count"]) / DAY, 1)}
                      for p, r in mo.iterrows()]
    # 3) 피크 반복 패턴 (평일)
    day = t.groupby(t.index.normalize())
    dmax, dtime = day.max(), day.idxmax()
    wkd = dmax.index.dayofweek < 5
    hours = pd.Series([x.hour for x in dtime[wkd]])
    hist = hours.value_counts().reindex(range(24), fill_value=0)
    top = hist.sort_values(ascending=False)
    lo, hi = int(top.index[0]), int(top.index[0])
    while hist.get(lo - 1, 0) >= hist.max() * 0.4:
        lo -= 1
    while hist.get(hi + 1, 0) >= hist.max() * 0.4:
        hi += 1
    share = hist.loc[lo:hi].sum() / max(1, hist.sum()) * 100
    out["peak_hours"] = {"hist": [int(v) for v in hist], "days": int(wkd.sum()),
                         "summary": f"평일 일 최대의 {share:.0f}%가 {lo}시~{hi + 1}시 사이에 발생 (가장 잦은 시각 {int(top.index[0])}시대)"}
    # 4) 기상요인 (평일 일 최고기온·평균습도 vs 일 최대)
    temp = pd.Series(g.temp[sel], index=times) if g.temp is not None else pd.Series(dtype=float)
    hum = pd.Series(g.hum[sel], index=times) if g.hum is not None else pd.Series(dtype=float)
    wdf = pd.DataFrame({"peak": dmax, "tmax": temp.groupby(temp.index.normalize()).max(),
                        "hum": hum.groupby(hum.index.normalize()).mean()}).dropna()
    wdf = wdf[wdf.index.dayofweek < 5]
    r_t = float(wdf.peak.corr(wdf.tmax)) if len(wdf) > 3 else None
    r_h = float(wdf.peak.corr(wdf.hum)) if len(wdf) > 3 else None
    out["weather"] = {"points": [{"date": f"{d:%m-%d}", "tmax": round(float(r.tmax), 1), "hum": round(float(r.hum), 0),
                                  "peak": round(float(r.peak), 2)} for d, r in wdf.iterrows()],
                      "r_temp": None if r_t is None else round(r_t, 2), "r_hum": None if r_h is None else round(r_h, 2),
                      "summary": ("자료 부족" if r_t is None else
                                  f"평일 일 최고기온과 일 최대 수요전력의 상관 {r_t:+.2f}, 평균습도와 {r_h:+.2f} "
                                  f"({'관계 약함' if abs(r_t) < 0.3 else '관계 있음'}, {len(wdf)}일)")}
    # 5) 이상 사용패턴: 같은 날 유형(평일은 요일별, 주말·공휴일은 한 묶음)·같은 시각의 평균·표준편차로 z 점수
    off = pd.Series(g.offday[sel], index=times)[ok].to_numpy()
    dtype = np.where(off, 9, t.index.dayofweek)
    key2 = [dtype, t.index.hour * 4 + t.index.minute // 15]
    mu, sd = t.groupby(key2).transform("mean"), t.groupby(key2).transform("std")
    z = ((t - mu) / sd.replace(0, np.nan)).dropna()
    big = z[z.abs() >= Z].sort_values(key=lambda s: -s.abs()).head(400)
    rooms = [i for i, c in enumerate(g.series) if i > 0 and c.split("-")[0] in FLOORS]
    an, seen = [], set()
    for ts, zz in big.items():
        if ts.normalize() in seen:                         # 같은 날은 가장 큰 것 하나만
            continue
        seen.add(ts.normalize())
        j = int(np.searchsorted(g.times, ts))
        same = [j - 7 * DAY * k for k in range(1, 5) if j - 7 * DAY * k >= 0 and g.offday[j - 7 * DAY * k] == g.offday[j]]
        same = same or [j - 7 * DAY]
        dev = {g.series[s]: float(g.y[s][j] - np.nanmean(g.y[s][same])) for s in rooms if not np.isnan(g.y[s][j])}
        who = max(dev, key=dev.get) if dev and zz > 0 else (min(dev, key=dev.get) if dev else None)
        an.append({"time": f"{ts:%Y-%m-%d %H:%M}", "wd": WD[ts.dayofweek] + ("(휴일)" if g.offday[j] and ts.dayofweek < 5 else ""), "kw": round(float(t[ts]), 2),
                   "usual_kw": round(float(mu[ts]), 2), "z": round(float(zz), 1), "kind": "급증" if zz > 0 else "급감",
                   "room": _label(who) if who else "—", "room_dev": round(dev[who], 2) if who else None})
        if len(an) >= 10:
            break
    out["anomalies"] = {"rule": "같은 날 유형(평일 요일별 / 주말·공휴일)·같은 시각 평균에서 표준편차 2.5배 이상 벗어난 15분 칸 (하루에 가장 큰 1건)",
                        "rows": an, "count": int((z.abs() >= Z).sum()), "days": int(len({x.normalize() for x in z[z.abs() >= Z].index}))}
    # 6) 피크 전 사용패턴 (위험일: 그날 최대 ≥ 그날 피크 기준)
    curves, risk_days, rise = [], 0, {}
    for d0, tm in dtime.items():
        th = thr_day.get(f"{d0:%Y-%m-%d}")
        if th is None or np.isnan(th) or dmax[d0] < th:
            continue
        j = int(np.searchsorted(g.times, tm))
        if j < 8:
            continue
        c = g.y[0][j - 8:j + 1]
        if np.isnan(c).any():
            continue
        risk_days += 1
        curves.append(c / c[-1] * 100)
        for s in rooms:
            a, b = g.y[s][j], g.y[s][j - 4]
            if not (np.isnan(a) or np.isnan(b)):
                rise.setdefault(g.series[s], []).append(a - b)
    avg = np.mean(curves, axis=0) if curves else None
    rr = sorted(((k, float(np.mean(v))) for k, v in rise.items()), key=lambda kv: -kv[1])[:5]
    out["pre_peak"] = {"risk_days": risk_days, "minutes": list(range(-120, 1, 15)),
                       "curve_pct": None if avg is None else [round(float(v), 1) for v in avg],
                       "rooms": [{"name": _label(k), "rise_kw": round(v, 2)} for k, v in rr],
                       "summary": ("위험일 없음" if avg is None else
                                   f"위험일 {risk_days}일: 피크 2시간 전 수요는 피크의 {avg[0]:.0f}%, 1시간 전 {avg[4]:.0f}%, "
                                   f"30분 전 {avg[6]:.0f}% — 마지막 1시간에 {100 - avg[4]:.0f}%p 오름")}
    # 7) 절감이 필요한 시간대: 그날 피크 기준 이상(위험)이 나온 빈도 (평일, 15분 칸)
    th_s = pd.Series([thr_day.get(f"{x:%Y-%m-%d}", np.nan) for x in t.index], index=t.index)
    hitm = (t >= th_s * ALERT_RATIO) & th_s.notna()
    wk = t.index.dayofweek < 5
    slot = t.index.hour * 4 + t.index.minute // 15
    freq = hitm[wk].groupby(slot[wk]).mean() * 100
    hot = freq[freq >= max(10.0, freq.max() * 0.5)] if len(freq) else freq
    blocks = []
    for s0 in hot.index:
        if blocks and s0 == blocks[-1][1] + 1:
            blocks[-1][1] = s0
        else:
            blocks.append([s0, s0])
    fmt = lambda s: f"{s // 4:02d}:{s % 4 * 15:02d}"  # noqa: E731
    sv = []
    for a, b in blocks:
        mask = wk & (slot >= a) & (slot <= b) & hitm.to_numpy()
        idx = [int(np.searchsorted(g.times, x)) for x in t.index[mask]]
        share = {}
        for s in rooms:
            v = np.nanmean(g.y[s][idx]) if idx else np.nan
            if not np.isnan(v):
                share[g.series[s]] = float(v)
        tops = sorted(share.items(), key=lambda kv: -kv[1])[:3]
        sv.append({"from": fmt(a), "to": fmt(b + 1), "freq_pct": round(float(freq.loc[a:b].mean()), 0),
                   "rooms": [f"{_label(k)} {v:.2f} kW" for k, v in tops]})
    sv.sort(key=lambda x: -x["freq_pct"])
    out["saving"] = {"rule": "평일 15분 칸 중 그날 피크 기준 이상이 나온 비율 (위험 빈도)",
                     "freq": {fmt(int(k)): round(float(v), 1) for k, v in freq.items()}, "blocks": sv[:4],
                     "summary": ("위험 빈도가 높은 시간대 없음" if not sv else
                                 "절감 필요 시간대: " + ", ".join(f"평일 {x['from']}~{x['to']} (위험 빈도 {x['freq_pct']:.0f}%)" for x in sv[:3]))}
    return out
