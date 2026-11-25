"""[문서] 예측모델 도감 그림·숫자 만들기 — docs/model_guide/*.png + docs/model_guide/numbers.json

    python -m peakguard.tools.model_guide

연결 ─ 화면: ③ 모델 성능 비교에 나오는 모델들의 기본 원리 설명용 (화면에는 나오지 않음)
       API : /api/performance 와 같은 계산(성능 숫자)
       DB  : measurement(실측) · st_forecast(시험기간 예측) · peak_threshold (읽기)

예시 한 장면: 2026-11-02(월) 13:15, 건물 합계(96채널) 15분 수요전력, 피크 기준(최근 30일 P95)
  - 이 시각에 다음 1시간(13:30·13:45·14:00·14:15)을 예측 → 실제로 기준을 넘은 장면
  - 전날(11-01)이 일요일이라 '전일 동일시각' 기준모델이 크게 빗나가는 장면이기도 함
작은 모형(결정나무·랜덤 포레스트·부스팅 그림)은 원리를 보여 주려고 입력 2개(현재 수요·시각)만 쓴 축소판이고,
실제 피크가드 모델(입력 18개)의 숫자는 st_forecast 의 시험기간 예측값을 그대로 씁니다.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..config import ROOT

OUT = ROOT / "docs" / "model_guide"
T0 = pd.Timestamp("2026-11-02 13:15")
C = {"real": "#1f2328", "thr": "#d1242f", "base": "#8a8f98", "rf": "#2e7d32", "lgb": "#e36209", "xgb": "#bf8700",
     "lstm": "#6639ba", "soft": "#f6f8fa", "line": "#d0d7de", "blue": "#0969da"}
NAME = {"전일 동일시각": "기준모델(전일 동일시각)", "RandomForest": "Random Forest", "LightGBM": "LightGBM",
        "XGBoost": "XGBoost", "BiLSTM": "Bi-LSTM"}
COL = {"전일 동일시각": C["base"], "RandomForest": C["rf"], "LightGBM": C["lgb"], "XGBoost": C["xgb"], "BiLSTM": C["lstm"]}


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for f in ("Noto Sans CJK KR", "Noto Sans CJK JP", "NanumGothic", "Malgun Gothic", "AppleGothic"):
        if any(f in x.name for x in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = f
            break
    plt.rcParams.update({"axes.unicode_minus": False, "font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": "#8a8f98", "axes.titleweight": "bold", "axes.titlesize": 13, "figure.dpi": 110})
    return plt


def data() -> dict:
    from ..api.common import engine, q
    from ..live.predictor import grid
    g = grid(engine)
    tot = pd.Series(g.y[0], index=g.times)
    hist = tot[T0 - pd.Timedelta(hours=5, minutes=45): T0]
    nxt = [T0 + pd.Timedelta(minutes=15 * h) for h in range(1, 5)]
    fc = q("SELECT model_name, horizon_min, predicted_kw FROM st_forecast WHERE series_id='TOTAL' AND issued_at=:t",
           t=f"{T0:%Y-%m-%d %H:%M:%S}")
    pred = {m: d.sort_values("horizon_min").predicted_kw.round(2).tolist() for m, d in fc.groupby("model_name")}
    thr = float(q("SELECT threshold_kw FROM peak_threshold WHERE date=:d", d=f"{T0:%Y-%m-%d}").threshold_kw[0])
    # 예측 후 합산(LSTM_SUM)의 호실 채널 값 = 같은 Bi-LSTM 의 채널 예측 (DB에는 호실 34개만 저장)
    ch = q("SELECT series_id, predicted_kw FROM st_forecast WHERE model_name='BiLSTM' AND issued_at=:t AND horizon_min=60 AND series_id!='TOTAL'",
           t=f"{T0:%Y-%m-%d %H:%M:%S}")
    # 원리 그림용 축소판 자료: 학습기간 평일, 입력 = [지금 수요 kW, 시각], 정답 = 1시간 뒤 수요 kW
    from ..config import TRAIN_END
    t = tot[tot.index < pd.Timestamp(TRAIN_END)]
    df = pd.DataFrame({"now": t, "hour": t.index.hour + t.index.minute / 60, "y": t.shift(-4)}).dropna()
    df = df[(df.index.dayofweek < 5)]
    from ..api.performance import performance
    perf = performance("recent30")
    res = {}
    for s in perf["sections"]:
        for r in s["rows"]:
            res[f"{s['key']}:{r['id']}"] = {"mae": r["mae"], "tp": r["peak"]["tp"], "fn": r["peak"]["fn"], "fp": r["peak"]["fp"],
                                             "gain": r["gain"]}
    return {"hist": hist, "next": nxt, "actual": [float(tot[x]) for x in nxt], "pred": pred, "thr": thr,
            "yday": [float(tot[x - pd.Timedelta(days=1)]) for x in nxt],
            "yday_hist": tot[T0 - pd.Timedelta(hours=5, minutes=45) - pd.Timedelta(days=1): T0 + pd.Timedelta(hours=1) - pd.Timedelta(days=1)],
            "today_full": tot[T0 - pd.Timedelta(hours=5, minutes=45): T0 + pd.Timedelta(hours=1)],
            "train": df, "channels": ch, "perf": res, "actual_risk": perf["thr"]["actual_risk"]}


def fig_example(D, plt):
    fig, ax = plt.subplots(figsize=(10, 4.6))
    h = D["hist"]
    xs = [f"{t:%H:%M}" for t in h.index] + [f"{t:%H:%M}" for t in D["next"]]
    n = len(h)
    ax.axvspan(n - 0.5, n + 3.5, color="#fff8c5", zorder=0)
    ax.text(n + 1.5, 9.3, "예측할 1시간", ha="center", fontsize=10, color="#7d4e00")
    ax.plot(range(n), h.values, color=C["real"], lw=2.2, label="실측 (지금까지)")
    ax.plot(range(n - 1, n + 4), [h.values[-1]] + D["actual"], color=C["real"], lw=2.2, ls=(0, (2, 2)), label="실측 (정답, 나중에 확인)")
    ax.axhline(D["thr"], color=C["thr"], ls="--", lw=1.5)
    ax.text(1, D["thr"] + 0.4, f"피크 기준 {D['thr']:.2f} kW (최근 30일 95번째 백분위수)", color=C["thr"], fontsize=10)
    for k, m in enumerate(["전일 동일시각", "RandomForest", "XGBoost", "LightGBM", "BiLSTM"]):
        if m in D["pred"]:
            ax.plot(range(n, n + 4), D["pred"][m], marker="o", ms=5, lw=1.4, color=COL[m], label=NAME[m])
    ax.set_xticks(range(0, len(xs), 4)); ax.set_xticklabels(xs[::4])
    ax.set_ylabel("건물 합계 수요전력 (kW)"); ax.set_ylim(8, 30)
    ax.set_title("같은 문제, 모델별 답 — 2026-11-02(월) 13:15에 다음 1시간 예측")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 0.02), fontsize=9, ncol=3, frameon=True, facecolor="white", edgecolor="#d0d7de")
    fig.tight_layout(); fig.savefig(OUT / "1_example.png"); plt.close(fig)


def fig_baseline(D, plt):
    fig, ax = plt.subplots(figsize=(10, 3.8))
    a, b = D["today_full"], D["yday_hist"]
    x = range(len(a))
    ax.plot(x, a.values, color=C["real"], lw=2.2, label="오늘 11-02(월) 실측")
    ax.plot(range(len(b)), b.values, color=C["base"], lw=2, label="어제 11-01(일) 실측")
    n = len(D["hist"])
    ax.axvspan(n - 0.5, n + 3.5, color="#fff8c5", zorder=0)
    for k in range(4):
        ax.annotate("", xy=(n + k, D["yday"][k] + 0.3), xytext=(n + k, D["yday"][k] + 6), arrowprops=dict(arrowstyle="->", color=C["base"]))
    ax.text(n + 1.5, D["yday"][0] + 7, "어제 같은 시각 값을\n그대로 예측으로", ha="center", fontsize=9.5, color="#57606a")
    ax.axhline(D["thr"], color=C["thr"], ls="--", lw=1.3)
    lab = [f"{t:%H:%M}" for t in a.index]
    ax.set_xticks(range(0, len(lab), 4)); ax.set_xticklabels(lab[::4])
    ax.set_ylabel("kW"); ax.legend(frameon=False, fontsize=9.5, loc="center left")
    ax.set_title("기준모델 '전일 동일시각' — 어제가 휴일이면 크게 빗나감")
    fig.tight_layout(); fig.savefig(OUT / "2_baseline.png"); plt.close(fig)


def _tree_fit(D):
    from sklearn.tree import DecisionTreeRegressor
    tr = D["train"]
    m = DecisionTreeRegressor(max_depth=2, min_samples_leaf=200, random_state=0).fit(tr[["now", "hour"]], tr["y"])
    return m


def _draw_tree(ax, m, x, title, color):
    t = m.tree_
    feat = ["지금 수요", "시각"]
    pos = {0: (0.5, 0.86), 1: (0.25, 0.52), 2: (0.75, 0.52), 3: (0.12, 0.14), 4: (0.38, 0.14), 5: (0.62, 0.14), 6: (0.88, 0.14)}
    # 노드 번호를 위치로 (깊이 2 완전 나무만 그림)
    order = {}
    def walk(i, slot):
        order[i] = slot
        if t.children_left[i] != -1:
            walk(t.children_left[i], 2 * slot + 1); walk(t.children_right[i], 2 * slot + 2)
    walk(0, 0)
    path, i = [0], 0
    while t.children_left[i] != -1:
        i = t.children_left[i] if x[t.feature[i]] <= t.threshold[i] else t.children_right[i]
        path.append(i)
    for i, slot in order.items():
        if slot not in pos:
            continue
        px, py = pos[slot]
        on = i in path
        if t.children_left[i] != -1:
            f = feat[t.feature[i]]
            thv = t.threshold[i]
            txt = f"{f} ≤ {thv:.1f} kW?" if f == "지금 수요" else f"{f} ≤ {int(thv)}:{int(round((thv % 1) * 60)):02d}?"
            for ch, side in ((t.children_left[i], "예"), (t.children_right[i], "아니오")):
                cx, cy = pos[order[ch]]
                ax.plot([px, cx], [py - 0.06, cy + 0.06], color=color if (on and ch in path) else C["line"], lw=2.4 if (on and ch in path) else 1.2)
                ax.text((px + cx) / 2 + (-0.03 if side == "예" else 0.03), (py + cy) / 2, side, fontsize=8.5, color="#57606a", ha="center")
        else:
            txt = f"{t.value[i][0][0]:.1f} kW"
        ax.text(px, py, txt, ha="center", va="center", fontsize=9.5, fontweight="bold" if on else "normal",
                bbox=dict(boxstyle="round,pad=0.4", fc="#fff8c5" if (on and t.children_left[i] == -1) else ("white" if not on else "#f1f8ff"),
                          ec=color if on else C["line"], lw=1.8 if on else 1))
    ax.set_title(title, fontsize=11.5); ax.axis("off"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    return float(m.predict(pd.DataFrame([x], columns=["now", "hour"]))[0])


def fig_tree(D, plt, x):
    m = _tree_fit(D)
    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    p = _draw_tree(ax, m, x, f"결정나무 1그루 (입력 2개 축소판) — 지금 {x[0]:.2f} kW, 13:15 → 예측 {m.predict(pd.DataFrame([x], columns=['now', 'hour']))[0]:.2f} kW", C["blue"])
    fig.tight_layout(); fig.savefig(OUT / "3_tree.png"); plt.close(fig)
    return p


def fig_rf(D, plt, x):
    from sklearn.tree import DecisionTreeRegressor
    tr = D["train"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.9))
    preds = []
    for k, ax in enumerate(axes):
        s = tr.sample(frac=1.0, replace=True, random_state=k + 1)          # 붓스트랩: 자료를 복원 추출로 다시 뽑음
        m = DecisionTreeRegressor(max_depth=2, min_samples_leaf=200, max_features=1, random_state=k + 3).fit(s[["now", "hour"]], s["y"])
        preds.append(_draw_tree(ax, m, x, f"나무 {k + 1} (자료를 다시 뽑아 학습) → {m.predict(pd.DataFrame([x], columns=['now', 'hour']))[0]:.2f} kW", C["rf"]))
    fig.suptitle(f"Random Forest — 여러 나무의 답을 평균: ({' + '.join(f'{p:.2f}' for p in preds)}) ÷ 3 = {np.mean(preds):.2f} kW",
                 fontsize=12.5, fontweight="bold")
    fig.tight_layout(); fig.savefig(OUT / "4_random_forest.png"); plt.close(fig)
    return preds


def fig_boost(D, plt, x):
    from sklearn.ensemble import GradientBoostingRegressor
    tr = D["train"]
    m = GradientBoostingRegressor(max_depth=2, n_estimators=40, learning_rate=0.3, random_state=0).fit(tr[["now", "hour"]], tr["y"])
    X1 = pd.DataFrame([x], columns=["now", "hour"])
    steps = [float(tr["y"].mean())] + [float(p[0]) for p in m.staged_predict(X1)]
    ans = D["actual"][-1]
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13, 4.2), gridspec_kw={"width_ratios": [1.5, 1]})
    ax.step(range(len(steps)), steps, where="post", color=C["lgb"], lw=2.2)
    ax.axhline(ans, color=C["real"], ls=(0, (2, 2)), lw=1.4)
    ax.text(len(steps) - 1, ans + 0.35, f"정답(14:15 실측) {ans:.2f} kW", ha="right", fontsize=9.5)
    for k in (0, 1, 2, 3, 10, len(steps) - 1):
        ax.annotate(f"{steps[k]:.2f}", (k, steps[k]), textcoords="offset points", xytext=(4, -14 if k else 6), fontsize=9, color=C["lgb"])
    ax.set_xlabel("더한 나무 수"); ax.set_ylabel("예측 (kW)")
    ax.set_title("부스팅 — 앞 나무가 틀린 만큼을 다음 나무가 고침 (평균에서 출발)")
    # 오른쪽: LightGBM(잎 중심) vs XGBoost(층 중심) 자라는 방식
    ax2.axis("off"); ax2.set_xlim(0, 1); ax2.set_ylim(0, 1)
    def node(x0, y0, c, t=""):
        ax2.add_patch(plt.Circle((x0, y0), 0.035, color=c)); ax2.text(x0, y0 - 0.08, t, ha="center", fontsize=8)
    def edge(a, b, c):
        ax2.plot([a[0], b[0]], [a[1], b[1]], color=c, lw=1.6)
    ax2.text(0.25, 0.97, "XGBoost: 층마다 고르게", ha="center", fontweight="bold", fontsize=10.5, color=C["xgb"])
    lv = [(0.25, 0.82), (0.13, 0.6), (0.37, 0.6), (0.07, 0.38), (0.19, 0.38), (0.31, 0.38), (0.43, 0.38)]
    for a, b in ((0, 1), (0, 2), (1, 3), (1, 4), (2, 5), (2, 6)):
        edge(lv[a], lv[b], C["xgb"])
    for p in lv:
        node(*p, C["xgb"])
    ax2.text(0.75, 0.97, "LightGBM: 오차 큰 잎부터 깊게", ha="center", fontweight="bold", fontsize=10.5, color=C["lgb"])
    lf = [(0.72, 0.82), (0.6, 0.6), (0.82, 0.6), (0.74, 0.38), (0.9, 0.38), (0.83, 0.16), (0.95, 0.16)]
    for a, b in ((0, 1), (0, 2), (2, 3), (2, 4), (4, 5), (4, 6)):
        edge(lf[a], lf[b], C["lgb"])
    for p in lf:
        node(*p, C["lgb"])
    ax2.text(0.5, 0.02, "같은 '부스팅'이지만 나무를 키우는 순서가 다름", ha="center", fontsize=9.5, color="#57606a")
    fig.tight_layout(); fig.savefig(OUT / "5_boosting.png"); plt.close(fig)
    return steps


def fig_lstm(D, plt):
    h = D["hist"]
    fig, ax = plt.subplots(figsize=(12, 4.4))
    n = len(h)
    ax.bar(range(n), h.values, color="#d8b9ff", width=0.8)
    ax.set_ylim(-9, 34)
    for k in range(0, n, 4):
        ax.text(k, -1.6, f"{h.index[k]:%H:%M}", ha="center", fontsize=8.5, color="#57606a")
    ax.annotate("", xy=(n - 0.5, 29), xytext=(-0.5, 29), arrowprops=dict(arrowstyle="->", color=C["lstm"], lw=2))
    ax.text(n / 2, 30.3, "앞 방향: 07:30 → 13:15 순서로 읽으며 '기억'을 갱신", ha="center", color=C["lstm"], fontsize=10)
    ax.annotate("", xy=(-0.5, 26), xytext=(n - 0.5, 26), arrowprops=dict(arrowstyle="->", color=C["blue"], lw=2))
    ax.text(n / 2, 27.3, "뒤 방향: 13:15 → 07:30 거꾸로도 읽음 (Bidirectional, 양방향)", ha="center", color=C["blue"], fontsize=10)
    ax.add_patch(plt.Rectangle((n + 0.6, 6), 3.2, 12, fc="#fbefff", ec=C["lstm"], lw=1.6))
    ax.text(n + 2.2, 12, "두 방향\n기억 합치기\n+ 어제·지난주\n같은 시각, 요일", ha="center", va="center", fontsize=9)
    for k, v in enumerate(D["pred"]["BiLSTM"]):
        ax.bar(n + 5 + k, v, color=C["lstm"], width=0.8)
        ax.text(n + 5 + k, v + 0.4, f"{v:.1f}", ha="center", fontsize=8.5, color=C["lstm"])
        ax.text(n + 5 + k, -1.6, f"{D['next'][k]:%H:%M}", ha="center", fontsize=8.5, color=C["lstm"])
    ax.axhline(D["thr"], color=C["thr"], ls="--", lw=1.2)
    ax.text(n + 0.7, D["thr"] + 0.5, "피크 기준", color=C["thr"], fontsize=9, ha="left")
    ax.text(n / 2, -6, f"입력: 최근 6시간 15분 값 {n}개 (수요전력·현재전력·시각·주말공휴일)", ha="center", fontsize=10)
    ax.text(n + 6.5, -6, "출력: 다음 1시간 4칸", ha="center", fontsize=10, color=C["lstm"])
    ax.set_xlim(-1.5, n + 9.5); ax.axis("off")
    ax.set_title("Bi-LSTM — 흐름(순서)을 통째로 읽고 4칸을 한 번에 예측", fontsize=13, fontweight="bold")
    fig.tight_layout(); fig.savefig(OUT / "6_bilstm.png"); plt.close(fig)


def fig_methods(D, plt):
    ch = D["channels"].sort_values("predicted_kw", ascending=False)
    from ..api.alerts import label
    top = ch.head(4)
    total_sum = float(D["pred"]["LSTM_SUM"][3])                       # 활성 채널 61개 예측의 합 (DB 저장값)
    rest = total_sum - float(top.predicted_kw.sum())
    fig, ax = plt.subplots(figsize=(12, 4.6)); ax.axis("off"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    def box(x, y, w, h, t, ec, fc="white", fs=9.5, bold=False):
        ax.add_patch(plt.Rectangle((x, y), w, h, fc=fc, ec=ec, lw=1.6)); ax.text(x + w / 2, y + h / 2, t, ha="center", va="center", fontsize=fs, fontweight="bold" if bold else "normal")
    def arr(a, b, c="#57606a"):
        ax.annotate("", xy=b, xytext=a, arrowprops=dict(arrowstyle="->", color=c, lw=1.5))
    ax.text(0.25, 0.95, "① 채널별 합산 후 예측 (기업 권장, 선정)", ha="center", fontweight="bold", fontsize=11.5, color=C["lstm"])
    box(0.05, 0.62, 0.4, 0.14, "96개 채널 실측을 먼저 더함 → 건물 합계 한 줄", C["line"], C["soft"])
    arr((0.25, 0.62), (0.25, 0.5))
    box(0.1, 0.34, 0.3, 0.14, "모델 1개가 건물 합계를 바로 예측", C["lstm"])
    arr((0.25, 0.34), (0.25, 0.22))
    box(0.1, 0.06, 0.3, 0.14, f"14:15 예측 {D['pred']['BiLSTM'][3]:.2f} kW", C["lstm"], "#fbefff", 10.5, True)
    ax.plot([0.5, 0.5], [0.05, 0.95], color=C["line"], lw=1)
    ax.text(0.75, 0.95, "② 채널별 예측 후 합산", ha="center", fontweight="bold", fontsize=11.5, color=C["blue"])
    xs = [0.53, 0.62, 0.71, 0.80]
    for k, (_, r) in enumerate(top.iterrows()):
        box(xs[k], 0.62, 0.08, 0.14, f"{label(r.series_id)}\n{r.predicted_kw:.2f}", C["blue"], fs=8)
        arr((xs[k] + 0.04, 0.62), (0.75, 0.48))
    box(0.89, 0.62, 0.09, 0.14, f"나머지\n57개\n{rest:.2f}", C["blue"], fs=8)
    arr((0.935, 0.62), (0.78, 0.48))
    ax.text(0.75, 0.8, "활성 채널 61개를 각각 예측 (14:15, kW)", ha="center", fontsize=9.5)
    box(0.6, 0.34, 0.3, 0.14, "같은 시각끼리 더함", C["blue"])
    arr((0.75, 0.34), (0.75, 0.22))
    box(0.6, 0.06, 0.3, 0.14, f"14:15 예측 {total_sum:.2f} kW", C["blue"], "#f1f8ff", 10.5, True)
    ax.text(0.5, -0.02, f"정답(14:15 실측) {D['actual'][3]:.2f} kW · 피크 기준 {D['thr']:.2f} kW — 두 방식 모두 '위험'", ha="center", fontsize=10)
    fig.tight_layout(); fig.savefig(OUT / "7_methods.png"); plt.close(fig)
    return total_sum


def fig_results(D, plt):
    P = D["perf"]
    ms = ["전일 동일시각", "RandomForest", "XGBoost", "LightGBM", "BiLSTM"]
    sums = {"RandomForest": "RF_SUM", "XGBoost": "XGB_SUM", "LightGBM": "LGBM_SUM", "BiLSTM": "LSTM_SUM"}
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.2))
    x = np.arange(len(ms)); w = 0.38
    d_mae = [P[f"direct:{m}"]["mae"] for m in ms]
    s_mae = [P[f"direct:{m}"]["mae"] if m == "전일 동일시각" else P[f"sum:{sums[m]}"]["mae"] for m in ms]
    a1.bar(x - w / 2, d_mae, w, color=C["lstm"], label="① 합산 후 예측"); a1.bar(x + w / 2, s_mae, w, color=C["blue"], label="② 예측 후 합산")
    for i, v in enumerate(d_mae):
        a1.text(i - w / 2, v + 0.04, f"{v:.2f}", ha="center", fontsize=8.5)
    a1.set_xticks(x); a1.set_xticklabels([NAME[m].replace("(전일 동일시각)", "\n(전일 동일시각)") for m in ms], fontsize=9)
    a1.set_ylabel("MAE (kW, 작을수록 정확)"); a1.set_title("예측 오차 — 시험기간 10-20~11-02"); a1.legend(frameon=False, fontsize=9)
    rd = [P[f"direct:{m}"]["tp"] for m in ms]
    rs = [P[f"direct:{m}"]["tp"] if m == "전일 동일시각" else P[f"sum:{sums[m]}"]["tp"] for m in ms]
    a2.bar(x - w / 2, rd, w, color=C["lstm"], label="① 합산 후 예측"); a2.bar(x + w / 2, rs, w, color=C["blue"], label="② 예측 후 합산")
    for i, v in enumerate(rd):
        a2.text(i - w / 2, v + 0.5, str(v), ha="center", fontsize=8.5)
    a2.axhline(D["actual_risk"], color=C["thr"], ls="--", lw=1.2); a2.text(4.4, D["actual_risk"] + 0.6, f"실제 위험 {D['actual_risk']}건", color=C["thr"], ha="right", fontsize=9)
    a2.set_xticks(x); a2.set_xticklabels([NAME[m].replace("(전일 동일시각)", "\n(전일 동일시각)") for m in ms], fontsize=9)
    a2.set_ylabel("미리 맞힌 위험 (건)"); a2.set_title("피크 위험 사전 감지 (많을수록 좋음)"); a2.legend(frameon=False, fontsize=9, loc="upper left")
    fig.tight_layout(); fig.savefig(OUT / "8_results.png"); plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    plt = _plt()
    D = data()
    x = [float(D["hist"].values[-1]), 13.25]
    fig_example(D, plt); fig_baseline(D, plt)
    tree_p = fig_tree(D, plt, x); rf_p = fig_rf(D, plt, x); steps = fig_boost(D, plt, x)
    fig_lstm(D, plt); sum_p = fig_methods(D, plt); fig_results(D, plt)
    nums = {"t0": f"{T0:%Y-%m-%d %H:%M}", "thr": round(D["thr"], 2), "now": round(x[0], 2),
            "next": [f"{t:%H:%M}" for t in D["next"]], "actual": [round(v, 2) for v in D["actual"]],
            "yday": [round(v, 2) for v in D["yday"]], "pred": D["pred"], "tree": round(tree_p, 2),
            "rf_trees": [round(p, 2) for p in rf_p], "rf_mean": round(float(np.mean(rf_p)), 2),
            "boost_steps": [round(s, 2) for s in steps[:4]] + [round(steps[10], 2), round(steps[-1], 2)], "boost_n": len(steps) - 1,
            "sum_method_1415": round(sum_p, 2), "perf": D["perf"], "actual_risk": D["actual_risk"],
            "train_rows": int(len(D["train"]))}
    (OUT / "numbers.json").write_text(json.dumps(nums, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {OUT} (그림 8장 + numbers.json)")


if __name__ == "__main__":
    main()
