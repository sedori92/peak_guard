"""[3] 예측모델 — Bi-LSTM(Bidirectional Long Short-Term Memory, 양방향 장단기 기억 신경망)

연결 ─ 화면: ① 예측모델 콤보 'Bi-LSTM' (기본값), ③ Bi-LSTM 줄
       API : —
       DB  : — (models/st_BiLSTM.pt 파일)

  입력: 최근 6시간(24칸)의 [수요전력, 현재전력, 시각 sin·cos, 주말·공휴일] 순서열
       + 예측 대상 4구간의 전일·전주 같은 시각 값, 요일, 시각
  출력: 다음 15·30·45·60분 수요전력 4개
  전체 합산과 채널 61개를 한 모델로 함께 학습 (시계열 구분은 임베딩). 전체 합산은 8배 가중.
"""
from __future__ import annotations

import copy
import time

import numpy as np

from ..config import DAY, H, SEED, WEEK
from .grid import Grid, take as _take

SEQ = 24          # 입력 길이 (6시간)


def lstm_arrays(g: Grid, s: int, issues: np.ndarray, scale: float):
    yi, p = g.yi[s] / scale, g.p[s] / scale
    k = np.arange(-SEQ + 1, 1)
    idx = issues[:, None] + k[None, :]                                     # (N, 24)
    ang = 2 * np.pi * (idx % DAY) / DAY
    on = bool(getattr(g, "use_weather", False))                 # config.USE_WEATHER=False 이면 기상 자리는 0
    tmp = g.temp if (on and g.temp is not None) else np.full(len(g.times), np.nan)
    tan = g.tanom if (on and g.tanom is not None) else np.full(len(g.times), np.nan)
    seq = np.stack([_take(yi, idx), _take(p, idx), np.sin(ang), np.cos(ang),
                    _take(g.offday.astype(float), idx),
                    _take(tan, idx) / 10.0, (_take(tmp, idx) - _take(tmp, idx - 4)) / 5.0], axis=2)   # 기온 평소 대비·1시간 변화
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
                s.lstm = nn.LSTM(7, hidden, batch_first=True, bidirectional=True)
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
