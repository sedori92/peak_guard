// [6] 첫 번째 화면 '예측 및 경보' — 예측 부분 (Front-end 담당)
//   왼쪽 위 콤보버튼 : 방식(채널별 예측 후 합산 / 채널별 합산 후 예측) · 예측모델(LightGBM·XGBoost·Random Forest·Bi-LSTM) · 재생 기준일
//   오른쪽 위 배너   : 재생 중 시각 · 일시정지 · 속도 · [실시간 연결]
//   상태 띠·숫자 4개·합산 수요전력 구성
//   상단 변동선      : 실측 + 다음 1시간 예측 + 직전 30일 피크 기준선  (drawChart)
//   하단 변동선      : 점검 우선 Top 5 호실 추이와 예측               (drawTop5)
//   오른쪽 아래      : 호실별 피크 예측 가능성 (막대 = 호실 기준 대비 %, 숫자 = 건물 비중 %) (drawPossibility)
//   서버 자료: /api/days(목록), /api/replay(하루치 재생), /api/live/sync·/api/live/predict(실시간 연결)
(() => {
  const PG = window.PG, $ = PG.$, el = PG.el, fmt = PG.fmt, css = PG.css, tm = PG.tm, addMin = PG.addMin;
  const QS = new URLSearchParams(location.search);
  const S = PG.S1 = { F: [], i: 0, sub: 0, playing: true, speed: 1, live: null, trail: [] };
  const SUB = 8, TICK = 200;
  const LINE5 = ['#FF7A1A', '#FFD166', '#4CC9F0', '#B388FF', '#7BE495'];
  S.modelId = () => { const f = S.families.find((x) => x.family === $('#family').value); return $('#method').value === 'sum' ? f.sum : f.direct; };
  const roomName = (c) => `${S.floors[c.slice(0, 4)]}층 ${c.slice(5)}번`;
  S.roomName = roomName;

  // ---------- 콤보버튼 채우기 + 하루치 재생 자료 불러오기 ----------
  S.init = async () => {
    const meta = await PG.api('api/days');
    S.families = meta.families; S.methods = meta.methods;
    const want = QS.get('model') || (PG.STATIC && PG.STATIC.model) || meta.default_model;
    const fam = meta.families.find((f) => f.direct === want || f.sum === want) || meta.families[0];
    const method = meta.families.some((f) => f.sum === want) ? 'sum' : (QS.get('model') ? 'direct' : meta.default_method);
    meta.methods.forEach((m) => $('#method').add(new Option(m.name, m.id, false, m.id === method)));
    meta.families.forEach((f) => $('#family').add(new Option(f.name, f.family, false, f.family === fam.family)));
    const date = QS.get('date') || (meta.days.find((d) => d.date === ((PG.STATIC && PG.STATIC.date) || '2026-10-28')) || meta.days[meta.days.length - 1]).date;
    meta.days.forEach((d) => $('#dsel').add(new Option(d.date, d.date, false, d.date === date)));
    const go = () => { location.href = location.pathname + '?date=' + $('#dsel').value + '&model=' + encodeURIComponent(S.modelId()) + location.hash; };
    ['#method', '#family', '#dsel'].forEach((s) => { $(s).onchange = () => (S.live ? S.goLive() : go()); });
    S.DATA = await PG.api(`api/replay?date=${date}&model=${encodeURIComponent(S.modelId())}`);
    S.F = S.DATA.frames; S.floors = S.DATA.floors; S.roomThr = S.DATA.room_thr || {};
    S.hos = Object.keys(S.F[0].rooms);
    $('#dayl').textContent = S.DATA.day;
    $('#methodnote').textContent = S.DATA.method === 'sum'
      ? '채널 61개를 각각 예측해 같은 시각끼리 더한 건물 합계'
      : '96개 채널을 먼저 더한 건물 합계를 바로 예측 (기업 권장) · 호실 숫자는 같은 모델의 채널 예측';
    document.querySelectorAll('.js-model').forEach((e) => { e.textContent = `${S.DATA.method_name} · ${S.DATA.model}`; });
    S.i = Math.max(0, S.F.findIndex((x) => x.t === '12:30'));
  };

  // ---------- 판정: 다음 1시간 예측 4개 중 하나라도 기준 이상 = 피크 위험, 95% 이상 = 주의 ----------
  S.state = (f) => {
    const mx = Math.max(...f.pred), k = f.pred.indexOf(mx);
    const first = f.thr == null ? -1 : f.pred.findIndex((v) => v >= f.thr);
    const lvl = f.thr == null ? 'ok' : first >= 0 ? 'bad' : (mx >= f.thr * 0.95 ? 'warn' : 'ok');
    return { mx, k, first, lvl, at: addMin(f.t, 15 * (k + 1)) };
  };
  S.topRooms = (f, n = 5) => Object.entries(Object.keys(f.proom || {}).length ? f.proom : f.rooms).sort((a, b2) => b2[1] - a[1]).slice(0, n);
  S.frame = () => (S.live ? S.live.frame : S.F[S.i]);
  S.history = () => (S.live ? S.live.hist : S.F.slice(0, S.i + 1));

  // ---------- 상태 띠 · 숫자 4개 ----------
  function drawStatus(f, s) {
    $('#status').classList.toggle('risk', s.lvl === 'bad');
    const pill = $('#pill'); pill.className = 'pill ' + s.lvl;
    pill.textContent = s.lvl === 'bad' ? '피크 위험' : s.lvl === 'warn' ? '주의' : '정상';
    const msg = $('#msg'); msg.replaceChildren();
    const line = document.createElement('div');
    if (f.thr == null) line.append('피크 기준을 계산할 실측(직전 30일 중 7일치)이 부족합니다');
    else if (s.lvl === 'bad') line.append('최초 위험 예상 ', PG.b(addMin(f.t, 15 * (s.first + 1))), ' · 최대 예상 ', PG.b(fmt(s.mx, 2) + ' kW'), ' · 기준 초과 ', PG.b('+' + fmt(s.mx - f.thr, 2) + ' kW'));
    else if (s.lvl === 'warn') line.append('다음 1시간 최대 ', PG.b(fmt(s.mx, 2) + ' kW'), ' (', PG.b(s.at), ') · 기준의 ', PG.b(fmt(s.mx / f.thr * 100, 0) + '%'));
    else line.append('다음 1시간 최대 ', PG.b(fmt(s.mx, 2) + ' kW'), ' · 기준까지 여유 ', PG.b(fmt(f.thr - s.mx, 2) + ' kW'));
    const sm = document.createElement('small');
    sm.textContent = s.lvl === 'bad' ? `먼저 점검할 호실: ${S.topRooms(f, 3).map((r) => roomName(r[0])).join(', ')} → 오른쪽 '경보발령 선택'에서 발령` : `다음 갱신 ${addMin(f.t, 15)}`;
    msg.append(line, sm);
    $('#thr').textContent = f.thr == null ? '—' : fmt(f.thr, 2) + ' kW';
    $('#thrnote').textContent = f.thr_note || '';
  }
  const setv = (sel, v, u) => { const e = $(sel); if (e.dataset.v !== v) { e.dataset.v = v; e.replaceChildren(document.createTextNode(v), Object.assign(document.createElement('small'), { textContent: u })); e.classList.remove('flash'); void e.offsetWidth; e.classList.add('flash'); } };
  function drawKpi(f, s) {
    setv('#k1', fmt(f.y, 2), 'kW'); setv('#k3', fmt(s.mx, 2), 'kW');
    $('#k3s').textContent = s.at + ' 예상' + (s.lvl === 'bad' ? ' · 기준 초과' : '');
    setv('#k4', f.temp == null ? '—' : fmt(f.temp, 1), '℃');
    $('#k5').textContent = fmt(Math.max(...S.history().map((x) => x.y || 0)), 2) + ' kW';
    if (S.live) { setv('#k2', fmt(f.p, 2), 'kW'); $('#k2s').textContent = `${f.t} 측정 (실시간 연결)`; }
  }
  // ---------- 합산 수요전력 구성 (호실 34개 / 공용 62개) ----------
  function drawShare(f) {
    const tot = f.y, h = tot - f.common;
    $('#sharet').textContent = `${f.t} 기준 ${fmt(tot, 2)} kW`;
    const sh = $('#share'); sh.replaceChildren();
    [[h, '호실 34개 ' + fmt(h / tot * 100, 0) + '%', css('--amber'), '#fff'], [f.common, '공용 62개 ' + fmt(f.common / tot * 100, 0) + '%', css('--grid'), css('--ink')]]
      .forEach(([v, t, bg, c]) => { const d = document.createElement('div'); d.style.width = (v / tot * 100) + '%'; d.style.background = bg; d.style.color = c; d.textContent = t; sh.append(d); });
  }

  // ---------- 상단 변동선: 실측 + 예측 + 피크 기준선 ----------
  function drawChart(f) {
    const svg = $('#chart'); svg.replaceChildren();
    const W = 900, H = 300, ml = 44, mr = 12, mt = 12, mb = 26;
    const X1 = tm(f.t) + 80, X0 = X1 - 390;
    const xs = (m) => ml + (m - X0) / (X1 - X0) * (W - ml - mr), ys = (v) => mt + (1 - v / 36) * (H - mt - mb);
    for (let v = 0; v <= 35; v += 5) { el('line', { x1: ml, x2: W - mr, y1: ys(v), y2: ys(v), stroke: css('--grid') }, svg); const t = el('text', { x: ml - 8, y: ys(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: css('--muted') }, svg); t.textContent = v; }
    for (let h = Math.ceil(X0 / 60); h * 60 <= X1; h += 1) { el('line', { x1: xs(h * 60), x2: xs(h * 60), y1: mt, y2: H - mb, stroke: css('--grid'), 'stroke-dasharray': '2 4' }, svg); const t = el('text', { x: xs(h * 60), y: H - 6, 'text-anchor': 'middle', 'font-size': 11, fill: css('--muted') }, svg); t.textContent = h + '시'; }
    if (f.thr != null) {
      const ty = ys(f.thr);
      el('rect', { x: ml, y: mt, width: W - ml - mr, height: Math.max(0, ty - mt), fill: css('--bad'), opacity: 0.06 }, svg);
      el('line', { x1: ml, x2: W - mr, y1: ty, y2: ty, stroke: css('--bad'), 'stroke-width': 1.5, 'stroke-dasharray': '6 4' }, svg);
      const tl = el('text', { x: ml + 6, y: ty - 6, 'font-size': 11, fill: css('--bad'), 'font-weight': 600 }, svg); tl.textContent = '피크 기준 ' + fmt(f.thr, 2) + ' kW';
    }
    if (!S.live && S.trail.length > 1) el('polyline', { points: S.trail.filter((p) => p[0] >= X0).map((p) => xs(p[0]) + ',' + ys(p[1])).join(' '), fill: 'none', stroke: css('--amber'), 'stroke-width': 1.2, opacity: 0.55 }, svg);
    const pts = S.history().filter((x) => x.y != null).map((x) => [tm(x.t), x.y]).filter((p) => p[0] >= X0 - 15);
    if (pts.length) {
      el('path', { d: 'M' + xs(pts[0][0]) + ',' + ys(0) + ' ' + pts.map((p) => 'L' + xs(p[0]) + ',' + ys(p[1])).join(' ') + ' L' + xs(pts[pts.length - 1][0]) + ',' + ys(0) + 'Z', fill: css('--ink'), opacity: 0.06 }, svg);
      el('polyline', { points: pts.map((p) => xs(p[0]) + ',' + ys(p[1])).join(' '), fill: 'none', stroke: css('--ink'), 'stroke-width': 2.4, 'stroke-linejoin': 'round' }, svg);
    }
    const t0 = tm(f.t), fp = [[t0, f.y], ...f.pred.map((v, k) => [t0 + 15 * (k + 1), v])];
    el('polyline', { points: fp.map((p) => xs(p[0]) + ',' + ys(p[1])).join(' '), fill: 'none', stroke: css('--pred'), 'stroke-width': 2.4, 'stroke-dasharray': '7 5' }, svg);
    f.pred.forEach((v, k) => { const over = f.thr != null && v >= f.thr; el('circle', { cx: xs(t0 + 15 * (k + 1)), cy: ys(v), r: over ? 6 : 4.5, fill: over ? css('--bad') : css('--panel'), stroke: over ? css('--bad') : css('--pred'), 'stroke-width': 2 }, svg); });
    el('line', { x1: xs(t0), x2: xs(t0), y1: mt, y2: H - mb, stroke: css('--ink2'), 'stroke-dasharray': '2 3' }, svg);
    const nt = el('text', { x: xs(t0) + 6, y: mt + 12, 'font-size': 11, fill: css('--ink2') }, svg); nt.textContent = (S.live ? '최근 실측 ' : '지금 ') + f.t;
  }

  // ---------- 하단 변동선: 점검 우선 Top 5 호실 추이 ----------
  function drawTop5(f, s) {
    const svg = $('#t5chart'); svg.replaceChildren();
    const H2 = 280, L = 44, R = 78, T = 12, B = 26, W2 = PG.fitWidth(svg, H2);
    const top5 = S.topRooms(f).map((r) => r[0]), now = tm(f.t), pk = tm(s.at), hist = S.history();
    const top = Math.max(0.2, ...top5.flatMap((c) => hist.map((x) => x.rooms[c] || 0).concat([f.proom[c] ?? 0]))) * 1.12, step = PG.niceStep(top);
    const x = (m) => L + m / 1440 * (W2 - L - R), y = (v) => T + (1 - v / top) * (H2 - T - B);
    PG.axisY(svg, y, top, L, R, W2, step, step < 1 ? 1 : 0);
    for (let h = 0; h <= 24; h += 3) { const t = el('text', { x: x(h * 60), y: H2 - 6, 'text-anchor': 'middle', 'font-size': 11, fill: css('--muted') }, svg); t.textContent = h + '시'; }
    el('line', { x1: x(now), x2: x(now), y1: T, y2: H2 - B, stroke: css('--ink2'), 'stroke-dasharray': '2 3' }, svg);
    const leg = $('#t5leg'); leg.replaceChildren(); const labs = [];
    top5.forEach((c, k) => {
      const col = LINE5[k]; let seg = [], prev = null;
      const flush = () => { if (seg.length > 1) el('polyline', { points: seg.join(' '), fill: 'none', stroke: col, 'stroke-width': k === 0 ? 2.6 : 1.8, 'stroke-linejoin': 'round' }, svg); seg = []; };
      hist.forEach((h) => { const m = tm(h.t); if (prev !== null && m - prev > 15) flush(); seg.push(x(m) + ',' + y(h.rooms[c] || 0)); prev = m; });
      flush();
      const pv = f.proom[c];
      if (pv != null) { el('line', { x1: x(now), y1: y(f.rooms[c] || 0), x2: x(pk), y2: y(pv), stroke: col, 'stroke-width': 1.8, 'stroke-dasharray': '5 4' }, svg); el('circle', { cx: x(pk), cy: y(pv), r: 4.5, fill: css('--panel'), stroke: col, 'stroke-width': 2 }, svg); }
      labs.push([y(pv ?? f.rooms[c] ?? 0), col, (k + 1) + '. ' + roomName(c)]);
      const sp = document.createElement('span'), sw = document.createElement('i'); sw.className = 'sw'; sw.style.background = col; sw.style.height = '4px';
      sp.append(sw, `${k + 1}. ${roomName(c)} ${fmt(f.rooms[c] || 0, 2)} → ${fmt(pv ?? 0, 2)} kW`); leg.append(sp);
    });
    labs.sort((a, b2) => a[0] - b2[0]);
    for (let k = 1; k < labs.length; k++) labs[k][0] = Math.max(labs[k][0], labs[k - 1][0] + 14);   // 이름표 겹치지 않게
    labs.forEach(([yy, col, t]) => { const lt = el('text', { x: x(pk) + 8, y: yy + 4, 'font-size': 11, fill: col, 'font-weight': 600 }, svg); lt.textContent = t; });
    $('#t5t').textContent = `${f.t} 기준 · 예상 피크 ${s.at}`;
  }

  // ---------- 호실별 피크 예측 가능성 ----------
  S.possibility = (f, s) => {
    const thr = S.live ? S.live.frame.room_thr : S.roomThr;
    const tot = f.pred[s.k];
    return S.hos.map((c) => {
      const p = S.live ? Math.max(...(S.live.frame.rooms_pred[c] || [0])) : (f.proom[c] ?? 0);
      const pct = thr[c] ? p / thr[c] * 100 : null;
      return { c, p, pct, share: tot ? (f.proom[c] ?? 0) / tot * 100 : null, thr: thr[c] };
    });
  };
  function drawPossibility(f, s) {
    const rows = S.possibility(f, s).filter((r) => r.thr).sort((a, b2) => b2.pct - a.pct).slice(0, 12);
    const box = $('#pbars'); box.replaceChildren();
    const mx = Math.max(120, ...rows.map((r) => r.pct));
    rows.forEach((r) => {
      const row = document.createElement('div'); row.className = 'pbrow' + (r.pct >= 100 ? ' over' : '');
      const nm = document.createElement('span'); nm.textContent = roomName(r.c);
      const bar = document.createElement('span'); bar.className = 'pb';
      const fill = document.createElement('i'); fill.style.width = Math.min(100, r.pct / mx * 100) + '%';
      const mark = document.createElement('em'); mark.style.left = (100 / mx * 100) + '%'; mark.title = '호실 기준 100%';
      bar.append(fill, mark);
      const n = document.createElement('span'); n.className = 'n'; n.textContent = `${fmt(r.pct, 0)}% · 건물 ${fmt(r.share, 0)}%`;
      n.title = `예측 ${fmt(r.p, 2)} kW / 호실 기준 ${fmt(r.thr, 2)} kW`;
      row.append(nm, bar, n); box.append(row);
    });
    $('#pbt').textContent = `${f.t} 발행 · 다음 1시간 · ${S.live ? S.live.model : S.DATA.model}`;
  }

  // ---------- 그리기 · 재생 ----------
  S.render = () => {
    if (!S.F.length && !S.live) return;
    const f = S.frame(), s = S.state(f);
    $('#clk').textContent = S.live ? S.live.frame.time : S.DATA.day + ' ' + f.t;
    drawStatus(f, s); drawKpi(f, s);
    if (!$('#s-predict').hidden) { drawShare(f); drawChart(f); drawTop5(f, s); drawPossibility(f, s); (PG.onFrame || []).forEach((fn) => fn(f, s)); }
  };
  function tick() {
    if (!S.playing || S.live || !S.F.length) return;
    const f = S.F[S.i], g = S.F[Math.min(S.i + 1, S.F.length - 1)], fr = S.sub / SUB;
    const p = f.p + (g.p - f.p) * fr + (Math.random() - 0.5) * 0.9;           // 현재전력 순간값 흉내 (재생용)
    S.trail.push([tm(f.t) + 15 * fr, p]); if (S.trail.length > 400) S.trail.shift();
    setv('#k2', fmt(p, 2), 'kW'); $('#k2s').textContent = `${addMin(f.t, Math.round(15 * fr))} 순간 측정`;
    if (!$('#s-predict').hidden) drawChart(f);
    S.sub++;
    if (S.sub >= SUB) { S.sub = 0; S.i = (S.i + 1) % S.F.length; if (S.i === 0) S.trail.length = 0; S.render(); }
  }
  S.start = () => { clearInterval(S.timer); S.timer = setInterval(tick, TICK / S.speed); };
  $('#play').addEventListener('click', () => { S.playing = !S.playing; $('#play').textContent = S.playing ? '일시정지' : '재생'; });
  $('#spd').addEventListener('click', () => { S.speed = S.speed === 1 ? 2 : S.speed === 2 ? 4 : 1; $('#spd').textContent = '속도 ×' + S.speed; S.start(); });
  S.jump = (t) => { const k = S.F.findIndex((x) => x.t === t); if (k >= 0) { S.i = k; S.sub = 0; S.trail.length = 0; S.render(); } };

  // ---------- [실시간 연결]: 빠진 구간 채우기 → 가장 최근 실측 기준 예측 ----------
  S.goLive = async () => {
    const bar = $('#livebar'); bar.hidden = false; bar.textContent = '실시간 연결 중… (새 데이터 확인 → 예측)';
    try {
      const sy = await PG.api('api/live/sync', { method: 'POST' });
      const model = S.modelId();
      const r = await PG.api('api/live/predict?model=' + encodeURIComponent(model), { method: 'POST' });
      const name = (S.families.find((f) => f.direct === model || f.sum === model) || {}).name;
      S.live = { frame: r, hist: r.hist, model: `${$('#method').selectedOptions[0].text} · ${name}` };
      S.live.frame.temp = null;
      $('#modetxt').textContent = '실시간'; $('#modepill').classList.add('on');
      document.querySelectorAll('.js-model').forEach((e) => { e.textContent = S.live.model; });
      bar.replaceChildren();
      const msg = document.createElement('span');
      msg.textContent = (sy.inserted ? `새 실측 ${sy.inserted.toLocaleString()}건 반영. ` : `${sy.before_last} 이후 새 데이터 없음. `) +
        `가장 최근 실측 ${r.time} 기준 예측` + (sy.missing_slots_to_now > 0 ? ` · 오늘(${sy.now})까지 빠진 15분 칸 ${sy.missing_slots_to_now.toLocaleString()}개 (새 CSV를 ${sy.incoming_folder} 폴더에 넣고 다시 누르세요)` : '');
      const back = document.createElement('button'); back.type = 'button'; back.textContent = '시연 재생으로 돌아가기';
      back.onclick = () => { S.live = null; bar.hidden = true; $('#modetxt').textContent = '재생 중'; $('#modepill').classList.remove('on'); location.reload(); };
      bar.append(msg, back);
      S.render();
    } catch (e) { bar.textContent = '실시간 연결 실패: ' + e.message; }
  };
  $('#golive').addEventListener('click', () => S.goLive());
  if (PG.STATIC) { $('#golive').disabled = true; $('#golive').title = '시연용 파일에서는 실시간 연결을 할 수 없습니다 (서버 실행 시 가능)'; }
  PG.onTheme.push(() => S.render());
})();
