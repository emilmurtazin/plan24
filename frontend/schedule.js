/* «План-график»: календарный план и факт по всем заказам. Строка заказа — две полосы по дням: План (синяя) и Факт (зелёная;
   янтарная — меньше плана). Значения в нормо-днях (объём ÷ норма операции). Фильтры: участок, проект, заказы, статус; масштаб дни/недели.
   Нажатие на заказ раскрывает операции (план/факт в единицах операции). */
const GT = { LEFT: 270, DAY: 26, WEEK: 66, ROW: 52, SUB: 40 };
const GT_WD = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'], GT_MON = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const gtD = s => new Date(s + 'T00:00:00Z'), gtIso = d => d.toISOString().slice(0, 10), gtAdd = (s, n) => { const d = gtD(s); d.setUTCDate(d.getUTCDate() + n); return gtIso(d); };
const gtDow = s => (gtD(s).getUTCDay() + 6) % 7;                       // 0 — понедельник
const gtStatusCls = s => s === 'Готово' ? 'b-done' : s === 'Риск опоздания' ? 'b-risk' : s === 'В работе' ? 'b-work' : s === 'Нет плана' ? 'b-none' : 'b-noop';

SCREENS.schedule = async v => {
  const foreman = S.me.role === 'foreman';
  const [targets, centers, projects] = await Promise.all([GET('/flow-map/targets'), foreman ? Promise.resolve({ items: [] }) : GET('/work-centers'), GET('/projects')]);
  const F = S.sch = S.sch || { wc: '', project: '', lines: [], status: '', scale: 'day', from: '', to: '' };
  let D = null, open = {}, details = {}, lineSearch = '';
  const lines = targets.lines;
  v.innerHTML = `<div class="row" style="margin-bottom:8px"><h2 class="grow" style="margin:0">План-график</h2></div>
    <p class="hint">Календарный план и факт по заказам. Синяя полоса — план по дням, зелёная — факт (янтарная — факт ниже плана). Значения — в нормо-днях. Нажмите на заказ, чтобы увидеть операции.</p>
    <div class="card"><div class="row" style="align-items:end;flex-wrap:wrap;gap:10px">
      ${foreman ? '' : `<label class="f">Участок<select id="gt-wc"><option value="">Все участки</option>${centers.items.map(c => `<option value="${c.id}" ${String(F.wc) === String(c.id) ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label>`}
      <label class="f">Проект<select id="gt-pr"><option value="">Все проекты</option>${projects.items.map(p => `<option value="${p.id}" ${String(F.project) === String(p.id) ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label>
      <div class="f"><span>Заказы</span><details class="gt-dd" id="gt-dd"><summary id="gt-dd-s"></summary><div class="gt-ddp"><input type="search" id="gt-dd-q" placeholder="Найти заказ…"><div class="row" style="margin:6px 0"><button type="button" class="small secondary" id="gt-all">Все</button><button type="button" class="small secondary" id="gt-sel">Выбрать видимые</button></div><div id="gt-dd-l" class="gt-ddl"></div></div></details></div>
      <label class="f">Статус<select id="gt-st">${[['', 'Любой'], ['В работе', 'В работе'], ['Риск опоздания', 'Риск опоздания'], ['Нет плана', 'Нет плана'], ['Готово', 'Готово']].map(([k, t]) => `<option value="${k}" ${F.status === k ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
      <div class="f"><span>Масштаб</span><div class="row" style="gap:4px"><button class="small ${F.scale === 'day' ? '' : 'secondary'}" data-sc="day">Дни</button><button class="small ${F.scale === 'week' ? '' : 'secondary'}" data-sc="week">Недели</button></div></div>
      <div class="f"><span>Период</span><div class="row" style="gap:4px"><button class="small secondary" id="gt-prev" aria-label="Назад">◀</button><button class="small secondary" id="gt-today">Сегодня</button><button class="small secondary" id="gt-next" aria-label="Вперёд">▶</button><button class="small secondary" id="gt-full">Весь период</button></div></div>
    </div></div><div id="gt-sum" class="grid4" style="margin:10px 0"></div><div id="gt-chart"></div>
    <div class="fm-legend muted"><span><i class="gt-sw plan"></i>план</span><span><i class="gt-sw fact"></i>факт</span><span><i class="gt-sw low"></i>факт ниже плана</span><span><b class="gt-due-l">▎</b>срок заказа</span><span>◆ прогноз завершения</span><span class="gt-todayl">▎</span><span>сегодня</span></div>`;

  /* ---------- фильтр «Заказы» ---------- */
  const ddLabel = () => $('#gt-dd-s', v).textContent = F.lines.length ? `Выбрано: ${F.lines.length} ▾` : 'Все заказы ▾';
  const visibleLines = () => lines.filter(l => (!F.project || projects.items.find(p => String(p.id) === String(F.project))?.name === l.project) && (!lineSearch || (l.order + ' ' + l.product + ' ' + l.project).toLowerCase().includes(lineSearch)));
  const drawDD = () => {
    $('#gt-dd-l', v).innerHTML = visibleLines().map(l => `<label class="chk"><input type="checkbox" value="${l.id}" ${F.lines.includes(l.id) ? 'checked' : ''}><span>${esc(l.order)} · ${esc(l.product)}<span class="muted" style="display:block;font-size:12px">${esc(l.project)} · ${num(l.qty)} шт</span></span></label>`).join('') || '<span class="muted">Нет заказов</span>';
    $$('#gt-dd-l input', v).forEach(i => i.onchange = () => { const id = +i.value; F.lines = i.checked ? [...new Set([...F.lines, id])] : F.lines.filter(x => x !== id); ddLabel(); load(); });
    ddLabel();
  };
  $('#gt-dd-q', v).oninput = e => { lineSearch = e.target.value.trim().toLowerCase(); drawDD(); };
  $('#gt-all', v).onclick = () => { F.lines = []; drawDD(); load(); };
  $('#gt-sel', v).onclick = () => { F.lines = visibleLines().map(l => l.id); drawDD(); load(); };

  /* ---------- данные ---------- */
  const query = () => { const p = new URLSearchParams(); if (F.from) p.set('date_from', F.from); if (F.to) p.set('date_to', F.to); if (F.wc) p.set('work_center_id', F.wc); if (F.project) p.set('project_id', F.project); F.lines.forEach(i => p.append('order_line_id', i)); return p.toString(); };
  async function load() {
    try { D = await GET('/schedule?' + query()); F.from = D.date_from; F.to = D.date_to; details = {}; draw(true); }
    catch (e) { toast(errText(e), true); }
  }
  const shift = days => { F.from = gtAdd(F.from, days); F.to = gtAdd(F.to, days); load(); };
  const bind = (sel, key, ev = 'change') => { const el = $(sel, v); if (el) el.addEventListener(ev, () => { F[key] = el.value; if (key === 'project') { F.lines = []; drawDD(); } load(); }); };
  bind('#gt-wc', 'wc'); bind('#gt-pr', 'project');
  $('#gt-st', v).onchange = e => { F.status = e.target.value; draw(false); };
  $$('[data-sc]', v).forEach(b => b.onclick = () => { F.scale = b.dataset.sc; $$('[data-sc]', v).forEach(x => x.className = 'small' + (x.dataset.sc === F.scale ? '' : ' secondary')); draw(true); });
  $('#gt-prev', v).onclick = () => shift(-28); $('#gt-next', v).onclick = () => shift(28);
  $('#gt-today', v).onclick = () => { F.from = gtAdd(D.today, -14); F.to = gtAdd(D.today, 120); load(); };
  $('#gt-full', v).onclick = () => { if (!D.bounds.min) return; F.from = gtAdd(D.bounds.min, -2); F.to = D.bounds.max > gtAdd(F.from, 399) ? gtAdd(F.from, 399) : gtAdd(D.bounds.max, 7); load(); };

  /* ---------- диаграмма ---------- */
  function columns() {
    const cols = [], from = D.date_from, to = D.date_to;
    if (F.scale === 'week') { let s = gtAdd(from, -gtDow(from)); while (s <= to) { cols.push({ s, e: gtAdd(s, 6) }); s = gtAdd(s, 7); } }
    else { let s = from; while (s <= to) { cols.push({ s, e: s }); s = gtAdd(s, 1); } }
    return cols;
  }
  function strip(values, cols, W, today, max, scaleFor) {                          // values: iso → [план, факт]
    const idx = {}; cols.forEach((c, i) => { for (let d = c.s; d <= c.e; d = gtAdd(d, 1)) idx[d] = i; });
    const agg = cols.map(() => [0, 0, 0]); for (const [d, [p, f]] of Object.entries(values)) { const i = idx[d]; if (i != null) { agg[i][0] += p; agg[i][1] += f; if (d < today) agg[i][2] += p; } }
    let pl = '', fa = '';
    agg.forEach(([p, f, pastPlan], i) => {
      const x = i * W;
      if (p > 0) pl += `<i class="gt-c plan" style="left:${x}px;width:${W - 2}px;opacity:${(.3 + .7 * Math.min(1, p / max)).toFixed(2)}" title="${esc(cols[i].s === cols[i].e ? fmtD(cols[i].s) : fmtD(cols[i].s) + '–' + fmtD(cols[i].e))}: план ${num(p)} ${scaleFor}"></i>`;
      if (f > 0) fa += `<i class="gt-c ${pastPlan > 0 && f < pastPlan * .95 ? 'low' : 'fact'}" style="left:${x}px;width:${W - 2}px;opacity:${(.4 + .6 * Math.min(1, f / max)).toFixed(2)}" title="${esc(cols[i].s === cols[i].e ? fmtD(cols[i].s) : fmtD(cols[i].s) + '–' + fmtD(cols[i].e))}: факт ${num(f)} ${scaleFor}${p > 0 ? ' из плана ' + num(p) : ''}"></i>`;
    });
    return `<div class="gt-strip">${pl}</div><div class="gt-strip">${fa}</div>`;
  }
  const xOf = (iso, cols, W) => { if (!iso) return null; for (let i = 0; i < cols.length; i++) if (iso >= cols[i].s && iso <= cols[i].e) return i * W + (cols[i].s === cols[i].e ? W / 2 : (gtDow(iso) + .5) / 7 * W); return null; };
  function draw(rescroll) {
    if (!D) return;
    const cols = columns(), W = F.scale === 'week' ? GT.WEEK : GT.DAY, width = cols.length * W, today = D.today, orders = D.orders.filter(o => !F.status || o.status === F.status);
    const maxOrder = Math.max(0.5, ...D.orders.flatMap(o => Object.values(o.days).map(x => Math.max(...x)))), maxTot = Math.max(0.5, ...Object.values(D.totals).map(x => Math.max(...x)));
    const maxAgg = F.scale === 'week' ? Math.max(1, maxOrder * 3) : maxOrder, maxTotAgg = F.scale === 'week' ? Math.max(1, maxTot * 3) : maxTot;
    const risk = orders.filter(o => o.status === 'Риск опоздания').length, done = orders.filter(o => o.status === 'Готово').length;
    const tp = Object.values(D.totals).reduce((a, x) => a + x[0], 0), tf = Object.values(D.totals).reduce((a, x) => a + x[1], 0);
    $('#gt-sum', v).innerHTML = `<div class="kpi k-blue">Заказов<b>${orders.length}</b></div><div class="kpi ${risk ? 'k-amber' : 'k-green'}">Риск опоздания<b>${risk}</b></div><div class="kpi k-green">Готово<b>${done}</b></div><div class="kpi k-grey">План / факт за период, нормо-дни<b>${num(tp)} / ${num(tf)}</b></div>`;
    if (!orders.length) { $('#gt-chart', v).innerHTML = '<div class="card muted">Нет заказов по выбранным фильтрам.</div>'; return; }
    // шапка: месяцы и дни/недели
    let months = '', run = null;
    cols.forEach((c, i) => { const m = c.s.slice(0, 7); if (!run || run.m !== m) { if (run) months += `<span style="left:${run.i * W}px;width:${(i - run.i) * W}px">${GT_MON[+run.m.slice(5) - 1]} ${run.m.slice(0, 4)}</span>`; run = { m, i }; } });
    if (run) months += `<span style="left:${run.i * W}px;width:${(cols.length - run.i) * W}px">${GT_MON[+run.m.slice(5) - 1]} ${run.m.slice(0, 4)}</span>`;
    const hdr = cols.map((c, i) => F.scale === 'week' ? `<span style="left:${i * W}px;width:${W}px" class="${c.s <= today && today <= c.e ? 'now' : ''}">${fmtD(c.s)}</span>` : `<span style="left:${i * W}px;width:${W}px" class="${gtDow(c.s) > 4 ? 'we' : ''} ${c.s === today ? 'now' : ''}"><b>${+c.s.slice(8)}</b><small>${GT_WD[gtDow(c.s)]}</small></span>`).join('');
    const grid = (F.scale === 'day' ? cols.map((c, i) => gtDow(c.s) > 4 ? `<i class="gt-we" style="left:${i * W}px;width:${W}px"></i>` : '').join('') : cols.map((c, i) => i % 2 ? `<i class="gt-we" style="left:${i * W}px;width:${W}px"></i>` : '').join('')) + (xOf(today, cols, W) != null ? `<i class="gt-today" style="left:${xOf(today, cols, W)}px"></i>` : '');
    const marks = o => { const d = xOf(o.due, cols, W), f = xOf(o.forecast, cols, W); return (d != null ? `<i class="gt-due" style="left:${d}px" title="Срок ${fmtDY(o.due)}"></i>` : '') + (f != null ? `<i class="gt-fc ${o.due && o.forecast > o.due ? 'late' : ''}" style="left:${f}px" title="Прогноз завершения ${fmtDY(o.forecast)}"></i>` : ''); };
    let rows = `<div class="gt-row gt-tot"><div class="gt-left"><b>Итого по выбранным</b><div class="muted" style="font-size:12px">нормо-дни</div></div><div class="gt-track" style="width:${width}px">${strip(D.totals, cols, W, today, maxTotAgg, 'н-д')}</div></div>`;
    let proj = null;
    for (const o of orders) {
      if (o.project !== proj) { proj = o.project; rows += `<div class="gt-row gt-proj"><div class="gt-left"><b>${esc(proj || 'Без проекта')}</b></div><div class="gt-track" style="width:${width}px"></div></div>`; }
      const isOpen = !!open[o.id];
      rows += `<div class="gt-row gt-ord" data-o="${o.id}" tabindex="0" role="button" aria-expanded="${isOpen}"><div class="gt-left"><div class="gt-t"><span class="gt-car">${isOpen ? '▾' : '▸'}</span><b>${esc(o.order)}</b> · ${esc(o.product)}</div>
        <div class="gt-m"><span class="badge ${gtStatusCls(o.status)}">${esc(o.status)}</span><span class="bar gt-bar"><i style="width:${o.progress}%"></i></span><span>${num(o.progress)}%</span></div></div>
        <div class="gt-track" style="width:${width}px" title="${esc(o.order)} · ${esc(o.product)}">${strip(o.days, cols, W, today, maxAgg, 'н-д')}${marks(o)}</div></div>`;
      if (isOpen) rows += (details[o.id] ? details[o.id].operations.map(p => subRow(p, cols, W, today, width)).join('') : `<div class="gt-row gt-sub"><div class="gt-left muted">Загрузка операций…</div><div class="gt-track" style="width:${width}px"></div></div>`);
    }
    $('#gt-chart', v).innerHTML = `<div class="gt-scroll" id="gt-scroll"><div class="gt-inner" style="width:${GT.LEFT + width}px"><div class="gt-head"><div class="gt-left"></div><div class="gt-hd" style="width:${width}px"><div class="gt-mon">${months}</div><div class="gt-days">${hdr}</div></div></div>
      <div class="gt-body"><div class="gt-grid" style="left:${GT.LEFT}px;width:${width}px">${grid}</div>${rows}</div></div></div>${D.truncated ? `<p class="muted">Показаны первые ${D.orders.length} заказов — сузьте выбор фильтрами.</p>` : ''}`;
    $$('.gt-ord', v).forEach(r => { const toggle = () => toggleOrder(+r.dataset.o); r.onclick = toggle; r.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } }; });
    if (rescroll) { const sc = $('#gt-scroll', v), x = xOf(today, cols, W); if (sc && x != null) sc.scrollLeft = Math.max(0, x - 120); }
  }
  function subRow(p, cols, W, today, width) {
    const max = Math.max(1, ...Object.values(p.days).map(x => Math.max(...x)));
    return `<div class="gt-row gt-sub"><div class="gt-left"><div class="gt-t">${esc(p.name)}</div><div class="gt-m muted"><span>${num(p.done)} из ${num(p.required)} ${esc(p.unit)}</span><span class="bar gt-bar"><i style="width:${p.pct}%"></i></span><span>${num(p.pct)}%</span></div>${p.sections.length ? `<div class="muted" style="font-size:12px">${esc(p.sections.join(', '))}</div>` : ''}</div><div class="gt-track" style="width:${width}px">${strip(p.days, cols, W, today, F.scale === 'week' ? max * 2 : max, esc(p.unit))}</div></div>`;
  }
  async function toggleOrder(id) {
    open[id] = !open[id]; draw(false); if (!open[id] || details[id]) return;
    try { details[id] = await GET(`/schedule/${id}/operations?date_from=${D.date_from}&date_to=${D.date_to}${F.wc ? '&work_center_id=' + F.wc : ''}`); draw(false); } catch (e) { open[id] = false; toast(errText(e), true); draw(false); }
  }
  drawDD(); await load();
};
