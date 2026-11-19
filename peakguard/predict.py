"""저장된 모델로 '다음날' 전체·채널별 피크 예측 → DB 저장

새 측정값을 DB에 적재한 뒤 실행하면, 마지막 데이터 다음 날의 피크를 예측합니다.
    python -m peakguard.predict                    # 저장된 모든 모델
    python -m peakguard.predict --models LightGBM
"""
from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import delete, select

from . import features
from .dataset import TOTAL, read_wide, to_days
from .db import forecast_slot, get_engine, model_run, peak_forecast
from .models import BiLSTMModel, lstm_tensors
from .train import _ts

MODEL_DIR = Path("models")


def latest_run(con, name: str, scope: str) -> int | None:
    return con.execute(select(model_run.c.run_id).where(model_run.c.model_name == name,
                                                        model_run.c.target_scope == scope)
                       .order_by(model_run.c.run_id.desc()).limit(1)).scalar()


def main() -> None:
    ap = argparse.ArgumentParser(description="다음날 피크 예측")
    ap.add_argument("--models", nargs="+", default=["LightGBM", "RandomForest", "BiLSTM"])
    a = ap.parse_args()

    engine = get_engine()
    dd = to_days(read_wide(engine), extra_days=1)
    has_data = ~np.isnan(dd.y).all(axis=(0, 2))
    fc_day = int(np.where(has_data)[0].max()) + 1
    target = dd.days[fc_day]
    print(f"예측일: {target.date()}")

    summary = {}
    for name in a.models:
        path = MODEL_DIR / (f"{name}.pt" if name == "BiLSTM" else f"{name}.joblib")
        if not path.exists():
            print(f"[건너뜀] {path} 없음 → 먼저 python -m peakguard.train 실행")
            continue
        if name == "BiLSTM":
            import torch
            ck = torch.load(path, weights_only=False)
            saved_series, dd.scale = ck["series"], ck["scale"]
        else:
            ck = joblib.load(path)
            saved_series, dd.scale = ck["series"], ck["scale"]
        if saved_series != dd.series:
            print(f"[건너뜀] {name}: 채널 구성이 학습 때와 다름 → 재학습 필요")
            continue

        pred = {}
        s_all = list(range(len(dd.series)))
        if name == "BiLSTM":
            m = BiLSTMModel(n_series=len(dd.series), **{k: v for k, v in ck["params"].items()
                                                        if k in ("hidden", "emb")})
            m.net = m._build(); m.net.load_state_dict(ck["state"])
            fc = lstm_tensors(dd, s_all, [fc_day])
            pred = {k: v for k, v in zip(fc[4], m.predict(fc, dd.scale))}
        else:
            for scope, s_idx in (("TOTAL", [0]), ("CHANNEL", s_all[1:])):
                fc = features.build(dd, s_idx, [fc_day], with_target=False)
                fc["pred"] = ck[scope].predict(fc)
                pred.update({(s, d): g.sort_values("slot")["pred"].to_numpy()
                             for (s, d), g in fc.groupby(["series", "day"])})

        with engine.begin() as con:
            for scope, s_idx in (("TOTAL", [0]), ("CHANNEL", s_all[1:])):
                run_id = latest_run(con, name, scope)
                if run_id is None:
                    continue
                con.execute(delete(peak_forecast).where(peak_forecast.c.run_id == run_id,
                                                        peak_forecast.c.target_date == target.date()))
                rows = []
                for s in s_idx:
                    p = pred[(s, fc_day)]
                    rows.append(dict(run_id=run_id, channel_id=None if dd.series[s] == TOTAL else dd.series[s],
                                     target_date=target.date(), peak_kw=float(p.max()),
                                     peak_time=_ts(engine, target + pd.Timedelta(minutes=15 * int(p.argmax())))))
                con.execute(peak_forecast.insert(), rows)
                ids = {r.channel_id: r.forecast_id for r in con.execute(
                    select(peak_forecast.c.forecast_id, peak_forecast.c.channel_id)
                    .where(peak_forecast.c.run_id == run_id, peak_forecast.c.target_date == target.date()))}
                con.execute(forecast_slot.insert(), [
                    dict(forecast_id=ids[None if dd.series[s] == TOTAL else dd.series[s]],
                         slot_time=_ts(engine, target + pd.Timedelta(minutes=15 * k)), predicted_kw=float(v))
                    for s in s_idx for k, v in enumerate(pred[(s, fc_day)])])
        p0 = pred[(0, fc_day)]
        top = sorted(((dd.series[s], pred[(s, fc_day)].max()) for s in s_all[1:]), key=lambda x: -x[1])[:5]
        summary[name] = p0.max()
        print(f"{name:12s} 전체 피크 {p0.max():6.2f} kW @ {(target + pd.Timedelta(minutes=15 * int(p0.argmax()))):%H:%M}"
              f" | 채널 상위5: " + ", ".join(f"{c} {v:.2f}" for c, v in top))
    if summary:
        print("저장 완료 (peak_forecast, forecast_slot)")


if __name__ == "__main__":
    main()
