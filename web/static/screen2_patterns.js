// [6] 두 번째 화면 아래 '사용패턴 분석' (Front-end 담당) — 실측만, 기업 과제 원문 목표 1 · 세부 2·4·5
//   [전체 기간 / 최근 90일 / 최근 30일] 버튼 (기간 끝 = 보고서 기준일)
//   요일 × 시간대 평균 수요전력(히트맵) + 월별 표 · 절감이 필요한 시간대(평일 15분 칸별 위험 빈도)
//   피크 반복 패턴(일 최대 발생 시각) · 피크 발생 전 사용패턴(위험일 2시간 전 곡선, 마지막 1시간 늘어난 호실)
//   기상요인 관계(일 최고기온 × 일 최대, 상관계수) · 이상 사용패턴(평소에서 크게 벗어난 칸)
//   서버 자료: /api/patterns?range=&date=
(() => {
  const PG = window.PG, $ = PG.$, el = PG.el, fmt = PG.fmt, css = PG.css;
  const P = PG.PAT = { range: 'all', D: null };
  const txt = (svg, x, y, t, o = {}) => { const e = el('text', { x, y, 'font-size': 11, fill: css('--muted'), ...o }, svg); e.textContent = t; return e; };

  P.load = async () => {
    const d = $('#repdate').value;
    try { P.D = await PG.api(`api/patterns?range=${P.range}&date=${d}`); } catch (e) { $('#patinfo').textContent = e.message; return; }
    const D = P.D;
    $('#patinfo').textContent = `${D.start} ~ ${D.end} · ${D.days}일 · 15분 칸 ${D.slots.toLocaleString()}개 (96채널이 모두 들어온 칸만)`;
    draw();
  };
  document.querySelectorAll('#patseg button').forEach((b) => b.addEventListener('click', () => {
    P.range = b.dataset.r; document.querySelectorAll('#patseg button').forEach((x) => x.setAttribute('aria-pressed', x === b ? 'true' : 'false')); P.load();
  }));
  $('#repdate').addEventListener('change', () => P.D && P.load());
  PG.onTheme.push(() => P.D && draw());

  function draw() { heat(); monthly(); saving(); peaks(); pre(); weather(); anomalies(); }

  // 요일 × 시간대 히트맵
  function heat() {
    const h = P.D.heat, svg = $('#pheat'); svg.replaceChildren();
    const H = 200, W = PG.fitWidth(svg, H), L = 30, T = 6, B = 20, cw = (W - L - 6) / 24, ch = (H - T - B) / h.rows.length;
    svg.style.height = H + 'px';
    const vals = h.values.flat().filter((v) => v != null), mx = Math.max(...vals), mn = Math.min(...vals);
    h.values.forEach((row, r) => {
      txt(svg, L - 6, T + r * ch + ch / 2 + 4, h.rows[r], { 'text-anchor': 'end', fill: css('--ink2') });
      row.forEach((v, c) => {
        const a = v == null ? 0 : (v - mn) / (mx - mn || 1);
        const rc = el('rect', { x: L + c * cw, y: T + r * ch, width: cw - 1, height: ch - 1, rx: 2, fill: v == null ? css('--grid') : `color-mix(in srgb, ${css('--bad')} ${Math.round(a * 90)}%, ${css('--panel')})` }, svg);
        const ti = document.createElementNS(PG.NS, 'title'); ti.textContent = `${h.rows[r]} ${c}시 평균 ${fmt(v, 2)} kW`; rc.append(ti);
      });
    });
    for (let c = 0; c < 24; c += 3) txt(svg, L + c * cw + cw / 2, H - 5, c + '시', { 'text-anchor': 'middle' });
  }
  function monthly() {
    const tb = $('#pmonth'); tb.replaceChildren();
    const tr0 = document.createElement('tr'); ['월', '평균 kW', '최대 kW', '사용량 kWh', '수집 일수'].forEach((t) => { const th = document.createElement('th'); th.textContent = t; tr0.append(th); }); tb.append(tr0);
    P.D.monthly.forEach((m) => { const tr = document.createElement('tr'); [m.month, fmt(m.avg_kw, 2), fmt(m.max_kw, 2), Number(m.energy_kwh).toLocaleString(), fmt(m.days, 1)].forEach((v) => { const td = document.createElement('td'); td.textContent = v; tr.append(td); }); tb.append(tr); });
  }
  // 절감이 필요한 시간대: 평일 15분 칸별 위험 빈도
  function saving() {
    const S = P.D.saving, svg = $('#psave'); svg.replaceChildren();
    const H = 180, W = PG.fitWidth(svg, H), L = 34, R = 8, T = 8, B = 20;
    svg.style.height = H + 'px';
    const keys = Object.keys(S.freq), mx = Math.max(10, ...Object.values(S.freq));
    const bw = (W - L - R) / 96, y = (v) => T + (1 - v / mx) * (H - T - B);
    const inBlock = (k) => S.blocks.some((b) => k >= b.from && k < b.to);
    keys.forEach((k) => {
      const [hh, mm] = k.split(':').map(Number), i = hh * 4 + mm / 15, v = S.freq[k];
      el('rect', { x: L + i * bw, y: y(v), width: Math.max(1, bw - 0.5), height: H - B - y(v), fill: inBlock(k) ? css('--bad') : css('--muted'), opacity: inBlock(k) ? 0.9 : 0.5 }, svg);
    });
    [0, Math.round(mx / 2), Math.round(mx)].forEach((v) => txt(svg, L - 4, y(v) + 4, v + '%', { 'text-anchor': 'end' }));
    for (let h = 0; h <= 24; h += 3) txt(svg, L + h * 4 * bw, H - 5, h + '시', { 'text-anchor': 'middle' });
    $('#psavesub').textContent = S.rule;
    const box = $('#psavelist'); box.replaceChildren();
    const sm = document.createElement('p'); sm.className = 'note strong'; sm.textContent = S.summary; box.append(sm);
    S.blocks.forEach((b) => { const p = document.createElement('p'); p.className = 'note'; p.textContent = `평일 ${b.from}~${b.to} · 위험 빈도 ${fmt(b.freq_pct, 0)}% · 이때 많이 쓰는 호실: ${b.rooms.join(', ')}`; box.append(p); });
  }
  // 피크 반복 패턴: 평일 일 최대 발생 시각
  function peaks() {
    const ph = P.D.peak_hours, svg = $('#ppeak'); svg.replaceChildren();
    const H = 170, W = PG.fitWidth(svg, H), L = 26, R = 8, T = 8, B = 20, mx = Math.max(1, ...ph.hist);
    svg.style.height = H + 'px';
    const bw = (W - L - R) / 24, y = (v) => T + (1 - v / mx) * (H - T - B);
    ph.hist.forEach((v, h) => { el('rect', { x: L + h * bw + 1, y: y(v), width: bw - 2, height: H - B - y(v), rx: 2, fill: v === mx ? css('--bad') : css('--amber'), opacity: 0.85 }, svg); if (v) txt(svg, L + h * bw + bw / 2, y(v) - 3, v, { 'text-anchor': 'middle', 'font-size': 10 }); });
    for (let h = 0; h <= 24; h += 3) txt(svg, L + h * bw, H - 5, h + '시', { 'text-anchor': 'middle' });
    $('#ppeaksum').textContent = `${ph.summary} · 평일 ${ph.days}일`;
  }
  // 피크 발생 전 사용패턴
  function pre() {
    const pp = P.D.pre_peak, svg = $('#ppre'); svg.replaceChildren();
    const H = 170, W = PG.fitWidth(svg, H), L = 40, R = 12, T = 10, B = 20;
    svg.style.height = H + 'px';
    if (!pp.curve_pct) { $('#ppresum').textContent = pp.summary; return; }
    const lo = Math.floor(Math.min(...pp.curve_pct) / 10) * 10, x = (m) => L + (m + 120) / 120 * (W - L - R), y = (v) => T + (1 - (v - lo) / (100 - lo)) * (H - T - B);
    for (let v = lo; v <= 100; v += 5) { el('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: css('--grid') }, svg); txt(svg, L - 4, y(v) + 4, v + '%', { 'text-anchor': 'end' }); }
    pp.minutes.forEach((m) => txt(svg, x(m), H - 5, m === 0 ? '피크' : m + '분', { 'text-anchor': 'middle' }));
    el('polyline', { points: pp.minutes.map((m, i) => x(m) + ',' + y(pp.curve_pct[i])).join(' '), fill: 'none', stroke: css('--bad'), 'stroke-width': 2.4 }, svg);
    pp.minutes.forEach((m, i) => el('circle', { cx: x(m), cy: y(pp.curve_pct[i]), r: 3, fill: css('--panel'), stroke: css('--bad'), 'stroke-width': 2 }, svg));
    el('rect', { x: x(-60), y: T, width: x(0) - x(-60), height: H - T - B, fill: css('--bad'), opacity: 0.06 }, svg);
    $('#ppresum').textContent = `${pp.summary}. 마지막 1시간 가장 많이 늘어난 호실: ${pp.rooms.map((r) => `${r.name} +${fmt(r.rise_kw, 2)} kW`).join(', ')}`;
  }
  // 기상요인 관계
  function weather() {
    const w = P.D.weather, svg = $('#pwx'); svg.replaceChildren();
    const H = 170, W = PG.fitWidth(svg, H), L = 34, R = 10, T = 8, B = 22;
    svg.style.height = H + 'px';
    if (!w.points.length) { $('#pwxsum').textContent = w.summary; return; }
    const xs = w.points.map((p) => p.tmax), ys = w.points.map((p) => p.peak);
    const x0 = Math.floor(Math.min(...xs)), x1 = Math.ceil(Math.max(...xs)), y0 = Math.floor(Math.min(...ys)), y1 = Math.ceil(Math.max(...ys));
    const x = (v) => L + (v - x0) / (x1 - x0 || 1) * (W - L - R), y = (v) => T + (1 - (v - y0) / (y1 - y0 || 1)) * (H - T - B);
    for (let v = y0; v <= y1; v += Math.max(1, Math.round((y1 - y0) / 4))) { el('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: css('--grid') }, svg); txt(svg, L - 4, y(v) + 4, v, { 'text-anchor': 'end' }); }
    for (let v = x0; v <= x1; v += Math.max(1, Math.round((x1 - x0) / 6))) txt(svg, x(v), H - 6, v + '℃', { 'text-anchor': 'middle' });
    w.points.forEach((p) => { const c = el('circle', { cx: x(p.tmax), cy: y(p.peak), r: 3.5, fill: css('--amber'), opacity: 0.75 }, svg); const ti = document.createElementNS(PG.NS, 'title'); ti.textContent = `${p.date} 최고 ${p.tmax}℃ · 습도 ${p.hum}% · 일 최대 ${p.peak} kW`; c.append(ti); });
    $('#pwxsum').textContent = w.summary + ' — 예측모델에는 기온 평소 대비·1시간 변화를 입력으로 넣고, 넣은 경우와 뺀 경우 성능을 세 번째 화면에서 비교';
  }
  // 이상 사용패턴
  function anomalies() {
    const a = P.D.anomalies, tb = $('#pano'); tb.replaceChildren();
    $('#panosub').textContent = `${a.days}일 · ${a.count}칸`;
    $('#panorule').textContent = a.rule + '. 호실 = 그 시각 평소(같은 날 유형 직전 4주)보다 가장 크게 늘거나 준 호실.';
    const tr0 = document.createElement('tr'); ['시각', '요일', '실측 kW', '평소 kW', '구분', '주요 호실'].forEach((t) => { const th = document.createElement('th'); th.textContent = t; tr0.append(th); }); tb.append(tr0);
    a.rows.forEach((r) => { const tr = document.createElement('tr'); if (r.kind === '급증') tr.className = 'over'; [r.time.slice(5), r.wd, fmt(r.kw, 2), fmt(r.usual_kw, 2), `${r.kind} (z ${r.z})`, r.room_dev != null ? `${r.room} ${r.room_dev > 0 ? '+' : ''}${fmt(r.room_dev, 2)}` : r.room].forEach((v) => { const td = document.createElement('td'); td.textContent = v; tr.append(td); }); tb.append(tr); });
  }
})();
