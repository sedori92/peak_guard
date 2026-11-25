// [6] 화면 전환과 시작 (Front-end 담당)
//   맨 위 탭 3개: '예측 및 경보' / '피크이력 및 영향도 분석' / '모델성능 및 오경보율 비교'
//   주소 끝(#predict, #history, #perf)으로 바로 열 수 있음. 재생 기준일·모델은 ?date=&model= 로 유지
//   두 번째·세 번째 화면은 하위 메뉴: #history/report·cause·pattern·weather, #perf/select·method·detail·daily·weather
//     data-sub="이름" 이 붙은 칸만 그 하위 메뉴에서 보임 (공백으로 여러 개 가능). 고른 하위 메뉴는 화면마다 기억
(() => {
  const PG = window.PG, $ = PG.$;
  const SCREENS = ['predict', 'history', 'perf'];
  const SUBS = { history: ['report', 'cause', 'pattern', 'weather'], perf: ['select', 'method', 'detail', 'daily', 'weather'] };
  PG.sub = { history: 'report', perf: 'select' };
  // 하위 메뉴 적용: 그 화면 안 data-sub 칸 보이기·숨기기 + 하위 탭 표시 + 숨어 있던 그래프 다시 그리기
  PG.applySub = (name) => {
    const cur = PG.sub[name], root = $('#s-' + name);
    if (!cur || !root) return;
    root.dataset.cur = cur;                                            // 하위 메뉴별 모양 조정용 (style.css)
    root.querySelectorAll('[data-sub]').forEach((e) => {
      if (e.tagName === 'A') { e.setAttribute('aria-current', e.dataset.sub === cur ? 'page' : 'false'); return; }
      e.hidden = !e.dataset.sub.split(' ').includes(cur);
    });
    if (name === 'history') { if (cur === 'pattern' || cur === 'weather') PG.PAT && PG.PAT.redraw && PG.PAT.redraw(); if (cur === 'cause') PG.S2.redraw && PG.S2.redraw(); }
  };
  const parse = () => { const [n, sub] = location.hash.slice(1).split('/'); return [SCREENS.includes(n) ? n : 'predict', sub]; };
  function show(name, sub) {
    if (sub && SUBS[name] && SUBS[name].includes(sub)) PG.sub[name] = sub;
    SCREENS.forEach((n) => { $('#s-' + n).hidden = n !== name; });
    document.querySelectorAll('nav.tabs a').forEach((a) => a.setAttribute('aria-current', a.dataset.s === name ? 'page' : 'false'));
    $('.replay').classList.toggle('dim', name !== 'predict');
    if (name === 'history') PG.S2.show();
    if (name === 'perf') PG.S3.show();
    PG.applySub(name);
    PG.S1.render();
    PG.refit();
  }
  window.addEventListener('hashchange', () => show(...parse()));

  // ---------- 한 화면 맞춤: 어느 화면·하위 메뉴든 브라우저 창(예: 1440×900) 안에 스크롤 없이 ----------
  //   맨 위 줄(로고·탭·재생)은 그대로 두고, 화면 내용이 많으면 줄이고(최소 0.88배), 적으면 키움(최대 1.3배, 글자도 같이 커짐). 줄인 만큼 가로 폭을 넓혀 다시 배치
  //   화면·하위 메뉴를 바꿀 때, 창 크기를 바꿀 때, 자료를 새로 불러올 때 다시 맞춤 (재생 중 15분마다는 하지 않음)
  const ZMIN = 0.88, ZMAX = 1.3;   // 0.88배 아래로는 글자가 작아 읽기 어려워 줄이지 않음 (그 대신 하위 메뉴로 나눔)
  PG.zoom = 1;
  const redrawActive = () => {
    const [n] = parse();
    if (n === 'predict') PG.S1.render();
    if (n === 'history') { PG.PAT && PG.PAT.redraw && PG.PAT.redraw(); PG.S2.redraw && PG.S2.redraw(); }
  };
  const setZoom = (z) => {                                            // 맨 위 줄은 그대로, 화면 내용(section.scr)과 아래 글만 확대·축소
    PG.zoom = z;
    const inner = $('.wrap').clientWidth;
    document.querySelectorAll('section.scr, .foot').forEach((e) => {
      e.style.zoom = z === 1 ? '' : String(z);
      e.style.width = z === 1 ? '' : `${Math.floor(inner / z)}px`;
    });
  };
  const bottom = () => { const f = $('.foot').getBoundingClientRect(); return f.bottom + window.scrollY; };
  PG.fit = () => {
    if (innerWidth < 900) { setZoom(1); redrawActive(); return; }        // 좁은 화면(휴대폰)은 맞추지 않고 스크롤
    const avail = innerHeight - 10;                                    // 아래 여백
    // 표가 커지면 줄바꿈이 생겨 높이가 고르게 늘지 않으므로, '들어가는 가장 큰 배율'을 반씩 좁혀 찾음 (이분 탐색)
    //   + 표가 가로로 넘쳐 옆으로 밀리지 않아야 '들어감'
    const wideOk = () => [...document.querySelectorAll('section.scr:not([hidden]) .tscroll')].every((e) => !e.offsetParent || e.scrollWidth <= e.clientWidth + 1);
    const fits = (z) => { setZoom(z); redrawActive(); return bottom() <= avail && wideOk(); };
    let lo = ZMIN, hi = ZMAX;
    if (fits(hi)) return;
    if (!fits(lo)) return;                                               // 가장 작게 해도 넘치면 그 배율에서 스크롤
    for (let k = 0; k < 7; k++) { const m = (lo + hi) / 2; if (fits(m)) lo = m; else hi = m; }
    fits(lo);
  };
  let fitT = null;
  PG.refit = () => { clearTimeout(fitT); fitT = setTimeout(PG.fit, 120); };
  window.addEventListener('resize', PG.refit);
  // 맨 위 탭: 그 화면에서 마지막으로 본 하위 메뉴로
  document.querySelectorAll('nav.tabs a').forEach((a) => a.addEventListener('click', (e) => {
    const n = a.dataset.s; if (!SUBS[n]) return;
    e.preventDefault(); location.hash = `#${n}/${PG.sub[n]}`;
  }));
  (async () => {
    try {
      await PG.S1.init();
      await PG.AL.init();
      PG.S2.init();
    } catch (e) {
      document.querySelector('.wrap').prepend(Object.assign(document.createElement('div'), { className: 'livebar', textContent: '화면 자료를 불러오지 못했습니다: ' + e.message }));
      return;
    }
    show(...parse());
    PG.S1.start();
    // 시연·점검용 (브라우저 콘솔): peakguard.jump('14:00'), peakguard.speed(4)
    window.peakguard = { jump: PG.S1.jump, speed: (s) => { PG.S1.speed = s; PG.S1.start(); }, show };
  })();
})();
