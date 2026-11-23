// [6] 두 번째 화면 '피크이력 및 영향도 분석' (Front-end 담당) — 실측으로 돌아보는 사후 보고 화면
//   상단 '최고관리자 보고서' : 일간·주간·월간·연간 + 기준일 → 피크 변동기록, 기여도 Top 10, 스프레드시트 내려받기
//                              (기준일은 처음엔 첫 화면 '재생 기준일'과 같게 맞춤, 바꾸면 그 날을 끝으로 하는 기간)
//   하단 '층별 호실 배치도'   : 위 표에서 고른 피크 시각의 호실별 실측 (진할수록 많이 씀, 빨간 테두리 = Top 5)
//   하단 '전체 및 채널별 영향요인' : 콤보버튼 [전체 / 호실 채널] → 그 시각을 1시간 전에 예측할 때 입력값별 기여 (kW)
//   서버 자료: /api/report/peaks, /api/report/xlsx, /api/snapshot, /api/factors
(() => {
  const PG = window.PG, $ = PG.$, el = PG.el, fmt = PG.fmt, css = PG.css;
  const H2 = PG.S2 = { period: 'week', time: null, rep: null, loaded: false };
  const key = (p, d) => `api/report/peaks?period=${p}&date=${d}`;

  H2.init = () => {
    const S1 = PG.S1;
    $('#repdate').value = S1.DATA.date;
    $('#repdate').min = S1.DATA.data_start || '2026-07-01'; $('#repdate').max = S1.DATA.data_end || '2026-11-02';
    document.querySelectorAll('#rep .seg button').forEach((bt) => bt.addEventListener('click', () => {
      H2.period = bt.dataset.p;
      document.querySelectorAll('#rep .seg button').forEach((x) => x.setAttribute('aria-pressed', x === bt ? 'true' : 'false'));
      loadReport();
    }));
    $('#repdate').addEventListener('change', loadReport);
    if (PG.STATIC) {             // 시연용 파일: 시험기간 날짜만 담겨 있음
      const ds = Object.keys(PG.STATIC.replay).sort();
      $('#repdate').min = ds[0]; $('#repdate').max = ds[ds.length - 1]; $('#repdate').title = `시연용 파일은 ${ds[0]} ~ ${ds[ds.length - 1]} 만 담겨 있습니다`;
    }
    const fs = $('#fsel'); fs.add(new Option('전체 (건물 합계)', 'TOTAL'));
    S1.hos.forEach((c) => fs.add(new Option('채널 · ' + S1.roomName(c), c)));
    fs.onchange = loadFactors;
    PG.onTheme.push(() => { if (H2.snap) drawPlan(H2.snap); if (H2.fac) drawFactors(H2.fac); });
  };
  H2.show = () => { if (!H2.loaded) { H2.loaded = true; loadReport(); } };

  // ---------- 상단: 최고관리자 보고서 ----------
  async function loadReport() {
    const d = $('#repdate').value, dl = $('#repdl');
    if (PG.STATIC) { dl.removeAttribute('href'); dl.title = '시연용 파일에서는 내려받기를 할 수 없습니다 (서버 실행 시 가능)'; }
    else dl.href = `api/report/xlsx?period=${H2.period}&date=${d}`;
    let r = null;
    try { r = await PG.api(key(H2.period, d)); } catch (e) { r = null; $('#repinfo').textContent = e.message; }
    const rec = $('#reprec'), tp = $('#reptop'); rec.replaceChildren(); tp.replaceChildren();
    H2.rep = r;
    if (!r || !r.records.length) { if (r) $('#repinfo').textContent = `${r.start} ~ ${r.end}: 96개 채널이 모두 수신된 데이터가 없습니다.`; return; }
    const sm = r.summary;
    $('#repinfo').textContent = `${r.title} ${r.start} ~ ${r.end} · 최대 ${fmt(sm.peak_kw, 2)} kW (${sm.peak_time.slice(5)})` +
      (sm.prev_change_pct != null ? ` · 직전 대비 ${sm.prev_change_pct > 0 ? '+' : ''}${fmt(sm.prev_change_pct, 1)}%` : '') + ` · 수신률 ${fmt(sm.coverage_pct, 0)}%`;
    $('#repunit').textContent = `${r.unit} 단위 최대 · 빨강 = 피크 기준 초과`;
    $('#reppk').textContent = `${sm.peak_time} 피크 ${fmt(sm.peak_kw, 2)} kW 시각`;
    const row = (tb, cells, th) => { const tr = document.createElement('tr'); cells.forEach((v) => { const c = document.createElement(th ? 'th' : 'td'); c.textContent = v; tr.append(c); }); tb.append(tr); return tr; };
    row(rec, ['구간', '최대 kW', '시각', '기준 kW', '최대 기여'], true);
    r.records.forEach((x) => {
      const tr = row(rec, [x.label, fmt(x.peak_kw, 2), x.peak_time.slice(5), x.threshold_kw != null ? fmt(x.threshold_kw, 2) : '—', `${x.top_name} ${fmt(x.top_share, 0)}%`]);
      if (x.over) tr.classList.add('over');
      tr.dataset.t = x.peak_time; tr.tabIndex = 0; tr.title = '눌러서 아래 배치도·영향요인을 이 피크 시각으로';
      tr.onclick = () => pick(x.peak_time); tr.onkeydown = (e) => { if (e.key === 'Enter') pick(x.peak_time); };
    });
    row(tp, ['순위', '채널', '구분', 'kW', '건물 대비', '호실 대비'], true);
    r.top10.forEach((x) => {
      const tr = row(tp, [x.rank, x.name, x.kind, fmt(x.kw, 2), fmt(x.share_building, 1) + '%', x.share_rooms != null ? fmt(x.share_rooms, 1) + '%' : '—']);
      if (x.kind === '호실' && x.channel) { tr.classList.add('link'); tr.onclick = () => { $('#fsel').value = x.channel.replace('-CH', '-'); loadFactors(); }; }
    });
    pick(sm.peak_time);
  }

  // 피크 시각 고르기 → 배치도·영향요인 갱신
  async function pick(t) {
    H2.time = t;
    document.querySelectorAll('#reprec tr').forEach((tr) => tr.classList.toggle('sel', tr.dataset.t === t));
    try { H2.snap = await PG.api('api/snapshot?time=' + encodeURIComponent(t)); drawPlan(H2.snap); } catch (e) { $('#snapt').textContent = e.message; $('#plan').replaceChildren(); }
    loadFactors();
  }

  // ---------- 하단 왼쪽: 층별 호실 배치도 ----------
  function drawPlan(d) {
    const S1 = PG.S1;
    const rooms = d.channels.filter((c) => c.kind === '호실');
    const by = Object.fromEntries(rooms.map((c) => [c.channel, c]));
    const top = rooms.slice().sort((a, b) => b.kw - a.kw).slice(0, 5).map((c) => c.channel);
    const mx = Math.max(0.01, ...rooms.map((c) => c.kw));
    $('#snapt').textContent = `${d.time} 실측 · 건물 ${fmt(d.total_kw, 2)} kW`;
    const plan = $('#plan'); plan.replaceChildren();
    for (let k = 4; k >= 1; k--) {
      const row = document.createElement('div'); row.className = 'fl';
      const lab = document.createElement('div'); lab.className = 'lab'; lab.textContent = k + 'F';
      const tiles = document.createElement('div'); tiles.className = 'tiles';
      S1.hos.filter((c) => S1.floors[c.slice(0, 4)] === k).forEach((c) => {
        const v = by[c] ? by[c].kw : 0, t = document.createElement('button'); t.type = 'button'; t.className = 'tile';
        const a = Math.min(1, Math.sqrt(v / mx));
        t.style.background = `color-mix(in srgb, ${css('--amber')} ${Math.round(a * 85)}%, ${css('--panel')})`;
        if (v < 0.02) t.classList.add('off');
        const r = top.indexOf(c); if (r >= 0) { t.classList.add('top'); t.dataset.rank = r + 1; }
        if ($('#fsel').value === c) t.classList.add('sel');
        const cc = document.createElement('span'); cc.className = 'c'; cc.textContent = c.slice(5) + '번';
        const kk = document.createElement('span'); kk.className = 'k'; kk.textContent = fmt(v, 2);
        if (a > 0.6) { kk.style.color = '#fff'; cc.style.color = '#fff'; }
        t.append(cc, kk); t.title = `${S1.roomName(c)} ${fmt(v, 3)} kW · 건물 대비 ${fmt(by[c]?.share_building, 1)}%`;
        t.onclick = () => { $('#fsel').value = c; loadFactors(); drawPlan(d); };
        tiles.append(t);
      });
      row.append(lab, tiles); plan.append(row);
    }
    const sh = $('#snapshare'); sh.replaceChildren();
    [[d.rooms_kw, '호실 34개 ' + fmt(d.rooms_kw / d.total_kw * 100, 0) + '%', css('--amber'), '#fff'], [d.common_kw, '공용 62개 ' + fmt(d.common_kw / d.total_kw * 100, 0) + '%', css('--grid'), css('--ink')]]
      .forEach(([v, t, bg, c]) => { const x = document.createElement('div'); x.style.width = (v / d.total_kw * 100) + '%'; x.style.background = bg; x.style.color = c; x.textContent = t; sh.append(x); });
  }

  // ---------- 하단 오른쪽: 전체 및 채널별 영향요인 ----------
  async function loadFactors() {
    if (!H2.time) return;
    const s = $('#fsel').value;
    try { H2.fac = await PG.api(`api/factors?time=${encodeURIComponent(H2.time)}&series=${encodeURIComponent(s)}`); drawFactors(H2.fac); }
    catch (e) { $('#fct').textContent = e.message; $('#fchart').replaceChildren(); }
    if (H2.snap) document.querySelectorAll('#plan .tile').forEach((t) => t.classList.remove('sel'));
  }
  function drawFactors(r) {
    $('#fct').textContent = `${r.label} · ${r.issued_at.slice(11)} 발행 → ${r.time.slice(11)} 예측 ${fmt(r.pred_kw, 2)} kW / 실측 ${fmt(r.actual_kw, 2)} kW`;
    const svg = $('#fchart'); svg.replaceChildren();
    const items = r.items.slice(0, 8), rest = r.pred_kw - r.base_kw - items.reduce((a, x) => a + x.kw, 0);
    const rows = [['평균 예측값', r.base_kw, 'base'], ...items.map((x) => [x.name, x.kw, 'c']), ['그 밖의 입력값', rest, 'c'], ['1시간 전 예측', r.pred_kw, 'end']];
    let cur = 0, lo = 0, hi = 0;
    rows.forEach((x) => { if (x[2] === 'base') cur = x[1]; else if (x[2] === 'c') cur += x[1]; hi = Math.max(hi, cur, x[1]); lo = Math.min(lo, cur); });
    if (r.actual_kw != null) hi = Math.max(hi, r.actual_kw);
    hi *= 1.1;
    const W = 460, L = 150, R = W - 60, rh = 360 / rows.length, xv = (v) => L + (v - lo) / (hi - lo) * (R - L);
    cur = 0;
    rows.forEach((x, k) => {
      const y = k * rh + rh * 0.2 + 10, hgt = rh * 0.6;
      const t = el('text', { x: L - 8, y: y + hgt / 2 + 4, 'text-anchor': 'end', 'font-size': 12, fill: css('--ink') }, svg); t.textContent = x[0];
      let x0, x1, col;
      if (x[2] === 'base') { x0 = xv(0); x1 = xv(x[1]); cur = x[1]; col = css('--muted'); }
      else if (x[2] === 'end') { x0 = xv(0); x1 = xv(x[1]); col = css('--ink'); }
      else { x0 = xv(cur); x1 = xv(cur + x[1]); cur += x[1]; col = x[1] >= 0 ? css('--amber') : css('--muted'); }
      el('rect', { x: Math.min(x0, x1), y, width: Math.max(2, Math.abs(x1 - x0)), height: hgt, rx: 4, fill: col, opacity: x[2] === 'c' && x[1] < 0 ? 0.6 : 1 }, svg);
      const v = el('text', { x: Math.max(x0, x1) + 5, y: y + hgt / 2 + 4, 'font-size': 11, fill: css('--ink2'), 'font-family': 'IBM Plex Mono, monospace' }, svg);
      v.textContent = x[2] === 'c' ? (x[1] >= 0 ? '+' : '') + fmt(x[1], 2) : fmt(x[1], 2);
    });
    if (r.actual_kw != null) {
      el('line', { x1: xv(r.actual_kw), x2: xv(r.actual_kw), y1: 4, y2: 372, stroke: css('--pred'), 'stroke-dasharray': '5 4' }, svg);
      const tt = el('text', { x: Math.min(xv(r.actual_kw) + 4, W - 70), y: 10, 'font-size': 11, fill: css('--pred'), 'font-weight': 600 }, svg); tt.textContent = '실측 ' + fmt(r.actual_kw, 2);
    }
  }
})();
