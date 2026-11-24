// [6] 세 번째 화면 '모델성능 및 오경보율 비교' (Front-end 담당)
//   위 구역  : 채널별 합산 후 예측   (96개 채널을 먼저 더한 건물 합계를 바로 예측, 기업 권장)
//   아래 구역: 채널별 예측 후 합산   (채널 61개를 각각 예측해 더한 건물 합계)
//   각 구역  : ① 예측 오차 표 (전일 동일시각 · LightGBM · XGBoost · Random Forest · Bi-LSTM)
//              ② 피크경보 오분류표 (모델 고르기 → 맞힘·놓침·헛경보·정상) + 재현율 비교표 (+ '경보 없음' 비교용 줄)
//   맨 위 '피크 기준' 콤보박스: 최근 30일·전월·당월·7~9월·12~2월 95번째 백분위수 → 오분류표를 그 기준으로 다시 판정
//     (첫 화면 피크 기준과 연동. 예측 오차 표는 기준과 상관없이 같음)
//   모두 시험기간(학습에 쓰지 않은 기간) 실측과 비교한 결과. 서버 자료: /api/performance?thr=
(() => {
  const PG = window.PG, $ = PG.$, fmt = PG.fmt;
  const P3 = PG.S3 = { loaded: false, sel: {} };
  const pct = (v, d = 1) => (v == null ? '—' : fmt(v * 100, d) + '%');
  const mk = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
  const row = (tb, cells, th, cls) => { const tr = mk('tr', cls); cells.forEach((v) => tr.append(mk(th ? 'th' : 'td', null, v))); tb.append(tr); return tr; };

  P3.show = async (force) => {
    const key = PG.S1.thrKey || 'recent30';
    if (P3.loaded === key && !force) return; P3.loaded = key;
    try { P3.D = await PG.api('api/performance?thr=' + key); } catch (e) { $('#perfnote').textContent = e.message; return; }
    const D = P3.D, sp = D.splits || {}, T = D.thr;
    const sel = $('#pthrsel'); sel.replaceChildren();
    D.thr_options.forEach((o) => { const op = new Option(`${o.name} 95번째 백분위수` + (o.n ? '' : ' — 자료 없음'), o.key, false, o.key === key); op.disabled = !o.n; sel.add(op); });
    sel.onchange = () => { PG.S1.setThr(sel.value); P3.show(true); };
    $('#pthrinfo').textContent = T.n ? `기준값 ${T.kw_min === T.kw_max ? fmt(T.kw_min, 2) : fmt(T.kw_min, 2) + ' ~ ' + fmt(T.kw_max, 2)} kW · 비교한 예측 시점 ${T.n.toLocaleString()}개 · 실제 피크 위험 ${T.actual_risk}건` : '이 기준은 계산할 자료가 없습니다';
    $('#perfnote').textContent = `시험기간 ${D.test[0]} ~ ${D.test[1]} (학습·검증에 쓰지 않은 기간)의 실측과 비교. ` +
      (sp.train ? `학습 ${sp.train[0].slice(0, 10)}~${sp.train[1].slice(0, 10)} · 검증 ${sp.val[0].slice(0, 10)}~${sp.val[1].slice(0, 10)} · ` : '') +
      `모든 모델은 같은 예측 시점에서 비교(공통 표본). 예측 '위험' = 다음 1시간 예측 4칸 중 하나라도 그날 '${T.name}' 기준의 95% 이상(화면 판정과 같음), 실제 위험 = 실측 4칸 중 하나라도 기준 이상. 기준을 바꾸면 ② 오분류표만 바뀌고 ① 예측 오차는 같습니다.` +
      (T.n && !T.actual_risk ? ` ※ 시험기간에 '${T.name}' 기준을 넘은 적이 없어 재현율을 계산할 수 없습니다(헛경보만 비교).` : '');
    const box = $('#perfsections'); box.replaceChildren();
    box.append(selectionCard(D));
    D.sections.forEach((s) => box.append(section(s)));
    box.append(extraCards(D));
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
    s.rows.forEach((r) => sel.add(new Option(r.name, r.id, false, r.id === (s.best_recall || s.rows[1].id))));
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
    row(t2, [na.name, na.tp, na.fn, na.fp, s.actual_risk ? pct(na.recall, 0) : '—', '—', pct(na.miscls)], false, 'muted');
    const w2 = mk('div', 'tscroll'); w2.append(t2);
    const n2 = mk('p', 'note', `실제 피크 위험 ${s.actual_risk}건. '경보 없음'은 한 번도 경보를 내지 않아도 오분류율이 ${pct(na.miscls)}로 낮게 나온다는 것을 보여 주는 비교용 줄입니다 — ` +
      `위험이 드물어서 오분류율만 보면 판단을 그르칩니다. 피크를 놓치지 않는 것이 목적이므로 재현율(맞힘 ÷ 실제 위험)을 먼저 보고, 헛경보(정밀도)는 그다음에 봅니다. ` +
      (bestR ? `이 방식에서 재현율이 가장 높은 모델: ${bestR.name} (${bestR.peak.tp}건 맞힘 · ${bestR.peak.fn}건 놓침 · 헛경보 ${bestR.peak.fp}건).` : ''));
    right.append(mk('h3', null, '모델별 재현율·오분류율 비교'), w2, n2);
    grid.append(left, right); card.append(grid);
    draw();
    return card;
  }
  // ---------- 최적 모델 선정 결론 (맨 위) ----------
  const nameOf = (D) => { const m = {}; D.sections.forEach((s) => s.rows.forEach((r) => { m[r.id] = r.id === '전일 동일시각' ? r.name : `${s.key === 'direct' ? '합산 후 예측' : '예측 후 합산'} · ${r.name}`; })); return m; };
  function selectionCard(D) {
    const c = mk('div', 'card selcard'), S = D.selection;
    c.append(mk('h2', null, '최적 모델 선정 결론'));
    (S.lines || [S.text]).forEach((t, i) => c.append(mk('p', i === 0 ? 'selbest' : 'note strong', t)));
    if (S.ranking) {
      const t = mk('table', 'perf');
      row(t, ['순위', '방식 · 모델', 'F1', '재현율', '정밀도', '헛경보', 'MAE', '개선율'], true);
      S.ranking.forEach((r, i) => { const tr = row(t, [i + 1, r.name, fmt(r.f1, 3), pct(r.recall, 0), pct(r.precision, 0), r.fp, fmt(r.mae, 3), fmt(r.gain, 1) + '%']); if (!i) tr.classList.add('best'); });
      const w = mk('div', 'tscroll'); w.append(t); c.append(w);
      c.append(mk('p', 'note', `기준: 기업 목표(기준모델 대비 MAE 10% 이상 개선)를 넘은 모델 중 F1(재현율과 정밀도의 조화평균, F1 score) → MAE 순. 피크 기준 = '${D.thr.name}' 95번째 백분위수, 위험 = 기준의 95% 이상 예측.`));
    }
    return c;
  }
  // ---------- 발생시각 오차 · 조건별 정확도 · 기상 포함/미포함 ----------
  function extraCards(D) {
    const wrap = mk('div', 'extragrid'), N = nameOf(D);
    // ③ 발생시각 오차
    const a = mk('div', 'card'); a.append(mk('h2', null, '③ 예측시각과 실제 발생시각 오차'));
    const T = D.timing, t1 = mk('table', 'perf');
    row(t1, ['방식 · 모델', '사전 경보', '평균 선행', '시각 오차', '±15분 이내'], true);
    Object.entries(T.rows).forEach(([id, r]) => row(t1, [N[id] || id, r.events ? `${r.warned}/${r.events} (${fmt(r.warned_pct, 0)}%)` : '—', r.lead_min == null ? '—' : r.lead_min + '분 전', r.abs_err_min == null ? '—' : `평균 ${r.abs_err_min}분 (이르게 ${r.early_pct}%)`, r.within15_pct == null ? '—' : r.within15_pct + '%']));
    const w1 = mk('div', 'tscroll'); w1.append(t1); a.append(w1, mk('p', 'note', `시험기간 실제 피크 사건 ${T.events}건. ` + T.rule));
    // ④ 조건별 정확도
    const b = mk('div', 'card'); b.append(mk('h2', null, '④ 조건별 예측 정확도 (MAE, kW)'));
    const C = D.by_condition, t2 = mk('table', 'perf');
    row(t2, ['방식 · 모델', ...C.conditions], true);
    Object.entries(C.rows).forEach(([id, r]) => row(t2, [N[id] || id, ...C.conditions.map((c) => fmt(r[c], 3))]));
    const w2 = mk('div', 'tscroll'); w2.append(t2); b.append(w2, mk('p', 'note', '시험기간 공통 표본, 예측 대상 시각 기준으로 나눔. 표본 수: ' + C.conditions.map((c) => `${c} ${C.n[c].toLocaleString()}`).join(' · ')));
    wrap.append(a, b);
    // ⑤ 기상 포함 / 미포함
    if (D.weather) {
      const w = mk('div', 'card wide'); w.append(mk('h2', null, '⑤ 기상(기온) 포함 / 미포함 비교'));
      const t3 = mk('table', 'perf');
      row(t3, ['방식 · 모델', 'MAE 포함', 'MAE 미포함', '차이', '맞힘 포함 / 미포함', '헛경보 포함 / 미포함', '재현율 포함 / 미포함'], true);
      D.weather.rows.forEach((r) => {
        const d = r.mae_w - r.mae_nw;
        const tr = row(t3, [N[r.id] || r.name, fmt(r.mae_w, 3), fmt(r.mae_nw, 3), (d > 0 ? '+' : '') + fmt(d, 3), `${r.tp_w ?? '—'} / ${r.tp_nw}`, `${r.fp_w ?? '—'} / ${r.fp_nw}`, `${pct(r.recall_w, 0)} / ${pct(r.recall_nw, 0)}`]);
        if (d < 0) tr.classList.add('best');
      });
      const w3 = mk('div', 'tscroll'); w3.append(t3);
      w.append(w3, mk('p', 'note', D.weather.note + ' 주황 줄 = 기상을 넣어 MAE가 줄어든 모델.' + (D.thr.key !== 'recent30' ? ' (맞힘·헛경보·재현율 비교는 최근 30일 기준에서만 표시)' : '')));
      wrap.append(w);
    }
    return wrap;
  }
})();
