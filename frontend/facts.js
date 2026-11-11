/* «Факты» — единое окно контроля внесённого факта (работниками и «на заказ») + ввод факта на заказ без привязки к работникам. */
const FACT_SRC = { web: 'веб', telegram: 'Telegram', import: 'импорт' };
const FACT_PAGE = 200;

SCREENS.facts = async v => {
  const staff = STAFF.includes(S.me.role), foreman = S.me.role === 'foreman';
  const [workers, projects, centers] = await Promise.all([GET('/workers'), GET('/projects'), foreman ? Promise.resolve({ items: [] }) : GET('/work-centers')]);
  const F = S.factsFilter = S.factsFilter || { date_from: '', date_to: '', work_center_id: '', worker: '', project_id: '', status: '', source: '', operation: '', deviations: false };
  let items = [], total = 0, summary = { count: 0, qty: 0, unlinked: 0, submitted: 0 }, offset = 0;
  const opt = (arr, sel, blank) => `<option value="">${esc(blank)}</option>` + arr.map(x => `<option value="${esc(x.id ?? x.v)}" ${String(sel) === String(x.id ?? x.v) ? 'selected' : ''}>${esc(x.name ?? x.t)}</option>`).join('');
  v.innerHTML = `<div class="row" style="margin-bottom:10px"><h2 class="grow" style="margin:0">Факты</h2>${staff ? '<button id="ft-add">+ Внести факт на заказ</button>' : ''}</div>
    <p class="hint">Все внесённые факты выполненных операций: и по работникам, и «на заказ» (без привязки к работнику). ${foreman ? 'Показан ваш участок.' : ''}</p>
    <div class="card"><div class="row" style="align-items:end;flex-wrap:wrap;gap:10px">
      <label class="f">С<input type="date" id="ft-from"></label><label class="f">По<input type="date" id="ft-to"></label>
      <div class="row" style="gap:6px"><button class="small secondary" data-q="0">Сегодня</button><button class="small secondary" data-q="6">7 дней</button><button class="small secondary" data-q="29">30 дней</button></div>
      ${foreman ? '' : `<label class="f">Участок<select id="ft-wc">${opt(centers.items, F.work_center_id, 'Все участки')}</select></label>`}
      <label class="f">Работник<select id="ft-w"><option value="">Все</option><option value="none" ${F.worker === 'none' ? 'selected' : ''}>— без привязки (на заказ) —</option>${workers.items.map(w => `<option value="${w.id}" ${String(F.worker) === String(w.id) ? 'selected' : ''}>${esc(w.name)}</option>`).join('')}</select></label>
      <label class="f">Проект<select id="ft-p">${opt(projects.items, F.project_id, 'Все проекты')}</select></label>
      <label class="f">Статус<select id="ft-s">${opt([{ v: 'submitted', t: 'Ждёт подтверждения' }, { v: 'confirmed', t: 'Подтверждён' }], F.status, 'Любой')}</select></label>
      <label class="f">Источник<select id="ft-src">${opt([{ v: 'web', t: 'Веб' }, { v: 'telegram', t: 'Telegram' }, { v: 'import', t: 'Импорт' }], F.source, 'Любой')}</select></label>
      <label class="f">Операция<input id="ft-op" value="${esc(F.operation)}" placeholder="часть названия" style="max-width:170px"></label>
      <label class="chk"><input type="checkbox" id="ft-dev" ${F.deviations ? 'checked' : ''}> <span>Только с причиной / комментарием</span></label></div></div>
    <div id="ft-sum" class="grid4" style="margin:10px 0"></div><div class="card flush scroll" id="ft-tbl"></div><div class="row" id="ft-more" style="margin-top:8px"></div>`;
  $('#ft-from', v).value = F.date_from; $('#ft-to', v).value = F.date_to;

  const query = off => { const p = new URLSearchParams({ limit: FACT_PAGE, offset: off });
    if (F.date_from) p.set('date_from', F.date_from); if (F.date_to) p.set('date_to', F.date_to);
    if (F.work_center_id) p.set('work_center_id', F.work_center_id);
    if (F.worker === 'none') p.set('no_worker', 'true'); else if (F.worker) p.set('worker_id', F.worker);
    for (const k of ['project_id', 'status', 'source']) if (F[k]) p.set(k, F[k]);
    if (F.operation.trim()) p.set('operation', F.operation.trim()); if (F.deviations) p.set('deviations', 'true'); return p.toString(); };
  const draw = () => {
    const sum = summary, shown = items.length;
    $('#ft-sum', v).innerHTML = `<div class="kpi k-blue">Записей<b>${sum.count}</b></div><div class="kpi k-grey">«На заказ», без работника<b>${sum.unlinked}</b></div>
      <div class="kpi ${sum.submitted ? 'k-amber' : 'k-green'}">Ждут подтверждения<b>${sum.submitted}</b></div><div class="kpi k-grey">Объём (сумма по записям)<b>${num(sum.qty)}</b></div>`;
    $('#ft-tbl', v).innerHTML = table([
      { label: 'Дата', get: f => fmtDY(f.date) },
      { label: 'Проект · заказ', html: f => `${esc(f.project)}<div class="muted" style="font-size:13px">${esc(f.order)}</div>` },
      { label: 'Изделие', get: f => f.product }, { label: 'Операция', get: f => f.operation },
      { label: 'Работник', html: f => f.worker ? esc(f.worker) : '<span class="badge b-none">на заказ</span>' },
      { label: 'Участок', get: f => f.section },
      { label: 'План', cls: 'num', get: f => f.plan_qty == null ? '—' : num(f.plan_qty) },
      { label: 'Факт', cls: 'num', html: f => `<b>${num(f.qty)}</b> <span class="muted">${esc(f.unit)}</span>${f.plan_qty != null && f.qty < f.plan_qty ? ' <span class="badge b-risk">меньше плана</span>' : ''}` },
      { label: 'Причина / комментарий', html: f => [f.reason, f.note].filter(Boolean).map(esc).join('<br>') || '<span class="muted">—</span>' },
      { label: 'Статус', html: f => f.status === 'submitted' ? '<span class="badge b-risk">ждёт подтверждения</span>' : '<span class="badge b-done">подтверждён</span>' },
      { label: 'Внёс', html: f => `${esc(f.entered_by || (f.source === 'telegram' ? 'работник (Telegram)' : '—'))}<div class="muted" style="font-size:13px">${esc(FACT_SRC[f.source] || f.source)} · ${fmtDT(f.updated_at.replace(/Z$/, ''))}</div>` }],
      items, staff ? f => `<button class="small secondary" data-e="${f.id}">Изменить</button>${f.status === 'submitted' ? ` <button class="small" data-c="${f.id}">Подтвердить</button>` : ''} <button class="small danger" data-d="${f.id}">Удалить</button>` : null);
    $('#ft-more', v).innerHTML = total > shown ? `<span class="muted">Показано ${shown} из ${total}</span><button class="small secondary" id="ft-next">Показать ещё</button>` : (total ? `<span class="muted">Показано всё: ${total}</span>` : '');
    const nx = $('#ft-next', v); if (nx) nx.onclick = () => load(true);
    const row = b => items.find(f => f.id == (b.dataset.e || b.dataset.c || b.dataset.d));
    $$('[data-e]', v).forEach(b => b.onclick = () => factEdit(row(b), () => load()));
    $$('[data-c]', v).forEach(b => b.onclick = async () => { try { await POST(`/facts/${row(b).id}/confirm`); toast('Подтверждено'); load(); } catch (e) { toast(errText(e), true); } });
    $$('[data-d]', v).forEach(b => b.onclick = async () => { const f = row(b); if (!await confirmBox(`Удалить факт ${num(f.qty)} ${f.unit} по операции «${f.operation}» за ${fmtDY(f.date)}?`)) return; try { await DEL(`/facts/${f.id}`); toast('Факт удалён'); load(); } catch (e) { toast(errText(e), true); } });
  };
  async function load(more) {
    try {
      const r = await GET('/facts/journal?' + query(more ? items.length : 0));
      items = more ? items.concat(r.items) : r.items; total = r.total; summary = r.summary;
      if (!F.date_from) { F.date_from = r.date_from; $('#ft-from', v).value = r.date_from; } if (!F.date_to) { F.date_to = r.date_to; $('#ft-to', v).value = r.date_to; }
      draw();
    } catch (e) { toast(errText(e), true); }
  }
  const bind = (sel, key, ev = 'change', get = el => el.value) => { const el = $(sel, v); if (el) el.addEventListener(ev, () => { F[key] = get(el); load(); }); };
  bind('#ft-from', 'date_from'); bind('#ft-to', 'date_to'); bind('#ft-wc', 'work_center_id'); bind('#ft-w', 'worker'); bind('#ft-p', 'project_id');
  bind('#ft-s', 'status'); bind('#ft-src', 'source'); bind('#ft-dev', 'deviations', 'change', el => el.checked);
  let timer; $('#ft-op', v).addEventListener('input', e => { clearTimeout(timer); timer = setTimeout(() => { F.operation = e.target.value; load(); }, 350); });
  $$('[data-q]', v).forEach(b => b.onclick = async () => {
    const t = (await GET('/facts/journal?limit=1')).date_to, d = new Date(t + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() - +b.dataset.q);
    F.date_to = t; F.date_from = d.toISOString().slice(0, 10); $('#ft-from', v).value = F.date_from; $('#ft-to', v).value = F.date_to; load();
  });
  const add = $('#ft-add', v); if (add) add.onclick = () => factAdd(() => load());
  await load();
};

/* Окно «Внести факт на заказ»: заказ → операция → объём; без привязки к работникам */
async function factAdd(done) {
  let d; try { d = await GET('/facts/order-options'); } catch (e) { return toast(errText(e), true); }
  const foreman = S.me.role === 'foreman', projects = [...new Set(d.items.map(i => i.project))];
  const m = modal(`<h2>Внести факт на заказ</h2><p class="hint">Выполнено по операции без привязки к работникам. Повторный ввод за тот же день, операцию и участок заменяет значение.</p><form novalidate>
    <label class="f">Дата<input type="date" name="date" value="${esc(d.today)}" max="${esc(d.today)}" required></label>
    <label class="f">Проект<select name="project">${['', ...projects].map(p => `<option value="${esc(p)}">${p ? esc(p) : 'Все проекты'}</option>`).join('')}</select></label>
    <label class="f">Заказ *<select name="line" required></select></label>
    <label class="f">Операция *<select name="op" required></select></label>
    ${foreman ? '' : '<label class="f">Участок<select name="wc"></select></label>'}
    <label class="f">Выполнено * <span class="muted" id="fa-unit"></span><input type="number" name="qty" min="0" step="any" required><span class="muted" id="fa-left"></span></label>
    <label class="f">Причина<select name="reason"><option value="">—</option>${d.reasons.map(r => `<option value="${r.id}">${esc(r.name)}</option>`).join('')}</select></label>
    <label class="f">Комментарий<input name="note" maxlength="400" placeholder="Нужен при отклонении (например, больше нужного)"></label>
    <div class="err" data-err role="alert"></div><div class="row"><button type="submit">Сохранить</button><button type="button" class="secondary" data-x>Отмена</button></div></form>`);
  const form = $('form', m.el), E = form.elements, line = () => d.items.find(i => i.id == E.line.value), op = () => line()?.operations.find(o => o.id == E.op.value);
  const fillLines = () => { const L = d.items.filter(i => !E.project.value || i.project === E.project.value);
    E.line.innerHTML = L.map(i => `<option value="${i.id}">${esc(i.project)} · ${esc(i.order)} · ${esc(i.product)} (${num(i.qty)} шт)</option>`).join('') || '<option value="">Нет подходящих заказов</option>'; fillOps(); };
  const fillOps = () => { const l = line(); E.op.innerHTML = l ? l.operations.map(o => `<option value="${o.id}">${esc(o.name)} — сделано ${num(o.done)} из ${num(o.required)}</option>`).join('') : ''; info(); };
  const info = () => { const o = op(); $('#fa-unit', m.el).textContent = o ? `(${o.unit})` : ''; $('#fa-left', m.el).textContent = o ? ` Осталось по заказу: ${num(Math.max(0, o.required - o.done))}` : '';
    if (E.wc) { const sel = o && o.centers.length === 1 ? o.centers[0] : ''; E.wc.innerHTML = `<option value="">${o && o.centers.length > 1 ? 'По операции целиком' : '—'}</option>` + d.sections.filter(s => !o || !o.centers.length || o.centers.includes(s.id)).map(s => `<option value="${s.id}" ${s.id === sel ? 'selected' : ''}>${esc(s.name)}</option>`).join(''); } };
  E.project.onchange = fillLines; E.line.onchange = fillOps; E.op.onchange = info; fillLines();
  $('[data-x]', m.el).onclick = m.close;
  form.onsubmit = async e => {
    e.preventDefault(); const er = $('[data-err]', m.el); er.textContent = ''; const btn = $('button[type=submit]', m.el); btn.disabled = true;
    try {
      const r = await POST('/facts/order', { fact_date: E.date.value, order_line_id: +E.line.value, operation_id: +E.op.value, qty: +E.qty.value, work_center_id: E.wc && E.wc.value ? +E.wc.value : null,
        reason_id: E.reason.value ? +E.reason.value : null, note: E.note.value });
      m.close(); toast(r.replaced ? `Факт заменён (было ${num(r.previous_qty)})` : 'Факт внесён'); done();
    } catch (x) { er.textContent = errText(x); btn.disabled = false; }
  };
}

/* Изменить внесённый факт: у факта работника — как обычный ввод (тот же ключ), у факта «на заказ» — через ввод на заказ */
async function factEdit(f, done) {
  let reasons = []; try { reasons = (await GET('/fact-reasons')).items.filter(r => r.work_center_id == null || r.work_center_id === f.section_id); } catch (e) { /* без списка причин */ }
  const m = modal(`<h2>Изменить факт</h2><p class="muted">${esc(f.project)} · ${esc(f.order)} · ${esc(f.operation)} · ${fmtDY(f.date)} · ${f.worker ? esc(f.worker) : 'на заказ'}</p><form novalidate>
    <label class="f">Выполнено (${esc(f.unit)}) *<input type="number" name="qty" min="0" step="any" value="${f.qty}" required></label>
    <label class="f">Причина<select name="reason"><option value="">—</option>${reasons.map(r => `<option value="${r.id}" ${r.name === f.reason ? 'selected' : ''}>${esc(r.name)}</option>`).join('')}</select></label>
    <label class="f">Комментарий<input name="note" maxlength="400" value="${esc(f.note)}"></label>
    <div class="err" data-err role="alert"></div><div class="row"><button type="submit">Сохранить</button><button type="button" class="secondary" data-x>Отмена</button></div></form>`);
  const form = $('form', m.el), E = form.elements; $('[data-x]', m.el).onclick = m.close;
  form.onsubmit = async e => {
    e.preventDefault(); const er = $('[data-err]', m.el); er.textContent = '';
    const base = { fact_date: f.date, order_line_id: f.order_line_id, operation_id: f.operation_id, qty: +E.qty.value, reason_id: E.reason.value ? +E.reason.value : null, note: E.note.value };
    try { if (f.worker_id) await POST('/facts', { ...base, worker_id: f.worker_id }); else await POST('/facts/order', { ...base, work_center_id: f.section_id || null });
      m.close(); toast('Сохранено'); done(); } catch (x) { er.textContent = errText(x); }
  };
}
