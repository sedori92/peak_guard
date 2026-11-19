"""다음 1시간(15분 × 4) 수요전력 예측 — 요구사항 FR-AI-001~003, FR-EV-001~004, FR-AL-002

발행 시각 t(15분마다)에 그때까지 알 수 있는 값만으로 t+15, t+30, t+45, t+60분의 수요전력(kW)을 예측한다.
  - 대상: 건물 전체 합산(TOTAL) + 활성 채널 61개
  - 모델: 기준모델 2종(전일 동일시각, 최근값 유지) + LightGBM + RandomForest + Bi-LSTM
  - 피크 기준: 그날 이전 30일 전체 합산 15분 수요전력의 95백분위수(P95)
  - 결과는 DB(st_forecast, st_eval, st_explain, peak_threshold)에 저장 → API·화면이 읽어 감

사용법
    python -m peakguard.shortterm                 # 전체 실행 (CPU 2코어 약 15~25분)
    python -m peakguard.shortterm --skip-lstm     # Bi-LSTM 제외 (약 5분)
"""
from __future__ import annotations

import argparse
import copy
import json
import time
from dataclasses import dataclass
from pathlib import Path

import holidays
import joblib
import numpy as np
import pandas as pd
from sqlalchemy import (Column, DateTime, Float, Integer, MetaData, PrimaryKeyConstraint, String, Table,
                        Text, text)

from .db import get_engine

H = 4            # 선행 구간 수 (15·30·45·60분)
DAY = 96         # 하루 15분 칸 수
WEEK = 7 * DAY
SEQ = 24         # Bi-LSTM 입력 길이 (6시간)
SEED = 42
ROOM_METERS = ("M002", "M004", "M006", "M008")   # 호실별 계량기 (나머지는 공용)
MODEL_DIR = Path("models")

BASE_DAY, BASE_LAST = "전일 동일시각", "최근값 유지"
MODELS = [BASE_DAY, BASE_LAST, "LightGBM", "RandomForest", "BiLSTM"]

# 특징 이름 → 화면 표시 이름
FEATURES = {
    "y0": "현재 수요전력", "p0": "현재전력 합계(순간값)", "y1": "15분 전 수요", "y2": "30분 전 수요",
    "y3": "45분 전 수요", "y4": "1시간 전 같은 위치", "y8": "2시간 전", "d1": "15분 변화량",
    "max1h": "최근 1시간 최대", "mean4h": "최근 4시간 평균", "lag_day": "전일 같은 시각",
    "lag_week": "전주 같은 시각", "slot": "예측 시각(시간대)", "dow": "요일", "offday": "주말·공휴일",
    "h": "선행시간", "series": "채널", "scale": "채널 규모",
}
FEAT_COLS = list(FEATURES)

# ---------------------------------------------------------------- DB 테이블
meta = MetaData()
st_forecast = Table(  # 15분 × 4 예측값 (전체 + 호실 채널)
    "st_forecast", meta,
    Column("model_name", String(20), nullable=False),
    Column("series_id", String(16), nullable=False),          # 'TOTAL' 또는 channel_id
    Column("issued_at", DateTime, nullable=False),            # 발행(예측 생성 기준) 시각
    Column("horizon_min", Integer, nullable=False),           # 15/30/45/60
    Column("predicted_kw", Float, nullable=False),
    PrimaryKeyConstraint("model_name", "series_id", "issued_at", "horizon_min"),
)
st_eval = Table(      # 평가 지표
    "st_eval", meta,
    Column("model_name", String(20), nullable=False),
    Column("scope", String(10), nullable=False),              # TOTAL / CHANNEL
    Column("horizon_min", Integer, nullable=False),           # 0 = 4개 구간 평균
    Column("mae", Float), Column("rmse", Float), Column("mape", Float),
    Column("n", Integer), Column("n_mape", Integer),
    Column("test_start", String(10)), Column("test_end", String(10)),
    PrimaryKeyConstraint("model_name", "scope", "horizon_min"),
)
st_peak_eval = Table(  # 피크 사전 감지 성능 (전체 합산)
    "st_peak_eval", meta,
    Column("model_name", String(20), primary_key=True),
    Column("precision", Float), Column("recall", Float), Column("f1", Float),
    Column("tp", Integer), Column("fp", Integer), Column("fn", Integer), Column("n", Integer),
)
st_explain = Table(   # LightGBM 전체 합산 1시간 뒤 예측의 SHAP 기여도 (kW)
    "st_explain", meta,
    Column("issued_at", DateTime, nullable=False),
    Column("feature", String(30), nullable=False),
    Column("contribution_kw", Float, nullable=False),
    PrimaryKeyConstraint("issued_at", "feature"),
)
st_meta = Table("st_meta", meta, Column("key", String(40), primary_key=True), Column("value", Text))
peak_threshold = Table(
    "peak_threshold", meta,
    Column("date", String(10), primary_key=True),
    Column("threshold_kw", Float), Column("n_samples", Integer),
    Column("method", String(40), nullable=False),
)


# ---------------------------------------------------------------- 데이터 준비
@dataclass
class Grid:
    times: pd.DatetimeIndex
    series: list[str]          # ['TOTAL', 활성 채널...]
    y: np.ndarray              # (S, T) 수요전력 원본 (결측 NaN) — 정답
    yi: np.ndarray             # (S, T) 입력용 (1시간 이하 끊김 보간)
    p: np.ndarray              # (S, T) 현재전력 순간값 (입력용)
    offday: np.ndarray         # (T,)
    holiday: np.ndarray        # (T,)
    rooms_all: list[str]       # 화면용 호실 채널 34개 (비활성 포함)
    inactive: set[str]


def load_grid(engine) -> Grid:
    with engine.connect() as con:
        m = pd.read_sql(text("SELECT channel_id, measured_at, power_kw, demand_kw FROM measurement"), con)
        ch = pd.read_sql(text("SELECT channel_id, meter_id, is_active FROM channel"), con)
    m["measured_at"] = pd.to_datetime(m["measured_at"])
    if getattr(m["measured_at"].dt, "tz", None) is not None:
        m["measured_at"] = m["measured_at"].dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    active = ch.loc[ch.is_active.astype(bool), "channel_id"].tolist()
    t0, t1 = m.measured_at.min().normalize(), m.measured_at.max()
    times = pd.date_range(t0, t1, freq="15min")
    dem = m.pivot_table(index="measured_at", columns="channel_id", values="demand_kw").reindex(times)
    pw = m.pivot_table(index="measured_at", columns="channel_id", values="power_kw").reindex(times)
    dem, pw = dem[active], pw[active]
    total = dem.sum(axis=1, min_count=len(active))
    total_p = pw.sum(axis=1, min_count=len(active))
    y = np.vstack([total.to_numpy(), dem.to_numpy().T])
    p = np.vstack([total_p.to_numpy(), pw.to_numpy().T])
    yi = pd.DataFrame(y.T).interpolate(limit=4, limit_area="inside").to_numpy().T
    pi = pd.DataFrame(p.T).interpolate(limit=4, limit_area="inside").to_numpy().T
    pi = np.where(np.isnan(pi), yi, pi)                     # 순간값 없으면 수요전력으로 대체
    kr = holidays.KR(years=sorted({t.year for t in times}))
    hol = np.array([d in kr for d in times.date])
    off = hol | (times.dayofweek >= 5)
    rooms_all = ch.loc[ch.meter_id.isin(ROOM_METERS), "channel_id"].sort_values().tolist()
    return Grid(times, ["TOTAL"] + active, y, yi, pi, off, hol, rooms_all,
                set(ch.loc[~ch.is_active.astype(bool), "channel_id"]))


def thresholds(g: Grid) -> pd.Series:
    """날짜별 피크 기준 = 직전 30일 전체 합산 15분 수요전력의 P95 (7일치 이상 있을 때만)."""
    tot = pd.Series(g.y[0], index=g.times)
    out = {}
    for d in pd.date_range(g.times[0].normalize(), g.times[-1].normalize(), freq="D"):
        w = tot[(tot.index >= d - pd.Timedelta("30D")) & (tot.index < d)].dropna()
        out[d.date().isoformat()] = (float(np.percentile(w, 95)) if len(w) >= 7 * DAY else np.nan, len(w))
    return pd.DataFrame(out, index=["threshold_kw", "n_samples"]).T


# ---------------------------------------------------------------- 특징
def _take(a: np.ndarray, idx: np.ndarray) -> np.ndarray:
    out = np.full(idx.shape, np.nan)
    ok = (idx >= 0) & (idx < len(a))
    out[ok] = a[idx[ok]]
    return out


def features(g: Grid, s: int, issues: np.ndarray, scale: float) -> pd.DataFrame:
    """시계열 s, 발행 시각 인덱스 issues → (발행 × 4구간) 행. 값은 scale 로 정규화."""
    yi, p, y = g.yi[s] / scale, g.p[s] / scale, g.y[s] / scale
    rows = []
    base = {
        "y0": _take(yi, issues), "p0": _take(p, issues),
        "y1": _take(yi, issues - 1), "y2": _take(yi, issues - 2), "y3": _take(yi, issues - 3),
        "y4": _take(yi, issues - 4), "y8": _take(yi, issues - 8),
    }
    base["d1"] = base["y0"] - base["y1"]
    base["max1h"] = np.nanmax(np.vstack([base[k] for k in ("y0", "y1", "y2", "y3")]), axis=0)
    base["mean4h"] = np.nanmean(np.vstack([_take(yi, issues - k) for k in range(16)]), axis=0)
    for h in range(1, H + 1):
        tgt = issues + h
        f = dict(base)
        f.update({
            "lag_day": _take(yi, tgt - DAY), "lag_week": _take(yi, tgt - WEEK),
            "slot": tgt % DAY, "dow": _take(g.times.dayofweek.to_numpy().astype(float), tgt),
            "offday": _take(g.offday.astype(float), tgt), "h": np.full(len(issues), h),
            "series": np.full(len(issues), s), "scale": np.full(len(issues), scale),
            "issue": issues, "y": _take(y, tgt),
        })
        rows.append(pd.DataFrame(f))
    return pd.concat(rows, ignore_index=True)


# ---------------------------------------------------------------- 모델
def fit_lgbm(df: pd.DataFrame, channel_scope: bool):
    import lightgbm as lgb
    m = lgb.LGBMRegressor(n_estimators=600 if channel_scope else 800, learning_rate=0.05, num_leaves=63,
                          min_child_samples=40, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                          random_state=SEED, verbose=-1)
    m.fit(df[FEAT_COLS], df["y"])
    return m


def fit_rf(df: pd.DataFrame, channel_scope: bool):
    from sklearn.ensemble import RandomForestRegressor
    if channel_scope and len(df) > 400_000:
        df = df.sample(400_000, random_state=SEED)
    m = RandomForestRegressor(n_estimators=150, min_samples_leaf=10 if channel_scope else 3,
                              max_features=0.6, n_jobs=-1, random_state=SEED)
    m.fit(df[FEAT_COLS].fillna(-1), df["y"])
    return m


def predict_tree(m, df: pd.DataFrame, kind: str) -> np.ndarray:
    x = df[FEAT_COLS] if kind == "LightGBM" else df[FEAT_COLS].fillna(-1)
    return np.clip(m.predict(x), 0, None)


# Bi-LSTM ---------------------------------------------------------
def lstm_arrays(g: Grid, s: int, issues: np.ndarray, scale: float):
    yi, p = g.yi[s] / scale, g.p[s] / scale
    k = np.arange(-SEQ + 1, 1)
    idx = issues[:, None] + k[None, :]                                     # (N, 24)
    ang = 2 * np.pi * (idx % DAY) / DAY
    seq = np.stack([_take(yi, idx), _take(p, idx), np.sin(ang), np.cos(ang),
                    _take(g.offday.astype(float), idx)], axis=2)
    last = seq[:, -1, 0:1]
    seq[:, :, 0] = np.where(np.isnan(seq[:, :, 0]), last, seq[:, :, 0])
    seq[:, :, 1] = np.where(np.isnan(seq[:, :, 1]), seq[:, :, 0], seq[:, :, 1])
    seq[:, :, 4] = np.nan_to_num(seq[:, :, 4])
    tg = issues[:, None] + np.arange(1, H + 1)[None, :]
    lagd, lagw = _take(yi, tg - DAY), _take(yi, tg - WEEK)
    lagd = np.where(np.isnan(lagd), last, lagd); lagw = np.where(np.isnan(lagw), lagd, lagw)
    dow = np.zeros((len(issues), 7)); dow[np.arange(len(issues)), g.times.dayofweek[np.minimum(tg[:, 0], len(g.times) - 1)]] = 1
    a1 = 2 * np.pi * (tg[:, 0] % DAY) / DAY
    stat = np.hstack([lagd, lagw, dow, _take(g.offday.astype(float), tg[:, 0])[:, None].clip(0, 1),
                      np.sin(a1)[:, None], np.cos(a1)[:, None]])
    stat = np.nan_to_num(stat)
    seq = np.nan_to_num(seq)
    y = _take(g.y[s] / scale, tg)
    y[np.isnan(_take(yi, issues))] = np.nan        # 발행 시점 최신값이 없으면 학습·평가에서 제외
    return seq.astype(np.float32), stat.astype(np.float32), y.astype(np.float32), np.full(len(issues), s)


class BiLSTM:
    def __init__(self, n_series: int, hidden: int = 48, emb: int = 8):
        import torch
        from torch import nn
        torch.manual_seed(SEED)
        self.params = dict(hidden=hidden, emb=emb, seq=SEQ)

        class Net(nn.Module):
            def __init__(s):
                super().__init__()
                s.lstm = nn.LSTM(5, hidden, batch_first=True, bidirectional=True)
                s.emb = nn.Embedding(n_series, emb)
                s.head = nn.Sequential(nn.Linear(2 * hidden + emb + 18, 64), nn.ReLU(), nn.Linear(64, H))

            def forward(s, seq, stat, sid):
                _, (h, _) = s.lstm(seq)
                return s.head(torch.cat([h[0], h[1], s.emb(sid), stat], 1))

        self.net = Net()

    def fit(self, tr, va, epochs: int = 25, patience: int = 4, lr: float = 2e-3, batch: int = 512):
        import torch
        from torch.utils.data import DataLoader, TensorDataset
        seq, stat, y, sid = tr
        ok = ~np.isnan(y).any(1)
        ds = TensorDataset(*(torch.from_numpy(a[ok]) for a in (seq, stat, y)), torch.from_numpy(sid[ok]))
        dl = DataLoader(ds, batch_size=batch, shuffle=True, generator=torch.Generator().manual_seed(SEED))
        opt = torch.optim.Adam(self.net.parameters(), lr=lr)
        loss_fn = torch.nn.HuberLoss(delta=0.5)
        best, best_state, bad = np.inf, None, 0
        for ep in range(1, epochs + 1):
            t0 = time.time(); self.net.train()
            for a, b, c, d in dl:
                opt.zero_grad(); loss = loss_fn(self.net(a, b, d), c); loss.backward(); opt.step()
            pv = self.predict(va); m = ~np.isnan(va[2])
            vl = float(np.mean(np.abs(pv - va[2])[m]))
            print(f"    epoch {ep:2d} val MAE(정규화) {vl:.4f} ({time.time() - t0:.0f}s)")
            if vl < best - 1e-4:
                best, best_state, bad = vl, copy.deepcopy(self.net.state_dict()), 0
            else:
                bad += 1
                if bad >= patience:
                    break
        self.net.load_state_dict(best_state)
        return self

    def predict(self, data) -> np.ndarray:
        import torch
        seq, stat, _, sid = data
        self.net.eval(); out = []
        with torch.no_grad():
            for i in range(0, len(seq), 8192):
                out.append(self.net(torch.from_numpy(seq[i:i + 8192]), torch.from_numpy(stat[i:i + 8192]),
                                    torch.from_numpy(sid[i:i + 8192])).numpy())
        return np.clip(np.vstack(out), 0, None)


# ---------------------------------------------------------------- 평가
def metrics(a: np.ndarray, p: np.ndarray, low: float) -> dict:
    ok = ~np.isnan(a) & ~np.isnan(p)
    a, p = a[ok], p[ok]
    if len(a) == 0:
        return dict(mae=None, rmse=None, mape=None, n=0, n_mape=0)
    m = a >= low                                       # MAPE 는 저사용량 제외 (FR-EV-003)
    return dict(mae=float(np.mean(np.abs(a - p))), rmse=float(np.sqrt(np.mean((a - p) ** 2))),
                mape=float(np.mean(np.abs(a[m] - p[m]) / a[m]) * 100) if m.any() else None,
                n=int(len(a)), n_mape=int(m.sum()))


# ---------------------------------------------------------------- 메인
def main() -> None:
    ap = argparse.ArgumentParser(description="다음 1시간(15분×4) 예측 학습·평가·저장")
    ap.add_argument("--test-start", default="2026-09-28", help="평가 시작일 (이전은 학습)")
    ap.add_argument("--skip-lstm", action="store_true")
    a = ap.parse_args()
    t_start = time.time()
    engine = get_engine()
    meta.create_all(engine)

    print("1) DB에서 15분 격자 만들기")
    g = load_grid(engine)
    T = len(g.times)
    test0 = int(np.searchsorted(g.times, pd.Timestamp(a.test_start)))
    val0 = test0 - 14 * DAY
    all_issues = np.arange(WEEK, T - 1)
    tr_iss, te_iss = all_issues[all_issues + H < test0], all_issues[all_issues >= test0]
    tr_iss_lstm, va_iss = tr_iss[tr_iss < val0 - H], tr_iss[tr_iss >= val0]
    S = len(g.series)
    scale = np.array([max(np.nanpercentile(g.y[s, WEEK:test0], 95), 0.05) for s in range(S)])
    print(f"   시계열 {S}개, 학습 발행 {len(tr_iss):,}개, 평가 발행 {len(te_iss):,}개 "
          f"({g.times[te_iss[0]]:%Y-%m-%d} ~ {g.times[te_iss[-1]]:%Y-%m-%d})")

    print("2) 피크 기준(직전 30일 P95)")
    thr = thresholds(g)

    print("3) 특징 만들기")
    tr = {s: features(g, s, tr_iss, scale[s]) for s in range(S)}
    te = {s: features(g, s, te_iss, scale[s]) for s in range(S)}
    ch_tr = pd.concat([tr[s] for s in range(1, S)], ignore_index=True).dropna(subset=["y", "y0"])
    ch_te = pd.concat([te[s] for s in range(1, S)], ignore_index=True)
    tot_tr = tr[0].dropna(subset=["y", "y0"])

    preds: dict[str, dict[int, np.ndarray]] = {}          # model → series → (len(te_iss), 4) kW
    def to_mat(df: pd.DataFrame, col: np.ndarray, s: int) -> np.ndarray:
        out = np.full((len(te_iss), H), np.nan)
        pos = np.searchsorted(te_iss, df["issue"].to_numpy())
        out[pos, df["h"].to_numpy().astype(int) - 1] = col
        return out * scale[s]

    def split_series(df: pd.DataFrame, p: np.ndarray) -> dict[int, np.ndarray]:
        res = {}
        for s, idx in df.groupby("series").indices.items():
            res[int(s)] = to_mat(df.iloc[idx], p[idx], int(s))
        return res

    print("4) 기준모델")
    for name, col in ((BASE_DAY, "lag_day"), (BASE_LAST, "y0")):
        preds[name] = {s: to_mat(te[s], te[s][col].to_numpy(), s) for s in range(S)}

    MODEL_DIR.mkdir(exist_ok=True)
    explain = None
    for name, fit in (("LightGBM", fit_lgbm), ("RandomForest", fit_rf)):
        print(f"5) {name}")
        t0 = time.time()
        m_tot = fit(tot_tr, False)
        m_ch = fit(ch_tr, True)
        p_tot = predict_tree(m_tot, te[0], name)
        p_ch = predict_tree(m_ch, ch_te, name)
        preds[name] = {0: to_mat(te[0], p_tot, 0)} | split_series(ch_te, p_ch)
        joblib.dump({"TOTAL": m_tot, "CHANNEL": m_ch, "scale": scale, "series": g.series},
                    MODEL_DIR / f"st_{name}.joblib", compress=3)
        print(f"   {time.time() - t0:.0f}초")
        if name == "LightGBM":  # 전체 합산 1시간 뒤(h=4) 예측의 SHAP 기여도
            x4 = te[0][te[0]["h"] == H]
            contrib = m_tot.predict(x4[FEAT_COLS], pred_contrib=True) * scale[0]
            explain = (x4["issue"].to_numpy(), contrib)

    if not a.skip_lstm:
        print("6) Bi-LSTM (전체 + 채널 함께 학습)")
        t0 = time.time()
        def cat(parts):
            return tuple(np.concatenate(z) for z in zip(*parts))
        trp = [lstm_arrays(g, 0, tr_iss_lstm, scale[0])] * 8      # 전체 합산 8배 가중
        trp += [lstm_arrays(g, s, tr_iss_lstm[::3], scale[s]) for s in range(1, S)]
        vap = [lstm_arrays(g, 0, va_iss, scale[0])] + [lstm_arrays(g, s, va_iss[::4], scale[s]) for s in range(1, S)]
        net = BiLSTM(S).fit(cat(trp), cat(vap))
        preds["BiLSTM"] = {}
        for s in range(S):
            d = lstm_arrays(g, s, te_iss, scale[s])
            pv = net.predict(d) * scale[s]
            pv[np.isnan(_take(g.yi[s], te_iss))] = np.nan            # 최신값 없는 발행은 예측 불가
            preds["BiLSTM"][s] = pv
        import torch
        torch.save({"state": net.net.state_dict(), "params": net.params, "scale": scale, "series": g.series},
                   MODEL_DIR / "st_BiLSTM.pt")
        print(f"   {time.time() - t0:.0f}초")

    print("7) 평가")
    models = [m for m in MODELS if m in preds]
    actual = {s: np.stack([_take(g.y[s], te_iss + h) for h in range(1, H + 1)], 1) for s in range(S)}
    # 모든 모델이 예측을 낸 칸만 비교 (공통 표본)
    common = {s: np.all([~np.isnan(preds[m][s]) for m in models], axis=0) & ~np.isnan(actual[s]) for s in range(S)}
    ev_rows, peak_rows = [], []
    t_s, t_e = g.times[te_iss[0]].date().isoformat(), g.times[te_iss[-1]].date().isoformat()
    for m in models:
        for scope, ss, low in (("TOTAL", [0], 1.0), ("CHANNEL", range(1, S), 0.1)):
            for h in range(0, H + 1):
                cols = range(H) if h == 0 else [h - 1]
                A = np.concatenate([actual[s][:, c][common[s][:, c]] for s in ss for c in cols])
                P = np.concatenate([preds[m][s][:, c][common[s][:, c]] for s in ss for c in cols])
                ev_rows.append(dict(model_name=m, scope=scope, horizon_min=15 * h, **metrics(A, P, low),
                                    test_start=t_s, test_end=t_e))
        # 피크 사전 감지: 발행 시점에 '다음 1시간 중 하나라도 기준 이상' 예측 vs 실제
        day_thr = thr["threshold_kw"].reindex([d.date().isoformat() for d in g.times[te_iss]]).to_numpy()
        ok = common[0].all(1) & ~np.isnan(day_thr)
        pa = (actual[0] >= day_thr[:, None]).any(1)[ok]
        pp = (preds[m][0] >= day_thr[:, None]).any(1)[ok]
        tp, fp, fn = int((pa & pp).sum()), int((~pa & pp).sum()), int((pa & ~pp).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0; rec = tp / (tp + fn) if tp + fn else 0.0
        peak_rows.append(dict(model_name=m, precision=prec, recall=rec,
                              f1=2 * prec * rec / (prec + rec) if prec + rec else 0.0, tp=tp, fp=fp, fn=fn, n=int(ok.sum())))
    ev = pd.DataFrame(ev_rows)
    print(ev[ev.horizon_min == 0][["model_name", "scope", "mae", "rmse", "mape", "n"]].round(3).to_string(index=False))
    print(pd.DataFrame(peak_rows).round(3).to_string(index=False))

    print("8) DB 저장")
    keep = [0] + [i for i, c in enumerate(g.series) if c in set(g.rooms_all)]   # 전체 + 호실 채널만 저장
    rows = []
    iss_txt = g.times[te_iss].strftime("%Y-%m-%d %H:%M:%S").to_numpy()
    for m in models:
        for s in keep:
            P = preds[m][s]
            for h in range(H):
                ok = ~np.isnan(P[:, h])
                rows.append(pd.DataFrame({"model_name": m, "series_id": g.series[s], "issued_at": iss_txt[ok],
                                          "horizon_min": 15 * (h + 1), "predicted_kw": np.round(P[ok, h], 4)}))
    fc = pd.concat(rows, ignore_index=True)
    with engine.begin() as con:
        for t in ("st_forecast", "st_eval", "st_peak_eval", "st_explain", "st_meta", "peak_threshold"):
            con.execute(text(f"DELETE FROM {t}"))
    fc.to_sql("st_forecast", engine, if_exists="append", index=False, chunksize=50_000)
    ev.to_sql("st_eval", engine, if_exists="append", index=False)
    pd.DataFrame(peak_rows).to_sql("st_peak_eval", engine, if_exists="append", index=False)
    thr.reset_index(names="date").assign(method="직전 30일 전체 합산 P95").dropna(subset=["threshold_kw"]) \
        .to_sql("peak_threshold", engine, if_exists="append", index=False)
    if explain is not None:
        iss, contrib = explain
        names = [FEATURES[c] for c in FEAT_COLS]
        ex = pd.DataFrame(contrib[:, :-1], columns=names)
        ex["issued_at"] = g.times[iss].strftime("%Y-%m-%d %H:%M:%S")
        ex = ex.melt(id_vars="issued_at", var_name="feature", value_name="contribution_kw")
        ex = ex[ex.contribution_kw.abs() > 1e-4]
        ex.to_sql("st_explain", engine, if_exists="append", index=False, chunksize=50_000)
        imp = pd.DataFrame(np.abs(contrib[:, :-1]), columns=names).mean().sort_values(ascending=False)
        base = float(contrib[0, -1])
    else:
        imp, base = pd.Series(dtype=float), None
    info = {"test_start": t_s, "test_end": t_e, "shap_base": base,
            "importance": [[k, round(float(v), 3)] for k, v in imp.head(10).items()],
            "rooms": g.rooms_all, "inactive": sorted(g.inactive), "models": models,
            "scale_total": float(scale[0])}
    with engine.begin() as con:
        con.execute(st_meta.insert(), [{"key": "info", "value": json.dumps(info, ensure_ascii=False)}])
    print(f"완료: 예측 {len(fc):,}행 저장, 총 {time.time() - t_start:.0f}초")


if __name__ == "__main__":
    main()
