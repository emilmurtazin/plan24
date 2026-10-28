/* «Назначение» — подвкладка «Выполнения»: заказ целиком в одной таблице.
   Строки — операции заказа, столбцы — дни ближайшей недели, в ячейках — работники. Выбрал работника — подтянулась норма
   (с учётом производительности, остатка по заказу и свободной части дня), день работника закрепился автоматически. */
'use strict';

/* построение DOM без innerHTML: данные (имена, названия) попадают на страницу только как текст */
function gh(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v; else if (k === 'text') el.textContent = v; else if (k.startsWith('on')) el[k] = v;
    else if (k === 'selected') el.selected = !!v; else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
}

SCREENS.grid = async v => {
  S.nav++;
  const { items: orders } = await GET('/plan-grid/orders');
  if (!orders.length) { v.innerHTML = '<h2>Назначение</h2><div class="card empty">Нет позиций заказов в работе, по которым можно назначать. Добавьте заказ или постройте план.</div>'; return; }
  if (!orders.some(o => o.order_line_id === S.gridOrder)) S.gridOrder = orders[0].order_line_id;
  let scroll = 0;
  const load = async () => {
    const wrap = $('.gridwrap', v); if (wrap) scroll = wrap.scrollLeft;
    let g;
    try { g = await GET(`/plan-grid?order_line_id=${S.gridOrder}` + (S.gridStart ? `&start=${S.gridStart}` : '')); }
    catch (e) { if (e instanceof Stale) return; v.innerHTML = `<div class="card err">${esc(errText(e))}</div>`; return; }
    S.gridStart = g.start; draw(g);
    const w2 = $('.gridwrap', v); if (w2) w2.scrollLeft = scroll;
  };
  const wk = g => Object.fromEntries(g.workers.map(w => [w.id, w]));

  /* вызов с показом предупреждений и перерисовкой */
  const run = async (fn, okText) => {
    try { const r = await fn(); if (r && r.warnings && r.warnings.length) toast(r.warnings.join('. '), true); else if (okText) toast(okText); await load(); return r; }
    catch (e) { toast(errText(e), true); await load(); }
  };

  function cell(g, op, d) {
    const W = wk(g), items = op.cells[d.date] || [];
    const td = gh('td', { class: 'gcell' + (d.workday ? '' : ' off') + (d.past ? ' past' : '') });
    const list = gh('div', { class: 'gl' });
    for (const it of items) {
      const w = W[it.worker_id], ld = w ? w.load[d.date] : 0;
      const inp = gh('input', { type: 'number', min: 1, step: 1, value: it.qty, class: 'gq', disabled: d.past, 'aria-label': 'Количество' + (it.capacity ? ` (норма ${num(it.capacity)})` : '') });
      let t; inp.oninput = () => { clearTimeout(t); t = setTimeout(() => { const q = Math.round(+inp.value); if (q >= 1) run(() => PATCH(`/plan-day/items/${it.item_id}`, { qty: q }).then(r => ({ warnings: r.warnings }))); }, 700); };
      list.append(gh('div', { class: 'gchip' + (ld > 1.0001 ? ' over' : '') , title: `${it.worker}: загрузка дня ${Math.round(ld * 100)}%${it.capacity ? ', норма ' + num(it.capacity) + ' за день' : ''}${it.locked ? ' · день закреплён' : ''}` },
        gh('span', { class: 'gn', text: it.worker }), inp,
        d.past ? null : gh('button', { class: 'gx', 'aria-label': 'Снять назначение', onclick: () => run(() => DEL(`/plan-grid/assign/${it.item_id}`).then(r => { toast(r && r.unpinned ? 'Назначение снято, закрепление дня снято' : 'Назначение снято'); return null; }) ) }, '×')));
    }
    td.append(list);
    const have = new Set(items.map(i => i.worker_id));
    const cand = d.past ? [] : op.eligible.map(id => W[id]).filter(w => w && !have.has(w.id));
    if (cand.length) {
      const sel = gh('select', { class: 'gadd', 'aria-label': 'Назначить работника' }, gh('option', { value: '' }, '＋ работник'),
        cand.sort((a, b) => (a.load[d.date] || 0) - (b.load[d.date] || 0)).map(w => {
          const abs = w.absent.includes(d.date), free = Math.max(0, Math.round((1 - (w.load[d.date] || 0)) * 100));
          return gh('option', { value: w.id, class: abs ? 'abs' : '' }, `${w.name}${w.skill < op.min_skill ? ' ⚠' : ''} · ${abs ? 'отсутствует' : 'свободно ' + free + '%'}`);
        }));
      sel.onchange = () => { if (sel.value) run(() => POST('/plan-grid/assign', { order_line_id: g.order.order_line_id, operation_id: op.operation_id, plan_date: d.date, worker_id: +sel.value })); };
      td.append(sel);
    } else if (!items.length && !d.past) td.append(gh('span', { class: 'muted', text: op.distributed ? 'нет работников' : 'не распределена' }));
    const tot = items.reduce((a, i) => a + i.qty, 0);
    if (tot) td.append(gh('div', { class: 'gtot', text: `Итого: ${tot}` }));
    return td;
  }

  function draw(g) {
    const o = g.order;
    const sel = gh('select', { id: 'gord', 'aria-label': 'Заказ' }, orders.map(x => gh('option', { value: x.order_line_id, selected: x.order_line_id === S.gridOrder },
      `${x.project} · ${x.order} · ${x.product} · ${num(x.qty)} шт${x.due_date ? ' · срок ' + fmtDY(x.due_date) : ''}`)));
    sel.onchange = () => { S.gridOrder = +sel.value; load(); };
    const shift = n => { const d = new Date(g.start + 'T00:00:00'); d.setDate(d.getDate() + n); S.gridStart = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); load(); };
    const head = gh('tr', null, gh('th', { class: 'gop', text: 'Операция' }), g.days.map(d => gh('th', { class: (d.workday ? '' : 'off') + (d.past ? ' past' : '') + (d.date === g.today ? ' today' : '') }, fmtD(d.date), gh('small', { text: d.wd + (d.workday ? '' : ' · вых.') }))));
    /* состояние объёма операции: больше нужного — оранжевым, весь объём (сделан + в плане) — зелёным, остаток не назначен — красным */
    const opState = op => op.over > 0 ? 'over' : (op.required - op.done <= 0 || op.complete) ? 'full' : op.unplanned > 0 ? 'short' : '';
    const rows = g.operations.map(op => {
      const st = opState(op);
      const note = st === 'over' ? gh('div', { class: 'gmeta over-t', text: `запланировано больше на ${num(op.over)}` })
        : st === 'full' ? gh('div', { class: 'gmeta ok-t', text: op.required - op.done <= 0 ? 'выполнено' : 'весь объём в плане' })
        : st === 'short' ? gh('div', { class: 'gmeta warn-t', text: `не назначено: ${num(op.unplanned)}` }) : null;
      return gh('tr', { class: st ? 'st-' + st : null },
        gh('td', { class: 'gop' }, gh('b', { text: op.name }),
          gh('div', { class: 'gmeta', text: `норма ${num(op.norm_per_day)}/день · нужно ${num(op.required)}` }),
          gh('div', { class: 'gmeta', text: `сделано ${num(op.done)} · в плане ${num(op.planned)}` }),
          note,
          op.sections.length ? gh('div', { class: 'gmeta', text: op.sections.join(', ') }) : null),
        g.days.map(d => cell(g, op, d)));
    });
    v.replaceChildren(
      gh('div', { class: 'row', style: 'margin-bottom:8px' }, gh('h2', { class: 'grow', style: 'margin:0', text: 'Назначение на неделю' }),
        gh('span', { class: 'row noprint' }, gh('button', { class: 'secondary small', 'aria-label': 'Неделя назад', onclick: () => shift(-7) }, '‹'),
          gh('b', { class: 'lrange', text: `${fmtD(g.days[0].date)} – ${fmtD(g.days[6].date)}` }), gh('button', { class: 'secondary small', 'aria-label': 'Неделя вперёд', onclick: () => shift(7) }, '›'),
          gh('button', { class: 'small secondary', onclick: () => { S.gridStart = null; load(); } }, 'Сегодня'))),
      gh('label', { class: 'f' }, 'Заказ', sel),
      gh('p', { class: 'hint' }, 'Выберите работника в ячейке — подставится норма (с учётом производительности, остатка по заказу и свободной части дня), а день работника закрепится: перепланирование назначенное не изменит. Количество можно поправить. ⚠ — квалификация ниже требуемой. «×» снимает назначение (если у работника в этот день больше ничего нет, закрепление снимается). Прошедшие дни доступны только для просмотра.'),
      gh('div', { class: 'glegend' }, gh('span', { class: 'lg full' }, 'весь объём в плане'), gh('span', { class: 'lg over' }, 'запланировано больше нужного'), gh('span', { class: 'lg short' }, 'есть неназначенный остаток')),
      gh('div', { class: 'card flush gridwrap' }, gh('table', { class: 'gridt' }, head, rows)));
  }
  await load();
};
