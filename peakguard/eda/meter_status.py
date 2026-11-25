"""[11] 계측 상태 — [실시간 연결]·파일 끌어다놓기로 들어온 자료만으로 채널별 이상을 찾음 (기업 API 불필요)

  GET /api/meter_status?date=YYYY-MM-DD   그날 00:00 ~ 23:45 (마지막 실측이 그 안이면 거기까지)
  GET /api/meter_status                   가장 최근 실측까지 24시간 (실시간 연결 때)

근거: 과제 원문 세부 1 '결측치·이상치 탐지', 기업 추가 답변 1-2(누적값 감소 기록 있음)·1-3(누적 차분과 수요전력)·5번(음수)

점검 필요 기준 (실제 자료에 맞춰 헛알림이 나지 않게 정함)
  끊김        마지막 측정이 기간 끝보다 1시간(4칸) 이상 이전
  빠진 칸     기간 안 15분 칸 중 측정이 없는 칸이 4칸(1시간) 이상
  음수        현재전력 음수 (기업 안내: 계측기 설정·측정 오류 가능성 → 결측 처리)
  누적 감소   누적 사용량(kWh)이 0.1 kWh 넘게 줄어듦 (계량값 초기화·교체 의심)
  누적·수요 불일치  기간 누적 사용량 증가분과 수요전력으로 계산한 사용량(Σ수요×0.25h)이 1 kWh 넘게, 30% 넘게 차이
  값 고정     하루 96칸 내내 같은 값이고 0.3 kW 이상 (센서 멈춤 의심 — 0.3 kW 미만 대기전력은 원래 일정할 수 있어 제외)
"""
from __future__ import annotations

import pandas as pd

DAY = 96
RULES = [
    ("끊김", "마지막 측정이 기간 끝보다 1시간 이상 이전"),
    ("빠진 칸", "기간 안 15분 칸 중 측정 없는 칸 4칸(1시간) 이상"),
    ("음수", "현재전력이 음수 (계측기 설정·측정 오류 가능성, 기업 추가 답변 5번)"),
    ("누적 감소", "누적 사용량이 0.1 kWh 넘게 줄어듦 (계량값 초기화·교체 의심, 추가 답변 1-2)"),
    ("누적·수요 불일치", "누적 사용량 증가분과 수요전력×0.25시간 합계가 1 kWh·30% 넘게 차이 (추가 답변 1-3)"),
    ("값 고정", "하루 내내 같은 값이고 0.3 kW 이상 (센서 멈춤 의심)"),
]


def status(date: str | None = None) -> dict:
    from ..api.alerts import label
    from ..api.common import q
    from ..config import FLOORS
    last = q("SELECT MAX(measured_at) t FROM measurement").t[0]
    if last is None:
        return {"total": 0, "ok": 0, "check": [], "rules": RULES, "note": "측정 자료 없음"}
    last = pd.Timestamp(last)
    if date:
        a = pd.Timestamp(date)
        b = min(a + pd.Timedelta(hours=23, minutes=45), last)
        if b < a:
            return {"total": 0, "ok": 0, "check": [], "rules": RULES, "note": f"{date} 자료 없음"}
    else:
        b = last
        a = b - pd.Timedelta(hours=23, minutes=45)
    m = q("SELECT channel_id, measured_at, power_kw, energy_kwh, demand_kw FROM measurement "
          "WHERE measured_at >= :a AND measured_at <= :b ORDER BY channel_id, measured_at",
          a=f"{a:%Y-%m-%d %H:%M:%S}", b=f"{b:%Y-%m-%d %H:%M:%S}")
    chans = q("SELECT DISTINCT channel_id FROM measurement").channel_id.tolist()
    m["measured_at"] = pd.to_datetime(m.measured_at)
    slots = int((b - a) / pd.Timedelta(minutes=15)) + 1
    check = []
    for c in sorted(chans):
        d = m[m.channel_id == c]
        iss = []
        lt = d.measured_at.max() if len(d) else None
        if lt is None or b - lt >= pd.Timedelta(hours=1):
            iss.append(("끊김", f"마지막 측정 {lt:%m-%d %H:%M}" if lt is not None else "기간 안 측정 없음"))
        miss = slots - d.measured_at.nunique()
        if len(d) and miss >= 4:
            iss.append(("빠진 칸", f"{miss}칸 / {slots}칸"))
        neg = int((d.power_kw < 0).sum())
        if neg:
            iss.append(("음수", f"{neg}칸 (최소 {d.power_kw.min():.3f} kW)"))
        de = d.energy_kwh.diff()
        if (de < -0.1).any():
            iss.append(("누적 감소", f"{int((de < -0.1).sum())}번, 최대 {de.min():.1f} kWh"))
        elif len(d) >= 8:
            e = float(d.energy_kwh.iloc[-1] - d.energy_kwh.iloc[0])
            dm = float(d.demand_kw.iloc[1:].clip(lower=0).sum() * 0.25)
            if abs(e - dm) > 1 and abs(e - dm) / max(abs(e), dm, 1e-9) > 0.3:
                iss.append(("누적·수요 불일치", f"누적 증가 {e:.1f} kWh / 수요 기준 {dm:.1f} kWh"))
        if len(d) >= DAY and d.demand_kw.nunique() == 1 and d.demand_kw.iloc[0] >= 0.3:
            iss.append(("값 고정", f"{d.demand_kw.iloc[0]:.3f} kW 그대로"))
        if iss:
            meter = c.split("-")[0]
            check.append({"channel": c, "label": label(c), "kind": "호실" if meter in FLOORS else "공용",
                          "issues": [{"kind": k, "detail": v} for k, v in iss],
                          "last": f"{lt:%Y-%m-%d %H:%M}" if lt is not None else None})
    gap = sum(1 for c in check if any(i["kind"] in ("빠진 칸", "끊김") for i in c["issues"]))
    common = (f"채널 {gap}개가 함께 비어 있음 → 개별 계측기보다 수집·통신 중단 의심 (예: 10-06~10-20 통신회선 교체)"
              if len(chans) and gap >= 0.9 * len(chans) else None)
    return {"from": f"{a:%Y-%m-%d %H:%M}", "to": f"{b:%Y-%m-%d %H:%M}", "total": len(chans),
            "ok": len(chans) - len(check), "check": check, "rules": RULES, "common": common,
            "note": "측정 자료만으로 판단 (기업 계측 상태 API 없음). [실시간 연결]·파일 끌어다놓기 때마다 다시 계산."}
