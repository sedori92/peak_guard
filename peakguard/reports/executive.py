"""[10] 보고서 — 최고관리자 보고용 피크 변동기록 + 영향도 Top10 (스프레드시트)

    python run.py 10x --period month --date 2026-10-15       # reports/피크보고서_month_2026-10-01.xlsx
    GET /api/report/peaks?period=day|week|month|year&date=    # 화면 미리보기(JSON)
    GET /api/report/xlsx?period=...&date=...                  # 스프레드시트 내려받기

기간과 변동기록 단위
    day   : 그날 0~24시, 1시간 단위 최대
    week  : 그 날이 속한 주(월~일), 하루 단위 최대
    month : 그 달, 하루 단위 최대
    year  : 그 해, 한 달 단위 최대

계산 기준 (기업 3차 멘토링 답변 ④ 적용)
    - 건물 합계 = 96개 계측채널을 같은 15분 시각끼리 더한 '96개 채널 단순합 수요전력' (96개가 모두 수신된 칸만)
    - 피크 = 그 기간 건물 합계의 최댓값, 피크 기준 = 그날의 직전 30일 P95
    - 영향도 = 피크 발생 시각의 채널별 수요전력 ÷ 건물 합계 (사용 비중이며 요금 배분율·인과 영향과는 다름)
    - 호실 비중 = 호실 34개 채널 합계를 분모로 한 비중 (건물 전체 비중과 구분)
"""
from __future__ import annotations

import argparse
import io

import numpy as np
import pandas as pd
from sqlalchemy import text

from ..config import COMMON_METERS, FLOORS, REPORT_DIR
from ..database.connection import get_engine

TITLE = {"day": "일간", "week": "주간", "month": "월간", "year": "연간"}
UNIT = {"day": "1시간", "week": "1일", "month": "1일", "year": "1개월"}
WEEK_KO = "월화수목금토일"


def period_range(period: str, date: str) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """(시작, 끝, 직전 기간 시작)"""
    d = pd.Timestamp(date).normalize()
    if period == "day":
        s = d; e = s + pd.Timedelta(days=1); ps = s - pd.Timedelta(days=1)
    elif period == "week":
        s = d - pd.Timedelta(days=d.dayofweek); e = s + pd.Timedelta(days=7); ps = s - pd.Timedelta(days=7)
    elif period == "month":
        s = d.replace(day=1); e = s + pd.offsets.MonthBegin(1); ps = s - pd.offsets.MonthBegin(1)
    elif period == "year":
        s = d.replace(month=1, day=1); e = s + pd.offsets.YearBegin(1); ps = s - pd.offsets.YearBegin(1)
    else:
        raise ValueError("period 는 day·week·month·year 중 하나")
    return s, e, ps


def label(cid: str) -> tuple[str, str, int | None]:
    """'M004-CH03' → ('2층 03번', '호실', 2)"""
    meter, n = cid.split("-")[0], cid[-2:]
    if meter in FLOORS:
        return f"{FLOORS[meter]}층 {n}번", "호실", FLOORS[meter]
    if meter in COMMON_METERS:
        return f"{COMMON_METERS[meter]}층 공용 {n}번", "공용", COMMON_METERS[meter]
    return cid, "기타", None


def _read(engine, a: pd.Timestamp, b: pd.Timestamp) -> pd.DataFrame:
    with engine.connect() as con:
        m = pd.read_sql(text("""SELECT m.channel_id, m.measured_at, m.demand_kw FROM measurement m
                                WHERE m.measured_at >= :a AND m.measured_at < :b"""), con,
                        params=dict(a=f"{a:%Y-%m-%d %H:%M:%S}", b=f"{b:%Y-%m-%d %H:%M:%S}"))
    m["measured_at"] = pd.to_datetime(m.measured_at)
    if getattr(m.measured_at.dt, "tz", None) is not None:
        m["measured_at"] = m.measured_at.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    return m


def _wide(m: pd.DataFrame, n_ch: int) -> pd.DataFrame:
    """시각 × 채널 표 (96개 채널이 모두 있는 칸만)."""
    if m.empty:
        return pd.DataFrame()
    w = m.pivot_table(index="measured_at", columns="channel_id", values="demand_kw", aggfunc="first")
    return w[w.notna().sum(axis=1) >= n_ch].sort_index()


def _top(row: pd.Series, n: int, rooms: list[str]) -> list[dict]:
    tot = float(row.sum())
    room_sum = float(row.reindex(rooms).sum())
    out = []
    for k, (cid, v) in enumerate(row.sort_values(ascending=False).head(n).items(), 1):
        name, kind, floor = label(cid)
        out.append({"rank": k, "channel": cid, "name": name, "kind": kind, "floor": floor, "kw": round(float(v), 3),
                    "share_building": round(float(v) / tot * 100, 1) if tot else None,
                    "share_rooms": round(float(v) / room_sum * 100, 1) if kind == "호실" and room_sum else None})
    return out


def build(period: str, date: str) -> dict:
    engine = get_engine()
    s, e, ps = period_range(period, date)
    with engine.connect() as con:
        ch = pd.read_sql(text("SELECT channel_id, meter_id FROM channel"), con)
        th = pd.read_sql(text("SELECT date, threshold_kw FROM peak_threshold"), con)
    n_ch = len(ch)
    rooms = ch[ch.meter_id.isin(FLOORS)].channel_id.tolist()
    thr = dict(zip(th.date, th.threshold_kw))
    w = _wide(_read(engine, s, e), n_ch)
    out = {"period": period, "title": TITLE[period], "unit": UNIT[period], "start": f"{s:%Y-%m-%d}",
           "end": f"{(e - pd.Timedelta(days=1)):%Y-%m-%d}", "generated": f"{pd.Timestamp.now():%Y-%m-%d %H:%M}",
           "basis": "96개 계측채널 단순합 수요전력(15분), 96개 채널이 모두 수신된 칸만 사용",
           "records": [], "top10": [], "series": [], "summary": None}
    if w.empty:
        return out
    tot = w.sum(axis=1)
    day_thr = pd.Series([thr.get(f"{t:%Y-%m-%d}", np.nan) for t in tot.index], index=tot.index)
    expected = int((e - s) / pd.Timedelta("15min"))

    key = {"day": tot.index.floor("h"), "week": tot.index.normalize(), "month": tot.index.normalize(),
           "year": tot.index.to_period("M").to_timestamp()}[period]
    for k, grp in tot.groupby(key):
        t_pk = grp.idxmax()
        tp = _top(w.loc[t_pk], 1, rooms)[0]
        thr_k = thr.get(f"{t_pk:%Y-%m-%d}")
        lab = {"day": f"{k:%H}시", "week": f"{k:%m-%d}({WEEK_KO[k.dayofweek]})",
               "month": f"{k:%m-%d}({WEEK_KO[k.dayofweek]})", "year": f"{k:%Y-%m}"}[period]
        out["records"].append({
            "label": lab, "peak_kw": round(float(grp.max()), 2), "peak_time": f"{t_pk:%Y-%m-%d %H:%M}",
            "threshold_kw": None if thr_k is None or np.isnan(thr_k) else round(float(thr_k), 2),
            "over": bool(thr_k is not None and not np.isnan(thr_k) and grp.max() >= thr_k),
            "over_slots": int((grp >= day_thr.loc[grp.index]).sum()),
            "avg_kw": round(float(grp.mean()), 2), "energy_kwh": round(float(grp.sum() * 0.25), 1),
            "slots": int(len(grp)), "top_name": tp["name"], "top_kind": tp["kind"], "top_kw": tp["kw"],
            "top_share": tp["share_building"]})

    t_pk = tot.idxmax()
    pk_thr = thr.get(f"{t_pk:%Y-%m-%d}")
    prev = _wide(_read(engine, ps, s), n_ch)
    prev_pk = float(prev.sum(axis=1).max()) if not prev.empty else None
    out["summary"] = {
        "peak_kw": round(float(tot.max()), 2), "peak_time": f"{t_pk:%Y-%m-%d %H:%M}",
        "threshold_kw": None if pk_thr is None or np.isnan(pk_thr) else round(float(pk_thr), 2),
        "over_slots": int((tot >= day_thr).sum()), "avg_kw": round(float(tot.mean()), 2),
        "energy_kwh": round(float(tot.sum() * 0.25), 1), "coverage_pct": round(len(tot) / expected * 100, 1),
        "prev_peak_kw": None if prev_pk is None else round(prev_pk, 2),
        "prev_change_pct": None if not prev_pk else round((float(tot.max()) / prev_pk - 1) * 100, 1),
        "room_share_at_peak": round(float(w.loc[t_pk].reindex(rooms).sum()) / float(tot.max()) * 100, 1)}
    out["top10"] = _top(w.loc[t_pk], 10, rooms)
    out["series"] = [{"time": f"{t:%Y-%m-%d %H:%M}", "kw": round(float(v), 3),
                      "threshold_kw": None if np.isnan(day_thr[t]) else round(float(day_thr[t]), 2)}
                     for t, v in tot.items()]
    return out


def to_xlsx(r: dict) -> bytes:
    """보고서 dict → 엑셀 파일 (요약 · 피크 변동기록 · 영향도 Top10 · 평소 대비 증가 Top10 · 15분 기록)."""
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    HEAD = PatternFill("solid", fgColor="1F2937")
    WARN = PatternFill("solid", fgColor="FDE2DD")
    thin = Side(style="thin", color="D1D5DB")
    BOX = Border(top=thin, bottom=thin, left=thin, right=thin)
    wb = Workbook()

    def table(ws, row0: int, headers: list[str], rows: list[list], widths: list[int], fmt: dict | None = None):
        for j, h in enumerate(headers, 1):
            c = ws.cell(row0, j, h)
            c.font, c.fill, c.alignment, c.border = Font(bold=True, color="FFFFFF"), HEAD, Alignment(horizontal="center", vertical="center", wrap_text=True), BOX
        for i, row in enumerate(rows, row0 + 1):
            for j, v in enumerate(row, 1):
                c = ws.cell(i, j, v)
                c.border = BOX
                if fmt and j in fmt:
                    c.number_format = fmt[j]
        for j, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(j)].width = w
        ws.freeze_panes = ws.cell(row0 + 1, 1)

    # 1) 요약
    ws = wb.active
    ws.title = "요약"
    ws["A1"] = f"피크가드 {r['title']} 피크 보고서"
    ws["A1"].font = Font(bold=True, size=16)
    ws["A2"] = f"기간 {r['start']} ~ {r['end']} · 작성 {r['generated']} · 대상: 광주테크노파크 생활로봇지원센터"
    ws["A3"] = f"기준: {r['basis']}"
    for c in ("A2", "A3"):
        ws[c].font = Font(color="6B7280")
    sm = r["summary"] or {}
    items = [("기간 최대 피크 (kW)", sm.get("peak_kw"), "0.00"), ("발생 시각", sm.get("peak_time"), None),
             ("그날 피크 기준 (직전 30일 P95, kW)", sm.get("threshold_kw"), "0.00"),
             ("기준 초과 15분 칸 수", sm.get("over_slots"), "0"), ("평균 수요전력 (kW)", sm.get("avg_kw"), "0.00"),
             ("사용량 (kWh)", sm.get("energy_kwh"), "#,##0.0"), ("데이터 수신률 (%)", sm.get("coverage_pct"), "0.0"),
             ("직전 기간 최대 피크 (kW)", sm.get("prev_peak_kw"), "0.00"),
             ("직전 기간 대비 증감 (%)", sm.get("prev_change_pct"), "+0.0;-0.0;0.0"),
             ("피크 시각 호실 34개 비중 (%)", sm.get("room_share_at_peak"), "0.0")]
    table(ws, 5, ["항목", "값"], [[a, b] for a, b, _ in items], [38, 22], None)
    for i, (_, _, f) in enumerate(items, 6):
        if f:
            ws.cell(i, 2).number_format = f
    n = 6 + len(items) + 1
    for k, t in enumerate(["※ 건물 합계는 96개 채널 단순합이며 한전 공식 최대수요전력과 같은 값이 아닙니다 (기업 3차 답변).",
                           "※ 영향도는 피크 시각의 사용 비중이며, 인과적 영향이나 전기요금 배분율과는 다릅니다.",
                           f"※ 데이터 수집 중단 구간(2026-10-06 15:00 ~ 10-20 15:00)은 계산에서 빠집니다."]):
        ws.cell(n + k, 1, t).font = Font(color="6B7280", size=9)

    # 2) 피크 변동기록
    ws = wb.create_sheet("피크 변동기록")
    hd = [f"구간({r['unit']})", "최대 피크(kW)", "발생 시각", "피크 기준(kW)", "기준 초과", "초과 15분 칸",
          "평균(kW)", "사용량(kWh)", "수신 칸", "최대 영향 채널", "구분", "영향 kW", "건물 대비(%)"]
    rows = [[x["label"], x["peak_kw"], x["peak_time"], x["threshold_kw"], "초과" if x["over"] else "", x["over_slots"],
             x["avg_kw"], x["energy_kwh"], x["slots"], x["top_name"], x["top_kind"], x["top_kw"], x["top_share"]]
            for x in r["records"]]
    table(ws, 1, hd, rows, [13, 13, 17, 13, 9, 11, 10, 12, 9, 16, 7, 9, 11],
          {2: "0.00", 4: "0.00", 7: "0.00", 8: "#,##0.0", 12: "0.000", 13: "0.0"})
    for i, x in enumerate(r["records"], 2):
        if x["over"]:
            for j in range(1, len(hd) + 1):
                ws.cell(i, j).fill = WARN
    if rows:
        ch = LineChart()
        ch.title, ch.y_axis.title, ch.height, ch.width = "구간별 최대 피크와 피크 기준", "kW", 8, 22
        ch.add_data(Reference(ws, min_col=2, min_row=1, max_row=len(rows) + 1), titles_from_data=True)
        ch.add_data(Reference(ws, min_col=4, min_row=1, max_row=len(rows) + 1), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=2, max_row=len(rows) + 1))
        ch.series[1].graphicalProperties.line.dashStyle = "dash"
        ch.series[1].graphicalProperties.line.solidFill = "E11D48"
        ch.series[0].graphicalProperties.line.solidFill = "F97316"
        ws.add_chart(ch, f"A{len(rows) + 4}")

    # 3) 영향도 Top10
    ws = wb.create_sheet("영향도 Top10")
    ws["A1"] = f"기간 최대 피크 {sm.get('peak_kw')} kW ({sm.get('peak_time')}) 시각의 채널별 수요전력 상위 10"
    ws["A1"].font = Font(bold=True)
    rows = [[x["rank"], x["name"], x["kind"], x["floor"], x["channel"], x["kw"], x["share_building"], x["share_rooms"]]
            for x in r["top10"]]
    table(ws, 3, ["순위", "채널", "구분", "층", "채널 ID", "피크 시각 kW", "건물 합계(96채널) 대비(%)", "호실 34개 합계 대비(%)"],
          rows, [7, 16, 8, 6, 13, 13, 20, 18], {6: "0.000", 7: "0.0", 8: "0.0"})
    if rows:
        bc = BarChart()
        bc.type, bc.title, bc.height, bc.width = "bar", "피크 시각 영향도 Top10 (건물 합계 대비 %)", 8, 18
        bc.add_data(Reference(ws, min_col=7, min_row=3, max_row=len(rows) + 3), titles_from_data=True)
        bc.set_categories(Reference(ws, min_col=2, min_row=4, max_row=len(rows) + 3))
        bc.y_axis.title = "%"
        bc.x_axis.scaling.orientation = "maxMin"
        bc.legend = None
        ws.add_chart(bc, f"A{len(rows) + 6}")

    # 4) 평소 대비 증가 Top10 (같은 요일·시각 직전 4주 평균과 비교, 실측만)
    ws = wb.create_sheet("평소 대비 증가 Top10")
    try:
        from .influence import rise_top
        rt = rise_top(sm.get("peak_time")) if sm.get("peak_time") else None
    except ValueError:
        rt = None
    if rt:
        ws["A1"] = (f"피크 시각 {rt['time']} 건물 {rt['total_kw']} kW = 평소 {rt['usual_total_kw']} kW + 증가 {rt['rise_total_kw']} kW "
                    f"(평소 = 같은 요일·시각 직전 4주 평균: {', '.join(rt['weeks'])})")
        ws["A1"].font = Font(bold=True)
        table(ws, 3, ["순위", "채널", "구분", "피크 시각 kW", "평소 kW", "증가 kW", "증가율(%)"],
              [[x["rank"], x["name"], x["kind"], x["kw"], x["usual_kw"], x["rise_kw"], x["rise_pct"]] for x in rt["rows"]],
              [7, 16, 8, 13, 11, 11, 11], {4: "0.000", 5: "0.000", 6: "+0.000;-0.000", 7: "0"})
    else:
        ws["A1"] = "직전 4주 같은 요일·시각 실측이 없어 '평소'를 계산할 수 없습니다."

    # 5) 15분 기록
    ws = wb.create_sheet("15분 기록")
    rows = [[x["time"], x["kw"], x["threshold_kw"], "초과" if x["threshold_kw"] and x["kw"] >= x["threshold_kw"] else ""]
            for x in r["series"]]
    table(ws, 1, ["시각", "건물 합계(kW)", "피크 기준(kW)", "기준 초과"], rows, [18, 14, 14, 10], {2: "0.000", 3: "0.00"})

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def filename(r: dict) -> str:
    return f"피크보고서_{r['period']}_{r['start']}.xlsx"


def main() -> None:
    ap = argparse.ArgumentParser(description="최고관리자 보고용 피크 스프레드시트")
    ap.add_argument("--period", choices=list(TITLE), default="month")
    ap.add_argument("--date", default="2026-10-28")
    a = ap.parse_args()
    r = build(a.period, a.date)
    REPORT_DIR.mkdir(exist_ok=True)
    p = REPORT_DIR / filename(r)
    p.write_bytes(to_xlsx(r))
    s = r["summary"] or {}
    print(f"{r['title']} {r['start']}~{r['end']} 최대 피크 {s.get('peak_kw')} kW ({s.get('peak_time')}) → 저장: {p}")


if __name__ == "__main__":
    main()
