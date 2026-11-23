// [6] 세 번째 화면 '모델성능 및 오경보율 비교' (Front-end 담당)
//   위 구역  : 채널별 예측 후 합산   (채널 61개를 각각 예측해 더한 건물 합계)
//   아래 구역: 채널별 합산 후 예측   (96개 채널을 먼저 더한 건물 합계를 바로 예측)
//   각 구역  : ① 예측 오차 표 (전일 동일시각 · LightGBM · XGBoost · Random Forest · Bi-LSTM)
//              ② 피크경보 오분류표 (모델 고르기 → 맞힘·놓침·헛경보·정상) + 재현율 비교표 (+ '경보 없음' 비교용 줄)
//   모두 시험기간(학습에 쓰지 않은 기간) 실측과 비교한 결과. 서버 자료: /api/performance
(() => {
  const PG = window.PG, $ = PG.$, fmt = PG.fmt;
  const P3 = PG.S3 = { loaded: false, sel: {} };
  const pct = (v, d = 1) => (v == null ? '—' : fmt(v * 100, d) + '%');
  const mk = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
  const row = (tb, cells, th, cls) => { const tr = mk('tr', cls); cells.forEach((v) => tr.append(mk(th ? 'th' : 'td', null, v))); tb.append(tr); return tr; };

  P3.show = async () => {
    if (P3.loaded) return; P3.loaded = true;
    try { P3.D = await PG.api('api/performance'); } catch (e) { $('#perfnote').textContent = e.message; return; }
    const D = P3.D, sp = D.splits || {};
    $('#perfnote').textContent = `시험기간 ${D.test[0]} ~ ${D.test[1]} (학습·검증에 쓰지 않은 기간)의 실측과 비교. ` +
      (sp.train ? `학습 ${sp.train[0].slice(0, 10)}~${sp.train[1].slice(0, 10)} · 검증 ${sp.val[0].slice(0, 10)}~${sp.val[1].slice(0, 10)} · ` : '') +
      `모든 모델은 같은 발행 시각 ${D.sections[0].rows[0].peak.n.toLocaleString()}개에서 비교(공통 표본). 피크 위험 = 다음 1시간 4칸 중 하나라도 그날 피크 기준 이상.`;
    const box = $('#perfsections'); box.replaceChildren();
    D.sections.forEach((s) => box.append(section(s)));
  };

  function section(s) {
    const card = mk('div', 'card perfsec');
    const h = mk('h2', null, s.title);
    h.append(mk('span', 'sub', s.key === 'sum' ? '채널 61개를 각각 예측 → 같은 시각끼리 더한 건물 합계' : '96개 채널을 먼저 더한 건물 합계를 바로 예측 (기업 권장 방식)'));
    card.append(h);
    // ① 예측 오차 표
    const best = s.rows.find((r) => r.id === s.best_mae), bestR = s.rows.find((r) => r.id === s.best_recall);
    const t1 = mk('table', 'perf');
    row(t1, ['모델', 'MAE (kW)', 'RMSE', 'MAPE', '편향', '15분', '30분', '45분', '60분', '검증 MAE', '개선율'], true);
    s.rows.forEach((r) => {
      const tr = row(t1, [r.name, fmt(r.mae, 3), fmt(r.rmse, 3), fmt(r.mape, 1) + '%', (r.bias > 0 ? '+' : '') + fmt(r.bias, 3),
        ...r.by_h.map((v) => fmt(v, 3)), r.val_mae != null ? fmt(r.val_mae, 3) : '—', r.gain == null ? '기준' : fmt(r.gain, 1) + '%']);
      if (r.id === s.best_mae) tr.classList.add('best');
    });
    const w1 = mk('div', 'tscroll'); w1.append(t1);
    const n1 = mk('p', 'note', `MAE = 평균 절대 오차(작을수록 정확). 편향 + = 실측보다 높게 예측. 15~60분 = 몇 분 뒤를 예측했는지별 MAE. 개선율 = 전일 동일시각 대비 MAE 감소. ` +
      `가장 정확: ${best ? best.name : "—"} (주황 굵은 줄).`);
    card.append(mk('h3', null, '① 예측 오차 (건물 합계 kW)'), w1, n1);

    // ② 피크경보 오분류표 + 재현율 비교
    const grid = mk('div', 'grid2 cmgrid');
    const left = mk('div'), right = mk('div');
    const ctl = mk('div', 'chctl'); const lab = mk('label', null, '모델 '); const sel = mk('select');
    s.rows.forEach((r) => sel.add(new Option(r.name, r.id, false, r.id === s.best_recall)));
    lab.append(sel); ctl.append(lab);
    const cm = mk('div', 'cm');
    left.append(mk('h3', null, '② 피크경보 오분류표'), ctl, cm);
    const draw = () => {
      const r = s.rows.find((x) => x.id === sel.value), p = r.peak; cm.replaceChildren();
      const cell = (cls, big, small) => { const d = mk('div', 'c ' + cls); d.append(mk('b', null, big), mk('span', null, small)); return d; };
      const hd = (t) => mk('div', 'h', t);
      cm.append(hd(''), hd('예측: 위험'), hd('예측: 정상'),
        hd('실제: 위험'), cell('good', p.tp, '맞힘'), cell('bad', p.fn, '놓침'),
        hd('실제: 정상'), cell('warn', p.fp, '헛경보'), cell('good', p.tn, '정상'));
      const sm = mk('div', 'cmsum');
      sm.append('재현율 ', PG.b(pct(p.recall, 0)), ` (실제 위험 ${p.tp + p.fn}건 중 ${p.tp}건 맞힘) · 정밀도 `, PG.b(pct(p.precision, 0)), ' · 오분류율 ', PG.b(pct(p.miscls)));
      cm.append(sm);
      t2.querySelectorAll('tr[data-id]').forEach((tr) => tr.classList.toggle('sel', tr.dataset.id === r.id));
    };
    sel.onchange = draw;
    const t2 = mk('table', 'perf click');
    row(t2, ['모델', '맞힘', '놓침', '헛경보', '재현율', '정밀도', '오분류율'], true);
    s.rows.forEach((r) => {
      const p = r.peak;
      const tr = row(t2, [r.name + (r.id === s.best_recall ? ' ★ 놓침 가장 적음' : ''), p.tp, p.fn, p.fp, pct(p.recall, 0), pct(p.precision, 0), pct(p.miscls)]);
      tr.dataset.id = r.id; if (r.id === s.best_recall) tr.classList.add('best');
      tr.onclick = () => { sel.value = r.id; draw(); };
    });
    const na = s.no_alarm;
    row(t2, [na.name, na.tp, na.fn, na.fp, pct(na.recall, 0), '—', pct(na.miscls)], false, 'muted');
    const w2 = mk('div', 'tscroll'); w2.append(t2);
    const n2 = mk('p', 'note', `실제 피크 위험 ${s.actual_risk}건. '경보 없음'은 한 번도 경보를 내지 않아도 오분류율이 ${pct(na.miscls)}로 낮게 나온다는 것을 보여 주는 비교용 줄입니다 — ` +
      `위험이 드물어서 오분류율만 보면 판단을 그르칩니다. 피크를 놓치지 않는 것이 목적이므로 재현율(맞힘 ÷ 실제 위험)을 먼저 보고, 헛경보(정밀도)는 그다음에 봅니다. ` +
      (bestR ? `이 방식에서 재현율이 가장 높은 모델: ${bestR.name} (${bestR.peak.tp}건 맞힘 · ${bestR.peak.fn}건 놓침 · 헛경보 ${bestR.peak.fp}건).` : ''));
    right.append(mk('h3', null, '모델별 재현율·오분류율 비교'), w2, n2);
    grid.append(left, right); card.append(grid);
    draw();
    return card;
  }
})();
