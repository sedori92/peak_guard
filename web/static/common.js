// [6] 화면 공통 — 세 화면이 함께 쓰는 도구 (Front-end 담당)
//   - 왼쪽 위 피크가드 로고: 누르면 밝은 화면 ↔ 어두운 화면 (브라우저에 기억)
//   - $, el(그림 요소 만들기), fmt(숫자 자리수), css(색 이름 → 실제 색), api(서버 조회)
//   - 시연용 HTML 파일(서버 없이 여는 파일)에서는 window.PEAKGUARD_STATIC 에 미리 넣어 둔 값을 씀
window.PG = window.PG || {};
(() => {
  const PG = window.PG;
  PG.$ = (s) => document.querySelector(s);
  PG.fmt = (v, d = 1) => (v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(d));
  PG.css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  PG.NS = 'http://www.w3.org/2000/svg';
  PG.el = (n, a, p) => { const e = document.createElementNS(PG.NS, n); for (const k in a) e.setAttribute(k, a[k]); if (p) p.appendChild(e); return e; };
  PG.b = (t) => { const e = document.createElement('b'); e.textContent = t; return e; };
  PG.tm = (hm) => { const [h, m] = hm.split(':').map(Number); return h * 60 + m; };
  PG.addMin = (hm, m) => { const t = PG.tm(hm) + m; return String(Math.floor(t / 60) % 24).padStart(2, '0') + ':' + String(t % 60).padStart(2, '0'); };
  PG.STATIC = window.PEAKGUARD_STATIC || null;           // 시연용 파일이면 미리 넣은 값

  // 서버 조회 (시연용 파일이면 미리 넣은 값에서 찾음)
  PG.api = async (path, opt) => {
    if (PG.STATIC) {
      if (path in PG.STATIC.api) { const v = PG.STATIC.api[path]; if (v && v.__error) throw new Error(v.__error); return v; }
      const m = path.match(/^api\/replay\?date=([\d-]+)&model=(.+)$/);     // 재생 자료: 날마다 실측 + 모델별 예측을 합침
      const day = m && PG.STATIC.replay[m[1]], mo = day && day.models[decodeURIComponent(m[2])];
      if (mo) {
        const at = Object.fromEntries(mo.t.map((t, k) => [t, k]));
        const frames = day.frames.filter((f) => f.t in at).map((f) => ({ ...f, pred: mo.pred[at[f.t]], proom: mo.proom[at[f.t]] }));
        return { ...day, ...mo, frames, models: undefined };
      }
      throw new Error('시연용 파일에는 이 자료가 없습니다 (서버 실행 시 가능)');
    }
    const r = await fetch(path, opt);
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || ('조회 실패 (' + r.status + ')')); }
    return r.json();
  };
  PG.postJSON = (path, body, method = 'POST') => PG.api(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

  // 그래프 눈금
  PG.niceStep = (top) => (top > 20 ? 5 : top > 8 ? 2 : top > 4 ? 1 : top > 1.5 ? 0.5 : top > 0.6 ? 0.2 : 0.1);
  PG.axisY = (svg, y, top, L, R, W2, step, d) => {
    for (let v = 0; v <= top + 1e-9; v += step) {
      PG.el('line', { x1: L, x2: W2 - R, y1: y(v), y2: y(v), stroke: PG.css('--grid') }, svg);
      const t = PG.el('text', { x: L - 6, y: y(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: PG.css('--muted') }, svg); t.textContent = PG.fmt(v, d);
    }
  };
  PG.fitWidth = (svg, h) => { const w = Math.max(600, Math.round(svg.clientWidth || 900)); svg.setAttribute('viewBox', `0 0 ${w} ${h}`); return w; };

  // 로고 = 밝은/어두운 화면 전환
  const KEY = 'peakguard-theme';
  PG.applyTheme = (t) => { document.documentElement.dataset.theme = t; PG.$('#theme').setAttribute('aria-pressed', t === 'light' ? 'true' : 'false'); };
  try { PG.applyTheme(localStorage.getItem(KEY) || 'dark'); } catch (e) { PG.applyTheme('dark'); }
  PG.$('#theme').addEventListener('click', () => {
    const t = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    PG.applyTheme(t);
    try { localStorage.setItem(KEY, t); } catch (e) { /* 저장 불가 환경 */ }
    (PG.onTheme || []).forEach((f) => f());           // 각 화면 다시 그리기
  });
  PG.onTheme = [];

  // 작은 창 (안내문구·연락처 관리)
  PG.dialog = (title, bodyNodes, buttons) => {
    const dlg = PG.$('#dlg'), body = PG.$('#dlgbody'), btns = PG.$('#dlgbtns');
    body.replaceChildren(Object.assign(document.createElement('h3'), { textContent: title }), ...bodyNodes);
    btns.replaceChildren(...buttons.map(([label, fn, cls]) => {
      const bt = document.createElement('button'); bt.type = 'button'; bt.textContent = label; if (cls) bt.className = cls;
      bt.onclick = async () => { const keep = fn ? await fn() : false; if (!keep) dlg.close(); };
      return bt;
    }));
    dlg.showModal();
  };
})();
