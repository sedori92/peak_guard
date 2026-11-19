// [6] 화면 연결 — 관리자 화면 동작 (Front-end 담당)
//   - 전체 피크 선: /api/replay 로 하루치 실측·예측·피크 기준을 받아 15분마다 재생
//   - 채널별 피크 선: /api/channel 로 선택한 채널의 실측·예측·채널 피크 기준을 그림 (아래 drawChannel)
// ===== 데이터 연결: API(/api/replay)에서 하루치 실측·예측을 받아 옴 =====
// 정적 파일로 내보낸 경우(export_static.py)에는 window.PEAKGUARD_DATA 를 그대로 사용
const QS = new URLSearchParams(location.search);
// ===== 밝은/어두운 화면 전환: 왼쪽 위 피크가드 로고를 누르면 바뀜 (브라우저에 기억) =====
const THEME_KEY = 'peakguard-theme';
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  const b = document.getElementById('theme'); if (b) b.setAttribute('aria-pressed', t === 'light' ? 'true' : 'false');
}
try { applyTheme(localStorage.getItem(THEME_KEY) || 'dark'); } catch (e) { applyTheme('dark'); }
document.getElementById('theme').addEventListener('click', () => {
  const t = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  applyTheme(t);
  try { localStorage.setItem(THEME_KEY, t); } catch (e) { /* 저장 불가 환경 */ }
  if (window.peakguard && window.peakguard.redraw) window.peakguard.redraw();
});
async function loadData() {
  if (window.PEAKGUARD_DATA) return window.PEAKGUARD_DATA;
  const meta = await fetch('api/days').then((r) => { if (!r.ok) throw new Error('목록 조회 실패 (' + r.status + ')'); return r.json(); });
  const date = QS.get('date') || (meta.days.find((d) => d.date === '2026-10-28') || meta.days[meta.days.length - 1]).date;
  const model = QS.get('model') || meta.default_model;
  const dsel = document.getElementById('dsel'), msel = document.getElementById('msel');
  meta.days.forEach((d) => dsel.add(new Option(d.date, d.date, false, d.date === date)));
  meta.models.forEach((m) => msel.add(new Option(m.name, m.id, false, m.id === model)));
  const go = () => { location.search = '?date=' + dsel.value + '&model=' + encodeURIComponent(msel.value) + location.hash; };
  dsel.onchange = go; msel.onchange = go;
  const r = await fetch('api/replay?date=' + date + '&model=' + encodeURIComponent(model));
  if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || ('조회 실패 (' + r.status + ')')); }
  return r.json();
}
(async () => {
let DATA;
try { DATA = await loadData(); }
catch (err) {
  const msg = document.getElementById('msg');
  msg.textContent = '데이터를 불러오지 못했습니다: ' + err.message + ' — 서버(uvicorn peakguard.api:app) 실행 여부를 확인하고 새로고침하세요.';
  document.getElementById('pill').textContent = '조회 실패'; document.getElementById('pill').className = 'pill warn';
  return;
}
document.querySelectorAll('.js-model').forEach((e) => { e.textContent = DATA.model; });
document.querySelectorAll('.js-test').forEach((e) => { e.textContent = DATA.test_start + ' ~ ' + DATA.test_end; });

const $ = (s) => document.querySelector(s);
const F = DATA.frames, N = F.length;
let i = 0, sub = 0, playing = true, speed = 1, liveP = F[0].p;
const SUB = 8, TICK = 200;
document.getElementById('dayl').textContent = DATA.day;
const hos = Object.keys(F[0].rooms);
const fl = (c) => DATA.floors[c.slice(0, 4)];
const fmt = (v, d = 1) => Number(v).toFixed(d);
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function addMin(hm, m) { const [h, mm] = hm.split(':').map(Number); const t = h * 60 + mm + m; return String(Math.floor(t / 60)).padStart(2, '0') + ':' + String(t % 60).padStart(2, '0'); }
function state(f) {
  const mx = Math.max(...f.pred), k = f.pred.indexOf(mx);
  const first = f.pred.findIndex((v) => v >= f.thr);
  const lvl = first >= 0 ? 'bad' : (mx >= f.thr * 0.95 ? 'warn' : 'ok');
  return { mx, k, first, lvl, at: addMin(f.t, 15 * (k + 1)) };
}
function topRooms(f, n = 5) {
  const src = Object.keys(f.proom).length ? f.proom : f.rooms;
  return Object.entries(src).sort((a, b) => b[1] - a[1]).slice(0, n);
}

function drawStatus(f, s) {
  const st = $('#status'), pill = $('#pill');
  st.classList.toggle('risk', s.lvl === 'bad');
  pill.className = 'pill ' + s.lvl;
  pill.textContent = s.lvl === 'bad' ? '피크 위험' : s.lvl === 'warn' ? '주의' : '정상';
  const msg = $('#msg');
  msg.replaceChildren();
  const line = document.createElement('div');
  if (s.lvl === 'bad') {
    const first = addMin(f.t, 15 * (s.first + 1));
    line.append('최초 위험 예상 ', b(first), ' · 최대 예상 ', b(fmt(s.mx, 2) + ' kW'), ' · 기준 초과 ', b('+' + fmt(s.mx - f.thr, 2) + ' kW'));
  } else if (s.lvl === 'warn') {
    line.append('다음 1시간 최대 ', b(fmt(s.mx, 2) + ' kW'), ' (', b(s.at), ') · 기준의 ', b(fmt(s.mx / f.thr * 100, 0) + '%'));
  } else {
    line.append('다음 1시간 최대 ', b(fmt(s.mx, 2) + ' kW'), ' · 기준까지 여유 ', b(fmt(f.thr - s.mx, 2) + ' kW'));
  }
  const sm = document.createElement('small');
  const tr = topRooms(f, 3).map((r) => `${fl(r[0])}층 ${r[0].slice(5)}번`).join(', ');
  sm.textContent = s.lvl === 'bad' ? `먼저 점검할 호실: ${tr} → 냉방·장비 가동 조정 검토` : `다음 갱신 ${addMin(f.t, 15)}`;
  msg.append(line, sm);
  $('#thr').textContent = fmt(f.thr, 2) + ' kW';
}
function b(t) { const e = document.createElement('b'); e.textContent = t; return e; }

function drawKpi(f, s) {
  setv('#k1', fmt(f.y, 2), 'kW');
  setv('#k3', fmt(s.mx, 2), 'kW');
  $('#k3s').textContent = s.at + ' 예상' + (s.lvl === 'bad' ? ' · 기준 초과' : '');
  setv('#k4', fmt(f.temp, 1), '℃');
  const mx = Math.max(...F.slice(0, i + 1).map((x) => x.y));
  $('#k5').textContent = fmt(mx, 2) + ' kW';
}
function setv(sel, v, u) { const e = $(sel); if (e.dataset.v !== v) { e.dataset.v = v; e.replaceChildren(document.createTextNode(v), Object.assign(document.createElement('small'), { textContent: u })); e.classList.remove('flash'); void e.offsetWidth; e.classList.add('flash'); } }

// main streaming chart
let X0 = 6 * 60, X1 = 21 * 60; const W = 900, H = 300, ml = 44, mr = 12, mt = 12, mb = 26;
const xs = (m) => ml + (m - X0) / (X1 - X0) * (W - ml - mr);
const ys = (v) => mt + (1 - (v - 0) / 36) * (H - mt - mb);
const tm = (hm) => { const [h, m] = hm.split(':').map(Number); return h * 60 + m; };
const NS = 'http://www.w3.org/2000/svg';
function el(n, a, p) { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); if (p) p.appendChild(e); return e; }
const liveTrail = [];
function drawChart(f, s, frac) {
  const svg = $('#chart'); svg.replaceChildren();
  X1 = tm(f.t) + 80; X0 = X1 - 390;
  for (let v = 0; v <= 35; v += 5) { el('line', { x1: ml, x2: W - mr, y1: ys(v), y2: ys(v), stroke: css('--grid') }, svg); const t = el('text', { x: ml - 8, y: ys(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: css('--muted') }, svg); t.textContent = v; }
  for (let h = Math.ceil(X0 / 60); h * 60 <= X1; h += 1) { el('line', { x1: xs(h * 60), x2: xs(h * 60), y1: mt, y2: H - mb, stroke: css('--grid'), 'stroke-dasharray': '2 4' }, svg); const t = el('text', { x: xs(h * 60), y: H - 6, 'text-anchor': 'middle', 'font-size': 11, fill: css('--muted') }, svg); t.textContent = h + '시'; }
  const ty = ys(f.thr);
  el('rect', { x: ml, y: mt, width: W - ml - mr, height: Math.max(0, ty - mt), fill: css('--bad'), opacity: 0.06 }, svg);
  el('line', { x1: ml, x2: W - mr, y1: ty, y2: ty, stroke: css('--bad'), 'stroke-width': 1.5, 'stroke-dasharray': '6 4' }, svg);
  const tl = el('text', { x: ml + 6, y: ty - 6, 'text-anchor': 'start', 'font-size': 11, fill: css('--bad'), 'font-weight': 600 }, svg); tl.textContent = '피크 기준 ' + fmt(f.thr, 2) + ' kW';
  // live instantaneous trail
  if (liveTrail.length > 1) el('polyline', { points: liveTrail.filter((p) => p[0] >= X0).map((p) => xs(p[0]) + ',' + ys(p[1])).join(' '), fill: 'none', stroke: css('--amber'), 'stroke-width': 1.2, opacity: 0.55 }, svg);
  // actual
  const pts = F.slice(0, i + 1).map((x) => [tm(x.t), x.y]).filter((p) => p[0] >= X0 - 15);
  const area = 'M' + xs(pts[0][0]) + ',' + ys(0) + ' ' + pts.map((p) => 'L' + xs(p[0]) + ',' + ys(p[1])).join(' ') + ' L' + xs(pts[pts.length - 1][0]) + ',' + ys(0) + 'Z';
  el('path', { d: area, fill: css('--ink'), opacity: 0.06 }, svg);
  el('polyline', { points: pts.map((p) => xs(p[0]) + ',' + ys(p[1])).join(' '), fill: 'none', stroke: css('--ink'), 'stroke-width': 2.4, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, svg);
  // forecast
  const t0 = tm(f.t);
  const fp = [[t0, f.y], ...f.pred.map((v, k) => [t0 + 15 * (k + 1), v])];
  const band = fp.map((p, k) => [p[0], p[1] + k * 0.45]).concat(fp.slice().reverse().map((p, k2) => [p[0], p[1] - (fp.length - 1 - k2) * 0.45]));
  el('polygon', { points: band.map((p) => xs(p[0]) + ',' + ys(p[1])).join(' '), fill: css('--pred'), opacity: 0.16 }, svg);
  el('polyline', { points: fp.map((p) => xs(p[0]) + ',' + ys(p[1])).join(' '), fill: 'none', stroke: css('--pred'), 'stroke-width': 2.4, 'stroke-dasharray': '7 5' }, svg);
  f.pred.forEach((v, k) => { const over = v >= f.thr; el('circle', { cx: xs(t0 + 15 * (k + 1)), cy: ys(v), r: over ? 6 : 4.5, fill: over ? css('--bad') : css('--panel'), stroke: over ? css('--bad') : css('--pred'), 'stroke-width': 2 }, svg); });
  // now marker
  el('line', { x1: xs(t0), x2: xs(t0), y1: mt, y2: H - mb, stroke: css('--ink2'), 'stroke-dasharray': '2 3' }, svg);
  const lt = liveTrail.length ? liveTrail[liveTrail.length - 1] : [t0, f.y];
  const halo = el('circle', { cx: xs(lt[0]), cy: ys(lt[1]), r: 11, fill: css('--yellow'), opacity: 0.35 }, svg);
  halo.innerHTML = '<animate attributeName="r" values="6;14;6" dur="1s" repeatCount="indefinite"/><animate attributeName="opacity" values=".5;0;.5" dur="1s" repeatCount="indefinite"/>';
  el('circle', { cx: xs(lt[0]), cy: ys(lt[1]), r: 4.5, fill: css('--amber'), stroke: css('--panel'), 'stroke-width': 1.5 }, svg);
  const nt = el('text', { x: xs(t0) + 6, y: mt + 12, 'font-size': 11, fill: css('--ink2') }, svg); nt.textContent = '지금 ' + f.t;
}

function drawTop(f, s) {
  $('#topat').textContent = s.at + ' 예상 기준';
  const box = $('#top5'); box.replaceChildren();
  const tr = topRooms(f); const mx = tr[0][1];
  tr.forEach(([c, v], k) => {
    const r = document.createElement('div'); r.className = 'room';
    const rk = document.createElement('span'); rk.className = 'rk'; rk.textContent = k + 1;
    const nm = document.createElement('span'); nm.textContent = `${fl(c)}층 ${c.slice(5)}번`; nm.title = c;
    const bar = document.createElement('span'); bar.className = 'bar'; const ii = document.createElement('i'); ii.style.width = (v / mx * 100) + '%'; bar.append(ii);
    const n = document.createElement('span'); n.className = 'n'; n.textContent = fmt(v, 2) + ' kW';
    r.append(rk, nm, bar, n); box.append(r);
  });
}
function drawFloors(f) {
  const box = $('#floors'); box.replaceChildren();
  const com = { 1: 'M001', 2: 'M003', 3: 'M005', 4: 'M007' };
  for (let k = 1; k <= 4; k++) {
    const hv = hos.filter((c) => fl(c) === k).reduce((a, c) => a + f.rooms[c], 0);
    const cv = f.floorCommon ? f.floorCommon[k] : null;
    const d = document.createElement('div'); d.className = 'floor';
    const h = document.createElement('div'); h.className = 'h'; h.append(k + '층 호실'); const sp = document.createElement('span'); sp.textContent = fmt(hv, 2) + ' kW'; h.append(sp);
    const st = document.createElement('div'); st.className = 'stack'; const a = document.createElement('i'); a.style.width = Math.min(100, hv / 16 * 100) + '%'; a.style.background = k === 4 ? css('--amber') : css('--yellow'); st.append(a);
    const dd = document.createElement('div'); dd.className = 'd'; const n = hos.filter((c) => fl(c) === k).length; const s1 = document.createElement('span'); s1.textContent = `호실 채널 ${n}개`; const s2 = document.createElement('span'); s2.textContent = `합산 대비 ${fmt(hv / f.y * 100, 0)}%`; dd.append(s1, s2);
    d.append(h, st, dd); box.append(d);
  }
}
function drawRooms(f, s) {
  const tot = f.y, h = tot - f.common;
  $('#sharet').textContent = `${f.t} 기준 ${fmt(tot, 2)} kW`;
  const sh = $('#share'); sh.replaceChildren();
  [[h, '호실 34개 ' + fmt(h / tot * 100, 0) + '%', css('--amber'), '#fff'], [f.common, '공용 62개 ' + fmt(f.common / tot * 100, 0) + '%', css('--grid'), css('--ink')]].forEach(([v, t, bg, c]) => { const d = document.createElement('div'); d.style.width = (v / tot * 100) + '%'; d.style.background = bg; d.style.color = c; d.textContent = t; sh.append(d); });
  const top = topRooms(f).map((r) => r[0]);
  const plan = $('#plan'); plan.replaceChildren();
  const mx = Math.max(...hos.map((c) => f.rooms[c]));
  for (let k = 4; k >= 1; k--) {
    const row = document.createElement('div'); row.className = 'fl';
    const lab = document.createElement('div'); lab.className = 'lab'; lab.textContent = k + 'F';
    const tiles = document.createElement('div'); tiles.className = 'tiles';
    hos.filter((c) => fl(c) === k).forEach((c) => {
      const v = f.rooms[c]; const t = document.createElement('div'); t.className = 'tile';
      const a = Math.min(1, Math.sqrt(v / mx));
      t.style.background = `color-mix(in srgb, ${css('--amber')} ${Math.round(a * 85)}%, ${css('--panel')})`;
      if (v < 0.02) t.classList.add('off');
      const r = top.indexOf(c); if (r >= 0) { t.classList.add('top'); t.dataset.rank = r + 1; }
      const cc = document.createElement('span'); cc.className = 'c'; cc.textContent = c.slice(5) + '번';
      const kk = document.createElement('span'); kk.className = 'k'; kk.textContent = fmt(v, 2);
      if (a > 0.6) { kk.style.color = '#fff'; cc.style.color = '#fff'; }
      t.append(cc, kk); t.title = c; tiles.append(t);
    });
    row.append(lab, tiles); plan.append(row);
  }
  const bars = $('#bars'); bars.replaceChildren();
  const src = Object.keys(f.proom).length ? f.proom : f.rooms;
  const order = hos.slice().sort((a, b2) => src[b2] - src[a]).slice(0, 12);
  const m2 = Math.max(...order.map((c) => Math.max(src[c], f.rooms[c])));
  order.forEach((c) => {
    const r = document.createElement('div'); r.className = 'brow';
    const nm = document.createElement('span'); nm.textContent = `${fl(c)}F ${c.slice(5)}번`;
    const bb = document.createElement('span'); bb.className = 'bb';
    const a = document.createElement('i'); a.className = 'now'; a.style.width = (f.rooms[c] / m2 * 100) + '%';
    const p = document.createElement('i'); p.className = 'pr'; p.style.width = (src[c] / m2 * 100) + '%';
    bb.append(a, p);
    const n = document.createElement('span'); n.className = 'n'; n.textContent = `${fmt(f.rooms[c], 2)} → ${fmt(src[c], 2)}`;
    r.append(nm, bb, n); bars.append(r);
  });
}
function drawFactors(f, s) {
  const sp = (f.shap_pred ?? f.pred[3]);
  $('#wft').textContent = `${f.t} 시점 → ${addMin(f.t, 60)} LightGBM 예측 ${fmt(sp, 2)} kW`;
  const svg = $('#wf'); svg.replaceChildren();
  const items = f.shap.slice(0, 6); const rest = sp - f.shap_base - items.reduce((a, x) => a + x[1], 0);
  const rows = [['평균 예측값', f.shap_base, 'base'], ...items.map((x) => [x[0], x[1], 'c']), ['그 밖의 변수', rest, 'c'], ['1시간 뒤 예측', sp, 'end']];
  const lo = 0, hi = Math.max(36, sp + 2); const L = 170, R = 880;
  const xv = (v) => L + (v - lo) / (hi - lo) * (R - L);
  const rh = 360 / rows.length;
  let cur = 0;
  rows.forEach((r, k) => {
    const y = k * rh + rh * 0.2, hgt = rh * 0.6;
    const t = el('text', { x: L - 10, y: y + hgt / 2 + 4, 'text-anchor': 'end', 'font-size': 13, fill: css('--ink') }, svg); t.textContent = r[0];
    let x0, x1, col;
    if (r[2] === 'base') { x0 = xv(0); x1 = xv(r[1]); cur = r[1]; col = css('--muted'); }
    else if (r[2] === 'end') { x0 = xv(0); x1 = xv(r[1]); col = r[1] >= f.thr ? css('--bad') : css('--ink'); }
    else { x0 = xv(cur); x1 = xv(cur + r[1]); cur += r[1]; col = r[1] >= 0 ? css('--amber') : css('--muted'); }
    el('rect', { x: Math.min(x0, x1), y, width: Math.max(2, Math.abs(x1 - x0)), height: hgt, rx: 5, fill: col }, svg);
    const v = el('text', { x: Math.max(x0, x1) + 6, y: y + hgt / 2 + 4, 'font-size': 12, fill: css('--ink2'), 'font-family': 'IBM Plex Mono, monospace' }, svg);
    v.textContent = r[2] === 'c' ? (r[1] >= 0 ? '+' : '') + fmt(r[1], 2) : fmt(r[1], 2) + ' kW';
  });
  el('line', { x1: xv(f.thr), x2: xv(f.thr), y1: 0, y2: 360, stroke: css('--bad'), 'stroke-dasharray': '5 4' }, svg); const tt = el('text', { x: xv(f.thr) + 5, y: 12, 'font-size': 11, fill: css('--bad'), 'font-weight': 600 }, svg); tt.textContent = '피크 기준 ' + fmt(f.thr, 2);
}
function drawStatic() {
  const svg = $('#imp'); svg.replaceChildren(); const mx = DATA.imp[0][1];
  DATA.imp.forEach(([n, v], k) => {
    const y = k * 32 + 6;
    const t = el('text', { x: 150, y: y + 15, 'text-anchor': 'end', 'font-size': 12.5, fill: css('--ink') }, svg); t.textContent = n;
    el('rect', { x: 158, y, width: Math.max(2, v / mx * 220), height: 20, rx: 5, fill: k < 3 ? css('--amber') : css('--yellow') }, svg);
    const vt = el('text', { x: 162 + v / mx * 220, y: y + 15, 'font-size': 11.5, fill: css('--ink2'), 'font-family': 'IBM Plex Mono, monospace' }, svg); vt.textContent = fmt(v, 2);
  });
  const tb = $('#perf'); tb.replaceChildren();
  const hd = document.createElement('tr'); ['모델', '방식', 'MAE (kW)', 'RMSE (kW)', 'MAPE (%)', '15분', '30분', '45분', '60분', '전일 기준 대비 개선'].forEach((h) => { const th = document.createElement('th'); th.textContent = h; hd.append(th); }); tb.append(hd);
  const base = DATA.perf['전일 동일시각'].mae;
  Object.entries(DATA.perf).forEach(([n, r]) => {
    const tr = document.createElement('tr'); if (n === DATA.model) tr.className = 'best';
    [n, r.method || '', fmt(r.mae, 3), fmt(r.rmse, 3), fmt(r.mape, 1), ...r.by_h.map((v) => fmt(v, 2)), n === '전일 동일시각' ? '기준' : fmt((base - r.mae) / base * 100, 1) + '%'].forEach((v) => { const td = document.createElement('td'); td.textContent = v; tr.append(td); });
    tb.append(tr);
  });
  drawConfusionInit();
}


// ---------- 채널별 피크 선 ----------
let CH = null;
const csel = document.getElementById('csel');
hos.slice().sort((a, b2) => fl(a) - fl(b2) || a.localeCompare(b2)).forEach((c) => csel.add(new Option(`${fl(c)}층 ${c.slice(5)}번 (${c})`, c)));
async function loadChannel(c) {
  csel.value = c;
  try {
    if (window.PEAKGUARD_CHANNELS) CH = window.PEAKGUARD_CHANNELS[c] || null;
    else {
      const r = await fetch(`api/channel?date=${DATA.date}&model=${encodeURIComponent(DATA.model_id)}&channel=${c}`);
      CH = r.ok ? await r.json() : null;
    }
  } catch (e) { CH = null; }
  drawChannel(F[i]);
}
csel.addEventListener('change', () => loadChannel(csel.value));
document.getElementById('plan').addEventListener('click', (e) => { const t = e.target.closest('.tile'); if (t && t.title) loadChannel(t.title); });
function drawChannel(f) {
  const svg = $('#chchart'); svg.replaceChildren();
  if (!CH) { $('#cht').textContent = '채널 데이터를 불러오지 못했습니다'; return; }
  const W2 = Math.max(600, Math.round(svg.clientWidth || 900)), H2 = 260, L2 = 44, R2 = 12, T2 = 12, B2 = 26;
  svg.setAttribute('viewBox', `0 0 ${W2} ${H2}`);   // 실제 폭에 맞춰 글자가 늘어나지 않게
  const now = tm(f.t), pr = CH.pred[f.t] || [];
  const ys2 = CH.actual.map((a) => a.y || 0).concat(pr.map((v) => v || 0), [CH.thr || 0]);
  const top = Math.max(0.2, ...ys2) * 1.15;
  const x = (m) => L2 + m / 1440 * (W2 - L2 - R2), y = (v) => T2 + (1 - v / top) * (H2 - T2 - B2);
  const step = top > 4 ? 1 : top > 1.5 ? 0.5 : top > 0.6 ? 0.2 : 0.1;
  for (let v = 0; v <= top; v += step) { el('line', { x1: L2, x2: W2 - R2, y1: y(v), y2: y(v), stroke: css('--grid') }, svg); const t = el('text', { x: L2 - 6, y: y(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: css('--muted') }, svg); t.textContent = fmt(v, step < 1 ? 1 : 0); }
  for (let h = 0; h <= 24; h += 3) { const t = el('text', { x: x(h * 60), y: H2 - 6, 'text-anchor': 'middle', 'font-size': 11, fill: css('--muted') }, svg); t.textContent = h + '시'; }
  if (CH.thr != null) {
    el('line', { x1: L2, x2: W2 - R2, y1: y(CH.thr), y2: y(CH.thr), stroke: css('--bad'), 'stroke-width': 1.5, 'stroke-dasharray': '6 4' }, svg);
    const tl = el('text', { x: L2 + 6, y: y(CH.thr) - 6, 'font-size': 11, fill: css('--bad'), 'font-weight': 600 }, svg); tl.textContent = '채널 피크 기준 ' + fmt(CH.thr, 2) + ' kW';
  }
  // 실측: 지금 시각까지만 (미래 실측은 보여 주지 않음), 결측 칸은 선을 끊음
  let seg = [];
  const flush = () => { if (seg.length > 1) el('polyline', { points: seg.join(' '), fill: 'none', stroke: css('--ink'), 'stroke-width': 2, 'stroke-linejoin': 'round' }, svg); seg = []; };
  let prev = null;
  CH.actual.filter((a) => tm(a.t) <= now).forEach((a) => { const m = tm(a.t); if (prev !== null && m - prev > 15) flush(); if (a.y != null) seg.push(x(m) + ',' + y(a.y)); prev = m; });
  flush();
  const cur = CH.actual.find((a) => a.t === f.t);
  if (pr.length && pr.every((v) => v != null)) {
    const pts = [[now, cur ? cur.y : pr[0]], ...pr.map((v, k) => [now + 15 * (k + 1), v])];
    el('polyline', { points: pts.map((p) => x(p[0]) + ',' + y(p[1])).join(' '), fill: 'none', stroke: css('--pred'), 'stroke-width': 2.2, 'stroke-dasharray': '6 4' }, svg);
    pr.forEach((v, k) => { const over = CH.thr != null && v >= CH.thr; el('circle', { cx: x(now + 15 * (k + 1)), cy: y(v), r: over ? 5.5 : 4, fill: over ? css('--bad') : css('--panel'), stroke: over ? css('--bad') : css('--pred'), 'stroke-width': 2 }, svg); });
  }
  el('line', { x1: x(now), x2: x(now), y1: T2, y2: H2 - B2, stroke: css('--ink2'), 'stroke-dasharray': '2 3' }, svg);
  const mx = pr.length ? Math.max(...pr) : null;
  $('#cht').textContent = `${CH.label} · ${f.t} 현재 ${cur && cur.y != null ? fmt(cur.y, 2) : '—'} kW` + (mx != null ? ` → 다음 1시간 최대 ${fmt(mx, 2)} kW` : '') + (CH.thr != null ? ` · 기준 ${fmt(CH.thr, 2)} kW` : '');
  $('#chnote').textContent = CH.note || '배치도의 호실 칸을 눌러도 바뀝니다.';
}

const SCREENS = ['overview', 'rooms', 'factors', 'sumcmp'];
const LINE5 = ['#FF7A1A', '#FFD166', '#4CC9F0', '#B388FF', '#7BE495'];
const roomName = (c) => `${fl(c)}층 ${c.slice(5)}번`;
function axisY(svg, y, top, L, R, W2, step, d) { for (let v = 0; v <= top + 1e-9; v += step) { el('line', { x1: L, x2: W2 - R, y1: y(v), y2: y(v), stroke: css('--grid') }, svg); const t = el('text', { x: L - 6, y: y(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: css('--muted') }, svg); t.textContent = fmt(v, d); } }
function niceStep(top) { return top > 20 ? 5 : top > 8 ? 2 : top > 4 ? 1 : top > 1.5 ? 0.5 : top > 0.6 ? 0.2 : 0.1; }

// ---------- 호실 기여도: 점검 우선 Top 5 채널 추이선 ----------
function drawTop5Lines(f, s) {
  const svg = $('#t5chart'); svg.replaceChildren();
  const W2 = Math.max(600, Math.round(svg.clientWidth || 900)), H2 = 280, L = 44, R = 70, T = 12, B = 26;
  svg.setAttribute('viewBox', `0 0 ${W2} ${H2}`);
  const top5 = topRooms(f).map((r) => r[0]);
  const now = tm(f.t), pk = tm(s.at);
  const hist = F.slice(0, i + 1);
  const vals = top5.flatMap((c) => hist.map((x) => x.rooms[c] || 0).concat([f.proom[c] ?? 0]));
  const top = Math.max(0.2, ...vals) * 1.12, step = niceStep(top);
  const x = (m) => L + m / 1440 * (W2 - L - R), y = (v) => T + (1 - v / top) * (H2 - T - B);
  axisY(svg, y, top, L, R, W2, step, step < 1 ? 1 : 0);
  for (let h = 0; h <= 24; h += 3) { const t = el('text', { x: x(h * 60), y: H2 - 6, 'text-anchor': 'middle', 'font-size': 11, fill: css('--muted') }, svg); t.textContent = h + '시'; }
  el('line', { x1: x(now), x2: x(now), y1: T, y2: H2 - B, stroke: css('--ink2'), 'stroke-dasharray': '2 3' }, svg);
  const leg = $('#t5leg'); leg.replaceChildren();
  const labs = [];
  top5.forEach((c, k) => {
    const col = LINE5[k];
    let seg = [], prev = null;
    const flush = () => { if (seg.length > 1) el('polyline', { points: seg.join(' '), fill: 'none', stroke: col, 'stroke-width': k === 0 ? 2.6 : 1.8, 'stroke-linejoin': 'round' }, svg); seg = []; };
    hist.forEach((h) => { const m = tm(h.t); if (prev !== null && m - prev > 15) flush(); seg.push(x(m) + ',' + y(h.rooms[c] || 0)); prev = m; });
    flush();
    const pv = f.proom[c];
    if (pv != null) {
      el('line', { x1: x(now), y1: y(f.rooms[c] || 0), x2: x(pk), y2: y(pv), stroke: col, 'stroke-width': 1.8, 'stroke-dasharray': '5 4' }, svg);
      el('circle', { cx: x(pk), cy: y(pv), r: 4.5, fill: css('--panel'), stroke: col, 'stroke-width': 2 }, svg);
    }
    labs.push([y(pv ?? f.rooms[c] ?? 0), col, (k + 1) + '. ' + roomName(c)]);
    const sp = document.createElement('span'); const sw = document.createElement('i'); sw.className = 'sw'; sw.style.background = col; sw.style.height = '4px';
    sp.append(sw, `${k + 1}. ${roomName(c)} ${fmt(f.rooms[c] || 0, 2)} → ${fmt(pv ?? 0, 2)} kW`); leg.append(sp);
  });
  labs.sort((a, b2) => a[0] - b2[0]);                       // 이름표가 겹치지 않게 14px 이상 띄움
  for (let k = 1; k < labs.length; k++) labs[k][0] = Math.max(labs[k][0], labs[k - 1][0] + 14);
  labs.forEach(([yy, col, t]) => { const lt = el('text', { x: x(pk) + 8, y: yy + 4, 'font-size': 11, fill: col, 'font-weight': 600 }, svg); lt.textContent = t; });
  $('#t5t').textContent = `${f.t} 기준 · 예상 피크 ${s.at} Top 5`;
}

// ---------- 영향요인: 피크 경보 오분류표 ----------
function drawConfusionInit() {
  const sel = $('#cmsel'); if (!DATA.peak) return;
  if (!sel.options.length) { Object.keys(DATA.peak).forEach((n) => sel.add(new Option(n, n, false, n === DATA.model))); sel.onchange = drawConfusion; }
  drawConfusion();
}
function drawConfusion() {
  const n = $('#cmsel').value, p = DATA.peak[n]; const box = $('#cm'); box.replaceChildren();
  if (!p) return;
  const cell = (cls, big, small) => { const d = document.createElement('div'); d.className = 'c ' + cls; const b2 = document.createElement('b'); b2.textContent = big; const s2 = document.createElement('span'); s2.textContent = small; d.append(b2, s2); return d; };
  const hd = (t) => { const d = document.createElement('div'); d.className = 'h'; d.textContent = t; return d; };
  box.append(hd(''), hd('예측: 위험'), hd('예측: 정상'),
    hd('실제: 위험'), cell('good', p.tp, '맞힘'), cell('bad', p.fn, '놓침'),
    hd('실제: 정상'), cell('warn', p.fp, '헛경보'), cell('good', p.tn, '정상'));
  const sum = document.createElement('div'); sum.className = 'cmsum';
  sum.append('오분류율 ', b(fmt(p.miscls * 100, 1) + '%'), ' · 재현율 ', b(fmt(p.recall * 100, 0) + '%'), ' · 정밀도 ', b(fmt(p.precision * 100, 0) + '%'), ` · 발행 ${p.n}개`);
  box.append(sum);
  const tb = $('#cmall'); tb.replaceChildren();
  const h = document.createElement('tr'); ['모델', '맞힘', '놓침', '헛경보', '오분류율'].forEach((t) => { const th = document.createElement('th'); th.textContent = t; h.append(th); }); tb.append(h);
  Object.entries(DATA.peak).forEach(([m, r]) => { const tr = document.createElement('tr'); if (m === n) tr.className = 'best'; [m, r.tp, r.fn, r.fp, fmt(r.miscls * 100, 1) + '%'].forEach((v) => { const td = document.createElement('td'); td.textContent = v; tr.append(td); }); tb.append(tr); });
}

// ---------- 관제 현황: 최고관리자 보고서 (일·주·월·연 피크 변동기록 + 기여도 Top 10) ----------
let REP_P = 'month';
function initReport() {
  const di = $('#repdate'); di.value = DATA.date;
  document.querySelectorAll('#rep .seg button').forEach((bt) => bt.addEventListener('click', () => {
    REP_P = bt.dataset.p; document.querySelectorAll('#rep .seg button').forEach((x) => x.setAttribute('aria-pressed', x === bt ? 'true' : 'false')); loadReport();
  }));
  di.addEventListener('change', loadReport);
  loadReport();
}
async function loadReport() {
  const d = $('#repdate').value || DATA.date, dl = $('#repdl');
  dl.href = `api/report/xlsx?period=${REP_P}&date=${d}`;
  let r = null;
  try {
    if (window.PEAKGUARD_REPORT) { r = window.PEAKGUARD_REPORT[REP_P] || null; dl.removeAttribute('href'); dl.title = '시연용 파일에서는 내려받기를 할 수 없습니다 (서버 실행 시 가능)'; }
    else { const res = await fetch(`api/report/peaks?period=${REP_P}&date=${d}`); r = res.ok ? await res.json() : null; }
  } catch (e) { r = null; }
  const rec = $('#reprec'), tp = $('#reptop'); rec.replaceChildren(); tp.replaceChildren();
  if (!r || !r.records.length) { $('#repinfo').textContent = '이 기간에는 96개 채널이 모두 수신된 데이터가 없습니다.'; return; }
  const sm = r.summary;
  $('#repinfo').textContent = `${r.title} ${r.start} ~ ${r.end} · 최대 ${fmt(sm.peak_kw, 2)} kW (${sm.peak_time.slice(5)})` + (sm.prev_change_pct != null ? ` · 직전 대비 ${sm.prev_change_pct > 0 ? '+' : ''}${fmt(sm.prev_change_pct, 1)}%` : '') + ` · 수신률 ${fmt(sm.coverage_pct, 0)}%`;
  $('#repunit').textContent = `${r.unit} 단위 최대 · 빨강 = 피크 기준 초과`;
  $('#reppk').textContent = `${sm.peak_time} 피크 ${fmt(sm.peak_kw, 2)} kW 시각`;
  const row = (tb, cells, th) => { const tr = document.createElement('tr'); cells.forEach((v) => { const c = document.createElement(th ? 'th' : 'td'); c.textContent = v; tr.append(c); }); tb.append(tr); return tr; };
  row(rec, ['구간', '최대 kW', '시각', '기준 kW', '최대 기여'], true);
  r.records.forEach((x) => { const tr = row(rec, [x.label, fmt(x.peak_kw, 2), x.peak_time.slice(11), x.threshold_kw != null ? fmt(x.threshold_kw, 2) : '—', `${x.top_name} ${fmt(x.top_share, 0)}%`]); if (x.over) tr.className = 'over'; });
  row(tp, ['순위', '채널', '구분', 'kW', '건물 대비', '호실 대비'], true);
  r.top10.forEach((x) => row(tp, [x.rank, x.name, x.kind, fmt(x.kw, 2), fmt(x.share_building, 1) + '%', x.share_rooms != null ? fmt(x.share_rooms, 1) + '%' : '—']));
}

// ---------- 보조 화면: 채널합산(Random Forest) 비교 ----------
let CMP = null, CMP_LOADING = false;
async function loadSumCmp() {
  if (CMP || CMP_LOADING) return; CMP_LOADING = true;
  try {
    if (window.PEAKGUARD_SUMCMP) CMP = window.PEAKGUARD_SUMCMP;
    else { const r = await fetch('api/sumcompare?date=' + DATA.date); CMP = r.ok ? await r.json() : null; }
  } catch (e) { CMP = null; }
  CMP_LOADING = false;
  if (!CMP) { $('#cmpsplit').textContent = '비교 데이터를 불러오지 못했습니다'; return; }
  const sp = CMP.splits;
  if (sp) $('#cmpsplit').textContent = `학습 ${sp.train[0].slice(0, 10)}~${sp.train[1].slice(0, 10)} · 검증 ${sp.val[0].slice(0, 10)}~${sp.val[1].slice(0, 10)} · 시험 ${sp.test[0]}~${sp.test[1].slice(5)}`;
  drawCmpPerf();
  $('#cmpm').onchange = () => drawSumCmp(F[i]); $('#cmph').onchange = () => drawSumCmp(F[i]);
  drawSumCmp(F[i]);
}
function drawCmpPerf() {
  const tb = $('#cmpperf'); tb.replaceChildren();
  const hd = document.createElement('tr'); ['모델', '방식', '검증 MAE', '시험 MAE', 'RMSE', '편향', '15분', '30분', '45분', '60분', '피크 맞힘/놓침/헛경보', '오분류율', '전일 기준 대비'].forEach((h) => { const th = document.createElement('th'); th.textContent = h; hd.append(th); }); tb.append(hd);
  const base = CMP.perf['전일 동일시각'].mae;
  const bestVal = Math.min(...Object.values(CMP.perf).filter((r) => r.method !== '기준' && r.method !== '비교' && r.val_mae != null).map((r) => r.val_mae));
  Object.entries(CMP.perf).forEach(([n, r]) => {
    const pk = CMP.peak[n] || {};
    const tr = document.createElement('tr'); if (r.method === '채널합산') tr.className = 'sum'; if (r.val_mae === bestVal) tr.classList.add('best');
    [n, r.method, r.val_mae != null ? fmt(r.val_mae, 3) : '—', fmt(r.mae, 3), fmt(r.rmse, 3), (r.bias > 0 ? '+' : '') + fmt(r.bias, 3), ...r.by_h.map((v) => fmt(v, 2)), `${pk.tp ?? '—'} / ${pk.fn ?? '—'} / ${pk.fp ?? '—'}`, pk.miscls != null ? fmt(pk.miscls * 100, 1) + '%' : '—', r.method === '기준' ? '기준' : fmt((base - r.mae) / base * 100, 1) + '%'].forEach((v) => { const td = document.createElement('td'); td.textContent = v; tr.append(td); });
    tb.append(tr);
  });
}
function drawSumCmp(f) {
  if (!CMP) return;
  const svg = $('#cmpchart'); svg.replaceChildren();
  const W2 = Math.max(600, Math.round(svg.clientWidth || 900)), H2 = 280, L = 44, R = 12, T = 12, B = 26;
  svg.setAttribute('viewBox', `0 0 ${W2} ${H2}`);
  const [dm, sm] = $('#cmpm').value === 'rf' ? ['RandomForest', 'RF_SUM'] : ['LightGBM', 'LGBM_SUM'];
  const hk = Number($('#cmph').value), lead = 15 * (hk + 1), now = tm(f.t);
  const toLine = (m) => Object.entries(CMP.lines[m] || {}).map(([t, v]) => [tm(t) + lead, v[hk]]).filter((p) => p[1] != null && p[0] <= now + lead).sort((a, b2) => a[0] - b2[0]);
  const act = Object.entries(CMP.actual).map(([t, v]) => [tm(t), v]).filter((p) => p[0] <= now && p[1] != null).sort((a, b2) => a[0] - b2[0]);
  const dl = toLine(dm), sl = toLine(sm);
  const top = Math.max(10, ...act.map((p) => p[1]), ...dl.map((p) => p[1]), ...sl.map((p) => p[1]), f.thr || 0) * 1.08, step = niceStep(top);
  const x = (m) => L + m / 1440 * (W2 - L - R), y = (v) => T + (1 - v / top) * (H2 - T - B);
  axisY(svg, y, top, L, R, W2, step, 0);
  for (let h = 0; h <= 24; h += 3) { const t = el('text', { x: x(h * 60), y: H2 - 6, 'text-anchor': 'middle', 'font-size': 11, fill: css('--muted') }, svg); t.textContent = h + '시'; }
  if (f.thr) { el('line', { x1: L, x2: W2 - R, y1: y(f.thr), y2: y(f.thr), stroke: css('--bad'), 'stroke-dasharray': '6 4', 'stroke-width': 1.5 }, svg); const tl = el('text', { x: L + 6, y: y(f.thr) - 6, 'font-size': 11, fill: css('--bad'), 'font-weight': 600 }, svg); tl.textContent = '피크 기준 ' + fmt(f.thr, 2) + ' kW'; }
  const line = (pts, col, w, dash) => { let seg = [], prev = null; const fl2 = () => { if (seg.length > 1) el('polyline', { points: seg.join(' '), fill: 'none', stroke: col, 'stroke-width': w, 'stroke-dasharray': dash || '', 'stroke-linejoin': 'round' }, svg); seg = []; }; pts.forEach((p) => { if (prev !== null && p[0] - prev > 15) fl2(); seg.push(x(p[0]) + ',' + y(p[1])); prev = p[0]; }); fl2(); };
  line(act, css('--ink'), 2.4); line(dl, css('--amber'), 2, ''); line(sl, css('--cyan'), 2, '6 4');
  el('line', { x1: x(now), x2: x(now), y1: T, y2: H2 - B, stroke: css('--ink2'), 'stroke-dasharray': '2 3' }, svg);
  const err = (pts) => { const a = new Map(act.map((p) => [p[0], p[1]])); const e = pts.filter((p) => a.has(p[0])).map((p) => Math.abs(p[1] - a.get(p[0]))); return e.length ? e.reduce((s2, v) => s2 + v, 0) / e.length : null; };
  const ed = err(dl), es = err(sl);
  $('#cmpt').textContent = `${DATA.day} ${f.t}까지 · ${lead}분 앞 예측` + (ed != null ? ` · 오늘 평균 오차 건물 직접 ${fmt(ed, 2)} kW / 채널합산 ${fmt(es, 2)} kW` : '');
  // 호실 화면
  const plan = $('#cmpplan'); plan.replaceChildren();
  const pv = (c) => { const v = (CMP.rooms[c] || {})[f.t]; return v && v.every((z) => z != null) ? Math.max(...v) : null; };
  const mx = Math.max(0.05, ...hos.map((c) => pv(c) || 0));
  const inact = new Set(CMP.inactive || []);
  for (let k = 4; k >= 1; k--) {
    const row = document.createElement('div'); row.className = 'fl';
    const lab = document.createElement('div'); lab.className = 'lab'; lab.textContent = k + 'F';
    const tiles = document.createElement('div'); tiles.className = 'tiles';
    hos.filter((c) => fl(c) === k).forEach((c) => {
      const p = pv(c), t = document.createElement('div'); t.className = 'tile ctile'; t.title = c;
      const a = Math.min(1, Math.sqrt((p || 0) / mx));
      t.style.background = `color-mix(in srgb, ${css('--cyan')} ${Math.round(a * 70)}%, ${css('--panel')})`;
      if (inact.has(c)) t.classList.add('off');
      const ev = CMP.room_eval[c] || {}, rf = ev.RandomForest, lg = ev.LightGBM;
      const c1 = document.createElement('span'); c1.className = 'c'; c1.textContent = `${c.slice(5)}번 · 지금 ${fmt(f.rooms[c] || 0, 2)}`;
      const k1 = document.createElement('span'); k1.className = 'k'; k1.textContent = p != null ? fmt(p, 2) : '—';
      const e1 = document.createElement('span'); e1.className = 'e';
      if (rf && lg) { const r1 = document.createElement('b'), l1 = document.createElement('b'); r1.textContent = fmt(rf.mae, 3); l1.textContent = fmt(lg.mae, 3); (rf.mae <= lg.mae ? r1 : l1).className = 'win'; e1.append(r1, ' / ', l1); }
      else e1.textContent = inact.has(c) ? '예측 제외' : '—';
      t.append(c1, k1, e1); tiles.append(t);
    });
    row.append(lab, tiles); plan.append(row);
  }
  $('#cmprt').textContent = `${f.t} 발행 · 다음 1시간 최대 예측`;
}

function render() {
  const f = F[i], s = state(f);
  $('#clk').textContent = DATA.day + ' ' + f.t;
  drawStatus(f, s); drawKpi(f, s);
  if (!$('#s-overview').hidden) { drawChart(f, s); drawTop(f, s); drawFloors(f); }
  if (!$('#s-rooms').hidden) { drawRooms(f, s); drawChannel(f); drawTop5Lines(f, s); }
  if (!$('#s-factors').hidden) drawFactors(f, s);
  if (!$('#s-sumcmp').hidden) drawSumCmp(f);
}
function tick() {
  if (!playing) return;
  const f = F[i], g = F[Math.min(i + 1, N - 1)];
  const fr = sub / SUB;
  // 순간값: 다음 칸 현재전력 쪽으로 이동하며 작은 흔들림
  liveP = f.p + (g.p - f.p) * fr + (Math.random() - 0.5) * 0.9;
  liveTrail.push([tm(f.t) + 15 * fr, liveP]); if (liveTrail.length > 400) liveTrail.shift();
  setv('#k2', fmt(liveP, 2), 'kW');
  $('#k2s').textContent = `${addMin(f.t, Math.round(15 * fr))} 순간 측정`;
  if (!$('#s-overview').hidden) drawChart(f, state(f));
  sub++;
  if (sub >= SUB) { sub = 0; i = (i + 1) % N; if (i === 0) liveTrail.length = 0; render(); }
}
function show(name) {
  SCREENS.forEach((n) => { $('#s-' + n).hidden = n !== name; });
  if (name === 'sumcmp') loadSumCmp();
  document.querySelectorAll('nav.tabs a').forEach((a) => a.setAttribute('aria-current', a.dataset.s === name ? 'page' : 'false'));
  render();
}
window.addEventListener('hashchange', () => { const n = location.hash.slice(1); show(SCREENS.includes(n) ? n : 'overview'); });
$('#play').addEventListener('click', () => { playing = !playing; $('#play').textContent = playing ? '일시정지' : '재생'; });
$('#spd').addEventListener('click', () => { speed = speed === 1 ? 2 : speed === 2 ? 4 : 1; $('#spd').textContent = '속도 ×' + speed; clearInterval(window._t); window._t = setInterval(tick, TICK / speed); });
window.peakguard = { redraw: () => { drawStatic(); render(); }, jump: (t) => { const k = F.findIndex((x) => x.t === t); if (k >= 0) { i = k; sub = 0; liveTrail.length = 0; render(); } }, speed: (s) => { speed = s; clearInterval(window._t); window._t = setInterval(tick, TICK / speed); } };
drawStatic();
const start = location.hash.slice(1);
i = F.findIndex((x) => x.t === '12:30'); if (i < 0) i = 0;
show(SCREENS.includes(start) ? start : 'overview');
initReport();
loadChannel(topRooms(F[i], 1)[0][0]);
window._t = setInterval(tick, TICK);
})();
