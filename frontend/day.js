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
SCREENS.day = async (v, day) => {
  S.nav++;
  const [d, reasons] = await Promise.all([GET('/reports/day' + (day ? '?day=' + day : '')), reasonsList()]);
  const worker = S.me.role === 'worker', can = d.can_edit;
  let h = `<div class="row noprint" style="margin-bottom:8px"><h1 class="grow" style="margin:0">${worker ? 'Мои задания' : 'План на день'} · ${fmtD(d.date)} ${wd(d.date)}</h1>
    ${can ? '<button id="addT">+ Задание</button><button class="secondary" id="addX">+ Работа вне перечня</button><button class="secondary" id="addW">+ Работник</button>' : ''}
    ${STAFF.includes(S.me.role) ? '<button class="secondary" id="rp">Перепланировать</button>' : ''}<button class="secondary" onclick="print()">Печать</button></div>`;
  if (!d.plan_id) h += `<div class="banner info">План ещё не построен.</div>`;
  if (can) h += `<p class="muted noprint" style="margin-top:0">Любое изменение закрепляет день работника: перепланирование его не меняет. Закрепление можно снять кнопкой в карточке работника.</p>`;
  if (d.days.length) h += `<div class="days noprint">${d.days.map(x => `<button data-d="${x}" aria-pressed="${x === d.date}"><b>${fmtD(x)}</b><small>${wd(x)}</small></button>`).join('')}</div>`;
  if (!d.workers.length) h += `<div class="card empty">На этот день заданий нет.${can ? ' Добавьте задание кнопкой «+ Задание».' : ''}</div>`;
  else h += `<div class="workers">${d.workers.map(w => workerCard(w, d, reasons, can)).join('')}</div>`;
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

function workerCard(w, d, reasons, can) {
  const todo = w.tasks.filter(t => t.plan_qty > 0 && t.fact_qty == null && (t.need_left == null || t.plan_qty <= t.need_left));
  return `<div class="wcard"><div class="row" style="margin-bottom:4px"><h3 class="grow" style="margin:0">${esc(w.worker)}</h3>${w.is_temporary ? '<span class="badge b-noop">временный</span>' : ''}${w.pinned ? '<span class="badge b-work" title="Перепланирование этот день не меняет">📌 день закреплён</span>' : ''}</div>
    ${w.tasks.map(t => taskHtml(t, w, d, reasons, can)).join('')}
    ${w.extras.map(x => extraHtml(x, w, d, reasons, can)).join('')}
    ${!w.tasks.length && !w.extras.length ? '<p class="muted">Заданий нет.</p>' : ''}
    <div class="tools noprint">${can ? `<button class="small secondary" data-wt="${w.worker_id}">+ Задание</button><button class="small secondary" data-wx="${w.worker_id}">+ Работа вне перечня</button>${w.pinned ? `<button class="small secondary" data-unpin="${w.worker_id}">Снять закрепление</button>` : ''}` : ''}
    ${d.can_enter && todo.length > 1 ? `<button class="small secondary" data-allplan="${w.worker_id}">Всё по плану (${todo.length})</button>` : ''}</div></div>`;
}

function taskHtml(t, w, d, reasons, can) {
  const state = { done: ['b-done', '✔ выполнено'], partial: ['b-none', 'частично'], none: ['b-work', 'ждёт'], extra: ['b-noop', 'вне плана'] }[t.state];
  const key = `data-w="${w.worker_id}" data-o="${t.order_line_id}" data-p="${t.operation_id}" data-plan="${t.plan_qty}" data-need="${t.need_left ?? ''}"`;
  const edit = can && t.item_id && t.plan_qty > 0;
  return `<div class="task"><div class="t1"><div><b>${esc(t.operation)}</b><small>${esc(t.product)} · заказ ${esc(t.order)}${t.project ? ' · ' + esc(t.project) : ''}</small>
      <small>${t.manual ? '✎ добавлено вручную' : t.frozen ? '❄ зафиксировано заранее' : ''}</small></div><div class="qty" title="План на день"><small>план</small>${t.plan_qty || '—'}</div></div>
    <div><span class="badge ${state[0]}">${state[1]}</span>${t.fact_status === 'submitted' ? ' <span class="badge b-none">ждёт подтверждения</span>' : ''}${t.reason ? ` <small>Причина: ${esc(t.reason)}</small>` : ''}${t.note ? ` <small>«${esc(t.note)}»</small>` : ''}</div>
    ${d.can_enter ? `<div class="fact" ${key}><label>Сделано<br><input type="number" min="0" step="any" inputmode="decimal" aria-label="Сделано, ${esc(t.operation)}" value="${t.fact_qty ?? ''}" placeholder="сколько"></label>
      ${t.plan_qty ? `<button class="small secondary" data-q="${t.plan_qty}">Всё: ${t.plan_qty}</button><button class="small secondary" data-q="0">Не делали</button>` : ''}
      ${reasonBox(reasons, t.reason_id, t.note)}<button class="small" data-save>Записать выполнение</button></div>` : (t.fact_qty != null ? `<div>Сделано: <b>${t.fact_qty}</b></div>` : '')}
    ${edit ? `<div class="tools noprint"><button class="small secondary" data-te="${w.worker_id}-${t.order_line_id}-${t.operation_id}">Изменить</button><button class="small danger" data-td="${t.item_id}">Убрать</button></div>` : ''}</div>`;
}

function extraHtml(x, w, d, reasons, can) {
  const kind = x.order ? `по заказу ${esc(x.order)}${x.product ? ' · ' + esc(x.product) : ''}` : 'не относится к заказу';
  const key = `data-xid="${x.id}" data-hours="${x.hours}"`;
  return `<div class="task extra"><div class="t1"><div><b>${esc(x.title)}</b><small>Работа вне перечня операций · ${kind}</small></div><div class="qty" title="План, часов"><small>часов</small>${fmtQ(x.hours)}</div></div>
    ${x.fact_hours != null ? `<div><span class="badge ${x.fact_hours >= x.hours ? 'b-done' : 'b-none'}">${x.fact_hours >= x.hours ? '✔ выполнено' : 'частично'}</span>${x.reason ? ` <small>Причина: ${esc(x.reason)}</small>` : ''}${x.note ? ` <small>«${esc(x.note)}»</small>` : ''}</div>` : ''}
    ${d.can_enter ? `<div class="fact" ${key}><label>Сделано, часов<br><input type="number" min="0" max="24" step="0.25" inputmode="decimal" value="${x.fact_hours ?? ''}" placeholder="часов"></label>
      <button class="small secondary" data-q="${x.hours}">Всё: ${fmtQ(x.hours)}</button>${reasonBox(reasons, x.reason_id, x.note)}<button class="small" data-save-x>Записать выполнение</button></div>` : ''}
    ${can && x.fact_hours == null ? `<div class="tools noprint"><button class="small secondary" data-xe="${x.id}">Изменить</button><button class="small danger" data-xd="${x.id}">Убрать</button></div>` : ''}</div>`;
}

/* ------------------------------ учёт факта ------------------------------ */
function wireFact(box, d, reload) {
  const inp = $('input', box), dev = $('.devbox', box), isX = !!box.dataset.xid;
  const plan = isX ? +box.dataset.hours : +box.dataset.plan, need = isX || box.dataset.need === '' ? null : +box.dataset.need;
  const refresh = () => {
    const q = inp.value === '' ? null : Number(inp.value), dv = isX ? (q != null && q < plan ? { text: `Меньше запланированного на ${fmtQ(plan - q)} ч` } : null) : deviation(plan, need, q);
    dev.hidden = !dv; if (dv) $('.dtext', box).textContent = dv.text + ' — укажите причину или комментарий';
    return dv;
  };
  inp.addEventListener('input', refresh); refresh();
  $$('[data-q]', box).forEach(b => b.onclick = () => { inp.value = b.dataset.q; const dv = refresh(); if (!dv) save(); else $('.dreason', box).focus(); });
  const save = async () => {
    const q = inp.value; if (q === '') { toast('Введите, сколько сделано', true); inp.focus(); return; }
    const dv = refresh(), reason = $('.dreason', box).value, note = $('.dnote', box).value.trim();
    if (dv && !reason && !note) { toast(dv.text + '. Укажите причину или комментарий', true); $('.dreason', box).focus(); return; }
    const btn = $('[data-save],[data-save-x]', box); btn.disabled = true;
    try {
      if (isX) { await PUT(`/extra-tasks/${box.dataset.xid}/fact`, { fact_hours: Number(q), reason_id: reason ? +reason : null, note }); toast('Записано'); }
      else {
        const r = await api('POST', '/facts', { fact_date: d.date, order_line_id: +box.dataset.o, operation_id: +box.dataset.p, worker_id: +box.dataset.w, qty: Number(q), reason_id: reason ? +reason : null, note }, { headers: { 'Idempotency-Key': uuid() } });
        toast(r.replaced ? `Исправлено: было ${r.previous_qty}, стало ${r.qty}` : `Записано: ${fmtQ(r.qty)}${plan ? ' из ' + fmtQ(plan) : ''}`);
      }
      await reload();
    } catch (e) { toast(errText(e), true); btn.disabled = false; }
  };
  $('[data-save],[data-save-x]', box).onclick = save;
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
