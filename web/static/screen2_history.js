// [6] 두 번째 화면 '피크이력 및 영향도 분석' (Front-end 담당) — 실측으로 돌아보는 사후 보고 화면
//   상단 '최고관리자 보고서' : 일간·주간·월간·연간 + 기준일 → 피크 변동기록 | 영향도 Top 10 | 평소 대비 증가 Top 10
//                              [스프레드시트 보기·출력] (기준일은 처음엔 첫 화면 '재생 기준일'과 같게 맞춤)
//   하단 '층별 호실 배치도'   : 위 표에서 고른 피크 시각의 호실별 실측 (진할수록 많이 씀, 빨간 테두리 = Top 5)
//   하단 '전체 및 채널별 영향도' : 콤보버튼 [전체 / 호실 채널] → 그 시각 실측이 평소보다 높아진 몫 (폭포 그래프, 실측만)
//   서버 자료: /api/report/peaks, /api/report/preview·xlsx, /api/snapshot, /api/rise, /api/influence
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
    fs.onchange = loadInfluence;
    PG.onTheme.push(() => { if (H2.snap) drawPlan(H2.snap); if (H2.inf) drawInfluence(H2.inf); });
  };
  H2.show = () => { if (!H2.loaded) { H2.loaded = true; loadReport(); PG.PAT && PG.PAT.load(); } };

  // ---------- 상단: 최고관리자 보고서 ----------
  // [스프레드시트 보기·출력] 버튼 활성/비활성: 보고서 자료가 있을 때만 누를 수 있음
  const setSheetBtn = (on, why) => { const b = $('#repdl'); b.disabled = !on; b.title = on ? '이 기간 보고서를 시트별로 보고, 내려받거나 출력' : why; };
  async function loadReport() {
    const d = $('#repdate').value;
    setSheetBtn(false, '보고서를 불러오는 중입니다');
    let r = null;
    try { r = await PG.api(key(H2.period, d)); } catch (e) { r = null; $('#repinfo').textContent = e.message; }
    const rec = $('#reprec'), tp = $('#reptop'); rec.replaceChildren(); tp.replaceChildren();
    H2.rep = r;
    if (!r || !r.records.length) { if (r) $('#repinfo').textContent = `${r.start} ~ ${r.end}: 96개 채널이 모두 수신된 데이터가 없습니다.`; setSheetBtn(false, '이 기간에는 보고서로 만들 실측이 없습니다'); return; }
    setSheetBtn(true);
    const sm = r.summary;
    $('#repinfo').textContent = `${r.title} ${r.start} ~ ${r.end} · 최대 ${fmt(sm.peak_kw, 2)} kW (${sm.peak_time.slice(5)})` +
      (sm.prev_change_pct != null ? ` · 직전 대비 ${sm.prev_change_pct > 0 ? '+' : ''}${fmt(sm.prev_change_pct, 1)}%` : '') + ` · 수신률 ${fmt(sm.coverage_pct, 0)}%`;
    $('#repunit').textContent = `${r.unit} 단위 최대 · 빨강 = 피크 기준 초과`;
    $('#reppk').textContent = `${sm.peak_time} 피크 ${fmt(sm.peak_kw, 2)} kW 시각`;
    const row = (tb, cells, th) => { const tr = document.createElement('tr'); cells.forEach((v) => { const c = document.createElement(th ? 'th' : 'td'); c.textContent = v; tr.append(c); }); tb.append(tr); return tr; };
    row(rec, ['구간', '최대 kW', '시각', '기준 kW', '최대 영향'], true);
    r.records.forEach((x) => {
      const tr = row(rec, [x.label, fmt(x.peak_kw, 2), x.peak_time.slice(5), x.threshold_kw != null ? fmt(x.threshold_kw, 2) : '—', `${x.top_name} ${fmt(x.top_share, 0)}%`]);
      if (x.over) tr.classList.add('over');
      tr.dataset.t = x.peak_time; tr.tabIndex = 0; tr.title = '눌러서 오른쪽 표와 아래 배치도·영향도를 이 피크 시각으로';
      tr.onclick = () => pick(x.peak_time); tr.onkeydown = (e) => { if (e.key === 'Enter') pick(x.peak_time); };
    });
    H2.row = row;
    pick(sm.peak_time);
  }

  // ---------- [스프레드시트 보기·출력]: 내려받을 .xlsx 를 화면에 시트별로 보여 주고 내려받기·출력 ----------
  //   출력: 서버 컴퓨터에 연결된 프린터를 확인 → 없으면 '프린터 연결 안 됨', 있으면 인쇄 창 열기
  const mk = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
  async function showSheet() {
    const d = $('#repdate').value, q = `period=${H2.period}&date=${d}`, btn = $('#repdl'), label = btn.textContent;
    let pv;
    btn.disabled = true; btn.textContent = '불러오는 중…';
    try { pv = await PG.api(`api/report/preview?${q}`); } catch (e) { $('#repinfo').textContent = e.message; return; }
    finally { btn.textContent = label; btn.disabled = !(H2.rep && H2.rep.records.length); }
    const tabs = mk('div', 'seg sheettabs'), body = mk('div', 'sheetbody'), msg = mk('p', 'note');
    const show = (k) => {
      tabs.querySelectorAll('button').forEach((b, j) => b.setAttribute('aria-pressed', j === k ? 'true' : 'false'));
      const sh = pv.sheets[k], t = mk('table', 'perf sheet');
      sh.rows.forEach((row, i) => { const tr = mk('tr'); row.forEach((v) => tr.append(mk(i === 0 && k > 0 ? 'th' : 'td', null, v))); t.append(tr); });
      body.replaceChildren(t);
      if (sh.total_rows > sh.rows.length) body.append(mk('p', 'note', `화면에는 앞 ${sh.rows.length}줄만 보입니다 (전체 ${sh.total_rows.toLocaleString()}줄은 내려받은 파일에 있음).`));
    };
    pv.sheets.forEach((sh, k) => { const b = mk('button', null, sh.name); b.type = 'button'; b.onclick = () => show(k); tabs.append(b); });
    show(0);
    const head = mk('p', 'note', `${pv.period} · 파일 이름 ${pv.filename}`);
    PG.dialog(pv.title, [head, tabs, body, msg], [
      ['내려받기 (.xlsx)', () => {
        if (PG.STATIC) { msg.textContent = '시연용 파일에서는 내려받기를 할 수 없습니다 (서버 실행 시 가능).'; return true; }
        const a = mk('a'); a.href = `api/report/xlsx?${q}`; a.download = pv.filename; document.body.append(a); a.click(); a.remove(); return true;
      }],
      ['출력', async () => {
        if (!PG.STATIC) {
          let pr = { available: false };
          try { pr = await PG.api('api/printer'); } catch (e) { /* 확인 실패 = 연결 안 됨 */ }
          if (!pr.available) { msg.className = 'note err'; msg.textContent = '프린터 연결 안 됨 — 프린터를 켜고 연결한 뒤 다시 누르세요. (내려받은 파일을 다른 컴퓨터에서 인쇄해도 됩니다)'; return true; }
          msg.className = 'note'; msg.textContent = `프린터: ${pr.default || pr.printers[0]} — 인쇄 창을 엽니다.`;
        }
        document.body.classList.add('printing-sheet'); window.print(); document.body.classList.remove('printing-sheet');
        return true;
      }, 'primary'],
      ['닫기'],
    ]);
    $('#dlg').classList.add('wide');
    $('#dlg').addEventListener('close', () => $('#dlg').classList.remove('wide'), { once: true });
  }
  $('#repdl').addEventListener('click', showSheet);

  // 피크 시각 고르기 → 배치도·영향요인 갱신
  async function pick(t) {
    H2.time = t; $('#causet').textContent = t;
    document.querySelectorAll('#reprec tr').forEach((tr) => tr.classList.toggle('sel', tr.dataset.t === t));
    try { H2.snap = await PG.api('api/snapshot?time=' + encodeURIComponent(t)); drawPlan(H2.snap); drawTop(H2.snap); } catch (e) { $('#snapt').textContent = e.message; $('#plan').replaceChildren(); }
    loadRise(t);
    loadInfluence();
  }

  // ---------- 상단 가운데: 영향도 Top 10 (고른 피크 시각의 채널별 실측 kW · 건물 합계 대비) ----------
  // 호실 줄을 누르면 '2. 피크 원인(영향도)' 하위 메뉴로 넘어가 그 호실 영향도를 보여 줌
  const toRoom = (c) => { $('#fsel').value = c; loadInfluence(); if (PG.sub.history !== 'cause') location.hash = '#history/cause'; };
  // 하위 메뉴를 바꿔 숨어 있던 그래프가 보일 때 화면 폭에 맞춰 다시 그림 (app.js PG.applySub)
  H2.redraw = () => { if (H2.inf) drawInfluence(H2.inf); };
  function drawTop(d) {
    const tp = $('#reptop'); tp.replaceChildren();
    $('#reppk').textContent = `${d.time} · 건물 ${fmt(d.total_kw, 2)} kW`;
    // 건물 대비 = 96채널 합계 대비, 호실 대비 = 호실 34개 합계 대비 (기업 3차 답변 ④: 두 비중을 구분해 표시)
    H2.row(tp, ['순위', '채널', '구분', 'kW', '건물 대비', '호실 대비'], true);
    d.channels.slice(0, 10).forEach((x, k) => {
      const tr = H2.row(tp, [k + 1, x.label, x.kind, fmt(x.kw, 2), fmt(x.share_building, 1) + '%', x.share_rooms != null ? fmt(x.share_rooms, 1) + '%' : '—']);
      if (x.kind === '호실') { tr.classList.add('link'); tr.onclick = () => toRoom(x.channel); }
    });
  }
  // ---------- 상단 오른쪽: 평소 대비 증가 Top 10 (그 시각 실측 − 같은 요일·시각 직전 4주 평균) ----------
  async function loadRise(t) {
    const tb = $('#reprise'); tb.replaceChildren();
    let r;
    try { r = await PG.api('api/rise?time=' + encodeURIComponent(t)); } catch (e) { $('#risesub').textContent = e.message; return; }
    $('#risesub').textContent = `평소 ${fmt(r.usual_total_kw, 2)} → ${fmt(r.total_kw, 2)} kW (+${fmt(r.rise_total_kw, 2)})`;
    H2.row(tb, ['순위', '채널', 'kW', '평소', '증가'], true);
    r.rows.forEach((x) => {
      const tr = H2.row(tb, [x.rank, x.name + (x.kind === '공용' ? ' (공용)' : ''), fmt(x.kw, 2), fmt(x.usual_kw, 2), (x.rise_kw >= 0 ? '+' : '') + fmt(x.rise_kw, 2)]);
      if (x.kind === '호실') { tr.classList.add('link'); tr.onclick = () => toRoom(x.channel); }
    });
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
        const v = by[c] ? by[c].kw : 0, t = document.createElement('button'); t.type = 'button'; t.className = 'tile'; t.dataset.c = c;
        const a = Math.min(1, Math.sqrt(v / mx));
        t.style.background = `color-mix(in srgb, ${css('--amber')} ${Math.round(a * 85)}%, ${css('--panel')})`;
        if (v < 0.02) t.classList.add('off');
        const r = top.indexOf(c); if (r >= 0) { t.classList.add('top'); t.dataset.rank = r + 1; }
        if ($('#fsel').value === c) t.classList.add('sel');
        const cc = document.createElement('span'); cc.className = 'c'; cc.textContent = c.slice(5) + '번';
        const kk = document.createElement('span'); kk.className = 'k'; kk.textContent = fmt(v, 2);
        if (a > 0.6) { kk.style.color = '#fff'; cc.style.color = '#fff'; }
        t.append(cc, kk); t.title = `${S1.roomName(c)} ${fmt(v, 3)} kW · 건물 대비 ${fmt(by[c]?.share_building, 1)}%`;
        t.onclick = () => { $('#fsel').value = c; loadInfluence(); drawPlan(d); };
        tiles.append(t);
      });
      row.append(lab, tiles); plan.append(row);
    }
    const sh = $('#snapshare'); sh.replaceChildren();
    [[d.rooms_kw, '호실 34개 ' + fmt(d.rooms_kw / d.total_kw * 100, 0) + '%', css('--amber'), '#fff'], [d.common_kw, '공용 62개 ' + fmt(d.common_kw / d.total_kw * 100, 0) + '%', css('--grid'), css('--ink')]]
      .forEach(([v, t, bg, c]) => { const x = document.createElement('div'); x.style.width = (v / d.total_kw * 100) + '%'; x.style.background = bg; x.style.color = c; x.textContent = t; sh.append(x); });
  }

  // ---------- 하단 오른쪽: 전체 및 채널별 영향도 (실측만) ----------
  //   전체 : 평소 같은 시각 건물 합계 → + 채널별 (실측 − 평소) → = 그 시각 건물 합계 실측
  //   채널 : 최근 4주 같은 시각 평균 → + 요일 영향 → + 최근 1주 변화 → + 당일 추가 사용 → = 그 시각 실측
  async function loadInfluence() {
    if (!H2.time) return;
    const s = $('#fsel').value;
    try { H2.inf = await PG.api(`api/influence?time=${encodeURIComponent(H2.time)}&series=${encodeURIComponent(s)}`); drawInfluence(H2.inf); }
    catch (e) { $('#fct').textContent = e.message; $('#fchart').replaceChildren(); $('#fnote').textContent = ''; }
    if (H2.snap) document.querySelectorAll('#plan .tile').forEach((t) => t.classList.toggle('sel', t.dataset.c === s));
  }
  function drawInfluence(r) {
    $('#fct').textContent = `${r.label} · ${r.time} 실측 ${fmt(r.actual_kw, 2)} kW`;
    $('#fnote').textContent = r.note + ' 주황 = 실측을 올린 몫, 회색 = 내린 몫 (kW).';
    const svg = $('#fchart'); svg.replaceChildren();
    const rows = [[r.base_name, r.base_kw, 'base'], ...r.items.map((x) => [x.name, x.kw, 'c']), [r.end_name, r.actual_kw, 'end']];
    let cur = 0, lo = 0, hi = 0;
    rows.forEach((x) => { if (x[2] === 'base') cur = x[1]; else if (x[2] === 'c') cur += x[1]; hi = Math.max(hi, cur, x[1]); lo = Math.min(lo, cur); });
    hi *= 1.12;
    const RH = 30, HH = rows.length * RH + 12, W = PG.fitWidth(svg, HH), L = Math.min(220, Math.max(170, W * 0.3)), R = W - 70;   // 화면 폭에 맞춰 글자 크기 그대로
    svg.style.height = HH + 'px';
    const xv = (v) => L + (v - lo) / (hi - lo || 1) * (R - L);
    cur = 0;
    rows.forEach((x, k) => {
      const y = k * RH + RH * 0.18 + 6, hgt = RH * 0.64;
      const t = el('text', { x: L - 10, y: y + hgt / 2 + 5, 'text-anchor': 'end', 'font-size': 13.5, fill: css('--ink'), 'font-weight': x[2] === 'c' ? 400 : 600 }, svg); t.textContent = x[0];
      let x0, x1, col;
      if (x[2] === 'base') { x0 = xv(0); x1 = xv(x[1]); cur = x[1]; col = css('--muted'); }
      else if (x[2] === 'end') { x0 = xv(0); x1 = xv(x[1]); col = css('--ink'); }
      else { x0 = xv(cur); x1 = xv(cur + x[1]); cur += x[1]; col = x[1] >= 0 ? css('--amber') : css('--muted'); }
      el('rect', { x: Math.min(x0, x1), y, width: Math.max(2, Math.abs(x1 - x0)), height: hgt, rx: 4, fill: col, opacity: x[2] === 'c' && x[1] < 0 ? 0.6 : 1 }, svg);
      const v = el('text', { x: Math.max(x0, x1) + 6, y: y + hgt / 2 + 5, 'font-size': 12.5, fill: css('--ink2'), 'font-family': 'IBM Plex Mono, monospace' }, svg);
      v.textContent = x[2] === 'c' ? (x[1] >= 0 ? '+' : '') + fmt(x[1], 2) : fmt(x[1], 2) + ' kW';
    });
  }
})();
