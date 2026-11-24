// [6] 첫 번째 화면 '예측 및 경보' — 예측 부분 (Front-end 담당)
//   왼쪽 위 콤보버튼 : 방식(채널별 예측 후 합산 / 채널별 합산 후 예측) · 예측모델(LightGBM·XGBoost·Random Forest·Bi-LSTM) · 재생 기준일
//   그 오른쪽        : 합산 수요전력 구성 (호실 34개 / 공용 62개)                        (drawShare)
//   오른쪽 위 배너   : 재생 중 시각 · 재생/일시정지 · 속도 · [실시간 연결] (누르면 재생 버튼 비활성화, 실시간 표시)
//   상태 띠          : 판정 · 안내 | 가운데 현재 수요전력 | 피크 기준 콤보박스(최근 30일·전월·당월·7~9월·12~2월 P95)
//   상단 변동선      : 실측 + 다음 1시간 예측 + 피크 기준선  (drawChart)  ┐ 두 그래프 모두 0시~24시,
//   하단 변동선      : 점검 우선 Top 5 호실 추이와 예측       (drawTop5)   ┘ 같은 가로 길이로 맞춤
//   오른쪽 아래      : 호실별 피크 예측 가능성 (막대 = 그 호실 직전 30일 P95 대비 %, 옆에 예측 kW) (drawPossibility)
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
    const date = QS.get('date') || (meta.days.find((d) => d.date === ((PG.STATIC && PG.STATIC.date) || meta.default_date || '2026-11-02')) || meta.days[meta.days.length - 1]).date;
    meta.days.forEach((d) => $('#dsel').add(new Option(d.date, d.date, false, d.date === date)));
    const go = () => { location.href = location.pathname + '?date=' + $('#dsel').value + '&model=' + encodeURIComponent(S.modelId()) + location.hash; };
    ['#method', '#family', '#dsel'].forEach((s) => { $(s).onchange = () => (S.live ? S.goLive() : go()); });
    S.DATA = await PG.api(`api/replay?date=${date}&model=${encodeURIComponent(S.modelId())}`);
    S.F = S.DATA.frames; S.floors = S.DATA.floors; S.roomThr = S.DATA.room_thr || {};
    S.hos = Object.keys(S.F[0].rooms);
    $('#dayl').textContent = S.DATA.day;
    $('#method').title = S.DATA.method === 'sum'
      ? '96개 채널 중 실제 사용 채널 61개(35개는 전 기간 0 kW)를 각각 예측해 같은 시각끼리 더한 건물 합계'
      : '96개 채널을 먼저 더한 건물 합계를 바로 예측 (기업 권장) · 호실 숫자는 같은 모델의 채널 예측';
    S.setThrOptions(S.DATA.thr_options);
    document.querySelectorAll('.js-model').forEach((e) => { e.textContent = `${S.DATA.method_name} · ${S.DATA.model}`; });
    S.i = Math.max(0, S.F.findIndex((x) => x.t === '12:30'));
  };

  // ---------- 피크 기준 콤보박스: 최근 30일 / 전월 / 당월 / 7~9월 / 12~2월 (모두 95번째 백분위수) ----------
  const THR_KEY = 'peakguard-thr';
  S.thrKey = (() => { try { return localStorage.getItem(THR_KEY) || 'recent30'; } catch (e) { return 'recent30'; } })();
  S.setThrOptions = (opts) => {
    S.thrOpts = opts || {};
    const sel = $('#thrsel'); sel.replaceChildren();
    Object.values(S.thrOpts).forEach((o) => {
      const op = new Option(`${o.name} 95번째 백분위수` + (o.kw == null ? ` — ${o.note}` : ''), o.key, false, o.key === S.thrKey);
      op.disabled = o.kw == null; sel.add(op);
    });
    if (!S.thrOpts[S.thrKey] || S.thrOpts[S.thrKey].kw == null) { S.thrKey = 'recent30'; sel.value = 'recent30'; }
  };
  // 기준 바꾸기 (첫 화면·세 번째 화면 어느 쪽에서 바꿔도 같이 바뀜)
  S.setThr = (key) => {
    S.thrKey = key; $('#thrsel').value = key; try { localStorage.setItem(THR_KEY, key); } catch (e) { /* 저장 불가 */ }
    PG.resetAlerts && PG.resetAlerts(); S.render();
  };
  $('#thrsel').addEventListener('change', () => S.setThr($('#thrsel').value));
  // 그 칸의 피크 기준: 고른 기준점 값 (최근 30일이면 칸마다 저장된 값)
  S.thrOf = (f) => (S.thrKey === 'recent30' || !S.thrOpts[S.thrKey] ? f.thr0 : S.thrOpts[S.thrKey].kw);
  const applyThr = (f) => { if (f.thr0 === undefined) f.thr0 = f.thr; f.thr = S.thrOf(f); return f; };

  // ---------- 판정: 다음 1시간 예측 4개 중 하나라도 기준 이상 = 피크 위험, 95% 이상 = 주의 ----------
  S.state = (f) => {
    const mx = Math.max(...f.pred), k = f.pred.indexOf(mx);
    const first = f.thr == null ? -1 : f.pred.findIndex((v) => v >= f.thr);
    const lvl = f.thr == null ? 'ok' : first >= 0 ? 'bad' : (mx >= f.thr * 0.95 ? 'warn' : 'ok');
    return { mx, k, first, lvl, at: addMin(f.t, 15 * (k + 1)) };
  };
  S.topRooms = (f, n = 5) => Object.entries(Object.keys(f.proom || {}).length ? f.proom : f.rooms).sort((a, b2) => b2[1] - a[1]).slice(0, n);
  S.frame = () => applyThr(S.live ? S.live.frame : S.F[S.i]);
  S.history = () => (S.live ? S.live.hist : S.F.slice(0, S.i + 1));

  // ---------- 상태 띠 · 숫자 4개 ----------
  function drawStatus(f, s) {
    $('#status').classList.toggle('risk', s.lvl === 'bad');
    const pill = $('#pill'); pill.className = 'pill ' + s.lvl;
    pill.textContent = s.lvl === 'bad' ? '피크 위험' : s.lvl === 'warn' ? '주의' : '정상';
    const msg = $('#msg'); msg.replaceChildren();
    const line = document.createElement('div');
    if (f.thr == null) line.append('피크 기준을 계산할 실측이 부족합니다 — 오른쪽에서 다른 기준을 고르세요');
    else if (s.lvl === 'bad') line.append('최초 위험 예상 ', PG.b(addMin(f.t, 15 * (s.first + 1))), ' · 최대 예상 ', PG.b(fmt(s.mx, 2) + ' kW'), ' · 기준 초과 ', PG.b('+' + fmt(s.mx - f.thr, 2) + ' kW'));
    else if (s.lvl === 'warn') line.append('다음 1시간 최대 ', PG.b(fmt(s.mx, 2) + ' kW'), ' (', PG.b(s.at), ') · 기준의 ', PG.b(fmt(s.mx / f.thr * 100, 0) + '%'));
    else line.append('다음 1시간 최대 ', PG.b(fmt(s.mx, 2) + ' kW'), ' · 기준까지 여유 ', PG.b(fmt(f.thr - s.mx, 2) + ' kW'));
    const sm = document.createElement('small');
    sm.textContent = s.lvl === 'bad' ? `먼저 점검할 호실: ${S.topRooms(f, 3).map((r) => roomName(r[0])).join(', ')} → 오른쪽 '경보발령 선택'에서 발령` : `다음 갱신 ${addMin(f.t, 15)}`;
    msg.append(line, sm);
    if (f.thr == null) $('#thr').textContent = '—'; else setv('#thr', fmt(f.thr, 2), 'kW');
    const o = S.thrOpts[S.thrKey];
    // 기준값 설명: 그 기간 15분 값 N개를 크기순으로 세웠을 때 상위 5%가 시작되는 경계 (최대값의 95%가 아님)
    $('#thrnote').textContent = o ? `${o.name} 15분 값 ${(o.n || 0).toLocaleString()}개 중 상위 5% 경계 (${o.period})` + (S.live && S.thrKey === 'recent30' && f.thr_note ? ` · ${f.thr_note}` : '') : (f.thr_note || '');
  }
  const setv = (sel, v, u) => { const e = $(sel); if (e.dataset.v !== v) { e.dataset.v = v; e.replaceChildren(document.createTextNode(v), Object.assign(document.createElement('small'), { textContent: u })); e.classList.remove('flash'); void e.offsetWidth; e.classList.add('flash'); } };
  // 상태 띠 ① 현재 피크: 한전 방식 최대수요전력(전날까지) — 오늘 실측이 넘으면 오늘 값으로
  function drawPeak(f) {
    const base = S.live ? S.live.frame.peak_now : S.DATA.peak_now;
    if (!base || base.kw == null) { $('#pk').textContent = '—'; $('#pks').textContent = '자료 없음'; return; }
    let kw = base.kw, when = base.time, note = base.basis;
    S.history().forEach((x) => { if (x.y != null && x.y > kw) { kw = x.y; when = `${S.live ? S.live.frame.date : S.DATA.date} ${x.t}`; note = '오늘 새 최대'; } });
    setv('#pk', fmt(kw, 2), 'kW');
    $('#pks').textContent = `${when.slice(5)} · ${note}`;
    $('#status').classList.toggle('newpeak', note === '오늘 새 최대');
  }
  // 상태 띠 ④: 현재 수요전력 (직전 15분 평균, 96개 계측채널 합산)
  function drawKpi(f) {
    setv('#k1', fmt(f.y, 2), 'kW');
    $('#k1s').textContent = `${f.t} 기준 · 96개 계측채널 합산` + (f.thr ? ` · 기준의 ${fmt(f.y / f.thr * 100, 0)}%` : '');
  }
  // ---------- 합산 수요전력 구성 (호실 34개 / 공용 62개) — 콤보버튼 오른쪽 ----------
  function drawShare(f) {
    const tot = f.y, h = tot - f.common;
    $('#sharet').textContent = `${f.t} 기준 ${fmt(tot, 2)} kW · 호실 ${fmt(h, 2)} kW / 공용 ${fmt(f.common, 2)} kW`;
    const sh = $('#share'); sh.replaceChildren();
    [[h, '호실 34개 ' + fmt(h / tot * 100, 0) + '%', css('--amber'), '#fff'], [f.common, '공용 62개 ' + fmt(f.common / tot * 100, 0) + '%', css('--grid'), css('--ink')]]
      .forEach(([v, t, bg, c]) => { const d = document.createElement('div'); d.style.width = (v / tot * 100) + '%'; d.style.background = bg; d.style.color = c; d.textContent = t; sh.append(d); });
  }

  // ---------- 상단 변동선: 실측 + 예측 + 피크 기준선 ----------
  // 두 그래프 공통 가로축: 지금 기준 '앞 2시간(왼쪽 2/3) + 다음 1시간 예측(오른쪽 1/3)'
  //   시간이 지나면 화면이 왼쪽으로 밀려남. 같은 여백이라 위·아래 '지금' 선이 같은 자리
  const AX = { L: 44, R: 78, T: 12, B: 26 }, PAST = 120, NEXT = 60;
  S.xAxis = (svg, H, now) => {
    const X0 = now - PAST, X1 = now + NEXT;
    const W = PG.fitWidth(svg, H), x = (m) => AX.L + (m - X0) / (X1 - X0) * (W - AX.L - AX.R);
    el('rect', { x: x(now), y: AX.T, width: x(X1) - x(now), height: H - AX.T - AX.B, fill: css('--pred'), opacity: 0.05 }, svg);   // 예측 구간
    for (let m = Math.ceil(X0 / 30) * 30; m <= X1; m += 30) {
      el('line', { x1: x(m), x2: x(m), y1: AX.T, y2: H - AX.B, stroke: css('--grid'), 'stroke-dasharray': '2 4' }, svg);
      const t = el('text', { x: x(m), y: H - 6, 'text-anchor': 'middle', 'font-size': 11, fill: css('--muted') }, svg);
      t.textContent = String(Math.floor(((m % 1440) + 1440) % 1440 / 60)).padStart(2, '0') + ':' + String(((m % 60) + 60) % 60).padStart(2, '0');
    }
    return { W, x, X0, X1 };
  };
  function drawChart(f) {
    const svg = $('#chart'); svg.replaceChildren();
    const H = 300, ml = AX.L, mt = AX.T, mb = AX.B;
    const { W, x: xs, X0 } = S.xAxis(svg, H, tm(f.t)), mr = AX.R;
    const ys = (v) => mt + (1 - v / 36) * (H - mt - mb);
    for (let v = 0; v <= 35; v += 5) { el('line', { x1: ml, x2: W - mr, y1: ys(v), y2: ys(v), stroke: css('--grid') }, svg); const t = el('text', { x: ml - 8, y: ys(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: css('--muted') }, svg); t.textContent = v; }
    if (f.thr != null) {
      const ty = ys(f.thr);
      el('rect', { x: ml, y: mt, width: W - ml - mr, height: Math.max(0, ty - mt), fill: css('--bad'), opacity: 0.06 }, svg);
      el('line', { x1: ml, x2: W - mr, y1: ty, y2: ty, stroke: css('--bad'), 'stroke-width': 1.5, 'stroke-dasharray': '6 4' }, svg);
      const tl = el('text', { x: ml + 6, y: ty - 6, 'font-size': 11, fill: css('--bad'), 'font-weight': 600 }, svg); tl.textContent = '피크 기준 ' + fmt(f.thr, 2) + ' kW';
    }
    if (!S.live && S.trail.length > 1) el('polyline', { points: S.trail.filter((p) => p[0] >= X0).map((p) => xs(p[0]) + ',' + ys(p[1])).join(' '), fill: 'none', stroke: css('--amber'), 'stroke-width': 1.2, opacity: 0.55 }, svg);
    const pts = S.history().filter((x) => x.y != null).map((x) => [tm(x.t), x.y]).filter((p) => p[0] >= X0);
    if (pts.length) {
      el('path', { d: 'M' + xs(pts[0][0]) + ',' + ys(0) + ' ' + pts.map((p) => 'L' + xs(p[0]) + ',' + ys(p[1])).join(' ') + ' L' + xs(pts[pts.length - 1][0]) + ',' + ys(0) + 'Z', fill: css('--ink'), opacity: 0.06 }, svg);
      el('polyline', { points: pts.map((p) => xs(p[0]) + ',' + ys(p[1])).join(' '), fill: 'none', stroke: css('--ink'), 'stroke-width': 2.4, 'stroke-linejoin': 'round' }, svg);
    }
    const t0 = tm(f.t), fp = [[t0, f.y], ...f.pred.map((v, k) => [t0 + 15 * (k + 1), v])];
    el('polyline', { points: fp.map((p) => xs(p[0]) + ',' + ys(p[1])).join(' '), fill: 'none', stroke: css('--pred'), 'stroke-width': 2.4, 'stroke-dasharray': '7 5' }, svg);
    f.pred.forEach((v, k) => { const over = f.thr != null && v >= f.thr; el('circle', { cx: xs(t0 + 15 * (k + 1)), cy: ys(v), r: over ? 4.5 : 3.5, fill: over ? css('--bad') : css('--panel'), stroke: over ? css('--bad') : css('--pred'), 'stroke-width': 2 }, svg); });
    el('line', { x1: xs(t0), x2: xs(t0), y1: mt, y2: H - mb, stroke: css('--ink2'), 'stroke-dasharray': '2 3' }, svg);
    const nt = el('text', { x: xs(t0) + 6, y: mt + 12, 'font-size': 11, fill: css('--ink2') }, svg); nt.textContent = (S.live ? '최근 실측 ' : '지금 ') + f.t;
  }

  // ---------- 하단 변동선: 점검 우선 Top 5 호실 추이 ----------
  function drawTop5(f, s) {
    const svg = $('#t5chart'); svg.replaceChildren();
    const H2 = 280, L = AX.L, R = AX.R, T = AX.T, B = AX.B;
    const { W: W2, x, X0 } = S.xAxis(svg, H2, tm(f.t));
    const top5 = S.topRooms(f).map((r) => r[0]), now = tm(f.t), pk = tm(s.at), hist = S.history().filter((h) => tm(h.t) >= X0);
    // 호실별 다음 1시간 예측 4점 (15·30·45·60분) — 상단 건물 그래프와 같은 모양
    const rp = (c) => (S.live ? S.live.frame.rooms_pred[c] : (f.rp && f.rp[c])) || [f.proom[c] ?? 0, f.proom[c] ?? 0, f.proom[c] ?? 0, f.proom[c] ?? 0];
    const top = Math.max(0.2, ...top5.flatMap((c) => hist.map((x) => x.rooms[c] || 0).concat(rp(c)))) * 1.12, step = PG.niceStep(top);
    const y = (v) => T + (1 - v / top) * (H2 - T - B);
    PG.axisY(svg, y, top, L, R, W2, step, step < 1 ? 1 : 0);
    el('line', { x1: x(now), x2: x(now), y1: T, y2: H2 - B, stroke: css('--ink2'), 'stroke-dasharray': '2 3' }, svg);
    const nt = el('text', { x: x(now) + 6, y: T + 12, 'font-size': 11, fill: css('--ink2') }, svg); nt.textContent = (S.live ? '최근 실측 ' : '지금 ') + f.t;
    const leg = $('#t5leg'); leg.replaceChildren(); const labs = [];
    top5.forEach((c, k) => {
      const col = LINE5[k]; let seg = [], prev = null;
      const flush = () => { if (seg.length > 1) el('polyline', { points: seg.join(' '), fill: 'none', stroke: col, 'stroke-width': k === 0 ? 2.6 : 1.8, 'stroke-linejoin': 'round' }, svg); seg = []; };
      hist.forEach((h) => { const m = tm(h.t); if (prev !== null && m - prev > 15) flush(); seg.push(x(m) + ',' + y(h.rooms[c] || 0)); prev = m; });
      flush();
      const pv = f.proom[c], ps = rp(c);
      const pts = [[now, f.rooms[c] || 0], ...ps.map((v, h) => [now + 15 * (h + 1), v])];
      el('polyline', { points: pts.map((q) => x(q[0]) + ',' + y(q[1])).join(' '), fill: 'none', stroke: col, 'stroke-width': 1.8, 'stroke-dasharray': '5 4' }, svg);
      ps.forEach((v, h) => { const atPk = now + 15 * (h + 1) === pk; el('circle', { cx: x(now + 15 * (h + 1)), cy: y(v), r: atPk ? 4.5 : 3.2, fill: atPk ? col : css('--panel'), stroke: col, 'stroke-width': 2 }, svg); });
      labs.push([y(ps[3]), col, (k + 1) + '. ' + roomName(c)]);
      const sp = document.createElement('span'), sw = document.createElement('i'); sw.className = 'sw'; sw.style.background = col; sw.style.height = '4px';
      sp.append(sw, `${k + 1}. ${roomName(c)} ${fmt(f.rooms[c] || 0, 2)} → ${fmt(pv ?? 0, 2)} kW`); leg.append(sp);
    });
    labs.sort((a, b2) => a[0] - b2[0]);
    for (let k = 1; k < labs.length; k++) labs[k][0] = Math.max(labs[k][0], labs[k - 1][0] + 14);   // 이름표 겹치지 않게
    labs.forEach(([yy, col, t]) => { const lt = el('text', { x: x(now + 60) + 6, y: yy + 4, 'font-size': 11, fill: col, 'font-weight': 600 }, svg); lt.textContent = t; });
    $('#t5t').textContent = `${f.t} 기준 · 점 4개 = 15·30·45·60분 뒤 예측 · 색칠한 점 = 건물 예상 피크 ${s.at}`;
  }

  // ---------- 호실별 피크 예측 가능성 ----------
  S.possibility = (f, s) => {
    const thr = S.live ? S.live.frame.room_thr : S.roomThr;
    const tot = f.pred[s.k];
    return S.hos.map((c) => {
      const p = f.proom[c] ?? 0;                   // 건물 예상 피크 시각의 그 호실 예측 kW (왼쪽 Top 5와 같은 기준)
      const pct = thr[c] ? p / thr[c] * 100 : null;
      return { c, p, pct, share: tot ? (f.proom[c] ?? 0) / tot * 100 : null, thr: thr[c] };
    });
  };
  function drawPossibility(f, s) {
    // 순서·막대 = 예측 kW (큰 순, 왼쪽 Top 5와 같은 기준). % = 그 호실 평소(직전 30일 P95) 대비 — 참고 숫자
    const rows = S.possibility(f, s).sort((a, b2) => b2.p - a.p).slice(0, 12);
    const box = $('#pbars'); box.replaceChildren();
    const mx = Math.max(0.1, ...rows.map((r) => r.p));
    rows.forEach((r) => {
      const row = document.createElement('div'); row.className = 'pbrow' + (r.pct >= 100 ? ' over' : '');
      const nm = document.createElement('span'); nm.textContent = roomName(r.c);
      const bar = document.createElement('span'); bar.className = 'pb';
      const fill = document.createElement('i'); fill.style.width = (r.p / mx * 100) + '%';
      bar.append(fill);
      const n = document.createElement('span'); n.className = 'n'; n.textContent = `${fmt(r.p, 2)} kW · 평소의 ${r.pct == null ? '—' : fmt(r.pct, 0) + '%'}`;
      n.title = `예측 ${fmt(r.p, 2)} kW / 호실 기준 ${fmt(r.thr, 2)} kW`;
      row.append(nm, bar, n); box.append(row);
    });
    $('#pbt').textContent = `${f.t}에 예측 · 다음 1시간 · ${S.live ? S.live.model : S.DATA.model}`;
  }

  // ---------- 그리기 · 재생 ----------
  S.render = () => {
    if (!S.F.length && !S.live) return;
    const f = S.frame(), s = S.state(f);
    $('#clk').textContent = S.live ? S.live.frame.time : S.DATA.day + ' ' + f.t;
    drawStatus(f, s); drawKpi(f); drawPeak(f);
    if (!$('#s-predict').hidden) { drawShare(f); drawChart(f); drawTop5(f, s); drawPossibility(f, s); (PG.onFrame || []).forEach((fn) => fn(f, s)); }
  };
  function tick() {
    if (!S.playing || S.live || !S.F.length) return;
    const f = S.F[S.i], g = S.F[Math.min(S.i + 1, S.F.length - 1)], fr = S.sub / SUB;
    const p = f.p + (g.p - f.p) * fr + (Math.random() - 0.5) * 0.9;           // 현재전력 순간값 흉내 (재생용)
    S.trail.push([tm(f.t) + 15 * fr, p]); if (S.trail.length > 400) S.trail.shift();
    if (!$('#s-predict').hidden) drawChart(f);
    S.sub++;
    if (S.sub >= SUB) { S.sub = 0; S.i = (S.i + 1) % S.F.length; if (S.i === 0) S.trail.length = 0; S.render(); }
  }
  S.start = () => { clearInterval(S.timer); S.timer = setInterval(tick, TICK / S.speed); };
  $('#play').addEventListener('click', () => { S.playing = !S.playing; $('#play').textContent = S.playing ? '일시정지' : '재생'; });
  $('#spd').addEventListener('click', () => { S.speed = S.speed === 1 ? 2 : S.speed === 2 ? 4 : 1; $('#spd').textContent = '속도 ×' + S.speed; S.start(); });
  S.jump = (t) => { const k = S.F.findIndex((x) => x.t === t); if (k >= 0) { S.i = k; S.sub = 0; S.trail.length = 0; S.render(); } };

  // ---------- [실시간 연결]: 마지막 실측 이후 새 데이터 넣기 → 가장 최근 실측 기준 예측 ----------
  //   누르면 재생 배너(재생/일시정지·속도·재생 기준일)를 비활성화하고 바로 실시간 화면으로 바꿈
  //   연결 중에는 5분마다 다시 확인 (새 15분 칸이 들어오면 예측도 새로)
  const LIVE_EVERY = 5 * 60 * 1000;
  const liveUI = (on) => {
    ['#play', '#spd', '#dsel'].forEach((sel) => { $(sel).disabled = on; });
    $('#modetxt').textContent = on ? '실시간' : '재생 중'; $('#modepill').classList.toggle('on', on);
    $('#golive').textContent = on ? '실시간 연결됨' : '실시간 연결';
    $('.replay').classList.toggle('is-live', on);
  };
  S.goLive = async (quiet) => {
    const bar = $('#livebar');
    if (!quiet) { bar.hidden = false; bar.textContent = '실시간 연결 중… (마지막 실측 이후 새 데이터 확인 → 예측)'; }
    try {
      const sy = await PG.api('api/live/sync', { method: 'POST' });
      const model = S.modelId();
      const r = await PG.api('api/live/predict?model=' + encodeURIComponent(model), { method: 'POST' });
      const name = (S.families.find((f) => f.direct === model || f.sum === model) || {}).name;
      S.playing = false; clearInterval(S.timer);
      S.live = { frame: r, hist: r.hist, model: `${$('#method').selectedOptions[0].text} · ${name}` };
      S.setThrOptions(r.thr_options);
      liveUI(true);
      document.querySelectorAll('.js-model').forEach((e) => { e.textContent = S.live.model; });
      bar.hidden = false; bar.replaceChildren();
      const msg = document.createElement('span');
      msg.textContent = (sy.inserted ? `새 실측 ${sy.inserted.toLocaleString()}건 반영. ` : `${sy.before_last} 이후 새 데이터 없음. `) +
        `가장 최근 실측 ${r.time} 기준 예측` + (sy.missing_slots_to_now > 1 ? ` · 지금(${sy.now})까지 빠진 15분 칸 ${sy.missing_slots_to_now.toLocaleString()}개 — 빠진 기간은 먼저 한꺼번에 넣어야 합니다 (README '실시간 연결 전 준비')` : ' · 5분마다 자동 갱신');
      const back = document.createElement('button'); back.type = 'button'; back.textContent = '시연 재생으로 돌아가기';
      back.onclick = () => { clearInterval(S.liveTimer); S.live = null; bar.hidden = true; liveUI(false); location.reload(); };
      bar.append(msg, back);
      clearInterval(S.liveTimer); S.liveTimer = setInterval(() => S.goLive(true), LIVE_EVERY);
      PG.resetAlerts && PG.resetAlerts();
      S.render();
    } catch (e) { bar.hidden = false; bar.textContent = '실시간 연결 실패: ' + e.message; }
  };
  // [실시간 연결] 버튼 → 빠진 자료가 있으면 먼저 물어봄
  //   ① 파일 끌어다놓기 : 기업에서 받은 CSV를 올려 빠진 기간 채움 → 실시간 연결 → 현재 시각으로 표시
  //   ② 바로 연결     : 기업 API(DB)에서 빠진 기간을 받아 채움 → 실시간 연결 → 현재 시각으로 표시
  const P = (t, cls) => Object.assign(document.createElement('p'), { textContent: t, className: cls || '' });
  function uploadDialog() {
    const zone = document.createElement('div'); zone.className = 'dropzone'; zone.tabIndex = 0;
    zone.textContent = '여기에 전력 CSV 파일을 끌어다 놓거나 눌러서 고르세요 (여러 개 가능)';
    const input = Object.assign(document.createElement('input'), { type: 'file', accept: '.csv', multiple: true, hidden: true });
    const log = document.createElement('div'); log.className = 'uplog';
    if (PG.STATIC) log.append(P('시연용 파일에서는 실제로 올리지 않습니다 (서버 실행 시 DB에 들어감). [실시간 연결]을 누르면 마지막 실측 기준으로 연결합니다.', 'note err'));
    let total = 0;
    const send = async (files) => {
      if (PG.STATIC) { [...files].forEach((f) => log.append(P(`${f.name} — 시연용 파일이라 올리지 않음`, 'note'))); return; }
      for (const f of files) {
        const line = P(`${f.name} 올리는 중…`); log.append(line);
        try {
          const fd = new FormData(); fd.append('file', f);
          const r = await PG.api('api/upload', { method: 'POST', body: fd });
          total += r.inserted || 0;
          line.textContent = `${f.name} — 새로 넣음 ${(r.inserted || 0).toLocaleString()}건 · 중복 건너뜀 ${(r.duplicates || 0).toLocaleString()}건`;
        } catch (e) { line.textContent = `${f.name} — 실패: ${e.message}`; line.className = 'note err'; }
      }
    };
    zone.onclick = () => input.click();
    input.onchange = () => send(input.files);
    zone.ondragover = (e) => { e.preventDefault(); zone.classList.add('over'); };
    zone.ondragleave = () => zone.classList.remove('over');
    zone.ondrop = (e) => { e.preventDefault(); zone.classList.remove('over'); send(e.dataTransfer.files); };
    PG.dialog('파일 끌어다놓기 — 빠진 기간 자료 올리기', [zone, input, log, P('같은 채널·시각은 한 번만 들어갑니다(여러 번 올려도 중복 없음). 빠진 기간을 가짜 값으로 채우지 않습니다.', 'note')],
      [['실시간 연결', () => { S.goLive(); }, 'primary'], ['취소']]);
  }
  async function directConnect() {
    const st = await PG.api('api/live/status');
    if (!st.api_configured) {
      PG.dialog('바로 연결', [P('기업 API(DB) 연결 정보가 아직 설정되지 않았습니다.'), P('.env 파일의 LIVE_DB_URL · LIVE_DB_QUERY 에 기업이 준 연결 정보를 넣으면 이 버튼으로 바로 받아 옵니다. 지금은 파일로 올리거나, 마지막 실측 기준으로만 연결할 수 있습니다.', 'note')],
        [['파일 끌어다놓기', () => { setTimeout(uploadDialog, 0); }], ['마지막 실측 기준으로 연결', () => { S.goLive(); }, 'primary'], ['취소']]);
      return;
    }
    S.goLive();
  }
  $('#golive').addEventListener('click', async () => {
    if (S.live) { S.goLive(); return; }
    let st;
    try { st = await PG.api('api/live/status'); } catch (e) { S.goLive(); return; }
    if (!st.missing_slots_to_now || st.missing_slots_to_now <= 1) { S.goLive(); return; }
    const next = st.last_measured ? PG.addMin(st.last_measured.slice(11, 16), 15) : '';
    PG.dialog('실시간 연결', [
      P(`${st.last_measured} 이후 자료가 없습니다. (빠진 첫 칸 ${next})`),
      P(`현재(${st.now})까지 빠진 15분 칸 ${st.missing_slots_to_now.toLocaleString()}개 — 자료를 업데이트하시겠습니까?`),
      P('파일 끌어다놓기: 기업에서 받은 CSV를 올린 뒤 실시간 연결 · 바로 연결: 기업 API로 받아 온 뒤 실시간 연결', 'note'),
    ], [['파일 끌어다놓기', () => { setTimeout(uploadDialog, 0); }], ['바로 연결', () => { setTimeout(directConnect, 0); }, 'primary'], ['취소']]);
  });
  // 시연용 파일: 질문 창·연결 흐름은 그대로 보여 주고, 파일 올리기·기업 API 받기는 서버에서만 (마지막 실측 기준 연결은 미리 담은 예측으로 동작)
  PG.onTheme.push(() => S.render());
})();
