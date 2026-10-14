/* План на день: правка плана вручную, работы вне перечня операций, временные работники, учёт факта.
   Любая правка закрепляет день работника — перепланирование участка его не меняет. */
'use strict';

const DAYC = {};
const reasonsList = () => listAll('/fact-reasons');
const byId = arr => Object.fromEntries(arr.map(x => [x.id, x]));
const fmtQ = x => num(x);

/* что требует объяснения: меньше плана или больше, чем нужно по заказу */
function deviation(plan, needLeft, qty) {
  if (qty === '' || qty == null || isNaN(qty)) return null;
  if (needLeft != null && qty > needLeft) return { kind: 'over', text: `Больше, чем нужно по заказу: осталось сделать ${fmtQ(Math.max(needLeft, 0))}, вы вводите ${fmtQ(qty)}` };
  if (plan > 0 && qty < plan) return { kind: 'under', text: `Меньше плана на ${fmtQ(plan - qty)} (план ${fmtQ(plan)})` };
  return null;
}
const reasonBox = (reasons, reasonId, note) => `<div class="devbox" hidden><div class="dtext err"></div>
  <div class="row"><select class="dreason" aria-label="Причина отклонения"><option value="">Причина…</option>${reasons.map(r => `<option value="${r.id}" ${r.id === reasonId ? 'selected' : ''}>${esc(r.name)}</option>`).join('')}</select>
  <input class="dnote" maxlength="400" placeholder="или напишите комментарий" aria-label="Комментарий к отклонению" value="${esc(note || '')}"></div></div>`;

/* ------------------------------ экран ------------------------------ */
const menu = (items, label = 'Действия') => { const l = items.filter(Boolean); return l.length ? `<details class="menu noprint"><summary aria-label="${label}">⋯</summary><div class="pop">${l.join('')}</div></details>` : ''; };

SCREENS.day = async (v, day) => {
  S.nav++;
  const [d, reasons] = await Promise.all([GET('/reports/day' + (day ? '?day=' + day : '')), reasonsList()]);
  const worker = S.me.role === 'worker', can = d.can_edit;
  const add = can ? `<details class="menu noprint"><summary class="btn" role="button">Добавить</summary><div class="pop"><button id="addT">Задание</button><button id="addX">Работа вне перечня</button><button id="addW">Новый работник</button></div></details>` : '';
  const more = menu([STAFF.includes(S.me.role) ? '<button id="rp">Перепланировать…</button>' : '', '<button onclick="print()">Печать</button>'], 'Ещё');
  let h = `<div class="row" style="margin-bottom:10px"><h1 class="grow" style="margin:0">${worker ? 'Мои задания' : 'План на день'} <span class="muted" style="font-weight:500">${fmtD(d.date)} ${wd(d.date)}</span></h1>${add}${more}</div>`;
  if (!d.plan_id) h += `<div class="banner info">План ещё не построен.</div>`;
  if (d.days.length) h += `<div class="days noprint">${d.days.map(x => `<button data-d="${x}" aria-pressed="${x === d.date}"><b>${fmtD(x)}</b><small>${wd(x)}</small></button>`).join('')}</div>`;
  if (can && d.workers.length) h += `<p class="hint noprint">Любая правка закрепляет день работника: перепланирование его не меняет.</p>`;
  if (!d.workers.length) h += `<div class="card empty">На этот день заданий нет.${can ? ' Добавьте задание через «Добавить».' : ''}</div>`;
  else h += `<div class="card flush"><div class="scroll"><table class="dt"><thead><tr><th>Задание</th><th class="num">План</th><th>${d.can_enter ? 'Сделано' : 'Факт'}</th><th>Статус</th><th></th></tr></thead>${d.workers.map(w => workerRows(w, d, reasons, can)).join('')}</table></div></div>`;
  v.innerHTML = h;
  const reload = () => SCREENS.day(v, d.date);
  $$('.days button', v).forEach(b => b.onclick = () => SCREENS.day(v, b.dataset.d));
  if ($('#rp')) $('#rp').onclick = openReplan;
  if ($('#addT')) { $('#addT').onclick = () => openTaskForm(d, {}, reload); $('#addX').onclick = () => openExtraForm(d, {}, reload); $('#addW').onclick = () => openTempWorker(d, reload); }
  $$('[data-wt]', v).forEach(b => b.onclick = () => openTaskForm(d, { workerId: +b.dataset.wt }, reload));
  $$('[data-wx]', v).forEach(b => b.onclick = () => openExtraForm(d, { workerId: +b.dataset.wx }, reload));
  $$('[data-te]', v).forEach(b => b.onclick = () => openTaskForm(d, { task: findTask(d, b.dataset.te) }, reload));
  $$('[data-td]', v).forEach(b => b.onclick = async () => { if (await confirmBox('Убрать это задание из плана дня?')) { try { await DEL('/plan-day/items/' + b.dataset.td); toast('Задание убрано'); reload(); } catch (e) { toast(errText(e), true); } } });
  $$('[data-xe]', v).forEach(b => b.onclick = () => openExtraForm(d, { extra: findExtra(d, b.dataset.xe) }, reload));
  $$('[data-xd]', v).forEach(b => b.onclick = async () => { if (await confirmBox('Убрать эту работу?')) { try { await DEL('/extra-tasks/' + b.dataset.xd); toast('Работа убрана'); reload(); } catch (e) { toast(errText(e), true); } } });
  $$('[data-unpin]', v).forEach(b => b.onclick = async () => { if (await confirmBox('Снять закрепление? При следующем перепланировании день этого работника рассчитается заново.')) { try { await DEL(`/plan-day/pins?worker_id=${b.dataset.unpin}&plan_date=${d.date}`); toast('Закрепление снято'); reload(); } catch (e) { toast(errText(e), true); } } });
  $$('.fact', v).forEach(box => wireFact(box, d, reload));
  $$('[data-allplan]', v).forEach(b => b.onclick = () => allByPlan(+b.dataset.allplan, d, reload));
};
const findTask = (d, key) => { const [w, o, p] = key.split('-').map(Number); return d.workers.find(x => x.worker_id === w).tasks.find(t => t.order_line_id === o && t.operation_id === p); };
const findExtra = (d, id) => d.workers.flatMap(w => w.extras).find(x => x.id == id);

/* исполнитель = заголовок группы + его строки; действия над исполнителем — в меню «⋯» */
function workerRows(w, d, reasons, can) {
  const todo = w.tasks.filter(t => t.plan_qty > 0 && t.fact_qty == null && (t.need_left == null || t.plan_qty <= t.need_left));
  const total = w.tasks.length + w.extras.length, done = w.tasks.filter(t => t.state === 'done').length + w.extras.filter(x => x.fact_hours != null && x.fact_hours >= x.hours).length;
  const wm = can ? menu([`<button data-wt="${w.worker_id}">Добавить задание</button>`, `<button data-wx="${w.worker_id}">Добавить работу вне перечня</button>`, w.pinned ? `<button data-unpin="${w.worker_id}">Снять закрепление дня</button>` : ''], 'Действия с работником') : '';
  return `<tbody class="wcard"><tr class="grp"><td colspan="5"><div class="gh"><h3 class="gn">${esc(w.worker)}</h3>${w.is_temporary ? '<span class="tag">временный</span>' : ''}${w.pinned ? '<span class="tag pin" title="Перепланирование этот день не меняет">день закреплён</span>' : ''}
    ${total ? `<span class="gc">${done} из ${total}</span>` : ''}<span class="grow"></span>${d.can_enter && todo.length > 1 ? `<button class="small secondary noprint" data-allplan="${w.worker_id}">Всё по плану (${todo.length})</button>` : ''}${wm}</div></td></tr>
    ${w.tasks.map(t => taskRow(t, w, d, reasons, can)).join('')}${w.extras.map(x => extraRow(x, w, d, reasons, can)).join('')}
    ${total ? '' : '<tr class="main"><td colspan="5" class="muted">Заданий нет.</td></tr>'}</tbody>`;
}
const reasonRow = (reasons, id, note) => `<tr class="dev" hidden><td colspan="5">${reasonBox(reasons, id, note)}</td></tr>`;
const factCell = (key, label, val, ph, step, max, saveAttr, done) => `<div class="fact" ${key}><input type="number" min="0" ${max ? `max="${max}"` : ''} step="${step}" inputmode="decimal" aria-label="${label}" value="${val ?? ''}" placeholder="${ph}"><button class="go ${done ? 'done' : ''}" ${saveAttr} aria-label="Записать выполнение" title="Записать. Пустое поле — выполнено по плану (${ph})">✓</button></div>`;

function taskRow(t, w, d, reasons, can) {
  const state = { done: ['b-done', '✔ выполнено'], partial: ['b-none', 'частично'], none: ['b-work', 'ждёт'], extra: ['b-noop', 'вне плана'] }[t.state];
  const key = `data-w="${w.worker_id}" data-o="${t.order_line_id}" data-p="${t.operation_id}" data-plan="${t.plan_qty}" data-need="${t.need_left ?? ''}"`;
  const edit = can && t.item_id && t.plan_qty > 0;
  return `<tr class="main task"><td><b>${esc(t.operation)}</b><small>${esc(t.product)} · заказ ${esc(t.order)}${t.project ? ' · ' + esc(t.project) : ''}</small>
      ${t.manual ? '<span class="tag">добавлено вручную</span>' : t.frozen ? '<span class="tag">зафиксировано заранее</span>' : ''}${t.reason ? `<small>Причина: ${esc(t.reason)}</small>` : ''}${t.note ? `<small>«${esc(t.note)}»</small>` : ''}</td>
    <td class="num qty" title="План на день">${t.plan_qty || '—'}</td>
    <td>${d.can_enter ? factCell(key, 'Сделано, ' + esc(t.operation), t.fact_qty, t.plan_qty || 'сколько', 'any', 0, 'data-save', t.state === 'done') : (t.fact_qty != null ? `<b>${t.fact_qty}</b>` : '<span class="muted">—</span>')}</td>
    <td><span class="badge ${state[0]}">${state[1]}</span>${t.fact_status === 'submitted' ? ' <span class="badge b-none">ждёт подтверждения</span>' : ''}</td>
    <td class="act">${edit ? menu([`<button data-te="${w.worker_id}-${t.order_line_id}-${t.operation_id}">Изменить…</button>`, `<button class="danger" data-td="${t.item_id}">Убрать</button>`], 'Действия с заданием') : ''}</td></tr>${d.can_enter ? reasonRow(reasons, t.reason_id, t.note) : ''}`;
}

function extraRow(x, w, d, reasons, can) {
  const kind = x.order ? `по заказу ${esc(x.order)}${x.product ? ' · ' + esc(x.product) : ''}` : 'не относится к заказу';
  const full = x.fact_hours != null && x.fact_hours >= x.hours;
  return `<tr class="main task extra"><td><b>${esc(x.title)}</b><small>Работа вне перечня операций · ${kind}</small>${x.reason ? `<small>Причина: ${esc(x.reason)}</small>` : ''}${x.note ? `<small>«${esc(x.note)}»</small>` : ''}</td>
    <td class="num qty" title="План, часов">${fmtQ(x.hours)}<small> ч</small></td>
    <td>${d.can_enter ? factCell(`data-xid="${x.id}" data-hours="${x.hours}"`, 'Сделано, часов', x.fact_hours, x.hours, '0.25', 24, 'data-save-x', full) : (x.fact_hours != null ? `<b>${fmtQ(x.fact_hours)} ч</b>` : '<span class="muted">—</span>')}</td>
    <td>${x.fact_hours != null ? `<span class="badge ${full ? 'b-done' : 'b-none'}">${full ? '✔ выполнено' : 'частично'}</span>` : '<span class="badge b-work">ждёт</span>'}</td>
    <td class="act">${can && x.fact_hours == null ? menu([`<button data-xe="${x.id}">Изменить…</button>`, `<button class="danger" data-xd="${x.id}">Убрать</button>`], 'Действия с работой') : ''}</td></tr>${d.can_enter ? reasonRow(reasons, x.reason_id, x.note) : ''}`;
}

/* ------------------------------ учёт факта ------------------------------ */
/* Кнопка ✓: пустое поле = «выполнено по плану»; число = ровно это число (включая 0 — «не делали»).
   Отклонение от плана раскрывает строку с причиной сразу под заданием. */
function wireFact(box, d, reload) {
  const row = box.closest('tr').nextElementSibling, dev = $('.devbox', row), inp = $('input', box), btn = $('[data-save],[data-save-x]', box), isX = !!box.dataset.xid;
  const plan = isX ? +box.dataset.hours : +box.dataset.plan, need = isX || box.dataset.need === '' ? null : +box.dataset.need, saved = inp.value;
  const refresh = () => {
    const q = inp.value === '' ? null : Number(inp.value), dv = isX ? (q != null && q < plan ? { text: `Меньше запланированного на ${fmtQ(plan - q)} ч` } : null) : deviation(plan, need, q);
    const known = inp.value === saved && ($('.dreason', row).value || $('.dnote', row).value);   // причина уже записана — не переспрашиваем
    row.hidden = dev.hidden = !dv || !!known; if (dv) $('.dtext', row).textContent = dv.text + ' — укажите причину или комментарий';
    return dv;
  };
  inp.addEventListener('input', refresh); inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); save(); } }); refresh();
  const save = async () => {
    if (inp.value === '' && plan > 0) inp.value = plan;
    const q = inp.value; if (q === '') { toast('Введите, сколько сделано', true); inp.focus(); return; }
    const dv = refresh(), reason = $('.dreason', row).value, note = $('.dnote', row).value.trim();
    if (dv && !reason && !note) { row.hidden = dev.hidden = false; toast(dv.text + '. Укажите причину или комментарий', true); $('.dreason', row).focus(); return; }
    btn.disabled = true;
    try {
      if (isX) { await PUT(`/extra-tasks/${box.dataset.xid}/fact`, { fact_hours: Number(q), reason_id: reason ? +reason : null, note }); toast('Записано'); }
      else {
        const r = await api('POST', '/facts', { fact_date: d.date, order_line_id: +box.dataset.o, operation_id: +box.dataset.p, worker_id: +box.dataset.w, qty: Number(q), reason_id: reason ? +reason : null, note }, { headers: { 'Idempotency-Key': uuid() } });
        toast(r.replaced ? `Исправлено: было ${r.previous_qty}, стало ${r.qty}` : `Записано: ${fmtQ(r.qty)}${plan ? ' из ' + fmtQ(plan) : ''}`);
      }
      await reload();
    } catch (e) { toast(errText(e), true); btn.disabled = false; }
  };
  btn.onclick = save;
}
async function allByPlan(workerId, d, reload) {
  const w = d.workers.find(x => x.worker_id === workerId); let n = 0, skipped = 0;
  for (const t of w.tasks) {
    if (!(t.plan_qty > 0 && t.fact_qty == null)) continue;
    if (t.need_left != null && t.plan_qty > t.need_left) { skipped++; continue; }
    try { await api('POST', '/facts', { fact_date: d.date, order_line_id: t.order_line_id, operation_id: t.operation_id, worker_id: workerId, qty: t.plan_qty }, { headers: { 'Idempotency-Key': uuid() } }); n++; } catch (e) { skipped++; }
  }
  toast(n ? `Записано заданий: ${n}${skipped ? '. Остальные внесите вручную: нужна причина' : ''}` : 'Нечего записывать автоматически', !n); await reload();
}

/* ------------------------------ правка плана ------------------------------ */
const orderLabel = (o, P, PR) => `${o.ext_id || '#' + o.id} · ${PR[o.product_id]?.name || ''} × ${num(o.qty)}${P[o.project_id] ? ' · ' + P[o.project_id].name : ''}${o.due_date ? ' · срок ' + fmtDY(o.due_date) : ''}`;
const dayOptions = d => d.days.filter(x => x >= d.today).map(x => ({ v: x, l: `${fmtD(x)} ${wd(x)}` }));
const workersFor = (ws) => ws.filter(w => S.me.role !== 'foreman' || w.work_center_id === S.me.work_center_id).map(w => ({ v: w.id, l: w.name + (w.is_temporary ? ' (временный)' : '') }));

async function openTaskForm(d, { task, workerId }, reload) {
  const [orders, projects, products, workers] = await Promise.all([listAll('/order-lines'), listAll('/projects'), listAll('/products'), listAll('/workers')]);
  const P = byId(projects), PR = byId(products), W = byId(workers), live = orders.filter(o => o.status === 'planned');
  const edit = !!task, wid = edit ? d.workers.find(w => w.tasks.includes(task)).worker_id : workerId;
  const fields = [
    { name: 'worker_id', label: 'Работник', type: 'select', required: true, num: true, options: workersFor(workers) },
    { name: 'plan_date', label: 'День', type: 'select', required: true, options: dayOptions(d) },
    { name: 'project_id', label: 'Проект', type: 'select', num: true, options: opts(projects), blank: 'Все проекты' },
    { name: 'product_id', label: 'Изделие', type: 'select', num: true, options: opts(products), blank: 'Все изделия' },
    { name: 'order_line_id', label: 'Заказ', type: 'select', required: true, num: true, options: [], help: 'Проект и изделие сужают список заказов' },
    { name: 'operation_id', label: 'Операция', type: 'select', required: true, num: true, options: [], help: 'Только операции, назначенные участку работника' },
    { name: 'qty', label: 'Количество', type: 'number', required: true, min: 1, step: 1 }];
  const values = edit ? { worker_id: wid, plan_date: d.date, project_id: task.project_id, product_id: task.product_id, order_line_id: task.order_line_id, operation_id: task.operation_id, qty: task.plan_qty } : { worker_id: wid, plan_date: d.date, qty: 1 };
  formModal({
    title: edit ? 'Изменить задание' : 'Добавить задание', fields, values, submit: 'Сохранить задание',
    extra: '<p class="muted" style="font-size:14px">После сохранения день работника закрепляется: перепланирование его не меняет.</p>',
    onOpen: form => {
      const el = n => form.elements[n]; let tok = 0, first = true;
      const fillOps = async () => {
        const my = ++tok, oid = el('order_line_id').value; el('operation_id').innerHTML = '';
        if (!oid) return;
        const r = await GET(`/order-lines/${oid}/operations`); if (my !== tok) return;
        const w = W[el('worker_id').value], items = r.items.filter(i => !r.centers.length || !w || i.work_center_ids.includes(w.work_center_id));
        const cur = first && edit ? String(task.operation_id) : el('operation_id').value;
        el('operation_id').innerHTML = items.length ? items.map(i => `<option value="${i.operation_id}" ${String(i.operation_id) === cur ? 'selected' : ''}>${esc(i.name)} (норма ${num(i.norm_per_day)}/день)</option>`).join('') : '<option value="">нет операций для этого участка</option>';
        first = false;
      };
      const fillOrders = () => {
        const p = el('project_id').value, pr = el('product_id').value, cur = el('order_line_id').value || (edit && first ? String(task.order_line_id) : '');
        const list = live.filter(o => (!p || o.project_id == p) && (!pr || o.product_id == pr));
        el('order_line_id').innerHTML = list.length ? list.map(o => `<option value="${o.id}" ${String(o.id) === cur ? 'selected' : ''}>${esc(orderLabel(o, P, PR))}</option>`).join('') : '<option value="">нет подходящих заказов</option>';
        return fillOps();
      };
      el('project_id').onchange = fillOrders; el('product_id').onchange = fillOrders; el('order_line_id').onchange = fillOps; el('worker_id').onchange = fillOps;
      fillOrders();
    },
    onSubmit: async (vals, m) => {
      const body = { plan_date: vals.plan_date, worker_id: vals.worker_id, order_line_id: vals.order_line_id, operation_id: vals.operation_id, qty: vals.qty };
      const r = edit ? await PATCH('/plan-day/items/' + task.item_id, body) : await POST('/plan-day/items', body);
      m.close(); toast(r.warnings.length ? 'Сохранено. ' + r.warnings.join('. ') : 'Задание сохранено', r.warnings.length > 0);
      await SCREENS.day($('#view'), vals.plan_date);
    }
  });
}

async function openExtraForm(d, { extra, workerId }, reload) {
  const [orders, projects, products, workers] = await Promise.all([listAll('/order-lines'), listAll('/projects'), listAll('/products'), listAll('/workers')]);
  const P = byId(projects), PR = byId(products), live = orders.filter(o => o.status !== 'cancelled');
  const edit = !!extra, wid = edit ? d.workers.find(w => w.extras.includes(extra)).worker_id : workerId;
  const fields = [
    { name: 'worker_id', label: 'Работник', type: 'select', required: true, num: true, options: workersFor(workers) },
    { name: 'plan_date', label: 'День', type: 'select', required: true, options: dayOptions(d) },
    { name: 'kind', label: 'Тип работы', type: 'select', required: true, options: [{ v: 'order', l: 'Относится к заказу, но нет в перечне операций' }, { v: 'other', l: 'Не относится к заказу (уборка, наладка и т. п.)' }] },
    { name: 'order_line_id', label: 'Заказ', type: 'select', num: true, options: live.map(o => ({ v: o.id, l: orderLabel(o, P, PR) })), required: true },
    { name: 'title', label: 'Что сделать', required: true },
    { name: 'hours', label: 'Сколько часов', type: 'number', required: true, min: 0.25, max: 24, step: 0.25 }];
  const values = edit ? { worker_id: wid, plan_date: d.date, kind: extra.order_line_id ? 'order' : 'other', order_line_id: extra.order_line_id, title: extra.title, hours: extra.hours } : { worker_id: wid, plan_date: d.date, kind: 'order', hours: 1 };
  formModal({
    title: edit ? 'Изменить работу вне перечня' : 'Работа вне перечня операций', fields, values, submit: 'Сохранить работу',
    extra: '<p class="muted" style="font-size:14px">Учитывается в часах. День работника закрепляется: планировщик ему в этот день ничего не добавит.</p>',
    onOpen: form => {
      const sync = () => { const o = form.elements.order_line_id, on = form.elements.kind.value === 'order'; o.closest('label').hidden = !on; o.required = on; };
      form.elements.kind.onchange = sync; sync();
    },
    onSubmit: async (vals, m) => {
      const order = vals.kind === 'order';
      if (edit) await PATCH('/extra-tasks/' + extra.id, { plan_date: vals.plan_date, worker_id: vals.worker_id, title: vals.title, hours: vals.hours, order_line_id: order ? vals.order_line_id : null, detach_order: !order });
      else await POST('/extra-tasks', { plan_date: vals.plan_date, worker_id: vals.worker_id, title: vals.title, hours: vals.hours, order_line_id: order ? vals.order_line_id : null });
      m.close(); toast('Работа сохранена'); await SCREENS.day($('#view'), vals.plan_date);
    }
  });
}

async function openTempWorker(d, reload) {
  const [centers, st] = await Promise.all([listAll('/work-centers'), GET('/settings')]);
  const foreman = S.me.role === 'foreman', myC = centers.find(c => c.id === S.me.work_center_id);
  const fields = [{ name: 'name', label: 'Имя', required: true },
    ...(foreman ? [] : [{ name: 'work_center_id', label: 'Участок', type: 'select', required: true, num: true, options: opts(centers) }]),
    { name: 'skill', label: 'Квалификация', type: 'select', required: true, num: true, options: qOpts(st.qualifications) },
    { name: 'perf', label: 'Производительность', type: 'number', required: true, min: 0.1, max: 10, step: 'any', help: '1 — как у обычного работника' }];
  formModal({
    title: 'Новый работник', fields, values: { skill: 1, perf: 1 }, submit: 'Добавить работника',
    extra: `${foreman ? `<p class="muted">Участок: <b>${esc(myC?.name || 'ваш')}</b></p>` : ''}<p class="muted" style="font-size:14px">Работник сохраняется в общем списке (Организация → Работники). Заказы ему автоматически не назначаются, пока вы не включите «Участвует в автопланировании» в настройках работника. Давать ему задания вручную можно сразу.</p>`,
    onSubmit: async (vals, m) => { const w = await POST('/plan-day/workers', foreman ? { name: vals.name, skill: vals.skill, perf: vals.perf } : vals); m.close(); toast(`Работник «${w.name}» добавлен. Теперь ему можно добавить задание`); openTaskForm(d, { workerId: w.id }, reload); }
  });
}
