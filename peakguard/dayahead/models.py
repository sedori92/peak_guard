"""예측 모델 4종
  - Naive        : 직전 같은 유형(평일/휴일) 날을 그대로 사용 (비교 기준선)
  - LightGBM     : 그래디언트 부스팅 트리
  - RandomForest : 랜덤 포레스트
  - BiLSTM       : 양방향 LSTM(Bidirectional Long Short-Term Memory) 신경망

모든 모델은 '예측일 하루치 96칸(15분)'을 예측하고, 그 최댓값을 '피크'로 본다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .dataset import SLOTS, DayData
from .features import FEATURES, same_type_history

SEED = 42


# ---------------------------------------------------------------- 트리/기준 모델
class NaiveModel:
    name = "Naive"
    params: dict = {}

    def fit(self, df: pd.DataFrame) -> "NaiveModel":
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return (df["same_type_1"] * df["scale"]).to_numpy()


class TreeModel:
    def __init__(self, kind: str, channel_scope: bool):
        self.name = kind
        self.channel_scope = channel_scope
        if kind == "LightGBM":
            self.params = dict(n_estimators=700, learning_rate=0.03, num_leaves=31, min_child_samples=20,
                               subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
                               random_state=SEED, verbose=-1)
        else:
            # 채널 통합 모델은 행이 50만 개 이상 → 나무 수·잎 크기를 조절해 속도·저장 용량을 줄임
            self.params = dict(n_estimators=100 if channel_scope else 300,
                               min_samples_leaf=20 if channel_scope else 3, max_features=0.5,
                               max_samples=0.3 if channel_scope else None, n_jobs=-1, random_state=SEED)
        self.model = None

    def _x(self, df: pd.DataFrame) -> pd.DataFrame:
        x = df[FEATURES].copy()
        if self.name == "LightGBM":
            x["series"] = x["series"].astype("category")
        return x

    def fit(self, df: pd.DataFrame) -> "TreeModel":
        df = df.dropna(subset=["y"])
        if self.name == "LightGBM":
            import lightgbm as lgb
            self.model = lgb.LGBMRegressor(**self.params)
            self._cats = sorted(df["series"].unique())
            x = self._x(df)
            x["series"] = pd.Categorical(df["series"], categories=self._cats)
            self.model.fit(x, df["y"])
        else:
            from sklearn.ensemble import RandomForestRegressor
            self.model = RandomForestRegressor(**self.params)
            self.model.fit(self._x(df), df["y"])
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        x = self._x(df)
        if self.name == "LightGBM":
            x["series"] = pd.Categorical(df["series"], categories=self._cats)
        return np.clip(self.model.predict(x), 0, None) * df["scale"].to_numpy()


# ---------------------------------------------------------------- Bi-LSTM
ENC_DAYS = 2  # 인코더가 보는 과거 일수 (2일 = 192칸)


def _slot_angles() -> tuple[np.ndarray, np.ndarray]:
    a = 2 * np.pi * np.arange(SLOTS) / SLOTS
    return np.sin(a), np.cos(a)


def lstm_tensors(dd: DayData, series_idx: list[int], day_idx: list[int]):
    """Bi-LSTM 입력 만들기.
    enc : (N, 192, 4)  과거 2일 [값, sin, cos, 휴일여부]
    dec : (N, 96, 14) 예측일 [sin, cos, 휴일, 공휴일, 요일(7), 직전같은유형, 같은유형평균, 1주전]
    """
    sin, cos = _slot_angles()
    sth = same_type_history(dd)
    enc, dec, y, sid, keys = [], [], [], [], []
    for s in series_idx:
        xs = dd.x[s] / dd.scale[s]
        ys = dd.y[s] / dd.scale[s]
        for d in day_idx:
            if d < 7:
                continue
            past = []
            for j in range(d - ENC_DAYS, d):
                past.append(np.stack([xs[j], sin, cos, np.full(SLOTS, float(dd.offday[j]))], axis=1))
            hist = [xs[j] for j in sth[d] if j >= 0] or [xs[d - 7]]
            dow = np.zeros((SLOTS, 7)); dow[:, dd.days[d].dayofweek] = 1
            dec.append(np.concatenate([
                np.stack([sin, cos, np.full(SLOTS, float(dd.offday[d])), np.full(SLOTS, float(dd.holiday[d]))], 1),
                dow,
                np.stack([hist[0], np.mean(hist, 0), xs[d - 7]], 1)], axis=1))
            enc.append(np.concatenate(past, 0))
            y.append(ys[d])
            sid.append(s)
            keys.append((s, d))
    return (np.asarray(enc, np.float32), np.asarray(dec, np.float32), np.asarray(y, np.float32),
            np.asarray(sid, np.int64), keys)


class BiLSTMModel:
    """여러 시계열(TOTAL + 채널)을 함께 학습하는 전역(global) 모델. 시계열 구분은 임베딩으로."""
    name = "BiLSTM"

    def __init__(self, n_series: int, hidden: int = 48, emb: int = 8, epochs: int = 40,
                 lr: float = 2e-3, batch: int = 128, patience: int = 5, total_weight: int = 10):
        self.params = dict(hidden=hidden, emb=emb, epochs=epochs, lr=lr, batch=batch,
                           patience=patience, enc_days=ENC_DAYS, total_weight=total_weight)
        self.n_series = n_series
        self.net = None
        self.best_epoch = None

    def _build(self):
        import torch
        from torch import nn
        p = self.params

        class Net(nn.Module):
            def __init__(s):
                super().__init__()
                s.lstm = nn.LSTM(4, p["hidden"], batch_first=True, bidirectional=True)
                s.emb = nn.Embedding(self.n_series, p["emb"])
                s.head = nn.Sequential(nn.Linear(2 * p["hidden"] + 14 + p["emb"], 64), nn.ReLU(),
                                       nn.Linear(64, 32), nn.ReLU(), nn.Linear(32, 1))

            def forward(s, enc, dec, sid):
                _, (h, _) = s.lstm(enc)                       # h: (2, N, H) 정방향·역방향 마지막 상태
                h = torch.cat([h[0], h[1]], dim=1)            # (N, 2H)
                ctx = torch.cat([h, s.emb(sid)], 1).unsqueeze(1).expand(-1, SLOTS, -1)
                return s.head(torch.cat([ctx, dec], 2)).squeeze(-1)  # (N, 96)

        torch.manual_seed(SEED)
        return Net()

    def _loader(self, enc, dec, y, sid, shuffle):
        import torch
        from torch.utils.data import DataLoader, TensorDataset
        ds = TensorDataset(torch.from_numpy(enc), torch.from_numpy(dec), torch.from_numpy(y), torch.from_numpy(sid))
        return DataLoader(ds, batch_size=self.params["batch"], shuffle=shuffle,
                          generator=torch.Generator().manual_seed(SEED))

    def _oversample_total(self, arrays, sid):
        idx = np.concatenate([np.arange(len(sid))] + [np.where(sid == 0)[0]] * (self.params["total_weight"] - 1))
        return [a[idx] for a in arrays]

    def fit(self, train, val=None, epochs: int | None = None, verbose: bool = True) -> "BiLSTMModel":
        """train/val = lstm_tensors(...) 결과. val 이 있으면 조기 종료(early stopping)."""
        import copy
        import torch
        enc, dec, y, sid, _ = train
        ok = ~np.isnan(y).any(axis=1)                       # 96칸이 다 있는 날만 학습
        enc, dec, y, sid = enc[ok], dec[ok], y[ok], sid[ok]
        enc, dec, y, sid = self._oversample_total([enc, dec, y, sid], sid)
        self.net = self._build()
        opt = torch.optim.Adam(self.net.parameters(), lr=self.params["lr"])
        loss_fn = torch.nn.HuberLoss(delta=0.5)
        dl = self._loader(enc, dec, y, sid, True)
        n_ep = epochs or self.params["epochs"]
        best, best_state, bad = np.inf, None, 0
        for ep in range(1, n_ep + 1):
            self.net.train()
            for e, dc, t, s in dl:
                opt.zero_grad()
                loss = loss_fn(self.net(e, dc, s), t)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.net.parameters(), 1.0)
                opt.step()
            if val is not None:
                pv = self._predict_norm(val)
                mask = ~np.isnan(val[2])
                vl = float(np.mean(np.abs(pv - val[2])[mask]))
                if verbose:
                    print(f"    epoch {ep:2d}  val MAE(정규화) {vl:.4f}")
                if vl < best - 1e-4:
                    best, best_state, bad, self.best_epoch = vl, copy.deepcopy(self.net.state_dict()), 0, ep
                else:
                    bad += 1
                    if bad >= self.params["patience"]:
                        break
        if best_state is not None:
            self.net.load_state_dict(best_state)
        else:
            self.best_epoch = n_ep
        return self

    def _predict_norm(self, data) -> np.ndarray:
        import torch
        enc, dec, _, sid, _ = data
        self.net.eval()
        with torch.no_grad():
            out = self.net(torch.from_numpy(enc), torch.from_numpy(dec), torch.from_numpy(sid)).numpy()
        return np.clip(out, 0, None)

    def predict(self, data, scale: np.ndarray) -> np.ndarray:
        """(N, 96) kW 예측."""
        return self._predict_norm(data) * scale[data[3]][:, None]
