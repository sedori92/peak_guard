"""[3] 예측모델 — 학습·예측·평가·DB 저장을 한 번에 실행

연결 ─ 화면: ①·③ 에 보이는 모든 예측값·성능 숫자를 만듦 (python run.py 3)
       API : —
       DB  : st_forecast·st_eval·st_peak_eval·st_channel_eval·st_explain·st_meta·peak_threshold (새로 씀)

    python run.py 3                  # 전체 (CPU 2코어 약 5분)
    python run.py 3 --skip-lstm      # Bi-LSTM 제외
    python run.py 3 --auto           # 운영 재학습: 마지막 실측 기준으로 학습·검증·시험 구간을 자동으로 나눔

발행 시각 t(15분마다)에 그때까지 알 수 있는 값만으로 t+15·30·45·60분 수요전력(kW)을 예측합니다.

기간 나누기 (config.py):
  학습  ~ 2026-09-14          → 모델 학습
  검증  2026-09-15 ~ 10-06    → Bi-LSTM 조기 종료, 모델 고르기(검증 MAE)
  시험  2026-10-20 15:15 ~ 11-02 16:30 발행 → 최종 성능 (공통 표본 1,155 발행)

모델 (화면 '예측모델 선택'·'방식 선택' 콤보버튼과 같은 구성):
  전일 동일시각 = 기업 기준모델 (어제 같은 시각 값을 그대로 씀)
  채널별 합산 후 예측 : LightGBM · XGBoost · RandomForest · BiLSTM  (건물 합계 한 줄을 바로 예측)
  채널별 예측 후 합산 : LGBM_SUM · XGB_SUM · RF_SUM · LSTM_SUM      (채널 61개 예측을 같은 시각끼리 더함)

결과: DB 의 st_forecast(시험기간 예측값), st_eval(검증·시험 성능), st_peak_eval(오분류표),
      st_channel_eval(채널별 오차), st_explain(SHAP), peak_threshold, st_meta + models/ 폴더 모델 파일
"""
from __future__ import annotations

import argparse
import json
import time

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import text

from ..config import (BASE_DAY, DAY, H, MAPE_MIN_CHANNEL_KW, MAPE_MIN_TOTAL_KW, MODEL_DIR, MODELS,
                      SUM_BASE, TEST_END, TEST_START, TRAIN_END, VAL_END, VAL_START, WEEK)
from ..database.connection import create_schema, get_engine
from ..database.tables_forecast import meta as fc_meta
from ..database.tables_forecast import st_meta
from ..evaluation.metrics import metrics, peak_detection
from . import baseline, bilstm_model, lightgbm_model, random_forest_model, xgboost_model
from .features import FEAT_COLS, FEATURES, features
from .grid import load_grid, take, thresholds

TREE_MODELS = {"LightGBM": lightgbm_model, "XGBoost": xgboost_model, "RandomForest": random_forest_model}
RESULT_TABLES = ("st_forecast", "st_eval", "st_peak_eval", "st_channel_eval", "st_explain", "st_meta", "peak_threshold")


def main() -> None:
    ap = argparse.ArgumentParser(description="다음 1시간(15분×4) 예측 학습·평가·저장")
    ap.add_argument("--skip-lstm", action="store_true")
    ap.add_argument("--compare", action="store_true",
                    help="기상 사용을 config.USE_WEATHER 와 반대로 바꿔 학습하고 성능만 models/compare_weather.json 에 저장 (DB·모델 파일은 그대로)")
    ap.add_argument("--auto", action="store_true",
                    help="학습·검증·시험 구간을 마지막 실측 기준으로 자동 계산 (시험 AUTO_TEST_DAYS일, 검증 AUTO_VAL_DAYS일, 나머지 학습)")
    a = ap.parse_args()
    run(a.skip_lstm, compare=a.compare, auto=a.auto)


def auto_split(times: pd.DatetimeIndex, total: np.ndarray) -> tuple[str, str, str, str, str]:
    """운영 재학습용 구간: 마지막 실측에서 거꾸로 '자료가 있는 날'을 세어
         시험 = 마지막 AUTO_TEST_DAYS일, 검증 = 그 앞 AUTO_VAL_DAYS일, 학습 = 그 앞 전부.
       수집이 멈춘 날(예: 10-06~10-20 통신회선 교체)은 날짜 수에 넣지 않습니다.
       (TRAIN_END, VAL_START, VAL_END, TEST_START, TEST_END) 를 config 와 같은 글자 형식으로 돌려줌."""
    from ..config import AUTO_TEST_DAYS, AUTO_VAL_DAYS
    have = pd.Series(~np.isnan(total), index=times)
    days = have.groupby(times.normalize()).sum()
    days = days[days >= 8].index                                          # 2시간 이상 자료가 있는 날
    if len(days) < AUTO_TEST_DAYS + AUTO_VAL_DAYS + 14:
        raise SystemExit(f"자료가 있는 날이 {len(days)}일뿐이라 자동 구간(시험 {AUTO_TEST_DAYS}·검증 {AUTO_VAL_DAYS}·학습 14일 이상)을 만들 수 없습니다")
    last = times[have.to_numpy()].max() - pd.Timedelta(hours=1)          # 마지막 발행 = 정답 1시간이 다 있는 시각
    te_day, va_day = days[-AUTO_TEST_DAYS], days[-(AUTO_TEST_DAYS + AUTO_VAL_DAYS)]
    te_a = times[have.to_numpy() & (times >= te_day)].min()               # 시험 첫날의 첫 실측
    va_b = times[have.to_numpy() & (times < te_day)].max() - pd.Timedelta(hours=1)
    f = lambda x: f"{x:%Y-%m-%d %H:%M}"   # noqa: E731
    return f(va_day), f(va_day), f(va_b), f(te_a), f(last)


def run(skip_lstm: bool = False, compare: bool = False, auto: bool = False) -> None:
    """compare=True: 기상 포함/미포함 비교 실행 — 성능 요약만 JSON 으로 남기고 DB·모델 파일은 건드리지 않음."""
    from ..config import USE_WEATHER
    t_start = time.time()
    engine = get_engine()
    save = not compare
    use_w = (not USE_WEATHER) if compare else USE_WEATHER
    if save:
        with engine.begin() as con:                       # 결과 표는 구조가 바뀔 수 있어 새로 만듦
            for t in RESULT_TABLES:
                con.execute(text(f"DROP TABLE IF EXISTS {t}"))
    create_schema(engine)
    fc_meta.create_all(engine)

    print("1) DB에서 15분 격자 만들기")
    g = load_grid(engine, use_weather=use_w)
    print(f"   현재전력 음수 {g.prep['power_negative']:,}건({g.prep['power_negative_pct']}%) → 결측 처리, "
          f"짧은 끊김 보간 {g.prep['short_interpolated_cells']:,}칸, 긴 결측 {g.prep['long_gap_cells']:,}칸(학습 제외) · "
          f"예측 입력 기상 {'포함' if use_w else '미포함'}{' (비교 실행)' if compare else ''}")
    T, S = len(g.times), len(g.series)
    pos = lambda ts: int(np.searchsorted(g.times, pd.Timestamp(ts)))   # noqa: E731
    bounds = (TRAIN_END, VAL_START, VAL_END, TEST_START, TEST_END)      # 기본: config 고정 구간 (발표·산출물 근거)
    if auto:
        bounds = auto_split(g.times, g.y[0])
        print(f"   구간 자동 계산(--auto): 학습 ~{bounds[0]} · 검증 {bounds[1]}~{bounds[2]} · 시험 {bounds[3]}~{bounds[4]}")
    tr_end, va_a, va_b, te_a, te_b = (pos(b) for b in bounds)
    iss = np.arange(WEEK, T - 1)
    split = {"train": iss[iss + H < tr_end],                         # 정답(1시간 뒤)까지 학습기간 안
             "val": iss[(iss >= va_a) & (iss + H <= va_b)],
             "test": iss[(iss >= te_a) & (iss <= te_b)]}
    scale = np.array([max(np.nanpercentile(g.y[s, WEEK:tr_end], 95), 0.05) for s in range(S)])
    for k, v in split.items():
        print(f"   {k:5s} 발행 {len(v):,}개  ({g.times[v[0]]:%Y-%m-%d %H:%M} ~ {g.times[v[-1]]:%Y-%m-%d %H:%M})")

    print("2) 피크 기준(직전 30일 P95)")
    thr = thresholds(g)

    print("3) 특징 만들기")
    X = {k: {s: features(g, s, v, scale[s]) for s in range(S)} for k, v in split.items()}
    def drop_bad(d: pd.DataFrame) -> pd.DataFrame:    # 현재전력 긴 결측(연속 음수 등) 칸은 학습에서 제외 (기업 안내)
        bad = g.p_bad[d["series"].to_numpy().astype(int), d["issue"].to_numpy().astype(int)]
        return d[~bad]
    ch_tr = drop_bad(pd.concat([X["train"][s] for s in range(1, S)], ignore_index=True).dropna(subset=["y", "y0"]))
    tot_tr = drop_bad(X["train"][0].dropna(subset=["y", "y0"]))
    excluded_train = int(sum(g.p_bad[s][split["train"]].sum() for s in range(S)))
    EV = ("val", "test")
    preds: dict[str, dict[str, dict[int, np.ndarray]]] = {k: {} for k in EV}   # 기간 → 모델 → 시계열 → (발행, 4) kW

    def to_mat(df: pd.DataFrame, col: np.ndarray, s: int, k: str) -> np.ndarray:
        out = np.full((len(split[k]), H), np.nan)
        p = np.searchsorted(split[k], df["issue"].to_numpy())
        out[p, df["h"].to_numpy().astype(int) - 1] = col
        return out * scale[s]

    print("4) 기준모델")
    for k in EV:
        for name in (BASE_DAY,):                      # 기업 기준모델: 전일 동일시각
            preds[k][name] = {s: to_mat(X[k][s], baseline.predict(X[k][s], name), s, k) for s in range(S)}

    MODEL_DIR.mkdir(exist_ok=True)
    explain = None
    for name, mod in TREE_MODELS.items():
        print(f"5) {name} (건물 직접 모델 + 채널 공동 모델)")
        t0 = time.time()
        m_tot, m_ch = mod.fit(tot_tr, False), mod.fit(ch_tr, True)
        for k in EV:
            ch = pd.concat([X[k][s] for s in range(1, S)], ignore_index=True)
            p = mod.predict(m_ch, ch)
            preds[k][name] = {0: to_mat(X[k][0], mod.predict(m_tot, X[k][0]), 0, k)}
            preds[k][name] |= {int(s): to_mat(ch.iloc[ix], p[ix], int(s), k) for s, ix in ch.groupby("series").indices.items()}
        if save:
            joblib.dump({"TOTAL": m_tot, "CHANNEL": m_ch, "scale": scale, "series": g.series},
                        MODEL_DIR / f"st_{name}.joblib", compress=3)
        print(f"   {time.time() - t0:.0f}초")
        if name == "LightGBM":                              # 건물 전체 1시간 뒤 예측의 SHAP
            x4 = X["test"][0][X["test"][0]["h"] == H]
            explain = (x4["issue"].to_numpy(), lightgbm_model.explain(m_tot, x4, scale[0]))

    if not skip_lstm:
        print("6) Bi-LSTM (전체 + 채널 함께 학습, 검증기간으로 조기 종료)")
        t0 = time.time()
        cat = lambda parts: tuple(np.concatenate(z) for z in zip(*parts))   # noqa: E731
        def tr_arr(s, iss):                                # 학습용: 현재전력 긴 결측 칸 제외
            a = bilstm_model.lstm_arrays(g, s, iss, scale[s])
            a[2][g.p_bad[s][iss]] = np.nan
            return a
        trp = [tr_arr(0, split["train"])] * 8
        trp += [tr_arr(s, split["train"][::3]) for s in range(1, S)]
        vap = [bilstm_model.lstm_arrays(g, 0, split["val"], scale[0])]
        vap += [bilstm_model.lstm_arrays(g, s, split["val"][::4], scale[s]) for s in range(1, S)]
        net = bilstm_model.BiLSTM(S).fit(cat(trp), cat(vap))
        for k in EV:
            preds[k]["BiLSTM"] = {s: net.predict(bilstm_model.lstm_arrays(g, s, split[k], scale[s])) * scale[s]
                                  for s in range(S)}
        import torch
        if save:
            torch.save({"state": net.net.state_dict(), "params": net.params, "scale": scale, "series": g.series},
                   MODEL_DIR / "st_BiLSTM.pt")
        print(f"   {time.time() - t0:.0f}초")

    actual, common, peak_rows, ev_rows, ch_rows = {}, {}, [], [], []
    models = []
    for k in EV:
        P = preds[k]
        for s in range(S):                 # 발행 시점 최신 실측이 없으면 모든 모델 '예측 불가' (FR-AI-004)
            stale = np.isnan(take(g.yi[s], split[k]))
            for m in P:
                P[m][s][stale] = np.nan
        for sm, base in SUM_BASE.items():  # 채널합산: 활성 채널 예측을 같은 시각끼리 더함 (하나라도 없으면 계산 안 함)
            if base in P:
                P[sm] = {0: np.sum(np.stack([P[base][s] for s in range(1, S)]), axis=0)}
        models = [m for m in MODELS if m in P]
        A = {s: np.stack([take(g.y[s], split[k] + h) for h in range(1, H + 1)], 1) for s in range(S)}
        cm = {s: np.all([~np.isnan(P[m][s]) for m in models if s in P[m]], axis=0) & ~np.isnan(A[s]) for s in range(S)}
        actual[k], common[k] = A, cm
        t_s, t_e = f"{g.times[split[k][0]]:%Y-%m-%d %H:%M}", f"{g.times[split[k][-1]]:%Y-%m-%d %H:%M}"
        day_thr = thr["threshold_kw"].reindex([d.date().isoformat() for d in g.times[split[k]]]).to_numpy().round(2)   # 화면에 쓰는 기준값(소수 둘째 자리)과 같게
        for m in models:
            for scope, ss, low in (("TOTAL", [0], MAPE_MIN_TOTAL_KW), ("CHANNEL", range(1, S), MAPE_MIN_CHANNEL_KW)):
                if scope == "CHANNEL" and m in SUM_BASE:
                    continue                                   # 채널합산 모델의 채널 값 = 원래 모델과 같음
                for h in range(0, H + 1):
                    cols = range(H) if h == 0 else [h - 1]
                    a = np.concatenate([A[s][:, c][cm[s][:, c]] for s in ss for c in cols])
                    p = np.concatenate([P[m][s][:, c][cm[s][:, c]] for s in ss for c in cols])
                    ev_rows.append(dict(model_name=m, scope=scope, horizon_min=15 * h, split=k, **metrics(a, p, low),
                                        test_start=t_s, test_end=t_e))
            ok = cm[0].all(1) & ~np.isnan(day_thr)
            # 저장되는 예측값(소수 넷째 자리)과 같은 값으로 판정 → 화면(피크 기준별 재판정)과 한 건도 다르지 않게
            peak_rows.append(dict(model_name=m, split=k, **peak_detection(A[0], np.round(P[m][0], 4), day_thr, ok)))
            if k == "test" and m not in SUM_BASE:              # 채널별 시험 오차 (보조화면 호실 표)
                for s in range(1, S):
                    c = cm[s]
                    if c.any():
                        e = P[m][s][c] - A[s][c]
                        ch_rows.append(dict(model_name=m, series_id=g.series[s], mae=float(np.mean(np.abs(e))),
                                            bias=float(np.mean(e)), n=int(c.sum())))
    ev, pk = pd.DataFrame(ev_rows), pd.DataFrame(peak_rows)
    show = ev[(ev.horizon_min == 0) & (ev.scope == "TOTAL")].pivot(index="model_name", columns="split", values="mae")
    show["bias(test)"] = ev[(ev.horizon_min == 0) & (ev.scope == "TOTAL") & (ev.split == "test")].set_index("model_name").bias
    print("7) 평가 — 건물 전체 MAE (공통 표본)\n" + show.round(3).to_string())
    print(pk[pk.split == "test"][["model_name", "tp", "fp", "fn", "tn", "recall", "miscls"]].round(3).to_string(index=False))

    if not save:                                           # 기상 미포함 비교: 성능 요약만 남김
        te = ev[(ev.split == "test") & (ev.scope == "TOTAL") & (ev.horizon_min == 0)].set_index("model_name")
        va = ev[(ev.split == "val") & (ev.scope == "TOTAL") & (ev.horizon_min == 0)].set_index("model_name")
        pt = pk[pk.split == "test"].set_index("model_name")
        out = {m: {"mae": float(te.loc[m, "mae"]), "val_mae": float(va.loc[m, "mae"]), "rmse": float(te.loc[m, "rmse"]),
                   "tp": int(pt.loc[m, "tp"]), "fn": int(pt.loc[m, "fn"]), "fp": int(pt.loc[m, "fp"]),
                   "recall": float(pt.loc[m, "recall"])} for m in models if m in te.index}
        (MODEL_DIR / "compare_weather.json").write_text(json.dumps({"weather": use_w, "models": out}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"완료(기상 {'포함' if use_w else '미포함'} 비교): {MODEL_DIR / 'compare_weather.json'} · {time.time() - t_start:.0f}초")
        return

    print("8) DB 저장 (시험기간 예측)")
    keep = [0] + [i for i, c in enumerate(g.series) if c in set(g.rooms_all)]   # 전체 + 호실 채널만 저장
    iss_txt = g.times[split["test"]].strftime("%Y-%m-%d %H:%M:%S").to_numpy()
    rows = []
    for m in models:
        for s in keep:
            if s not in preds["test"][m]:
                continue
            P = preds["test"][m][s]
            for h in range(H):
                ok = ~np.isnan(P[:, h])
                rows.append(pd.DataFrame({"model_name": m, "series_id": g.series[s], "issued_at": iss_txt[ok],
                                          "horizon_min": 15 * (h + 1), "predicted_kw": np.round(P[ok, h], 4)}))
    fc = pd.concat(rows, ignore_index=True)
    fc.to_sql("st_forecast", engine, if_exists="append", index=False, chunksize=50_000)
    ev.to_sql("st_eval", engine, if_exists="append", index=False)
    pk.to_sql("st_peak_eval", engine, if_exists="append", index=False)
    pd.DataFrame(ch_rows).to_sql("st_channel_eval", engine, if_exists="append", index=False)
    thr.reset_index(names="date").assign(method="직전 30일 전체 합산 P95").dropna(subset=["threshold_kw"]) \
        .to_sql("peak_threshold", engine, if_exists="append", index=False)
    imp, base = pd.Series(dtype=float), None
    if explain is not None:
        iss_x, contrib = explain
        names = [FEATURES[c] for c in FEAT_COLS]
        ex = pd.DataFrame(contrib[:, :-1], columns=names)
        ex["issued_at"] = g.times[iss_x].strftime("%Y-%m-%d %H:%M:%S")
        ex = ex.melt(id_vars="issued_at", var_name="feature", value_name="contribution_kw")
        ex[ex.contribution_kw.abs() > 1e-4].to_sql("st_explain", engine, if_exists="append", index=False,
                                                    chunksize=50_000)
        imp = pd.DataFrame(np.abs(contrib[:, :-1]), columns=names).mean().sort_values(ascending=False)
        base = float(contrib[0, -1])
    sp = {k: [f"{g.times[v[0]]:%Y-%m-%d %H:%M}", f"{g.times[v[-1]]:%Y-%m-%d %H:%M}", int(len(v))] for k, v in split.items()}
    info = {"test_start": sp["test"][0], "test_end": sp["test"][1], "splits": sp, "shap_base": base,
            "importance": [[k, round(float(v), 3)] for k, v in imp.head(10).items()],
            "rooms": g.rooms_all, "inactive": sorted(g.inactive), "models": models,
            "scale_total": float(scale[0]), "prep": {**g.prep, "excluded_train_cells": excluded_train},
            "weather": use_w, "auto_split": auto, "alert_ratio": __import__("peakguard.config", fromlist=["ALERT_RATIO"]).ALERT_RATIO}
    with engine.begin() as con:
        con.execute(st_meta.insert(), [{"key": "info", "value": json.dumps(info, ensure_ascii=False)}])
    print(f"완료: 예측 {len(fc):,}행 저장, 총 {time.time() - t_start:.0f}초")


if __name__ == "__main__":
    main()
