// [6] 화면 전환과 시작 (Front-end 담당)
//   맨 위 탭 3개: '예측 및 경보' / '피크이력 및 영향도 분석' / '모델성능 및 오경보율 비교'
//   주소 끝(#predict, #history, #perf)으로 바로 열 수 있음. 재생 기준일·모델은 ?date=&model= 로 유지
(() => {
  const PG = window.PG, $ = PG.$;
  const SCREENS = ['predict', 'history', 'perf'];
  function show(name) {
    SCREENS.forEach((n) => { $('#s-' + n).hidden = n !== name; });
    document.querySelectorAll('nav.tabs a').forEach((a) => a.setAttribute('aria-current', a.dataset.s === name ? 'page' : 'false'));
    $('.replay').classList.toggle('dim', name !== 'predict');
    if (name === 'history') PG.S2.show();
    if (name === 'perf') PG.S3.show();
    PG.S1.render();
  }
  window.addEventListener('hashchange', () => { const n = location.hash.slice(1); show(SCREENS.includes(n) ? n : 'predict'); });
  (async () => {
    try {
      await PG.S1.init();
      await PG.AL.init();
      PG.S2.init();
    } catch (e) {
      document.querySelector('.wrap').prepend(Object.assign(document.createElement('div'), { className: 'livebar', textContent: '화면 자료를 불러오지 못했습니다: ' + e.message }));
      return;
    }
    const start = location.hash.slice(1);
    show(SCREENS.includes(start) ? start : 'predict');
    PG.S1.start();
    // 시연·점검용 (브라우저 콘솔): peakguard.jump('14:00'), peakguard.speed(4)
    window.peakguard = { jump: PG.S1.jump, speed: (s) => { PG.S1.speed = s; PG.S1.start(); }, show };
  })();
})();
