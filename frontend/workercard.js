/* Карточка работника: все предстоящие назначения по дням. Здесь же их можно менять — количество, день, исполнителя, удалить,
   добавить новое. Изменения идут теми же запросами, что и в «Плане на день» (/plan-day/items) и в «Назначении» (/plan-grid/assign). */
'use strict';

let wcDays = 14;

async function openWorkerCard(worker) {
  const v = $('#view'); S.nav++;
  v.innerHTML = `<div class="wc-page"><div class="rt-top"><button class="secondary small" data-x>← К работникам</button></div><div id="wc-body"><p class="muted">Загрузка…</p></div></div>`;
  $('[data-x]', v).onclick = () => show(S.screen);
  await wcLoad(worker.id);
}

async function wcLoad(wid, keepScroll) {
  const y = window.scrollY, body = $('#wc-body');
  if (!body) return;
  let c, workers;
  try { [c, workers] = await Promise.all([GET(`/workers/${wid}/assignments?days=${wcDays}`), listAll('/workers')]); }
  catch (e) { if (e instanceof Stale) return; body.innerHTML = `<div class="card err">${esc(errText(e))}</div>`; return; }
  wcRender(body, c, workers);
  if (keepScroll && y > 0) window.scrollTo(0, y);
}

function wcRender(body, c, workers) {
  const w = c.worker, t = c.totals, quals = null;
  const loadCls = l => l > 1.0001 ? 'over' : l >= .85 ? 'high' : '';
  const others = workers.filter(x => x.id !== w.id && x.status === 'available');
  const dayHtml = d => {
    const off = !d.workday ? '<span class="badge b-grey">выходной</span>' : '';
    const ab = d.absent ? `<span class="badge b-risk">${esc(d.absent)}</span>` : '';
    const pct = Math.round(d.load * 100);
    const items = d.items.map(i => `<li class="wc-item" data-id="${i.id}">
      <div class="wc-t"><b>${esc(i.operation)}</b><span>заказ ${esc(i.order)} · ${esc(i.product)}${i.project ? ' · ' + esc(i.project) : ''}${i.due_date ? ' · срок ' + fmtD(i.due_date) : ''}</span>${i.manual ? '<small class="muted">назначено вручную</small>' : ''}</div>
      <label class="wc-q" title="Количество"><input type="number" min="1" step="1" value="${i.qty}" data-wq="${i.id}" data-fk="wq${i.id}" aria-label="Количество: ${esc(i.operation)}"> шт</label>
      <label class="wc-d" title="Перенести на другой день"><input type="date" value="${d.date}" min="${c.from}" data-wd="${i.id}" aria-label="Перенести: ${esc(i.operation)}"></label>
      <select class="inl" data-ww="${i.id}" aria-label="Передать другому работнику: ${esc(i.operation)}"><option value="">передать…</option>${others.map(o => `<option value="${o.id}">${esc(o.name)}</option>`).join('')}</select>
      <button class="small danger" data-wx="${i.id}" aria-label="Убрать назначение: ${esc(i.operation)}">×</button></li>`).join('');
    const extra = d.extra.map(x => `<li class="wc-extra"><span>🛠 ${esc(x.title)}${x.order ? ' · заказ ' + esc(x.order) : ''}</span><b>${num(x.hours)} ч</b></li>`).join('');
    return `<section class="wc-day ${d.workday ? '' : 'off'} ${d.absent ? 'abs' : ''}">
      <header><b>${esc(d.wd)} ${fmtD(d.date)}</b>${off}${ab}${d.pinned ? '<span class="badge b-noop" title="День закреплён: перепланирование его не меняет">📌 закреплён</span>' : ''}
        <span class="grow"></span>${d.items.length ? `<div class="wc-load ${loadCls(d.load)}" title="Загрузка по плану"><div class="bar"><i style="width:${Math.min(100, pct)}%"></i></div><small>${pct}%</small></div>` : ''}
        <button class="small secondary" data-wadd="${d.date}">+ назначить</button></header>
      ${items || extra ? `<ul>${items}${extra}</ul>` : `<p class="muted wc-empty">${d.absent ? 'Не работает' : d.workday ? 'Заданий нет' : 'Выходной'}</p>`}</section>`;
  };
  body.innerHTML = `<div class="wc-head"><h2>${esc(w.name)} <small class="muted">${w.section ? esc(w.section) + ' · ' : ''}квалификация ${w.skill} · производительность ${num(w.perf)}</small></h2>
      ${w.status !== 'available' ? '<span class="badge b-risk">недоступен</span>' : ''}${!w.auto_plan ? '<span class="badge b-noop">только вручную</span>' : ''}</div>
    ${c.absences.length ? `<div class="banner info">Ближайшие отсутствия: ${c.absences.map(a => `${fmtD(a.from)}–${fmtD(a.to)}${a.reason ? ' (' + esc(a.reason) + ')' : ''}`).join(', ')}</div>` : ''}
    ${!c.has_plan ? '<div class="banner warn">План ещё не построен. Назначать задания вручную можно после первого перепланирования.</div>' : ''}
    <div class="tiles wc-tiles">
      <div class="tile"><div class="tile-h">Заданий</div><div class="tile-big">${t.tasks}</div><div class="tile-sub">на ${c.days.length} дн. вперёд</div></div>
      <div class="tile"><div class="tile-h">Дни</div><div class="tile-big">${t.days_busy}<small> с работой</small></div><div class="tile-sub">свободных рабочих дней: ${t.days_free}</div></div>
      <div class="tile"><div class="tile-h">Загрузка</div><div class="tile-big">${t.avg_load == null ? '—' : Math.round(t.avg_load * 100)}<small>%</small></div><div class="tile-sub">${t.overloaded ? `<span class="err">перегружено дней: ${t.overloaded}</span>` : 'в среднем по рабочим дням'}</div></div>
    </div>
    <div class="row wc-bar"><label class="f" style="flex-direction:row;align-items:center;gap:8px">Период <select id="wc-days">${[7, 14, 30, 60].map(n => `<option value="${n}" ${n === wcDays ? 'selected' : ''}>${n} дней</option>`).join('')}</select></label>
      <span class="grow"></span><button id="wc-add" ${c.has_plan ? '' : 'disabled'}>+ Назначить задание</button></div>
    <div id="wc-list">${c.days.map(dayHtml).join('')}</div>`;
  const reload = () => wcLoad(w.id, true);
  const save = async (fn, ok) => {
    try { const r = await fn(); if (r && r.warnings && r.warnings.length) toast(r.warnings.join('. '), true); else if (ok) toast(ok); }
    catch (e) { toast(errText(e), true); }
    await reload();
  };
  $('#wc-days', body).onchange = e => { wcDays = +e.target.value; wcLoad(w.id); };
  $('#wc-add', body).onclick = () => wcAdd(w, c, reload);
  $$('[data-wadd]', body).forEach(b => b.onclick = () => wcAdd(w, c, reload, b.dataset.wadd));
  $$('[data-wq]', body).forEach(i => i.onchange = () => { const q = Math.floor(+i.value); if (!(q >= 1)) { toast('Количество — целое число, не меньше 1', true); return reload(); } save(() => PATCH(`/plan-day/items/${i.dataset.wq}`, { qty: q }), 'Количество изменено'); });
  $$('[data-wd]', body).forEach(i => i.onchange = () => { if (i.value) save(() => PATCH(`/plan-day/items/${i.dataset.wd}`, { plan_date: i.value }), 'Задание перенесено'); });
  $$('[data-ww]', body).forEach(s => s.onchange = () => { if (s.value) { const who = workers.find(x => x.id == s.value); save(() => PATCH(`/plan-day/items/${s.dataset.ww}`, { worker_id: +s.value }), 'Передано: ' + (who ? who.name : '')); } });
  $$('[data-wx]', body).forEach(b => b.onclick = async () => { if (await confirmBox('Убрать это задание у работника? Чтобы перепланирование не вернуло его обратно, закрепите день в «Плане на день».')) save(() => DEL(`/plan-day/items/${b.dataset.wx}`).then(() => ({})), 'Назначение убрано'); });
}

/* новое назначение: заказ → операция (из тех, что работник может делать) → день → количество (по умолчанию — норма) */
async function wcAdd(w, c, reload, date) {
  let orders;
  try { orders = (await GET('/plan-grid/orders')).items; } catch (e) { toast(errText(e), true); return; }
  if (!orders.length) { toast('Нет заказов, по которым можно назначать', true); return; }
  const m = modal(`<h2>Назначить: ${esc(w.name)}</h2><form id="wcf" novalidate>
    <label class="f">Заказ<select name="order">${orders.map(o => `<option value="${o.order_line_id}">${esc([o.project, o.order, o.product, o.qty != null ? o.qty + ' шт' : '', o.due_date ? 'срок ' + fmtD(o.due_date) : ''].filter(Boolean).join(' · ') || '#' + o.order_line_id)}</option>`).join('')}</select></label>
    <label class="f">Операция<select name="op"><option value="">загрузка…</option></select></label>
    <label class="f">День<input type="date" name="date" min="${c.from}" value="${date || c.from}" required></label>
    <label class="f">Количество, шт<input type="number" name="qty" min="1" step="1" placeholder="по умолчанию — норма"></label>
    <div class="err" id="wce" role="alert"></div><div class="row"><button type="submit">Назначить</button><button type="button" class="secondary" data-n>Отмена</button></div></form>`);
  const f = $('#wcf', m.el), opSel = f.elements.op;
  const loadOps = async () => {
    opSel.innerHTML = '<option value="">загрузка…</option>';
    try {
      const g = await GET(`/plan-grid?order_line_id=${f.elements.order.value}`);
      const ok = g.operations.filter(o => o.eligible.includes(w.id));
      opSel.innerHTML = ok.length ? ok.map(o => `<option value="${o.operation_id}">${esc(o.name)} · осталось ${num(o.unplanned)}</option>`).join('') : '<option value="">нет операций для этого работника</option>';
    } catch (e) { opSel.innerHTML = '<option value="">—</option>'; $('#wce', m.el).textContent = errText(e); }
  };
  f.elements.order.onchange = loadOps; await loadOps();
  $('[data-n]', m.el).onclick = () => m.close();
  f.onsubmit = async e => {
    e.preventDefault();
    if (!opSel.value) { $('#wce', m.el).textContent = 'Выберите операцию'; return; }
    try {
      const r = await POST('/plan-grid/assign', { order_line_id: +f.elements.order.value, operation_id: +opSel.value, plan_date: f.elements.date.value, worker_id: w.id, ...(f.elements.qty.value ? { qty: +f.elements.qty.value } : {}) });
      m.close(); if (r.warnings && r.warnings.length) toast(r.warnings.join('. '), true); else toast('Назначено'); reload();
    } catch (x) { $('#wce', m.el).textContent = errText(x); }
  };
}
