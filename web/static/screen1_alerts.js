// [6] 첫 번째 화면 '예측 및 경보' — 오른쪽 '경보발령 선택' 상자 (Front-end 담당)
//   목록 순서 : 맨 위 '공용부분'(관리자의 최후 수단) → 그 아래 호실 34개 (피크 예측 가능성 높은 순)
//   처음엔 Top 5 호실에 체크, 관리자가 직접 바꾼 체크는 15분마다 다시 골라도 그대로 둠
//   [더보기]     : 34개 호실 전부 펼치기
//   [안내문구]   : 기본 문구 확인·수정, 고른 호실마다 다른 문구 쓰기 (작은 창)
//   [연락처 관리]: 입주자 이메일·전화번호 추가·수정·삭제 (서버의 연락처 장부, GitHub에 올리지 않음)
//   [경보 발령]  : 서버의 '경보 발령 기록 장부'에 남기고, 메일 서버가 설정되어 있으면 메일 발송
//   서버 자료: /api/alerts/template, /api/contacts, /api/alerts
(() => {
  const PG = window.PG, $ = PG.$, fmt = PG.fmt, S1 = PG.S1;
  const A = PG.AL = { touched: {}, checked: {}, expanded: false, message: '', perRoom: {}, contacts: [] };
  const SHOW = 8;

  A.init = async () => {
    try { A.message = (await PG.api('api/alerts/template')).message; } catch (e) { A.message = '앞으로 한 시간 안에 전력 피크기준을 넘을 것으로 예상됩니다. 불필요한 전열기구 나 전등 등 스위치를 차단하여 주시기 바랍니다'; }
    await A.loadContacts();
    if (PG.STATIC) ['#alcontact', '#alsend'].forEach((s) => { $(s).disabled = true; $(s).title = '시연용 파일에서는 사용할 수 없습니다 (서버 실행 시 가능)'; });
  };
  A.loadContacts = async () => { try { A.contacts = await PG.api('api/contacts'); } catch (e) { A.contacts = []; } };
  const nContacts = (c) => A.contacts.filter((x) => x.channel === c).length;

  // ---------- 목록 그리기 (15분마다 호출) ----------
  let lastKey = '';
  function draw(f, s) {
    const rows = S1.possibility(f, s).sort((a, b) => (b.pct ?? -1) - (a.pct ?? -1));
    const top5 = new Set(S1.topRooms(f).map((r) => r[0]));
    rows.forEach((r) => { if (!A.touched[r.c]) A.checked[r.c] = top5.has(r.c); });
    if (!A.touched.COMMON) A.checked.COMMON = false;
    const key = f.t + (S1.live ? 'L' : '') + A.expanded + JSON.stringify(A.checked) + A.contacts.length;
    if (key === lastKey) return; lastKey = key;
    const box = $('#alist'); box.replaceChildren();
    const item = (id, name, sub, warn) => {
      const lab = document.createElement('label'); lab.className = 'aitem' + (warn ? ' over' : '') + (id === 'COMMON' ? ' common' : '');
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = !!A.checked[id];
      cb.onchange = () => { A.touched[id] = true; A.checked[id] = cb.checked; lastKey = ''; updCount(); };
      const nm = document.createElement('span'); nm.textContent = name;
      const sm = document.createElement('small'); sm.textContent = sub;
      lab.append(cb, nm, sm); box.append(lab);
    };
    item('COMMON', '공용부분 (최후 수단)', `${fmt(f.common, 2)} kW · 관리자 직접 조치`, false);
    rows.slice(0, A.expanded ? rows.length : SHOW).forEach((r) => {
      const nc = nContacts(r.c);
      item(r.c, S1.roomName(r.c), `${fmt(r.p, 2)} kW · ${r.pct == null ? '기준 없음' : fmt(r.pct, 0) + '%'} · 연락처 ${nc ? nc + '명' : '없음'}`, r.pct >= 100);
    });
    $('#almore').textContent = A.expanded ? '접기' : `더보기 (${rows.length - SHOW}개 호실 더)`;
    $('#alsub').textContent = `${f.t} 발행 · 예상 피크 ${s.at}`;
    updCount();
  }
  const chosen = () => Object.keys(A.checked).filter((k) => A.checked[k] && k !== 'COMMON');
  function updCount() { $('#alsend').textContent = `경보 발령 (${chosen().length}개 호실${A.checked.COMMON ? ' + 공용' : ''})`; }
  (PG.onFrame = PG.onFrame || []).push(draw);
  PG.resetAlerts = () => { lastKey = ''; };
  $('#almore').onclick = () => { A.expanded = !A.expanded; lastKey = ''; S1.render(); };

  // ---------- [안내문구] 작은 창 ----------
  $('#almsg').onclick = () => {
    const base = document.createElement('textarea'); base.rows = 3; base.value = A.message;
    const lb = document.createElement('p'); lb.className = 'note'; lb.textContent = '기본 안내문구 (모든 호실에 보냄)';
    const nodes = [lb, base];
    const per = {};
    if (chosen().length) {
      const p2 = document.createElement('p'); p2.className = 'note'; p2.textContent = '호실마다 다르게 보내려면 아래에 적으세요 (비우면 기본 문구).'; nodes.push(p2);
      chosen().forEach((c) => {
        const row = document.createElement('label'); row.className = 'dlgrow';
        const nm = document.createElement('span'); nm.textContent = S1.roomName(c);
        const ta = document.createElement('input'); ta.type = 'text'; ta.value = A.perRoom[c] || ''; ta.placeholder = '기본 문구 사용';
        per[c] = ta; row.append(nm, ta); nodes.push(row);
      });
    }
    PG.dialog('안내문구', nodes, [['저장', () => {
      A.message = base.value.trim() || A.message;
      Object.entries(per).forEach(([c, ta]) => { if (ta.value.trim()) A.perRoom[c] = ta.value.trim(); else delete A.perRoom[c]; });
    }, 'primary'], ['취소']]);
  };

  // ---------- [연락처 관리] 작은 창 ----------
  $('#alcontact').onclick = async () => {
    await A.loadContacts();
    const wrap = document.createElement('div');
    const table = document.createElement('table'); table.className = 'perf';
    const head = document.createElement('tr'); ['호실', '이름', '이메일', '전화번호', ''].forEach((t) => { const th = document.createElement('th'); th.textContent = t; head.append(th); });
    table.append(head);
    const input = (v, ph, type = 'text') => { const i = document.createElement('input'); i.type = type; i.value = v || ''; i.placeholder = ph; return i; };
    const roomSel = (v) => { const s = document.createElement('select'); S1.hos.forEach((c) => s.add(new Option(S1.roomName(c), c, false, c === v))); return s; };
    const err = document.createElement('p'); err.className = 'note err';
    const addRow = (c) => {
      const tr = document.createElement('tr');
      const room = roomSel(c ? c.channel : S1.hos[0]), nm = input(c?.name, '이름'), em = input(c?.email, 'name@example.com', 'email'), ph = input(c?.phone, '010-0000-0000', 'tel');
      const save = document.createElement('button'); save.type = 'button'; save.textContent = c ? '수정' : '추가';
      const del = document.createElement('button'); del.type = 'button'; del.textContent = '삭제'; del.hidden = !c;
      save.onclick = async () => {
        err.textContent = '';
        try {
          const body = { channel_id: room.value, name: nm.value, email: em.value || null, phone: ph.value || null };
          if (c) await PG.postJSON('api/contacts/' + c.contact_id, body, 'PUT'); else await PG.postJSON('api/contacts', body);
          await A.loadContacts(); lastKey = ''; $('#alcontact').click();
        } catch (e) { err.textContent = e.message; }
      };
      del.onclick = async () => { await PG.api('api/contacts/' + c.contact_id, { method: 'DELETE' }); await A.loadContacts(); lastKey = ''; $('#alcontact').click(); };
      const tdb = document.createElement('td'); tdb.append(save, del);
      [room, nm, em, ph].forEach((x) => { const td = document.createElement('td'); td.append(x); tr.append(td); });
      tr.append(tdb); table.append(tr);
    };
    A.contacts.forEach(addRow); addRow(null);
    const note = document.createElement('p'); note.className = 'note';
    note.textContent = '연락처는 이 컴퓨터의 피크가드 DB(peakguard.db) 안 연락처 장부에만 저장되고 GitHub에는 올라가지 않습니다.';
    wrap.append(table, err, note);
    PG.dialog(`연락처 관리 (${A.contacts.length}명)`, [wrap], [['닫기']]);
  };

  // ---------- [경보 발령] ----------
  $('#alsend').onclick = async () => {
    const ch = chosen(), f = S1.frame(), s = S1.state(f);
    if (!ch.length && !A.checked.COMMON) { $('#alresult').textContent = '발령할 호실을 하나 이상 고르세요.'; return; }
    const names = ch.map(S1.roomName).join(', ');
    const p = document.createElement('p'); p.textContent = `${names || '(호실 없음)'}${A.checked.COMMON ? ' + 공용부분' : ''} 에 경보를 발령합니다.`;
    const m = document.createElement('p'); m.className = 'note'; m.textContent = '안내문구: ' + A.message + (Object.keys(A.perRoom).length ? ` (호실별 문구 ${Object.keys(A.perRoom).length}건)` : '');
    PG.dialog('경보 발령 확인', [p, m], [['발령', async () => {
      try {
        const r = await PG.postJSON('api/alerts', {
          channels: ch, include_common: !!A.checked.COMMON, message: A.message, messages: A.perRoom,
          ref_time: S1.live ? S1.live.frame.time : `${S1.DATA.date} ${f.t}`, mode: S1.live ? '실시간' : '시연',
          model_name: S1.modelId(), predicted_max_kw: s.mx, threshold_kw: f.thr,
        });
        $('#alresult').textContent = `#${r.alert_id} 발령 기록됨 · 연락처 ${r.recipients}명 · ${r.status}` + (r.no_contact.length ? ` · 연락처 없는 호실: ${r.no_contact.join(', ')}` : '');
      } catch (e) { $('#alresult').textContent = '발령 실패: ' + e.message; }
    }, 'btn-alert'], ['취소']]);
  };
})();
