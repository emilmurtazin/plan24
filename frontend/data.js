/* Plan 24 — экраны ввода данных, настроек, пользователей, журнала. Использует ядро из app.js. */
'use strict';

async function loadRefs() {
  const [projects, products, workers, centers, orders, reasons, routings] = await Promise.all(
    ['/projects', '/products', '/workers', '/work-centers', '/order-lines', '/fact-reasons', '/routings'].map(listAll));
  const by = a => Object.fromEntries(a.map(x => [x.id, x]));
  const tg = await GET('/telegram/status').catch(() => ({ configured: false, links: [] }));
  const dist = Object.fromEntries(((await GET('/order-distribution').catch(() => ({ items: [] }))).items || []).map(x => [x.order_line_id, x]));
  const quals = (await GET('/settings').catch(() => ({ qualifications: [] }))).qualifications || [];
  return { dist, quals, projects, products, workers, centers, orders, reasons, routings, tg, TGW: Object.fromEntries(tg.links.filter(l => l.worker_id).map(l => [l.worker_id, l])),
    P: by(projects), PR: by(products), W: by(workers), C: by(centers) };
}
/* Уровень квалификации: название из настроек организации, иначе число */
const qn = (quals, n) => (Number.isInteger(+n) && quals && quals[n - 1]) || num(n);
const qOpts = quals => (quals || []).map((l, i) => ({ v: i + 1, l: `${i + 1} — ${l}` }));
const verLabel = v => 'v' + v.version_no + (v.name ? ' · ' + v.name : '');
const opts = (arr, label = x => x.name) => arr.map(x => ({ v: x.id, l: label(x) }));
const ORDER_ST = { draft: 'Черновик', planned: 'В плане', done: 'Выполнен', cancelled: 'Отменён' };
const table = (cols, rows, actions) => `<table><thead><tr>${cols.map(c => `<th class="${c.cls || ''}">${esc(c.label)}</th>`).join('')}${actions ? '<th></th>' : ''}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => `<td class="${c.cls || ''}">${c.html ? c.html(r) : esc(c.get(r))}</td>`).join('')}${actions ? `<td class="act">${actions(r)}</td>` : ''}</tr>`).join('') || `<tr><td colspan="${cols.length + 1}" class="empty">Пока пусто</td></tr>`}</tbody></table>`;

/* Универсальная вкладка «список + добавить/изменить/удалить» */
async function crudTab(box, cfg, reload) {
  const refs = await loadRefs(), rows = await listAll(cfg.path);
  const acts = r => `${cfg.extra ? cfg.extra(r) : ''}<button class="small secondary" data-e="${r.id}">Изменить</button> <button class="small danger" data-d="${r.id}">Удалить</button>`;
  box.innerHTML = `<div class="row" style="margin-bottom:10px"><h2 class="grow" style="margin:0">${esc(cfg.title)}</h2>${cfg.headerExtra || ''}${cfg.noAdd ? '' : `<button data-add>+ ${esc(cfg.add)}</button>`}</div>${cfg.hint ? `<p class="hint">${esc(cfg.hint(refs, rows))}</p>` : ''}<div class="card flush scroll">${table(cfg.cols(refs), rows, acts)}</div>`;
  const open = (row) => {
    const fields = (row && cfg.editFields ? cfg.editFields(refs) : cfg.fields(refs));
    formModal({
      title: row ? cfg.editTitle || 'Изменить' : cfg.add, fields, values: row || cfg.defaults || {}, onOpen: cfg.wire ? form => cfg.wire(form, refs, row) : undefined,
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
      title: 'Заказы (позиции)', add: 'Добавить позицию', path: '/order-lines', defaults: { qty: 1, priority: 1, status: 'planned' },
      hint: (r) => r.products.length && r.projects.length ? 'Позиция = изделие в нужном количестве к сроку. Можно выбрать версию изделия; если не выбрать — берётся текущая. Уже созданные позиции остаются на своей версии.' : 'Сначала добавьте проект (вкладка «Проекты») и изделие с операциями («Каталог изделий»).',
      cols: r => [{ label: '№', get: x => x.ext_id || '#' + x.id }, { label: 'Проект', get: x => r.P[x.project_id]?.name }, { label: 'Заказчик', get: x => x.customer || r.P[x.project_id]?.customer || '—' },
      { label: 'Изделие', get: x => r.PR[x.product_id]?.name }, { label: 'Версия', get: x => { const v = r.routings.find(q => q.id === x.routing_id); return v ? verLabel(v) : '—'; } },
      { label: 'Кол-во', cls: 'num', get: x => num(x.qty) }, { label: 'Срок', get: x => fmtDY(x.due_date) }, { label: 'Приоритет', cls: 'num', get: x => x.priority }, { label: 'Статус', html: x => `<span class="badge ${{ planned: 'b-work', done: 'b-done', cancelled: 'b-noop', draft: 'b-none' }[x.status] || 'b-noop'}">${esc(ORDER_ST[x.status])}</span>` },
      ...(r.centers.length ? [{ label: 'Операции по участкам', html: x => { const q = r.dist[x.id] || { total: 0, distributed: 0 }; return q.distributed >= q.total && q.total ? `<span class="badge b-done">${q.distributed} из ${q.total}</span>` : `<span class="badge b-risk">${q.distributed} из ${q.total} · не распределено</span>`; } }] : [])],
      extra: x => `<button class="small" data-dist="${x.id}">По участкам</button> `,
      after: (box, refs, rows, reload) => $$('[data-dist]', box).forEach(b => b.onclick = () => openDistribution(rows.find(x => x.id == b.dataset.dist), refs, reload)),
      fields: r => [{ name: 'project_id', label: 'Проект', type: 'select', options: opts(r.projects), required: true, num: true },
      { name: 'product_id', label: 'Изделие', type: 'select', options: opts(r.products), required: true, num: true },
      { name: 'routing_id', label: 'Версия изделия', type: 'select', options: [], num: true, blank: 'Текущая версия' },
      { name: 'customer', label: 'Заказчик', help: 'Подставляется из проекта, можно изменить' },
      { name: 'ext_id', label: 'Номер позиции (необязательно)' }, { name: 'qty', label: 'Количество', type: 'number', required: true, min: 0, step: 'any' },
      { name: 'due_date', label: 'Срок', type: 'date' }, { name: 'priority', label: 'Приоритет (1 — самый важный)', type: 'number', required: true, min: 1, step: 1 },
      { name: 'status', label: 'Статус', type: 'select', required: true, options: Object.entries(ORDER_ST).map(([v, l]) => ({ v, l })) }],
      editFields: r => [{ name: 'project_id', label: 'Проект', type: 'select', options: opts(r.projects), required: true, num: true },
      { name: 'routing_id', label: 'Версия изделия', type: 'select', options: [], num: true, required: true, help: 'После смены версии пересчитайте план' },
      { name: 'customer', label: 'Заказчик' }, { name: 'ext_id', label: 'Номер позиции' },
      { name: 'qty', label: 'Количество', type: 'number', required: true, min: 0, step: 'any' }, { name: 'due_date', label: 'Срок', type: 'date' },
      { name: 'priority', label: 'Приоритет', type: 'number', required: true, min: 1, step: 1 },
      { name: 'status', label: 'Статус', type: 'select', required: true, options: Object.entries(ORDER_ST).map(([v, l]) => ({ v, l })) }],
      // версии зависят от выбранного изделия; заказчик по умолчанию берётся из проекта
      wire: (form, r, row) => {
        const prod = form.elements.product_id, ver = form.elements.routing_id, proj = form.elements.project_id, cust = form.elements.customer;
        const fillVersions = (pid, sel) => {
          const list = r.routings.filter(q => q.product_id === +pid).sort((x, y) => y.version_no - x.version_no);
          ver.innerHTML = (row ? '' : '<option value="">Текущая версия</option>') + list.map(q => `<option value="${q.id}" ${String(q.id) === String(sel) ? 'selected' : ''}>${esc(verLabel(q))}${q.is_current ? ' · текущая' : ''} (операций: ${q.operations})</option>`).join('');
        };
        fillVersions(row ? row.product_id : prod?.value, row ? row.routing_id : '');
        if (prod) prod.onchange = () => fillVersions(prod.value, '');
        let last = r.P[proj.value]?.customer || '';
        if (!row && cust && !cust.value) cust.value = last;
        proj.addEventListener('change', () => { const c = r.P[proj.value]?.customer || ''; if (!cust.value || cust.value === last) cust.value = c; last = c; });
      }
    }
  },
  projects: {
    label: 'Проекты', cfg: {
      title: 'Проекты', add: 'Добавить проект', path: '/projects', cols: () => [{ label: 'Название', get: x => x.name }, { label: 'Заказчик', get: x => x.customer }],
      fields: () => [nameField, { name: 'customer', label: 'Заказчик' }]
    }
  },
  products: {
    label: 'Каталог изделий', cfg: {
      title: 'Изделия', add: 'Добавить изделие', path: '/products', headerExtra: '<button class="secondary" data-import>Загрузить из файла</button>',
      cols: r => {
        const cur = x => r.routings.find(q => q.product_id === x.id && q.is_current);
        return [{ label: 'Изделие', get: x => x.name }, { label: 'Ед.', get: x => x.unit },
        { label: 'Текущая версия', get: x => cur(x) ? verLabel(cur(x)) : '—' }, { label: 'Версий', cls: 'num', get: x => r.routings.filter(q => q.product_id === x.id).length },
        { label: 'Операций', cls: 'num', get: x => cur(x) ? cur(x).operations : 0 }];
      },
      hint: () => 'У изделия может быть несколько версий маршрута; у каждой — свой список операций с нормами и требуемой квалификацией. В заказ берётся выбранная версия. «Операций» — в текущей версии.',
      fields: () => [nameField, { name: 'unit', label: 'Единица', required: true }], defaults: { unit: 'шт' },
      extra: r => `<button class="small" data-rt="${r.id}">Версии и операции</button> `,
      after: (box, refs, rows, reload) => { $$('[data-rt]', box).forEach(b => b.onclick = () => openRouting(refs.PR[b.dataset.rt], undefined, reload)); $('[data-import]', box).onclick = () => importCatalog(refs, reload); }
    }
  },
  workers: {
    label: 'Работники', cfg: {
      title: 'Работники', add: 'Добавить работника', path: '/workers', defaults: { perf: 1, skill: 1, status: 'available', auto_plan: true },
      hint: () => 'Производительность: 1 — норма, 1,2 — на 20% быстрее. Квалификация — допуск к операциям с требуемым уровнем. «Допуски» ограничивают позиции, к которым допущен человек (пусто = ко всем). Укажите участок: перепланирование идёт по участкам, и работник без участка в него не попадает.',
      cols: r => [{ label: 'Имя', html: x => esc(x.name) + (x.is_temporary ? ' <span class="badge b-noop">временный</span>' : '') }, { label: 'Участок', get: x => r.C[x.work_center_id]?.name || '—' }, { label: 'Производит.', cls: 'num', get: x => num(x.perf) },
      { label: 'Квалификация', get: x => qn(r.quals, x.skill) }, { label: 'Планирование', html: x => x.auto_plan ? 'автоматически' : '<span class="warn">только вручную</span>' }, { label: 'Статус', html: x => x.status === 'available' ? '<span class="badge b-done">Доступен</span>' : '<span class="badge b-none">Недоступен</span>' },
      { label: 'Telegram', html: x => r.TGW[x.id] ? '<span class="ok">✔ привязан</span>' : '<span class="muted">—</span>' }],
      fields: r => [nameField, { name: 'work_center_id', label: 'Участок', type: 'select', options: opts(r.centers), num: true },
        { name: 'perf', label: 'Производительность', type: 'number', required: true, min: 0.1, max: 10, step: 0.05 },
        { name: 'skill', label: 'Квалификация', type: 'select', options: qOpts(r.quals), required: true, num: true, help: 'Уровни настраиваются в «Организация → Об организации»' },
        { name: 'status', label: 'Статус', type: 'select', required: true, options: [{ v: 'available', l: 'Доступен' }, { v: 'unavailable', l: 'Недоступен' }] },
        { name: 'auto_plan', label: 'Участвует в автопланировании', type: 'checkbox', help: 'Выключено — планировщик не даёт работнику заказы сам; задания ему ставят вручную в «Выполнение → План на день». Так добавляются временные работники.' }],
      extra: r => `<button class="small" data-ac="${r.id}">Допуски</button> <button class="small" data-ab="${r.id}">Отпуска</button> <button class="small" data-tg="${r.id}">Telegram</button> `,
      after: (box, refs, rows, reload) => { $$('[data-tg]', box).forEach(b => b.onclick = () => openTelegram('worker', +b.dataset.tg, refs.W[b.dataset.tg].name, reload)); $$('[data-ac]', box).forEach(b => b.onclick = () => openAccess(refs.W[b.dataset.ac], refs)); $$('[data-ab]', box).forEach(b => b.onclick = () => openAbsences(refs.W[b.dataset.ab])); }
    }
  },
  centers: {
    label: 'Участки', cfg: {
      title: 'Участки', add: 'Добавить участок', path: '/work-centers', cols: () => [{ label: 'Название', get: x => x.name }], fields: () => [nameField],
      headerExtra: '<button class="secondary" data-settings>Настройки</button>',
      hint: () => 'Кнопка «Настройки» — параметры планировщика (жёсткость приоритета, рабочие дни, заморозка плана и др.).',
      after: (box, refs, rows, reload) => { $('[data-settings]', box).onclick = () => SCREENS.settings(box, reload); }
    }
  },
  holidays: {
    label: 'Календарь', cfg: {
      title: 'Праздники и нерабочие дни', add: 'Добавить день', path: '/holidays', hint: () => 'Рабочие дни недели настраиваются в «Работники → Участки → Настройки». Здесь — дополнительные нерабочие даты.',
      cols: () => [{ label: 'Дата', get: x => fmtDY(x.holiday_date) }, { label: 'Название', get: x => x.name }],
      fields: () => [{ name: 'holiday_date', label: 'Дата', type: 'date', required: true }, { name: 'name', label: 'Название' }], editFields: () => [{ name: 'name', label: 'Название' }]
    }
  },
  reasons: { label: 'Причины отклонений', cfg: { title: 'Причины отклонений от плана', add: 'Добавить причину', path: '/fact-reasons', cols: () => [{ label: 'Причина', get: x => x.name }], fields: () => [nameField] } },
};

/* Группа вкладок: шапка + переключатель + контейнер. Вкладка — либо CRUD-таблица из TABS, либо свой экран (custom). */
function tabsScreen(v, { title, tabs, tab, key, self, def }) {
  S.nav++;
  const ids = Object.keys(tabs); tab = ids.includes(tab) ? tab : (ids.includes(S[key]) ? S[key] : (ids.includes(def) ? def : ids[0])); S[key] = tab;
  v.innerHTML = `<h1>${esc(title)}</h1>${ids.length > 1 ? `<nav class="seg" aria-label="Вкладки раздела">${ids.map(k => `<button data-t="${k}" ${k === tab ? 'aria-current="page"' : ''}>${esc(tabs[k].label)}</button>`).join('')}</nav>` : ''}<div id="dt"></div>`;
  $$('[data-t]', v).forEach(b => b.onclick = () => self(v, b.dataset.t));
  const box = $('#dt', v), t = tabs[tab];
  const fail = e => { if (!(e instanceof Stale)) box.innerHTML = `<div class="card err">${esc(errText(e))}</div>`; };
  const reload = () => (t.custom ? t.custom(box, reload) : crudTab(box, t.cfg, reload)).catch(fail);
  reload();
}

/* Проекты и Заказы */
SCREENS.data = (v, tab) => tabsScreen(v, { title: 'Проекты и Заказы', tabs: { projects: TABS.projects, orders: TABS.orders }, tab, key: 'dataTab', self: SCREENS.data });

/* Каталог изделий: изделия, версии, операции (создают и редактируют владелец, руководитель, технолог) */
SCREENS.catalog = async v => {
  S.nav++;
  v.innerHTML = '<h1>Каталог изделий</h1><div id="dt"></div>';
  const box = $('#dt', v), reload = () => crudTab(box, TABS.products.cfg, reload).catch(e => { if (!(e instanceof Stale)) box.innerHTML = `<div class="card err">${esc(errText(e))}</div>`; });
  reload();
};

/* Организация: сведения, календарь, пользователи, журнал. Участки, работники и причины отклонений — в разделе «Работники» */
const ORG_TABS = {
  info: { label: 'Об организации', custom: async (box, reload) => {
    const t = S.me.tenant || {}, [centers, workers, st] = await Promise.all([listAll('/work-centers'), listAll('/workers'), GET('/settings')]);
    const users = S.me.role === 'owner' ? await GET('/users') : null;
    const quals = [...(st.qualifications || [])];
    const used = {}; workers.forEach(w => { used[w.skill] = (used[w.skill] || 0) + 1; });
    const lvl = (l, i) => `<div class="row" style="margin-bottom:6px"><b style="min-width:24px">${i + 1}</b><input data-q="${i}" value="${esc(l)}" maxlength="40" aria-label="Название уровня ${i + 1}" style="flex:1">${used[i + 1] ? `<span class="muted">работников: ${used[i + 1]}</span>` : ''}</div>`;
    box.innerHTML = `<h2>Об организации</h2><div class="card" style="max-width:640px"><table>
      <tr><th>Название</th><td>${esc(t.name)}</td></tr><tr><th>Часовой пояс</th><td>${esc(t.timezone)}</td></tr>
      <tr><th>Участков</th><td>${centers.length}</td></tr><tr><th>Работников</th><td>${workers.length}</td></tr>${users ? `<tr><th>Пользователей</th><td>${users.length}</td></tr>` : ''}
      <tr><th>Вы вошли как</th><td>${esc(S.me.name)} · ${ROLE_RU[S.me.role]}</td></tr></table></div>
      <h2 style="margin-top:20px">Квалификации</h2><div class="card" style="max-width:640px">
      <p class="muted" style="margin-top:0">Уровни квалификации организации — от низшего к высшему. Работнику назначается уровень, операции — требуемый; работник допускается к операции, если его уровень не ниже требуемого. Число уровней — от 1 до 10.</p>
      <div id="ql">${quals.map(lvl).join('')}</div>
      <div class="err" id="qe" role="alert"></div>
      <div class="row"><button class="secondary" id="qa" ${quals.length >= 10 ? 'disabled' : ''}>+ Уровень</button><button class="secondary" id="qd" ${quals.length <= 1 ? 'disabled' : ''}>Убрать последний</button><span class="grow"></span><button id="qs">Сохранить уровни</button></div></div>`;
    const read = () => $$('[data-q]', box).map(i => i.value);
    const redraw = list => { quals.length = 0; quals.push(...list); $('#ql', box).innerHTML = quals.map(lvl).join(''); $('#qa', box).disabled = quals.length >= 10; $('#qd', box).disabled = quals.length <= 1; };
    $('#qa', box).onclick = () => redraw([...read(), 'Уровень ' + (quals.length + 1)]);
    $('#qd', box).onclick = () => redraw(read().slice(0, -1));
    $('#qs', box).onclick = async () => {
      try { await PATCH('/settings', { qualifications: read() }); toast('Уровни квалификации сохранены'); reload(); }
      catch (e) { $('#qe', box).textContent = errText(e); }
    };
  } },
  holidays: TABS.holidays,
  users: { label: 'Пользователи', owner: true, custom: box => SCREENS.users(box) },
  audit: { label: 'Журнал', custom: box => SCREENS.audit(box) },
};
/* Работники: участки (+ настройки планировщика), работники, причины отклонений */
SCREENS.people = (v, tab) => tabsScreen(v, { title: 'Работники', tabs: { centers: TABS.centers, workers: TABS.workers, reasons: TABS.reasons }, tab, key: 'peopleTab', self: SCREENS.people, def: 'workers' });

SCREENS.org = (v, tab) => {
  const tabs = Object.fromEntries(Object.entries(ORG_TABS).filter(([, t]) => !t.owner || S.me.role === 'owner'));
  return tabsScreen(v, { title: 'Организация', tabs, tab, key: 'orgTab', self: SCREENS.org });
};

/* ---------- маршрут изделия: версии и операции ---------- */
async function openRouting(product, selId, onClose) {
  const [rts, st] = await Promise.all([GET(`/products/${product.id}/routings`), GET('/settings')]);
  const quals = st.qualifications || [];
  const cur = rts.find(r => r.id === selId) || rts[0];
  const canEdit = !cur.used;
  const m = modal(`<h2>${esc(product.name)}: версии и операции</h2>
    <div class="row" style="margin-bottom:10px">${rts.map(r => `<button class="small ${r.id === cur.id ? '' : 'secondary'}" data-v="${r.id}">${esc(verLabel(r))}${r.is_current ? ' · текущая' : ''}</button>`).join('')}<span class="grow"></span><button class="small secondary" data-nv>Новая версия</button></div>
    <div class="row" style="margin-bottom:8px"><b>Версия: ${esc(verLabel(cur))}</b> <span class="muted">· операций: ${cur.operations.length}</span><span class="grow"></span><button class="small secondary" data-rn>Переименовать версию</button></div>
    <p class="muted">${cur.used ? 'Версия используется в заказах: структуру (порядок, норму, квалификацию, количество на изделие) менять нельзя, чтобы не сломать идущие заказы. Можно переименовать версию и операции и изменить блокировку. Для остального создайте новую версию.' : 'Одинаковый «Порядок» — операции идут параллельно. Норма — сколько изделий делает работник с производительностью 1 за полный рабочий день. «На изделие» — сколько раз операцию выполняют на одно изделие.'}</p>
    <div class="scroll">${table([{ label: 'Порядок', cls: 'num', get: o => num(o.sequence) }, { label: 'Операция', get: o => o.name }, { label: 'Норма/день', cls: 'num', get: o => num(o.norm_per_day) }, { label: 'Требуемая квалификация', get: o => qn(quals, o.min_skill) }, { label: 'На изделие', cls: 'num', get: o => num(o.per_item) },
    { label: 'Блок', html: o => o.hold_qty ? `<span class="warn">${num(o.hold_qty)}${o.hold_until ? ' до ' + fmtDY(o.hold_until) : ''}</span> ${esc(o.hold_reason)}` : '—' }], cur.operations,
      o => `<button class="small secondary" data-oe="${o.id}">${canEdit ? 'Изменить' : 'Название / блок'}</button>${canEdit ? ` <button class="small danger" data-od="${o.id}">Удалить</button>` : ''}`)}</div>
    <div class="row" style="margin-top:12px">${canEdit ? '<button data-oa>+ Операция</button>' : ''}<button class="secondary" data-x>Закрыть</button></div>`, true);
  const done = () => { m.close(); if (onClose) onClose(); };
  const again = () => { m.close(); openRouting(product, cur.id, onClose); };
  $('[data-x]', m.el).onclick = done;
  $$('[data-v]', m.el).forEach(b => b.onclick = () => { m.close(); openRouting(product, +b.dataset.v, onClose); });
  $('[data-nv]', m.el).onclick = () => formModal({
    title: 'Новая версия', submit: 'Создать', fields: [{ name: 'name', label: 'Название версии (необязательно)', help: 'Например: «Усиленная» или «2026». Операции копируются из текущей версии.' }], values: {},
    onSubmit: async (vals, f) => { const r = await POST(`/products/${product.id}/routings` + (vals.name ? '?name=' + encodeURIComponent(vals.name) : '')); f.close(); m.close(); toast(`Создана версия v${r.version_no}`); openRouting(product, r.id, onClose); }
  });
  $('[data-rn]', m.el).onclick = () => formModal({
    title: 'Переименовать версию v' + cur.version_no, fields: [{ name: 'name', label: 'Название версии', help: 'Пустое название — версия называется просто «v' + cur.version_no + '»' }], values: { name: cur.name },
    onSubmit: async (vals, f) => { await PATCH(`/routings/${cur.id}`, { name: vals.name || '' }); f.close(); toast('Название сохранено'); again(); }
  });
  const opFields = (full) => [{ name: 'name', label: 'Операция', required: true },
  ...(full ? [{ name: 'sequence', label: 'Порядок', type: 'number', required: true, min: 0, step: 'any' },
    { name: 'norm_per_day', label: 'Норма за день', type: 'number', required: true, min: 0, step: 'any' },
    { name: 'min_skill', label: 'Требуемая квалификация', type: 'select', options: qOpts(quals), required: true, num: true, help: 'Работник допускается, если его уровень не ниже' },
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

/* ---------- распределение операций позиции заказа по участкам ---------- */
async function openDistribution(order, refs, onClose) {
  const v = await GET(`/order-lines/${order.id}/operations`);
  const title = `${order.ext_id || '#' + order.id} · ${refs.PR[order.product_id]?.name || ''}`;
  if (!v.centers.length) { toast('В организации нет участков. Добавьте их в разделе «Работники → Участки»', true); return; }
  const m = modal(`<h2>Операции по участкам</h2><p class="muted" style="margin-top:0">${esc(title)} · версия ${esc(verLabel(refs.routings.find(q => q.id === v.routing_id) || { version_no: '?' }))}</p>
    <p class="muted">Отметьте участки, где выполняется каждая операция. Участок видит только свои операции. <b>Операция без участка — ошибка:</b> она не попадает в план, и следующие за ней операции тоже не планируются.</p>
    <div class="row" style="margin-bottom:8px"><label class="f" style="margin:0">Быстро: все операции на участок<select id="dq">${v.centers.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></label><button class="small secondary" id="dqa">Назначить всем операциям</button><button class="small secondary" id="dqc">Снять все</button></div>
    <div class="scroll"><table><tr><th>Порядок</th><th>Операция</th><th>Норма/день</th>${v.centers.map(c => `<th>${esc(c.name)}</th>`).join('')}<th></th></tr>
    ${v.items.map(i => `<tr data-op="${i.operation_id}"><td class="num">${num(i.sequence)}</td><td>${esc(i.name)}</td><td class="num">${num(i.norm_per_day)}</td>${v.centers.map(c => `<td><input type="checkbox" data-c="${c.id}" aria-label="${esc(i.name)}: ${esc(c.name)}" ${i.work_center_ids.includes(c.id) ? 'checked' : ''}></td>`).join('')}<td data-st></td></tr>`).join('')}</table></div>
    <div class="err" id="de" role="alert"></div>
    <div class="row" style="margin-top:12px"><button id="ds">Сохранить распределение</button><button class="secondary" id="dx">Закрыть</button><span class="muted" id="dsum"></span></div>`, true);
  const rows = () => $$('tr[data-op]', m.el);
  const refresh = () => { let ok = 0; rows().forEach(tr => { const n = $$('input:checked', tr).length; if (n) ok++; $('[data-st]', tr).innerHTML = n ? '' : '<span class="err">не распределено</span>'; }); $('#dsum', m.el).textContent = `Распределено: ${ok} из ${v.items.length}`; };
  m.el.addEventListener('change', refresh); refresh();
  $('#dqa', m.el).onclick = () => { const id = $('#dq', m.el).value; rows().forEach(tr => $$('input', tr).forEach(i => { i.checked = i.dataset.c === id; })); refresh(); };
  $('#dqc', m.el).onclick = () => { $$('input', m.el).forEach(i => { i.checked = false; }); refresh(); };
  $('#dx', m.el).onclick = () => m.close();
  $('#ds', m.el).onclick = async () => {
    try {
      const assignments = rows().map(tr => ({ operation_id: +tr.dataset.op, work_center_ids: $$('input:checked', tr).map(i => +i.dataset.c) }));
      const r = await PUT(`/order-lines/${order.id}/operations`, { assignments });
      toast(r.distributed === r.total ? 'Распределение сохранено' : `Сохранено. Не распределено операций: ${r.total - r.distributed}`, r.distributed !== r.total);
      m.close(); if (onClose) onClose();
    } catch (e) { $('#de', m.el).textContent = errText(e); }
  };
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
SCREENS.settings = async (v, back) => {
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
  v.innerHTML = `<div class="row" style="margin-bottom:10px">${back ? '<button class="secondary" id="sb">← К участкам</button>' : ''}<h2 class="grow" style="margin:0">Настройки планировщика</h2></div><div class="card" style="max-width:640px"><form id="sf" novalidate>${F.map(f => fieldHtml(f, s[f.name])).join('')}
    <label class="f">Жёсткость по уровням приоритета (например «2:60; 3:30»)<input name="lv" value="${esc(Object.entries(s.strictness_by_level || {}).map(([k, x]) => k + ':' + x).join('; '))}"></label>
    <fieldset style="border:1px solid var(--line);border-radius:6px;margin-bottom:10px"><legend class="muted">Рабочие дни недели</legend><div class="row">${WDN.map(([k, l]) => `<label class="chk"><input type="checkbox" name="wd" value="${k}" ${days.has(+k) ? 'checked' : ''}> ${l}</label>`).join('')}</div></fieldset>
    <div class="err" id="se" role="alert"></div><button type="submit">Сохранить</button></form></div>`;
  if (back) $('#sb').onclick = back;
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
    ...(isNew ? [{ name: 'email', label: 'E-mail', type: 'email', required: true }] : []),
    { name: 'first_name', label: 'Имя', required: true }, { name: 'last_name', label: 'Фамилия', required: true },
    { name: 'role', label: 'Роль', type: 'select', required: true, options: roleOpts, help: 'Бригадиру нужен участок, сборщику — исполнитель.' },
    { name: 'work_center_id', label: 'Участок (для бригадира)', type: 'select', options: opts(centers), num: true },
    { name: 'worker_id', label: 'Исполнитель (для сборщика)', type: 'select', options: opts(workers), num: true },
    ...(isNew ? [{ name: 'send_invite', label: 'Отправить приглашение по e-mail', type: 'checkbox', help: 'Человек сам задаст пароль по ссылке из письма (действует 7 суток). Тогда пароль ниже не нужен.' },
      { name: 'password', label: 'Или задайте временный пароль (не короче 8)', type: 'password' }] : [{ name: 'is_active', label: 'Доступ разрешён', type: 'checkbox' }, { name: 'password', label: 'Новый пароль (если нужно сбросить)', type: 'password' }])];
  const rowName = u => u.work_center_id ? ' · ' + (centers.find(c => c.id === u.work_center_id)?.name || '') : (u.worker_id ? ' · ' + (workers.find(w => w.id === u.worker_id)?.name || '') : '');
  v.innerHTML = `<div class="row" style="margin-bottom:10px"><h2 class="grow" style="margin:0">Пользователи</h2><button class="secondary" id="imp">Загрузить из файла</button><button id="add">+ Добавить</button></div>
    <div class="card flush scroll">${table([{ label: 'Фамилия', get: u => u.last_name || '—' }, { label: 'Имя', get: u => u.first_name }, { label: 'E-mail', get: u => u.email }, { label: 'Роль', get: u => ROLE_RU[u.role] + rowName(u) }, { label: 'Доступ', html: u => u.is_active ? '<span class="badge b-done">разрешён</span>' : '<span class="badge b-risk">закрыт</span>' }], users, u => `<button class="small secondary" data-e="${u.id}">Изменить</button>${u.id !== S.me.id ? ` <button class="small secondary" data-inv="${u.id}">Пригласить снова</button>` : ''}${u.role === 'foreman' ? ` <button class="small" data-tgu="${u.id}">Telegram</button>` : ''}`)}</div>
    <p class="muted">Права: администратор — всё, включая пользователей; руководитель проекта — данные, настройки и планирование; директор и наблюдатель — только просмотр отчётов; технолог — создаёт и редактирует каталог изделий, остальное смотрит; бригадир — свой участок и факт; сборщик — только свои задания и факт.</p>`;
  $('#add').onclick = () => formModal({ title: 'Новый пользователь', fields: fields(true), values: { role: 'viewer' }, onSubmit: async (vals, m) => { await POST('/users', vals); m.close(); toast(vals.send_invite ? 'Приглашение отправлено' : 'Пользователь создан'); SCREENS.users(v); } });
  $('#imp').onclick = () => importUsers(() => SCREENS.users(v));
  $$('[data-inv]', v).forEach(b => b.onclick = async () => { try { await POST(`/users/${b.dataset.inv}/invite`); toast('Приглашение отправлено'); } catch (e) { toast(errText(e), true); } });
  $$('[data-tgu]', v).forEach(b => b.onclick = () => openTelegram('user', +b.dataset.tgu, users.find(x => x.id == b.dataset.tgu).name));
  $$('[data-e]', v).forEach(b => b.onclick = () => { const u = users.find(x => x.id == b.dataset.e); formModal({ title: u.name, fields: fields(false), values: u, onSubmit: async (vals, m) => { if (!vals.password) delete vals.password; await PATCH('/users/' + u.id, vals); m.close(); toast('Сохранено'); SCREENS.users(v); } }); });
};

/* ---------- журнал ---------- */
const ENT = { settings: 'Настройки', order_line: 'Позиция заказа', worker: 'Исполнитель', fact: 'Факт', plan: 'План', user: 'Пользователь', product: 'Изделие', operation: 'Операция', routing: 'Маршрут', project: 'Проект', worker_access: 'Допуски', absence: 'Отсутствие', holiday: 'Праздник', work_center: 'Участок', fact_reason: 'Причина', tenant: 'Организация', demo: 'Демо', day_pin: 'Закрепление дня', extra_task: 'Работа вне перечня' };
const ACT = { create: 'создание', update: 'изменение', delete: 'удаление', replan: 'перепланирование', activate: 'откат плана', confirm: 'подтверждение', register: 'регистрация', load: 'загрузка', password_change: 'смена пароля', pin: 'закрепление', import: 'загрузка из файла', profile_update: 'изменение профиля' };
SCREENS.audit = async v => {
  let before = 0, all = [];
  const draw = async () => {
    const r = await GET('/audit?limit=100' + (before ? '&before=' + before : '')); all = all.concat(r.items); before = r.next_before;
    v.innerHTML = `<h2>Журнал изменений</h2><div class="card flush scroll">${table([{ label: 'Когда', get: a => fmtDT(a.at) }, { label: 'Кто', get: a => a.user || '—' }, { label: 'Что', get: a => (ENT[a.entity] || a.entity) + ' ' + (a.entity_id ?? '') }, { label: 'Действие', get: a => ACT[a.action] || a.action },
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
