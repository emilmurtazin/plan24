/* Plan 24 — экраны ввода данных, настроек, пользователей, журнала. Использует ядро из app.js. */
'use strict';

async function loadRefs() {
  const [projects, products, workers, centers, orders, reasons] = await Promise.all(
    ['/projects', '/products', '/workers', '/work-centers', '/order-lines', '/fact-reasons'].map(listAll));
  const by = a => Object.fromEntries(a.map(x => [x.id, x]));
  const tg = await GET('/telegram/status').catch(() => ({ configured: false, links: [] }));
  return { projects, products, workers, centers, orders, reasons, tg, TGW: Object.fromEntries(tg.links.filter(l => l.worker_id).map(l => [l.worker_id, l])),
    P: by(projects), PR: by(products), W: by(workers), C: by(centers) };
}
const opts = (arr, label = x => x.name) => arr.map(x => ({ v: x.id, l: label(x) }));
const ORDER_ST = { draft: 'Черновик', planned: 'В плане', done: 'Выполнен', cancelled: 'Отменён' };
const table = (cols, rows, actions) => `<table><tr>${cols.map(c => `<th class="${c.cls || ''}">${esc(c.label)}</th>`).join('')}${actions ? '<th></th>' : ''}</tr>${rows.map(r => `<tr>${cols.map(c => `<td class="${c.cls || ''}">${c.html ? c.html(r) : esc(c.get(r))}</td>`).join('')}${actions ? `<td style="white-space:nowrap">${actions(r)}</td>` : ''}</tr>`).join('') || `<tr><td colspan="${cols.length + 1}" class="muted">Пока пусто</td></tr>`}</table>`;

/* Универсальная вкладка «список + добавить/изменить/удалить» */
async function crudTab(box, cfg, reload) {
  const refs = await loadRefs(), rows = await listAll(cfg.path);
  const acts = r => `${cfg.extra ? cfg.extra(r) : ''}<button class="small secondary" data-e="${r.id}">Изменить</button> <button class="small danger" data-d="${r.id}">Удалить</button>`;
  box.innerHTML = `<div class="row" style="margin-bottom:10px"><h2 class="grow" style="margin:0">${esc(cfg.title)}</h2>${cfg.noAdd ? '' : `<button data-add>+ ${esc(cfg.add)}</button>`}</div>${cfg.hint ? `<p class="muted">${esc(cfg.hint(refs, rows))}</p>` : ''}<div class="card scroll">${table(cfg.cols(refs), rows, acts)}</div>`;
  const open = (row) => {
    const fields = (row && cfg.editFields ? cfg.editFields(refs) : cfg.fields(refs));
    formModal({
      title: row ? cfg.editTitle || 'Изменить' : cfg.add, fields, values: row || cfg.defaults || {},
      onSubmit: async (vals, m) => {
        if (row) await PATCH(`${cfg.path}/${row.id}`, { ...vals, version: row.version }); else await POST(cfg.path, vals);
        m.close(); toast('Сохранено'); reload();
      }
    });
  };
  const add = $('[data-add]', box); if (add) add.onclick = () => open(null);
  $$('[data-e]', box).forEach(b => b.onclick = () => open(rows.find(r => r.id == b.dataset.e)));
  $$('[data-d]', box).forEach(b => b.onclick = async () => {
    if (!await confirmBox('Удалить запись?')) return;
    try { await DEL(`${cfg.path}/${b.dataset.d}`); toast('Удалено'); reload(); } catch (e) { toast(errText(e), true); }
  });
  if (cfg.after) cfg.after(box, refs, rows, reload);
}

const nameField = { name: 'name', label: 'Название', required: true };
const TABS = {
  orders: {
    label: 'Заказы', cfg: {
      title: 'Позиции заказов', add: 'Добавить позицию', path: '/order-lines', defaults: { qty: 1, priority: 1, status: 'planned' },
      hint: (r) => r.products.length && r.projects.length ? 'Позиция = изделие в нужном количестве к сроку. Маршрут берётся текущий; уже созданные позиции остаются на своей версии маршрута.' : 'Сначала добавьте проект и изделие с операциями (вкладки «Проекты» и «Изделия и маршруты»).',
      cols: r => [{ label: '№', get: x => x.ext_id || '#' + x.id }, { label: 'Проект', get: x => r.P[x.project_id]?.name }, { label: 'Изделие', get: x => r.PR[x.product_id]?.name },
      { label: 'Кол-во', cls: 'num', get: x => num(x.qty) }, { label: 'Срок', get: x => fmtDY(x.due_date) }, { label: 'Приоритет', cls: 'num', get: x => x.priority }, { label: 'Статус', get: x => ORDER_ST[x.status] }],
      fields: r => [{ name: 'project_id', label: 'Проект', type: 'select', options: opts(r.projects), required: true, num: true },
      { name: 'product_id', label: 'Изделие', type: 'select', options: opts(r.products), required: true, num: true },
      { name: 'ext_id', label: 'Номер позиции (необязательно)' }, { name: 'qty', label: 'Количество', type: 'number', required: true, min: 0, step: 'any' },
      { name: 'due_date', label: 'Срок', type: 'date' }, { name: 'priority', label: 'Приоритет (1 — самый важный)', type: 'number', required: true, min: 1, step: 1 },
      { name: 'status', label: 'Статус', type: 'select', required: true, options: Object.entries(ORDER_ST).map(([v, l]) => ({ v, l })) }],
      editFields: r => [{ name: 'project_id', label: 'Проект', type: 'select', options: opts(r.projects), required: true, num: true }, { name: 'ext_id', label: 'Номер позиции' },
      { name: 'qty', label: 'Количество', type: 'number', required: true, min: 0, step: 'any' }, { name: 'due_date', label: 'Срок', type: 'date' },
      { name: 'priority', label: 'Приоритет', type: 'number', required: true, min: 1, step: 1 },
      { name: 'status', label: 'Статус', type: 'select', required: true, options: Object.entries(ORDER_ST).map(([v, l]) => ({ v, l })) }],
    }
  },
  projects: {
    label: 'Проекты', cfg: {
      title: 'Проекты', add: 'Добавить проект', path: '/projects', cols: () => [{ label: 'Название', get: x => x.name }, { label: 'Заказчик', get: x => x.customer }],
      fields: () => [nameField, { name: 'customer', label: 'Заказчик' }]
    }
  },
  products: {
    label: 'Изделия и маршруты', cfg: {
      title: 'Изделия и маршруты', add: 'Добавить изделие', path: '/products', cols: () => [{ label: 'Изделие', get: x => x.name }, { label: 'Ед.', get: x => x.unit }],
      hint: () => 'У каждого изделия есть маршрут — список операций с нормами. Откройте «Маршрут», чтобы добавить операции.',
      fields: () => [nameField, { name: 'unit', label: 'Единица', required: true }], defaults: { unit: 'шт' },
      extra: r => `<button class="small" data-rt="${r.id}">Маршрут</button> `,
      after: (box, refs) => $$('[data-rt]', box).forEach(b => b.onclick = () => openRouting(refs.PR[b.dataset.rt]))
    }
  },
  workers: {
    label: 'Исполнители', cfg: {
      title: 'Исполнители', add: 'Добавить исполнителя', path: '/workers', defaults: { perf: 1, skill: 1, status: 'available' },
      hint: () => 'Производительность: 1 — норма, 1,2 — на 20% быстрее. Квалификация — допуск к операциям с требуемым уровнем. «Допуски» ограничивают позиции, к которым допущен человек (пусто = ко всем).',
      cols: r => [{ label: 'Имя', get: x => x.name }, { label: 'Участок', get: x => r.C[x.work_center_id]?.name || '—' }, { label: 'Производит.', cls: 'num', get: x => num(x.perf) },
      { label: 'Квалиф.', cls: 'num', get: x => num(x.skill) }, { label: 'Статус', html: x => x.status === 'available' ? 'Доступен' : '<span class="warn">Недоступен</span>' },
      { label: 'Telegram', html: x => r.TGW[x.id] ? '<span class="ok">✔ привязан</span>' : '<span class="muted">—</span>' }],
      fields: r => [nameField, { name: 'work_center_id', label: 'Участок', type: 'select', options: opts(r.centers), num: true },
        { name: 'perf', label: 'Производительность', type: 'number', required: true, min: 0.1, max: 10, step: 0.05 },
        { name: 'skill', label: 'Квалификация', type: 'number', required: true, min: 1, max: 10, step: 1 },
        { name: 'status', label: 'Статус', type: 'select', required: true, options: [{ v: 'available', l: 'Доступен' }, { v: 'unavailable', l: 'Недоступен' }] }],
      extra: r => `<button class="small" data-ac="${r.id}">Допуски</button> <button class="small" data-ab="${r.id}">Отпуска</button> <button class="small" data-tg="${r.id}">Telegram</button> `,
      after: (box, refs, rows, reload) => { $$('[data-tg]', box).forEach(b => b.onclick = () => openTelegram('worker', +b.dataset.tg, refs.W[b.dataset.tg].name, reload)); $$('[data-ac]', box).forEach(b => b.onclick = () => openAccess(refs.W[b.dataset.ac], refs)); $$('[data-ab]', box).forEach(b => b.onclick = () => openAbsences(refs.W[b.dataset.ab])); }
    }
  },
  centers: { label: 'Участки', cfg: { title: 'Участки', add: 'Добавить участок', path: '/work-centers', cols: () => [{ label: 'Название', get: x => x.name }], fields: () => [nameField] } },
  holidays: {
    label: 'Календарь', cfg: {
      title: 'Праздники и нерабочие дни', add: 'Добавить день', path: '/holidays', hint: () => 'Рабочие дни недели настраиваются в «Настройках». Здесь — дополнительные нерабочие даты.',
      cols: () => [{ label: 'Дата', get: x => fmtDY(x.holiday_date) }, { label: 'Название', get: x => x.name }],
      fields: () => [{ name: 'holiday_date', label: 'Дата', type: 'date', required: true }, { name: 'name', label: 'Название' }], editFields: () => [{ name: 'name', label: 'Название' }]
    }
  },
  reasons: { label: 'Причины отклонений', cfg: { title: 'Причины отклонений от плана', add: 'Добавить причину', path: '/fact-reasons', cols: () => [{ label: 'Причина', get: x => x.name }], fields: () => [nameField] } },
};

SCREENS.data = async (v, tab) => {
  S.nav++;
  tab = tab || S.dataTab || 'orders'; S.dataTab = tab;
  v.innerHTML = `<h1>Данные</h1><nav class="tabs" style="margin-bottom:14px;border:1px solid var(--line)">${Object.entries(TABS).map(([k, t]) => `<button data-t="${k}" ${k === tab ? 'aria-current="page"' : ''}>${esc(t.label)}</button>`).join('')}</nav><div id="dt"></div>`;
  $$('[data-t]', v).forEach(b => b.onclick = () => SCREENS.data(v, b.dataset.t));
  const box = $('#dt', v), reload = () => crudTab(box, TABS[tab].cfg, reload).catch(e => { if (!(e instanceof Stale)) box.innerHTML = `<div class="card err">${esc(errText(e))}</div>`; });
  reload();
};

/* ---------- маршрут изделия: версии и операции ---------- */
async function openRouting(product, selId) {
  const rts = await GET(`/products/${product.id}/routings`);
  const cur = rts.find(r => r.id === selId) || rts[0];
  const canEdit = !cur.used;
  const m = modal(`<h2>Маршрут: ${esc(product.name)}</h2>
    <div class="row" style="margin-bottom:10px">${rts.map(r => `<button class="small ${r.id === cur.id ? '' : 'secondary'}" data-v="${r.id}">v${r.version_no}${r.is_current ? ' · текущий' : ''}</button>`).join('')}<span class="grow"></span><button class="small secondary" data-nv>Новая версия</button></div>
    <p class="muted">${cur.used ? 'Маршрут используется в позициях заказов — структуру менять нельзя, чтобы не сломать идущие заказы. Создайте новую версию. Блокировку операции менять можно.' : 'Одинаковый «Порядок» — операции идут параллельно. Норма — сколько изделий делает исполнитель с производительностью 1 за полный рабочий день.'}</p>
    <div class="scroll">${table([{ label: 'Порядок', cls: 'num', get: o => num(o.sequence) }, { label: 'Операция', get: o => o.name }, { label: 'Норма/день', cls: 'num', get: o => num(o.norm_per_day) }, { label: 'Квалиф.', cls: 'num', get: o => num(o.min_skill) }, { label: 'На изделие', cls: 'num', get: o => num(o.per_item) },
    { label: 'Блок', html: o => o.hold_qty ? `<span class="warn">${num(o.hold_qty)}${o.hold_until ? ' до ' + fmtDY(o.hold_until) : ''}</span> ${esc(o.hold_reason)}` : '—' }], cur.operations,
      o => `<button class="small secondary" data-oe="${o.id}">${canEdit ? 'Изменить' : 'Блокировка'}</button>${canEdit ? ` <button class="small danger" data-od="${o.id}">Удалить</button>` : ''}`)}</div>
    <div class="row" style="margin-top:12px">${canEdit ? '<button data-oa>+ Операция</button>' : ''}<button class="secondary" data-x>Закрыть</button></div>`, true);
  const again = () => { m.close(); openRouting(product, cur.id); };
  $('[data-x]', m.el).onclick = m.close;
  $$('[data-v]', m.el).forEach(b => b.onclick = () => { m.close(); openRouting(product, +b.dataset.v); });
  $('[data-nv]', m.el).onclick = async () => { try { const r = await POST(`/products/${product.id}/routings`); m.close(); toast(`Создана версия v${r.version_no}`); openRouting(product, r.id); } catch (e) { toast(errText(e), true); } };
  const opFields = (full) => [...(full ? [{ name: 'name', label: 'Операция', required: true }, { name: 'sequence', label: 'Порядок', type: 'number', required: true, min: 0, step: 'any' },
    { name: 'norm_per_day', label: 'Норма за день', type: 'number', required: true, min: 0, step: 'any' }, { name: 'min_skill', label: 'Требуемая квалификация', type: 'number', required: true, min: 1, max: 10, step: 1 },
    { name: 'per_item', label: 'Операций на изделие', type: 'number', required: true, min: 0, step: 'any' }] : []),
  { name: 'hold_qty', label: 'Блок: изделий, по которым операция не идёт', type: 'number', min: 0, step: 'any' }, { name: 'hold_until', label: 'Блок снимается автоматически с даты', type: 'date' }, { name: 'hold_reason', label: 'Причина блокировки' }];
  const edit = (o) => formModal({
    title: o ? o.name : 'Новая операция', fields: opFields(canEdit), values: o || { sequence: (cur.operations.at(-1)?.sequence || 0) + 1, norm_per_day: 10, min_skill: 1, per_item: 1, hold_qty: 0 },
    onSubmit: async (vals, f) => { const body = { ...vals, hold_qty: vals.hold_qty || 0, hold_reason: vals.hold_reason || '' }; if (o) await PATCH(`/operations/${o.id}`, { ...body, version: o.version }); else await POST(`/routings/${cur.id}/operations`, body); f.close(); again(); }
  });
  const oa = $('[data-oa]', m.el); if (oa) oa.onclick = () => edit(null);
  $$('[data-oe]', m.el).forEach(b => b.onclick = () => edit(cur.operations.find(o => o.id == b.dataset.oe)));
  $$('[data-od]', m.el).forEach(b => b.onclick = async () => { if (await confirmBox('Удалить операцию?')) { try { await DEL(`/operations/${b.dataset.od}`); again(); } catch (e) { toast(errText(e), true); } } });
}

/* ---------- допуски и отпуска исполнителя ---------- */
async function openAccess(w, refs) {
  const { order_line_ids: cur } = await GET(`/workers/${w.id}/access`), set = new Set(cur), planned = refs.orders.filter(o => o.status !== 'cancelled');
  const m = modal(`<h2>Допуски: ${esc(w.name)}</h2><p class="muted">Отметьте позиции, к которым допущен исполнитель. Если ничего не отмечено — допущен ко всем.</p>
    <div style="max-height:50vh;overflow:auto">${planned.map(o => `<label class="chk"><input type="checkbox" value="${o.id}" ${set.has(o.id) ? 'checked' : ''}> <span>${esc(o.ext_id || '#' + o.id)} — ${esc(refs.PR[o.product_id]?.name)} (${esc(refs.P[o.project_id]?.name)})</span></label>`).join('') || '<p class="muted">Позиций нет</p>'}</div>
    <div class="row"><button data-s>Сохранить</button><button class="secondary" data-x>Отмена</button></div>`);
  $('[data-x]', m.el).onclick = m.close;
  $('[data-s]', m.el).onclick = async () => { try { await PUT(`/workers/${w.id}/access`, { order_line_ids: $$('input:checked', m.el).map(i => +i.value) }); m.close(); toast('Допуски сохранены'); } catch (e) { toast(errText(e), true); } };
}
async function openAbsences(w) {
  const all = (await listAll('/absences')).filter(a => a.worker_id === w.id).sort((a, b) => a.date_from < b.date_from ? 1 : -1);
  const m = modal(`<h2>Отпуска и отсутствия: ${esc(w.name)}</h2>${table([{ label: 'С', get: a => fmtDY(a.date_from) }, { label: 'По', get: a => fmtDY(a.date_to) }, { label: 'Причина', get: a => a.reason }], all, a => `<button class="small danger" data-ad="${a.id}">Удалить</button>`)}
    <h3>Добавить</h3><div class="row"><label class="f">С<input type="date" id="af"></label><label class="f">По<input type="date" id="at"></label><label class="f grow">Причина<input id="ar"></label></div>
    <div class="err" id="ae" role="alert"></div><div class="row"><button data-a>Добавить</button><button class="secondary" data-x>Закрыть</button></div>`);
  $('[data-x]', m.el).onclick = m.close;
  $$('[data-ad]', m.el).forEach(b => b.onclick = async () => { await DEL(`/absences/${b.dataset.ad}`); m.close(); openAbsences(w); });
  $('[data-a]', m.el).onclick = async () => {
    try { await POST('/absences', { worker_id: w.id, date_from: $('#af', m.el).value, date_to: $('#at', m.el).value, reason: $('#ar', m.el).value }); m.close(); toast('Добавлено — пересчитайте план'); openAbsences(w); }
    catch (e) { $('#ae', m.el).textContent = errText(e); }
  };
}

/* ---------- настройки планировщика ---------- */
SCREENS.settings = async v => {
  const s = await GET('/settings'), days = new Set(s.work_weekdays.split(',').map(Number)), WDN = [['1', 'Пн'], ['2', 'Вт'], ['3', 'Ср'], ['4', 'Чт'], ['5', 'Пт'], ['6', 'Сб'], ['0', 'Вс']];
  const F = [
    { name: 'priority_strictness', label: 'Жёсткость приоритета по умолчанию, %', type: 'number', min: 0, max: 100, step: 10, help: '100 — следующий приоритет ждёт окончания предыдущего; 0 — приоритет влияет только на очерёдность.' },
    { name: 'max_workers_per_op', label: 'Максимум исполнителей на одну операцию', type: 'number', min: 1, step: 1, help: 'Больше людей — быстрее, но больше переключений.' },
    { name: 'freeze_days', label: 'Фиксировать план на ближайшие N рабочих дней', type: 'number', min: 0, max: 5, step: 1, help: '2 — задания на сегодня и завтра не меняются при пересчёте. 0 — всё пересчитывается.' },
    { name: 'today_load_threshold', label: 'Порог загрузки «сегодня», доля дня', type: 'number', min: 0, max: 1, step: 0.05, help: 'Кто уже выработал столько сегодня, новых заданий на сегодня не получает.' },
    { name: 'min_start_share', label: 'Минимальная доля старта операции', type: 'number', min: 0.1, max: 1, step: 0.05, help: 'Операцию начинают, если в этот день можно сделать хотя бы эту долю одного изделия.' },
    { name: 'efficiency', label: 'Коэффициент эффективности', type: 'number', min: 0.1, max: 1.5, step: 0.05, help: '1 — нормы как заданы. 0,85 — учесть, что полный день работают не на 100%.' },
    { name: 'shift_hours', label: 'Длительность смены, ч', type: 'number', min: 1, max: 24, step: 0.5, help: 'Только для отображения.' },
    { name: 'fact_confirmation', label: 'Факт сборщика подтверждает бригадир', type: 'checkbox', help: 'Пока не подтверждён, факт виден как «ждёт подтверждения», но в расчёт идёт.' },
    { name: 'fix_group_start', label: 'Не начинать следующую операцию раньше окончания предыдущей у опаздывающих заказов', type: 'checkbox', help: 'Исправляет особенность прежнего скрипта (D-04). По умолчанию выключено — как в скрипте.' }];
  v.innerHTML = `<h1>Настройки планировщика</h1><div class="card" style="max-width:640px"><form id="sf" novalidate>${F.map(f => fieldHtml(f, s[f.name])).join('')}
    <label class="f">Жёсткость по уровням приоритета (например «2:60; 3:30»)<input name="lv" value="${esc(Object.entries(s.strictness_by_level || {}).map(([k, x]) => k + ':' + x).join('; '))}"></label>
    <fieldset style="border:1px solid var(--line);border-radius:6px;margin-bottom:10px"><legend class="muted">Рабочие дни недели</legend><div class="row">${WDN.map(([k, l]) => `<label class="chk"><input type="checkbox" name="wd" value="${k}" ${days.has(+k) ? 'checked' : ''}> ${l}</label>`).join('')}</div></fieldset>
    <div class="err" id="se" role="alert"></div><button type="submit">Сохранить</button></form></div>`;
  $('#sf').onsubmit = async e => {
    e.preventDefault(); const form = e.target, body = readForm(form, F);
    body.strictness_by_level = {}; let bad = false;
    for (const part of form.elements.lv.value.split(/[;,]/).map(x => x.trim()).filter(Boolean)) { const m = part.match(/^(\d+)\s*:\s*([\d.,]+)$/); if (m) body.strictness_by_level[m[1]] = Number(m[2].replace(',', '.')); else bad = true; }
    if (bad) { $('#se').textContent = 'Жёсткость по уровням: формат «2:60; 3:30»'; return; }
    body.work_weekdays = $$('[name=wd]:checked', form).map(i => i.value).join(',');
    if (!body.work_weekdays) { $('#se').textContent = 'Отметьте хотя бы один рабочий день'; return; }
    try { await PATCH('/settings', body); $('#se').textContent = ''; toast('Настройки сохранены. Пересчитайте план.'); } catch (x) { $('#se').textContent = errText(x); }
  };
};

/* ---------- пользователи ---------- */
SCREENS.users = async v => {
  const [users, centers, workers] = await Promise.all([GET('/users'), listAll('/work-centers'), listAll('/workers')]);
  const roleOpts = Object.entries(ROLE_RU).map(([v, l]) => ({ v, l }));
  const fields = (isNew) => [
    ...(isNew ? [{ name: 'email', label: 'E-mail', type: 'email', required: true }] : []), { name: 'name', label: 'Имя', required: true },
    { name: 'role', label: 'Роль', type: 'select', required: true, options: roleOpts, help: 'Бригадиру нужен участок, сборщику — исполнитель.' },
    { name: 'work_center_id', label: 'Участок (для бригадира)', type: 'select', options: opts(centers), num: true },
    { name: 'worker_id', label: 'Исполнитель (для сборщика)', type: 'select', options: opts(workers), num: true },
    ...(isNew ? [{ name: 'send_invite', label: 'Отправить приглашение по e-mail', type: 'checkbox', help: 'Человек сам задаст пароль по ссылке из письма (действует 7 суток). Тогда пароль ниже не нужен.' },
      { name: 'password', label: 'Или задайте временный пароль (не короче 8)', type: 'password' }] : [{ name: 'is_active', label: 'Доступ разрешён', type: 'checkbox' }, { name: 'password', label: 'Новый пароль (если нужно сбросить)', type: 'password' }])];
  const rowName = u => u.work_center_id ? ' · ' + (centers.find(c => c.id === u.work_center_id)?.name || '') : (u.worker_id ? ' · ' + (workers.find(w => w.id === u.worker_id)?.name || '') : '');
  v.innerHTML = `<div class="row" style="margin-bottom:10px"><h1 class="grow" style="margin:0">Пользователи</h1><button id="add">+ Добавить</button></div>
    <div class="card scroll">${table([{ label: 'Имя', get: u => u.name }, { label: 'E-mail', get: u => u.email }, { label: 'Роль', get: u => ROLE_RU[u.role] + rowName(u) }, { label: 'Доступ', html: u => u.is_active ? '<span class="ok">разрешён</span>' : '<span class="err">закрыт</span>' }], users, u => `<button class="small secondary" data-e="${u.id}">Изменить</button>${u.id !== S.me.id ? ` <button class="small secondary" data-inv="${u.id}">Пригласить снова</button>` : ''}${u.role === 'foreman' ? ` <button class="small" data-tgu="${u.id}">Telegram</button>` : ''}`)}</div>
    <p class="muted">Права: владелец — всё, включая пользователей; руководитель — данные, настройки и планирование; бригадир — свой участок и факт; сборщик — только свои задания и факт; наблюдатель — только просмотр отчётов.</p>`;
  $('#add').onclick = () => formModal({ title: 'Новый пользователь', fields: fields(true), values: { role: 'viewer' }, onSubmit: async (vals, m) => { await POST('/users', vals); m.close(); toast(vals.send_invite ? 'Приглашение отправлено' : 'Пользователь создан'); SCREENS.users(v); } });
  $$('[data-inv]', v).forEach(b => b.onclick = async () => { try { await POST(`/users/${b.dataset.inv}/invite`); toast('Приглашение отправлено'); } catch (e) { toast(errText(e), true); } });
  $$('[data-tgu]', v).forEach(b => b.onclick = () => openTelegram('user', +b.dataset.tgu, users.find(x => x.id == b.dataset.tgu).name));
  $$('[data-e]', v).forEach(b => b.onclick = () => { const u = users.find(x => x.id == b.dataset.e); formModal({ title: u.name, fields: fields(false), values: u, onSubmit: async (vals, m) => { if (!vals.password) delete vals.password; await PATCH('/users/' + u.id, vals); m.close(); toast('Сохранено'); SCREENS.users(v); } }); });
};

/* ---------- журнал ---------- */
const ENT = { settings: 'Настройки', order_line: 'Позиция заказа', worker: 'Исполнитель', fact: 'Факт', plan: 'План', user: 'Пользователь', product: 'Изделие', operation: 'Операция', routing: 'Маршрут', project: 'Проект', worker_access: 'Допуски', absence: 'Отсутствие', holiday: 'Праздник', work_center: 'Участок', fact_reason: 'Причина', tenant: 'Организация', demo: 'Демо' };
const ACT = { create: 'создание', update: 'изменение', delete: 'удаление', replan: 'перепланирование', activate: 'откат плана', confirm: 'подтверждение', register: 'регистрация', load: 'загрузка', password_change: 'смена пароля' };
SCREENS.audit = async v => {
  let before = 0, all = [];
  const draw = async () => {
    const r = await GET('/audit?limit=100' + (before ? '&before=' + before : '')); all = all.concat(r.items); before = r.next_before;
    v.innerHTML = `<h1>Журнал изменений</h1><div class="card scroll">${table([{ label: 'Когда', get: a => fmtDT(a.at) }, { label: 'Кто', get: a => a.user || '—' }, { label: 'Что', get: a => (ENT[a.entity] || a.entity) + ' ' + (a.entity_id ?? '') }, { label: 'Действие', get: a => ACT[a.action] || a.action },
    { label: 'Детали', html: a => `<small class="muted">${esc(JSON.stringify(a.changes)).slice(0, 160)}</small>` }], all)}</div>${before ? '<button class="secondary" id="more">Показать ещё</button>' : ''}`;
    if ($('#more')) $('#more').onclick = draw;
  };
  await draw();
};

/* ---------- Telegram: привязка исполнителя или бригадира (код выдаёт руководитель) ---------- */
async function openTelegram(kind, id, name, after) {
  const st = await GET('/telegram/status');
  const link = st.links.find(l => kind === 'worker' ? l.worker_id === id : l.user_id === id);
  let body;
  if (!st.configured) body = '<p class="banner warn">Telegram-бот ещё не подключён администратором сервиса (нужны токен бота и секрет вебхука).</p>';
  else body = `<p class="muted">Человек открывает бота, отправляет код — и получает задания дня, вносит факт, а вечером — напоминание. Пароль не нужен. Код одноразовый, действует 24 часа.</p>
    ${link ? `<p class="ok">✔ Привязан ${fmtDT(link.linked_at)}</p>` : '<p>Пока не привязан.</p>'}
    <div class="row"><button data-code>${link ? 'Выдать новый код (новый телефон)' : 'Выдать код'}</button>${link ? '<button class="danger" data-un>Отвязать</button>' : ''}</div><div id="tgc" style="margin-top:12px" aria-live="polite"></div>`;
  const m = modal(`<h2>Telegram: ${esc(name)}</h2>${body}<div class="row" style="margin-top:12px"><button class="secondary" data-x>Закрыть</button></div>`);
  $('[data-x]', m.el).onclick = m.close;
  const code = $('[data-code]', m.el), un = $('[data-un]', m.el);
  if (code) code.onclick = async () => {
    try {
      const r = await POST('/telegram/codes', kind === 'worker' ? { worker_id: id } : { user_id: id });
      $('#tgc', m.el).innerHTML = `<div class="card" style="margin:0"><div style="font-size:28px;font-weight:700;letter-spacing:2px">${esc(r.code)}</div>
        <p style="margin:8px 0">${esc(r.instruction)}</p>${r.deep_link ? `<p><a href="${esc(r.deep_link)}" target="_blank" rel="noopener">Открыть бота по ссылке</a></p>` : ''}
        <p class="muted">Действует ${r.expires_in_hours} ч. Передайте код лично — он работает один раз.</p></div>`;
    } catch (e) { toast(errText(e), true); }
  };
  if (un) un.onclick = async () => { if (!await confirmBox('Отвязать Telegram? Человек перестанет получать задания и вносить факт через бота.')) return; try { await DEL('/telegram/links/' + link.id); m.close(); toast('Отвязано'); if (after) after(); } catch (e) { toast(errText(e), true); } };
}
