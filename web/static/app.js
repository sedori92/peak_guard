// [6] 화면 전환과 시작 (Front-end 담당)
//   맨 위 탭 3개: '예측 및 경보' / '피크이력 및 영향도 분석' / '모델성능 및 오경보율 비교'
//   주소 끝(#predict, #history, #perf)으로 바로 열 수 있음. 재생 기준일·모델은 ?date=&model= 로 유지
//   두 번째·세 번째 화면은 하위 메뉴: #history/report·cause·pattern·weather, #perf/select·method·detail·weather
//     data-sub="이름" 이 붙은 칸만 그 하위 메뉴에서 보임 (공백으로 여러 개 가능). 고른 하위 메뉴는 화면마다 기억
(() => {
  const PG = window.PG, $ = PG.$;
  const SCREENS = ['predict', 'history', 'perf'];
  const SUBS = { history: ['report', 'cause', 'pattern', 'weather'], perf: ['select', 'method', 'detail', 'weather'] };
  PG.sub = { history: 'report', perf: 'select' };
  // 하위 메뉴 적용: 그 화면 안 data-sub 칸 보이기·숨기기 + 하위 탭 표시 + 숨어 있던 그래프 다시 그리기
  PG.applySub = (name) => {
    const cur = PG.sub[name], root = $('#s-' + name);
    if (!cur || !root) return;
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
  }
  window.addEventListener('hashchange', () => show(...parse()));
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
