/* «Карта потока»: операции карточками на доске, связи-стрелки (предшественники), цвет карточки — доля выполнения по заказу.
   Карточка — операция версии маршрута: добавление/удаление/связи меняют маршрут (все заказы на этой версии). */
const FM = { W: 214, H: 96, GX: 76, GY: 34, MIN: 0.35, MAX: 1.8 };

SCREENS.flow = async v => {
  const t = await GET('/flow-map/targets');
  if (!t.lines.length && !t.routings.length) { v.innerHTML = '<h2>Карта потока</h2><div class="card muted">Пока нет ни заказов, ни маршрутов изделий. Добавьте изделие в «Каталоге изделий».</div>'; return; }
  const valid = k => k && (k[0] === 'l' ? t.lines.some(l => l.id === +k.slice(2)) : t.routings.some(r => r.id === +k.slice(2)));
  let sel = valid(S.flowSel) ? S.flowSel : (t.lines.length ? 'l:' + t.lines[0].id : 'r:' + t.routings[0].id);
  let M = null, P = {}, view = { x: 30, y: 20, k: 1 }, pick = null, saveTimer = null, dirty = new Set();       // pick: {type:'node'|'edge', id|from,to}
  const group = (arr, key) => arr.reduce((m, x) => ((m[key(x)] = m[key(x)] || []).push(x), m), {});
  const lineOpts = Object.entries(group(t.lines, l => l.project)).map(([p, ls]) => `<optgroup label="${esc(p)}">${ls.map(l => `<option value="l:${l.id}" ${sel === 'l:' + l.id ? 'selected' : ''}>${esc(l.order)} · ${esc(l.product)} (${num(l.qty)} шт)</option>`).join('')}</optgroup>`).join('');
  const rtOpts = t.routings.length ? `<optgroup label="Версии маршрутов (без выполнения)">${t.routings.map(r => `<option value="r:${r.id}" ${sel === 'r:' + r.id ? 'selected' : ''}>${esc(r.product)} — версия ${r.version_no}${r.name ? ' «' + esc(r.name) + '»' : ''}${r.current ? '' : ' (не текущая)'}</option>`).join('')}</optgroup>` : '';
  v.innerHTML = `<div class="row" style="margin-bottom:8px"><h2 class="grow" style="margin:0">Карта потока</h2></div>
    <div class="card fm-bar"><label class="f fm-sel">Заказ или маршрут<select id="fm-sel">${lineOpts}${rtOpts}</select></label>
      <div class="row fm-tools"><button class="small" id="fm-add" hidden>+ Карточка</button><button class="small secondary" id="fm-auto" hidden>Расставить автоматически</button>
      <button class="small secondary" id="fm-zo" title="Уменьшить" aria-label="Уменьшить">−</button><button class="small secondary" id="fm-zi" title="Увеличить" aria-label="Увеличить">+</button><button class="small secondary" id="fm-fit">Вписать</button></div></div>
    <div id="fm-head" class="fm-head"></div>
    <div class="fm-wrap"><div class="fm-board" id="fm-board" tabindex="0" aria-label="Доска операций"><div class="fm-world" id="fm-world"><svg class="fm-svg" id="fm-svg" width="1" height="1"></svg></div></div>
      <aside class="fm-side card" id="fm-side" aria-live="polite"></aside></div>
    <div class="fm-legend muted"><span><i class="fm-dot d0"></i>не начата</span><span><i class="fm-dot d1"></i>в работе (заливка — доля выполнения)</span><span><i class="fm-dot d2"></i>выполнена</span><span>── связь «после»</span><span>- - связь по порядку операций</span></div>`;
  const board = $('#fm-board', v), world = $('#fm-world', v), svg = $('#fm-svg', v), side = $('#fm-side', v);
  const NS = 'http://www.w3.org/2000/svg', canEdit = () => !!(M && M.can_edit);

  /* ---------- раскладка ---------- */
  function auto(nodes, edges) {
    const lvl = {}; nodes.forEach(n => lvl[n.id] = 0);
    for (let i = 0; i < nodes.length + 1; i++) { let ch = false; for (const e of edges) if (lvl[e.to] < lvl[e.from] + 1) { lvl[e.to] = lvl[e.from] + 1; ch = true; } if (!ch) break; }
    const rows = {}, out = {};
    [...nodes].sort((a, b) => a.sequence - b.sequence || a.id - b.id).forEach(n => { const r = rows[lvl[n.id]] = (rows[lvl[n.id]] || 0); out[n.id] = { x: 20 + lvl[n.id] * (FM.W + FM.GX), y: 20 + r * (FM.H + FM.GY) }; rows[lvl[n.id]]++; });
    return out;
  }
  const place = () => { const a = auto(M.nodes, M.edges); P = {}; M.nodes.forEach(n => P[n.id] = n.x != null && n.y != null ? { x: n.x, y: n.y } : a[n.id]); };
  const apply = () => { world.style.transform = `translate(${view.x}px,${view.y}px) scale(${view.k})`; };
  const fit = () => {
    const xs = M.nodes.map(n => P[n.id]); if (!xs.length) { view = { x: 30, y: 20, k: 1 }; return apply(); }
    const x0 = Math.min(...xs.map(p => p.x)) - 20, y0 = Math.min(...xs.map(p => p.y)) - 20, x1 = Math.max(...xs.map(p => p.x)) + FM.W + 20, y1 = Math.max(...xs.map(p => p.y)) + FM.H + 20;
    const bw = board.clientWidth || 900, bh = board.clientHeight || 520, k = Math.max(FM.MIN, Math.min(1, bw / (x1 - x0), bh / (y1 - y0)));
    view = { k, x: (bw - (x1 - x0) * k) / 2 - x0 * k, y: Math.max(8, (bh - (y1 - y0) * k) / 2) - y0 * k }; apply();
  };
  const toWorld = (cx, cy) => { const r = board.getBoundingClientRect(); return { x: (cx - r.left - view.x) / view.k, y: (cy - r.top - view.y) / view.k }; };

  /* ---------- рисование ---------- */
  const pathOf = (a, b) => { const x1 = a.x + FM.W, y1 = a.y + FM.H / 2, x2 = b.x, y2 = b.y + FM.H / 2, d = Math.max(50, Math.abs(x2 - x1) / 2); return `M${x1},${y1} C${x1 + d},${y1} ${x2 - d},${y2} ${x2},${y2}`; };
  const el = (name, attrs) => { const e = document.createElementNS(NS, name); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
  function drawEdges() {
    svg.innerHTML = '<defs><marker id="fm-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker></defs>';
    for (const e of M.edges) {
      const a = P[e.from], b = P[e.to]; if (!a || !b) continue; const d = pathOf(a, b), on = pick && pick.type === 'edge' && pick.from === e.from && pick.to === e.to;
      const g = el('g', { class: 'fm-edge' + (e.implicit ? ' imp' : '') + (on ? ' on' : ''), 'data-from': e.from, 'data-to': e.to });
      g.appendChild(el('path', { d, class: 'fm-hit' })); g.appendChild(el('path', { d, class: 'fm-line', 'marker-end': 'url(#fm-arr)' }));
      if (e.pct != null) {                                                  // партия по связи: «после N % предшественника»
        const mx = (a.x + FM.W + b.x) / 2, my = (a.y + b.y) / 2 + FM.H / 2, t = el('text', { x: mx, y: my + 4, class: 'fm-pct', 'text-anchor': 'middle' }); t.textContent = num(e.pct) + '%';
        g.appendChild(el('rect', { x: mx - 20, y: my - 9, width: 40, height: 18, rx: 9, class: 'fm-pctbg' })); g.appendChild(t);
      }
      svg.appendChild(g);
    }
  }
  const stateOf = n => n.foreign ? 'foreign' : n.pct == null ? 'plain' : n.pct >= 99.95 ? 'done' : n.pct > 0 ? 'part' : 'none';
  function drawCards() {
    world.querySelectorAll('.fm-card').forEach(c => c.remove());
    for (const n of M.nodes) {
      const c = document.createElement('div'), st = stateOf(n), pct = n.pct == null ? 0 : n.pct;
      c.className = `fm-card st-${st}${pick && pick.type === 'node' && pick.id === n.id ? ' sel' : ''}`; c.dataset.id = n.id; c.tabIndex = 0; c.setAttribute('role', 'button');
      c.style.cssText = `left:${P[n.id].x}px;top:${P[n.id].y}px;width:${FM.W}px;height:${FM.H}px`;
      const meta = M.line ? (n.foreign ? 'другой участок' : `${num(n.done)} из ${num(n.required)} ${esc(n.unit)} · ${num(pct)}%`) : `норма ${num(n.norm_per_day)} ${esc(n.unit)}/день`;
      c.innerHTML = `<div class="fm-fill" style="width:${pct}%;--p:${pct}%"></div><div class="fm-body"><div class="fm-name" title="${esc(n.name)}">${esc(n.name)}</div><div class="fm-meta">${meta}</div>${M.line && !n.foreign && n.sections.length ? `<div class="fm-sec">${esc(n.sections.join(', '))}</div>` : ''}</div>${st === 'done' ? '<span class="fm-ok" aria-label="выполнена">✔</span>' : ''}<span class="fm-in"></span>${canEdit() ? '<span class="fm-out" title="Потяните к другой карточке, чтобы указать, что она идёт после этой"></span>' : ''}`;
      c.setAttribute('aria-label', `${n.name}: ${n.pct == null ? '' : num(pct) + '%'}`); world.appendChild(c);
    }
  }
  function drawHead() {
    const r = M.routing, l = M.line;
    $('#fm-head', v).innerHTML = l ? `<div class="card fm-line-head"><div><b>${esc(r.product)}</b> · заказ ${esc(l.order)} · ${num(l.qty)} шт${l.due ? ` · срок ${fmtDY(l.due)}` : ''}<div class="muted" style="font-size:13px">Версия маршрута ${r.version_no}${r.name ? ' «' + esc(r.name) + '»' : ''}</div></div>
      <div class="fm-total"><div class="bar"><i style="width:${l.percent}%"></i></div><b>${num(l.percent)}%</b> заказа выполнено</div></div>`
      : `<div class="card fm-line-head"><div><b>${esc(r.product)}</b> · версия ${r.version_no}${r.name ? ' «' + esc(r.name) + '»' : ''}<div class="muted" style="font-size:13px">Структура маршрута. Чтобы увидеть выполнение, выберите позицию заказа.</div></div></div>`;
    $('#fm-add', v).hidden = !canEdit(); $('#fm-auto', v).hidden = !canEdit();
  }
  function drawSide() {
    if (!M) return;
    const warn = M.routing.used && canEdit() ? '<p class="muted fm-note">Изменения касаются всех заказов на этой версии маршрута.</p>' : '';
    if (pick && pick.type === 'edge') {
      const a = M.nodes.find(n => n.id === pick.from), b = M.nodes.find(n => n.id === pick.to);
      const ed = M.edges.find(x => x.from === pick.from && x.to === pick.to), dflt = b.default_pct != null && b.default_pct < 100 ? b.default_pct : 100;
      side.innerHTML = `<h3>Связь</h3><p><b>${esc(a.name)}</b> → <b>${esc(b.name)}</b></p><p class="muted">«${esc(b.name)}» начинается, когда «${esc(a.name)}» выполнена на указанный процент партии.</p>
        ${canEdit() ? `<label class="f">Партия: «${esc(b.name)}» стартует после, %<input type="number" id="fm-pct" min="1" max="100" step="any" value="${ed && ed.pct_own ? ed.pct : ''}" placeholder="${dflt}"></label><p class="muted fm-note">Пусто — общая партия операции (${dflt}%). 100 — только после полного окончания.</p><button class="danger" id="fm-deledge">Удалить связь</button>${warn}` : `<p>Партия: <b>${ed && ed.pct != null ? num(ed.pct) : dflt}%</b></p>`}`;
      const d = $('#fm-deledge', side); if (d) d.onclick = () => delLink(pick.from, pick.to);
      const pc = $('#fm-pct', side); if (pc) pc.onchange = () => setPct(pick.from, pick.to, pc.value.trim() === '' ? null : Number(pc.value)); return;
    }
    const n = pick && pick.type === 'node' ? M.nodes.find(x => x.id === pick.id) : null;
    if (!n) { side.innerHTML = `<h3>Как пользоваться</h3><ul class="fm-help"><li>Карточка — операция маршрута. Заливка показывает, какая часть объёма по заказу уже выполнена.</li>${canEdit() ? '<li>Потяните карточку, чтобы переставить. Потяните точку справа на карточке к другой — получится связь «после».</li><li>Нажмите на стрелку, чтобы удалить связь.</li><li>«+ Карточка» добавляет операцию.</li>' : '<li>Нажмите на карточку или стрелку, чтобы увидеть подробности.</li>'}<li>Колесо мыши — масштаб, перетаскивание фона — сдвиг.</li></ul>`; return; }
    const others = M.nodes.filter(x => x.id !== n.id && !n.predecessors.includes(x.name));
    side.innerHTML = `<h3>${esc(n.name)}</h3>
      <dl class="fm-dl">${M.line ? (n.foreign ? '<dt>Выполнение</dt><dd class="muted">другой участок</dd>' : `<dt>Выполнено</dt><dd><b>${num(n.done)}</b> из ${num(n.required)} ${esc(n.unit)} (${num(n.pct)}%)</dd>${n.sections.length ? `<dt>Участки</dt><dd>${esc(n.sections.join(', '))}</dd>` : ''}`) : ''}
        <dt>Норма в день</dt><dd>${num(n.norm_per_day)} ${esc(n.unit)}</dd><dt>На изделие</dt><dd>${num(n.per_item)}</dd><dt>Порядок</dt><dd>${num(n.sequence)}</dd></dl>
      ${workBlock(n)}
      <h4>Идёт после</h4>${n.predecessors.length ? `<ul class="fm-preds">${n.predecessors.map(p => `<li><span>${esc(p)}<span class="muted fm-pp">${(n.predecessor_pcts || {})[p] != null ? ' · после ' + num(n.predecessor_pcts[p]) + '%' : (n.default_pct != null && n.default_pct < 100 ? ' · после ' + num(n.default_pct) + '%' : '')}</span></span>${canEdit() ? ` <span class="fm-pps"><input type="number" min="1" max="100" step="any" data-pp="${esc(p)}" value="${(n.predecessor_pcts || {})[p] ?? ''}" placeholder="${n.default_pct != null && n.default_pct < 100 ? n.default_pct : 100}" aria-label="Партия по предшественнику ${esc(p)}, %" title="Партия, %: когда ${esc(p)} выполнена на столько, ${esc(n.name)} может стартовать"><button class="small secondary" data-rm="${esc(p)}" aria-label="Убрать связь с ${esc(p)}">×</button></span>` : ''}</li>`).join('')}</ul>` : '<p class="muted">Нет — операция может начинаться сразу.</p>'}
      ${n.implicit && n.predecessors.length ? '<p class="muted fm-note">Связи заданы порядком операций (пунктир). При изменении они станут явными.</p>' : ''}
      ${canEdit() ? `<label class="f">Добавить предшественника<select id="fm-padd"><option value="">— выбрать —</option>${others.map(o => `<option value="${o.id}">${esc(o.name)}</option>`).join('')}</select></label><button class="danger" id="fm-delnode">Удалить карточку</button>${warn}` : ''}`;
    wireWork(n);
    $$('[data-rm]', side).forEach(b => b.onclick = () => delLink(M.nodes.find(x => x.name === b.dataset.rm).id, n.id));
    $$('[data-pp]', side).forEach(i => i.onchange = () => setPct(M.nodes.find(x => x.name === i.dataset.pp).id, n.id, i.value.trim() === '' ? null : Number(i.value)));
    const pa = $('#fm-padd', side); if (pa) pa.onchange = () => pa.value && addLink(+pa.value, n.id);
    const dn = $('#fm-delnode', side); if (dn) dn.onclick = () => deleteOperation(n.id, async () => { pick = null; await load(true); });
  }

  /* ---------- работа по заказу из карточки: факт, норма, исполнители ---------- */
  function workBlock(n) {
    if (n.foreign) return '';
    let h = '';
    if (M.line && M.can_fact) {
      const secs = (n.center_ids || []).map(id => (M.sections || []).find(s => s.id === id)).filter(Boolean);
      h += `<h4>Внести факт</h4><p class="muted fm-note">За любую прошедшую дату. Повторный ввод за ту же дату заменяет значение.</p>
        <label class="f">Дата<input type="date" id="fm-fd" value="${M.today}" max="${M.today}"></label>
        <label class="f">Выполнено за день, ${esc(n.unit)}<input type="number" id="fm-fq" min="0" step="any" inputmode="decimal" placeholder="осталось ${num(Math.max((n.required || 0) - (n.done || 0), 0))}"></label>
        ${secs.length > 1 ? `<label class="f">Участок<select id="fm-fs">${secs.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select></label>` : ''}
        <button class="small" id="fm-fact">Внести факт</button>
        ${(n.facts || []).length ? `<ul class="fm-preds">${n.facts.map(f => `<li><span>${dd(f.date)} — <b>${num(f.qty)}</b> ${esc(n.unit)}<span class="muted fm-pp">${f.worker ? ' · ' + esc(f.worker) : ' · на заказ'}</span></span></li>`).join('')}</ul>` : ''}`;
    }
    if (canEdit()) {
      h += `<h4>Норма и количество</h4>
        <label class="f">Норма в день, ${esc(n.unit)}<input type="number" id="fm-norm" min="0" step="any" value="${n.norm_per_day}"></label>
        <label class="f">Операций на изделие<input type="number" id="fm-per" min="0" step="any" value="${n.per_item}"></label>
        <button class="small secondary" id="fm-saveop">Сохранить норму</button>
        ${M.routing.used ? '<p class="muted fm-note">Изменится во всех заказах на этой версии маршрута. После сохранения перепланируйте участки.</p>' : ''}`;
    }
    if (M.line && M.can_assign) {
      const ok = (M.workers || []).filter(w => (!(n.center_ids || []).length || n.center_ids.includes(w.work_center_id)) && w.skill >= n.min_skill);
      const sname = id => ((M.sections || []).find(s => s.id === id) || {}).name || '';
      h += `<h4>Исполнители</h4>${ok.length ? `<div class="fm-execs">${ok.map(w => `<label class="chk"><input type="checkbox" data-ex="${w.id}" ${(n.assignees || []).includes(w.id) ? 'checked' : ''}> <span>${esc(w.name)}${sname(w.work_center_id) ? `<span class="muted"> · ${esc(sname(w.work_center_id))}</span>` : ''}</span></label>`).join('')}</div>
        <label class="chk"><input type="checkbox" id="fm-strict" ${n.assign_strict !== false ? 'checked' : ''}> <span>Только они (иначе — они в первую очередь, остальных добавит планировщик)</span></label>
        <button class="small secondary" id="fm-saveex">Сохранить исполнителей</button><p class="muted fm-note">Планировщик учтёт назначение при следующем перепланировании.</p>` : '<p class="muted">Нет подходящих работников (участок, квалификация, доступность).</p>'}`;
    }
    return h;
  }
  function wireWork(n) {
    const run = async (btn, fn) => { btn.disabled = true; try { await fn(); } catch (e) { toast(errText(e), true); } finally { btn.disabled = false; } };
    const f = $('#fm-fact', side);
    if (f) f.onclick = () => run(f, async () => {
      const qty = $('#fm-fq', side).value.trim(), date = $('#fm-fd', side).value, sec = $('#fm-fs', side);
      if (qty === '' || isNaN(+qty) || +qty < 0) return toast('Введите количество', true);
      if (!date || date > M.today) return toast('Выберите сегодняшнюю или прошедшую дату', true);
      const wc = sec ? +sec.value : ((n.center_ids || []).length === 1 ? n.center_ids[0] : null);
      const r = await POST('/facts/order', { fact_date: date, order_line_id: M.line.id, operation_id: n.id, qty: +qty, work_center_id: wc });
      toast(r.replaced ? `Факт за ${dd(date)} заменён (было ${num(r.previous_qty)})` : 'Факт внесён');
      await load(true);
    });
    const so = $('#fm-saveop', side);
    if (so) so.onclick = () => run(so, async () => {
      const norm = +$('#fm-norm', side).value, per = +$('#fm-per', side).value;
      if (!(norm > 0) || !(per >= 0)) return toast('Норма должна быть больше нуля, количество — не меньше нуля', true);
      if (norm === n.norm_per_day && per === n.per_item) return toast('Значения не изменились');
      const r = await PATCH(`/operations/${n.id}`, { norm_per_day: norm, per_item: per, version: n.version });
      toast(r.orders_affected ? `Сохранено. Затронуто заказов: ${r.orders_affected}. Перепланируйте участки` : 'Сохранено');
      await load(true);
    });
    const se = $('#fm-saveex', side);
    if (se) se.onclick = () => run(se, async () => {
      const ids = $$('[data-ex]', side).filter(i => i.checked).map(i => +i.dataset.ex);
      await PUT('/plan-grid/executors', { order_line_id: M.line.id, operation_id: n.id, worker_ids: ids, strict: $('#fm-strict', side).checked });
      toast(ids.length ? 'Исполнители сохранены. Перепланируйте участок' : 'Назначение снято');
      await load(true);
    });
  }
  const redraw = () => { drawEdges(); drawCards(); drawSide(); };

  /* ---------- данные ---------- */
  async function load(keep) {
    try {
      M = await GET('/flow-map?' + (sel[0] === 'l' ? 'order_line_id=' : 'routing_id=') + sel.slice(2));
      if (pick && pick.type === 'node' && !M.nodes.some(n => n.id === pick.id)) pick = null;
      if (pick && pick.type === 'edge' && !M.edges.some(e => e.from === pick.from && e.to === pick.to)) pick = null;
      place(); drawHead(); redraw(); if (!keep) fit();
    } catch (e) { toast(errText(e), true); side.innerHTML = `<div class="err">${esc(errText(e))}</div>`; }
  }
  async function addLink(from, to) { try { const r = await POST(`/flow-map/${M.routing.id}/links`, { from_id: from, to_id: to }); if (r.shifted && r.shifted.length) toast('Связь добавлена; порядок операций обновлён'); pick = { type: 'node', id: to }; await load(true); } catch (e) { toast(errText(e), true); } }
  async function setPct(from, to, pct) { try { await PUT(`/flow-map/${M.routing.id}/links/pct`, { from_id: from, to_id: to, pct }); } catch (e) { toast(errText(e), true); } await load(true); }
  async function delLink(from, to) { try { await DEL(`/flow-map/${M.routing.id}/links?from_id=${from}&to_id=${to}`); pick = { type: 'node', id: to }; await load(true); } catch (e) { toast(errText(e), true); } }
  const saveLayout = () => { clearTimeout(saveTimer); saveTimer = setTimeout(async () => { const ids = [...dirty]; dirty.clear(); if (!ids.length || !canEdit()) return;
    try { await PUT(`/flow-map/${M.routing.id}/layout`, { positions: ids.map(id => ({ id, x: P[id].x, y: P[id].y })) }); } catch (e) { toast(errText(e), true); } }, 400); };

  /* ---------- перетаскивание, связи, сдвиг, масштаб ---------- */
  let drag = null;
  board.addEventListener('pointerdown', e => {
    const card = e.target.closest('.fm-card'), edge = e.target.closest('.fm-edge');
    if (e.target.closest('.fm-out') && card && canEdit()) {
      const id = +card.dataset.id; drag = { kind: 'link', from: id, tmp: el('path', { class: 'fm-line fm-tmp', 'marker-end': 'url(#fm-arr)' }) }; svg.appendChild(drag.tmp); board.setPointerCapture?.(e.pointerId); e.preventDefault(); return;
    }
    if (card) { const id = +card.dataset.id; drag = { kind: 'card', id, sx: e.clientX, sy: e.clientY, ox: P[id].x, oy: P[id].y, moved: false }; board.setPointerCapture?.(e.pointerId); return; }
    if (edge) { pick = { type: 'edge', from: +edge.dataset.from, to: +edge.dataset.to }; redraw(); return; }
    drag = { kind: 'pan', sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y, moved: false }; board.setPointerCapture?.(e.pointerId);
  });
  board.addEventListener('pointermove', e => {
    if (!drag) return;
    if (drag.kind === 'pan') { if (Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 3) drag.moved = true; view.x = drag.ox + e.clientX - drag.sx; view.y = drag.oy + e.clientY - drag.sy; apply(); }
    else if (drag.kind === 'card') {
      const dx = (e.clientX - drag.sx) / view.k, dy = (e.clientY - drag.sy) / view.k; if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
      if (!canEdit()) return; drag.moved = true; P[drag.id] = { x: Math.round(drag.ox + dx), y: Math.round(drag.oy + dy) };
      const c = world.querySelector(`.fm-card[data-id="${drag.id}"]`); c.style.left = P[drag.id].x + 'px'; c.style.top = P[drag.id].y + 'px'; drawEdges();
    } else if (drag.kind === 'link') { const w = toWorld(e.clientX, e.clientY), a = P[drag.from]; drag.tmp.setAttribute('d', pathOf(a, { x: w.x, y: w.y - FM.H / 2 })); }
  });
  const finish = e => {
    if (!drag) return; const d = drag; drag = null; board.releasePointerCapture?.(e.pointerId);
    if (d.kind === 'link') { d.tmp.remove(); const t2 = document.elementFromPoint(e.clientX, e.clientY)?.closest('.fm-card'); if (t2 && +t2.dataset.id !== d.from) addLink(d.from, +t2.dataset.id); return; }
    if (d.kind === 'card') { if (d.moved) { dirty.add(d.id); saveLayout(); } else { pick = { type: 'node', id: d.id }; redraw(); } return; }
    if (d.kind === 'pan' && !d.moved) { pick = null; redraw(); }
  };
  board.addEventListener('pointerup', finish); board.addEventListener('pointercancel', e => { if (drag && drag.kind === 'link') drag.tmp.remove(); drag = null; });
  board.addEventListener('keydown', e => { const c = e.target.closest?.('.fm-card'); if (c && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); pick = { type: 'node', id: +c.dataset.id }; redraw(); } });
  const zoom = (f, cx, cy) => { const r = board.getBoundingClientRect(), px = (cx ?? r.left + r.width / 2) - r.left, py = (cy ?? r.top + r.height / 2) - r.top, k = Math.max(FM.MIN, Math.min(FM.MAX, view.k * f)); view.x = px - (px - view.x) * (k / view.k); view.y = py - (py - view.y) * (k / view.k); view.k = k; apply(); };
  board.addEventListener('wheel', e => { e.preventDefault(); zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1, e.clientX, e.clientY); }, { passive: false });
  $('#fm-zi', v).onclick = () => zoom(1.2); $('#fm-zo', v).onclick = () => zoom(1 / 1.2); $('#fm-fit', v).onclick = () => M && fit();
  $('#fm-sel', v).onchange = e => { sel = S.flowSel = e.target.value; pick = null; load(); };
  $('#fm-auto', v).onclick = async () => { const a = auto(M.nodes, M.edges); try { await PUT(`/flow-map/${M.routing.id}/layout`, { positions: M.nodes.map(n => ({ id: n.id, x: a[n.id].x, y: a[n.id].y })) }); await load(); toast('Карточки расставлены'); } catch (e) { toast(errText(e), true); } };
  $('#fm-add', v).onclick = () => flowAddCard(M, P, view, board, async id => { pick = { type: 'node', id }; await load(true); });
  S.flowSel = sel; await load();
};

/* Окно «+ Карточка»: новая операция маршрута; после создания ставится в центр видимой части доски */
function flowAddCard(M, P, view, board, done) {
  const maxSeq = Math.max(0, ...M.nodes.map(n => n.sequence));
  const m = modal(`<h2>Новая карточка</h2><p class="hint">Операция добавится в маршрут${M.routing.used ? ' и во все заказы на этой версии' : ''}.</p><form novalidate>
    <label class="f">Название *<input name="name" maxlength="160" required></label>
    <div class="row"><label class="f grow">Норма в день *<input type="number" name="norm" min="0.01" step="any" required></label><label class="f">Единица<input name="unit" value="шт" maxlength="24" style="max-width:100px"></label></div>
    <div class="row"><label class="f grow">Объём на изделие<input type="number" name="per" min="0.01" step="any" value="1"></label><label class="f grow">Квалификация (уровень)<input type="number" name="skill" min="1" step="1" value="1"></label></div>
    <fieldset style="border:1px solid var(--line);border-radius:6px;margin-bottom:10px"><legend class="muted">Идёт после</legend>${M.nodes.length ? `<div class="row">${M.nodes.map(n => `<label class="chip"><input type="checkbox" name="pre" value="${esc(n.name)}"><span>${esc(n.name)}</span></label>`).join('')}</div>` : '<span class="muted">В маршруте пока нет операций.</span>'}</fieldset>
    <div class="err" data-err role="alert"></div><div class="row"><button type="submit">Добавить</button><button type="button" class="secondary" data-x>Отмена</button></div></form>`);
  const form = $('form', m.el), E = form.elements; $('[data-x]', m.el).onclick = m.close;
  form.onsubmit = async e => {
    e.preventDefault(); const er = $('[data-err]', m.el); er.textContent = ''; const btn = $('button[type=submit]', m.el); btn.disabled = true;
    try {
      const pre = $$('[name=pre]:checked', form).map(i => i.value);
      const r = await POST(`/routings/${M.routing.id}/operations`, { name: E.name.value.trim(), norm_per_day: +E.norm.value, unit: E.unit.value.trim() || 'шт', per_item: +E.per.value || 1, min_skill: +E.skill.value || 1, sequence: maxSeq + 1, predecessors: pre });
      const br = board.getBoundingClientRect(), x = Math.round((br.width / 2 - view.x) / view.k - FM.W / 2), y = Math.round((br.height / 2 - view.y) / view.k - FM.H / 2);
      try { await PUT(`/flow-map/${M.routing.id}/layout`, { positions: [{ id: r.id, x, y }] }); } catch (x2) { /* положение не критично */ }
      m.close(); toast(r.orders_undistributed ? `Карточка добавлена. В ${r.orders_undistributed} заказах операцию нужно распределить по участкам` : 'Карточка добавлена'); done(r.id);
    } catch (x) { er.textContent = errText(x); btn.disabled = false; }
  };
}
