"""[4] 최적 모델 선정 근거 문서 만들기 — docs/MODEL_SELECTION.md

    python -m peakguard.tools.model_report      (학습 run.py 3 이 끝난 뒤)

세 번째 화면과 같은 계산(/api/performance, 피크 기준 = 최근 30일 95번째 백분위수)으로 표와 결론을 씁니다.
"""
from __future__ import annotations

import json

from ..config import ROOT


def pct(v, d=0):
    return "—" if v is None else f"{v * 100:.{d}f}%"


def main() -> None:
    from ..api.common import info
    from ..api.performance import performance
    p = performance("recent30")
    inf = info()
    sp = inf["splits"]
    L = ["# 최적 모델 선정 근거", "",
         f"- 시험기간: {p['test'][0]} ~ {p['test'][1]} (학습 {sp['train'][0][:10]}~{sp['train'][1][:10]}, 검증 {sp['val'][0][:10]}~{sp['val'][1][:10]}), 모든 모델 같은 예측 시점(공통 표본)",
         f"- 피크 기준: 직전 30일 건물 합계(96채널) 15분 수요전력의 95번째 백분위수 · 위험 = 다음 1시간 예측이 기준의 {int(inf.get('alert_ratio', 0.95) * 100)}% 이상",
         f"- 실제 피크 위험(실측 4칸 중 하나라도 기준 이상): {p['thr']['actual_risk']}건 / {p['thr']['n']:,}개 예측 시점",
         f"- 전처리: 현재전력 음수 {inf['prep']['power_negative']:,}건({inf['prep']['power_negative_pct']}%) 결측 처리, 긴 결측 {inf['prep']['long_gap_cells']:,}칸 학습 제외",
         "- 입력: 직전 수요·현재전력·전일/전주 같은 시각·시간·요일·공휴일 + 기온 평소 대비·1시간 변화", "",
         "## 결론", ""]
    L += [f"- {x}" for x in p["selection"].get("lines", [p["selection"].get("text", "")])]
    for s in p["sections"]:
        L += ["", f"## {s['title']}", "", "| 모델 | MAE | RMSE | MAPE | 개선율 | 맞힘 | 놓침 | 헛경보 | 재현율 | 정밀도 |", "|---|---|---|---|---|---|---|---|---|---|"]
        for r in s["rows"]:
            k = r["peak"]
            L.append(f"| {r['name']} | {r['mae']:.3f} | {r['rmse']:.3f} | {r['mape']:.1f}% | {'기준' if r['gain'] is None else str(r['gain']) + '%'} | "
                     f"{k['tp']} | {k['fn']} | {k['fp']} | {pct(k['recall'])} | {pct(k['precision'])} |")
        na = s["no_alarm"]
        L.append(f"| 경보 없음(비교용) | — | — | — | — | 0 | {na['fn']} | 0 | 0% | — |")
    if p.get("weather"):
        L += ["", "## 기상(기온) 포함 / 미포함", "", "| 모델 | MAE 포함 | MAE 미포함 | 재현율 포함 | 재현율 미포함 |", "|---|---|---|---|---|"]
        for r in p["weather"]["rows"]:
            L.append(f"| {r['id']} | {r['mae_w']:.3f} | {r['mae_nw']:.3f} | {pct(r['recall_w'])} | {pct(r['recall_nw'])} |")
    T = p["timing"]
    L += ["", f"## 예측시각과 실제 발생시각 오차 (실제 피크 사건 {T['events']}건)", "", "| 모델 | 사전 경보 | 평균 선행 | 평균 시각 오차 | ±15분 이내 |", "|---|---|---|---|---|"]
    for m, r in T["rows"].items():
        L.append(f"| {m} | {r['warned']}/{r['events']} | {r['lead_min'] or '—'}분 | {r['abs_err_min'] or '—'}분 | {r['within15_pct'] or '—'}% |")
    C = p["by_condition"]
    L += ["", "## 조건별 MAE (kW)", "", "| 모델 | " + " | ".join(C["conditions"]) + " |", "|---" * (len(C["conditions"]) + 1) + "|"]
    for m, r in C["rows"].items():
        L.append(f"| {m} | " + " | ".join(f"{r[c]:.3f}" for c in C["conditions"]) + " |")
    L += ["", "※ 숫자는 `python run.py 3` 학습 후 `python -m peakguard.tools.model_report` 로 다시 만들어집니다."]
    out = ROOT / "docs" / "MODEL_SELECTION.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
