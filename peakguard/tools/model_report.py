"""[4] 최적 모델 선정 근거 문서 만들기 — docs/MODEL_SELECTION.md

연결 ─ 화면: — (③ 과 같은 계산으로 문서 작성)
       API : /api/performance 와 같은 함수
       DB  : docs/MODEL_SELECTION.md · docs/TUNING.md (쓰기)

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
         "- 피크 기준: 직전 30일 건물 합계(96채널) 15분 수요전력의 95번째 백분위수 · 위험 = 다음 1시간 예측 4칸 중 하나라도 기준 이상",
         f"- 실제 피크 위험(실측 4칸 중 하나라도 기준 이상): {p['thr']['actual_risk']}건 / {p['thr']['n']:,}개 예측 시점",
         f"- 전처리: 현재전력 음수 {inf['prep']['power_negative']:,}건({inf['prep']['power_negative_pct']}%) 결측 처리, 긴 결측 {inf['prep']['long_gap_cells']:,}칸 학습 제외",
         f"- 입력: 직전 수요·현재전력·전일/전주 같은 시각·시간·요일·공휴일 (기상: {'사용' if inf.get('weather') else '검증 결과 미채택 — 아래 비교 참고'})", "",
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
        L += ["", "## 기상(기온) 포함 / 미포함", "", f"- {p['weather']['decision']}", "",
              "| 모델 | 검증 MAE 포함 | 검증 MAE 미포함 | 시험 MAE 포함 | 시험 MAE 미포함 | 재현율 포함 | 재현율 미포함 |", "|---|---|---|---|---|---|---|"]
        for r in p["weather"]["rows"]:
            L.append(f"| {r['id']} | {r['val_w']:.3f} | {r['val_nw']:.3f} | {r['mae_w']:.3f} | {r['mae_nw']:.3f} | {pct(r['recall_w'])} | {pct(r['recall_nw'])} |")
    T = p["timing"]
    L += ["", f"## 예측시각과 실제 발생시각 오차 (실제 피크 사건 {T['events']}건)", "", "| 모델 | 사전 경보 | 평균 선행 | 평균 시각 오차 | ±15분 이내 |", "|---|---|---|---|---|"]
    for m, r in T["rows"].items():
        L.append(f"| {m} | {r['warned']}/{r['events']} | {r['lead_min'] or '—'}분 | {r['abs_err_min'] or '—'}분 | {r['within15_pct'] or '—'}% |")
    C = p["by_condition"]
    L += ["", "## 조건별 MAE (kW)", "", "| 모델 | " + " | ".join(C["conditions"]) + " |", "|---" * (len(C["conditions"]) + 1) + "|"]
    for m, r in C["rows"].items():
        L.append(f"| {m} | " + " | ".join(f"{r[c]:.3f}" for c in C["conditions"]) + " |")
    Y = p.get("by_day")
    if Y:
        L += ["", "## 기간별(시험기간 일별) MAE (kW)", "", "| 모델 | " + " | ".join(f"{d['date'][5:]}({d['wd']})" for d in Y["days"]) + " | 평일 | 주말·공휴일 |",
              "|---" * (len(Y["days"]) + 3) + "|"]
        for m, r in Y["rows"].items():
            L.append(f"| {m} | " + " | ".join("—" if v is None else f"{v:.3f}" for v in r["daily"]) + f" | {r['weekday']:.3f} | {r['offday']:.3f} |")
    L += ["", "설정값(하이퍼파라미터) 비교는 [TUNING.md](TUNING.md)."]
    L += ["", "※ 숫자는 `python run.py 3` 학습 후 `python -m peakguard.tools.model_report` 로 다시 만들어집니다."]
    out = ROOT / "docs" / "MODEL_SELECTION.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"저장: {out}")
    tuning_doc()


def tuning_doc() -> None:
    """models/tuning.json → docs/TUNING.md"""
    from ..config import MODEL_DIR
    f = MODEL_DIR / "tuning.json"
    if not f.exists():
        return
    T = json.loads(f.read_text(encoding="utf-8"))
    NAME = {"LightGBM": "LightGBM", "XGBoost": "XGBoost", "RandomForest": "Random Forest", "BiLSTM": "Bi-LSTM"}
    KEY = {"num_leaves": "잎 수", "min_child_samples": "잎 최소 표본", "learning_rate": "학습률", "n": "나무 수",
           "max_depth": "최대 깊이", "min_child_weight": "잎 최소 가중", "min_samples_leaf": "잎 최소 표본",
           "max_features": "특징 비율", "hidden": "은닉 크기", "lr": "학습률"}
    L = ["# 하이퍼파라미터(모델 설정값) 비교", "",
         "과제 원문 필요 기술 '학습·하이퍼파라미터 튜닝'. 선정 방식(채널별 합산 후 예측, 건물 합계를 바로 예측)의 4개 모델을 대상으로,",
         f"학습기간(~{T['period']['train_to']})으로 학습하고 **검증기간({T['period']['val'][0]} ~ {T['period']['val'][1]}, 예측 시점 {T['period']['val_issues']:,}개) 건물 합계 MAE** 로 비교했습니다.",
         "시험기간은 보지 않았습니다(시험 성능은 마지막 한 번만 측정). 실행: `python -m peakguard.tools.tune`", "",
         f"판단 규칙: {T['rule']}", "",
         "맞힘·놓침·헛경보는 검증기간 최근 30일 기준 피크 위험(참고). ★ = 현재 적용 설정, 굵게 = 검증 MAE 가장 작음.", ""]
    for m, R in T["models"].items():
        rows = R["rows"]
        best = min(r["val_mae"] for r in rows)
        L += [f"## {NAME.get(m, m)} — {R['decision']} (처음 설정 대비 검증 MAE {R['gain_pct']}% 감소 가능)", "",
              "| 설정 | 검증 MAE | 맞힘 | 놓침 | 헛경보 |", "|---|---|---|---|---|"]
        for r in rows:
            cfg = ", ".join(f"{KEY.get(k, k)} {v}" for k, v in r["cfg"].items())
            mae = f"**{r['val_mae']:.4f}**" if r["val_mae"] == best else f"{r['val_mae']:.4f}"
            L.append(f"| {'★ ' if r.get('applied', r['current']) else ''}{cfg} | {mae} | {r['tp']} | {r['fn']} | {r['fp']} |")
        L.append("")
    if T.get("note"):
        L += ["## 결과 정리", "", T["note"], ""]
    if T.get("before_after"):
        L += ["## 교체 전후 시험기간 결과 (채널별 합산 후 예측, 피크 기준 최근 30일)", "",
              "| 모델 | 시험 MAE 전 → 후 | 맞힘 전 → 후 | 헛경보 전 → 후 | 놓침 전 → 후 |", "|---|---|---|---|---|"]
        for m, r in T["before_after"].items():
            L.append(f"| {NAME.get(m, m)} | {r['mae'][0]:.3f} → {r['mae'][1]:.3f} | {r['tp'][0]} → {r['tp'][1]} | {r['fp'][0]} → {r['fp'][1]} | {r['fn'][0]} → {r['fn'][1]} |")
        L.append("")
    out = ROOT / "docs" / "TUNING.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
