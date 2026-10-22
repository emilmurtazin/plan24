/* Мини-приложение Telegram для сборщика и бригадира. Без внешних библиотек и скриптов.
   Вход: данные запуска (initData) из Telegram → сервер проверяет подпись → токен сессии (хранится только в памяти страницы).
   Данные пользователей выводятся через textContent (никакого innerHTML с данными). */
'use strict';

const API = '/api/v1/tgapp';
const $ = id => document.getElementById(id);
const S = { token: null, me: null, data: null, tab: null, wid: null, undo: null, tm: null };

/* ---------------- построение DOM ---------------- */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'text') el.textContent = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
}
const fnum = x => (x == null ? '—' : Number.isInteger(+x) ? String(+x) : (+x).toFixed(2).replace(/0+$/, '').replace(/\.$/, '').replace('.', ','));
const WD = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
const fday = iso => { const d = new Date(iso + 'T00:00:00'); return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')} (${WD[d.getDay()]})`; };

/* ---------------- мост с Telegram (без внешнего telegram-web-app.js) ---------------- */
function hashParams() { return new URLSearchParams(location.hash.replace(/^#/, '')); }
function tgInitData() {
  const w = window.Telegram && window.Telegram.WebApp;
  return (w && w.initData) || hashParams().get('tgWebAppData') || '';
}
function tgEvent(name, data) {
  try {
    const json = JSON.stringify(data || {});
    if (window.TelegramWebviewProxy) window.TelegramWebviewProxy.postEvent(name, json);
    else if (window.external && window.external.notify) window.external.notify(JSON.stringify({ eventType: name, eventData: data || {} }));
    else if (window.parent !== window) window.parent.postMessage(JSON.stringify({ eventType: name, eventData: data || {} }), 'https://web.telegram.org');
  } catch (e) { /* вне Telegram события некому принимать */ }
}
function applyTheme() {
  try {
    const raw = hashParams().get('tgWebAppThemeParams');
    if (!raw) return;
    const t = JSON.parse(raw), r = document.documentElement.style;
    const map = { bg_color: '--bg', text_color: '--text', hint_color: '--hint', button_color: '--btn', button_text_color: '--btn-text', secondary_bg_color: '--bg', section_bg_color: '--card' };
    for (const [k, v] of Object.entries(map)) if (/^#[0-9a-f]{6}$/i.test(t[k] || '')) r.setProperty(v, t[k]);
    if (/^#[0-9a-f]{6}$/i.test(t.section_bg_color || '') ) r.setProperty('--card', t.section_bg_color);
    else if (/^#[0-9a-f]{6}$/i.test(t.secondary_bg_color || '') && /^#[0-9a-f]{6}$/i.test(t.bg_color || '')) { r.setProperty('--bg', t.secondary_bg_color); r.setProperty('--card', t.bg_color); }
  } catch (e) { /* тема по умолчанию */ }
}

/* ---------------- API ---------------- */
class ApiError extends Error { constructor(status, message) { super(message); this.status = status; } }
async function raw(method, path, body) {
  const r = await fetch(API + path, { method, headers: { 'Content-Type': 'application/json', ...(S.token ? { Authorization: 'Bearer ' + S.token } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body) });
  let d = null; try { d = await r.json(); } catch (e) { /* пустой ответ */ }
  if (!r.ok) throw new ApiError(r.status, (d && (d.detail || d.title)) ? (typeof d.detail === 'string' ? d.detail : (d.title || 'Ошибка')) : 'Ошибка ' + r.status);
  return d;
}
async function login() {
  const d = await raw('POST', '/auth', { init_data: tgInitData() });
  S.token = d.token; S.me = d;
}
async function api(method, path, body) {
  try { return await raw(method, path, body); }
  catch (e) {
    if (e.status === 401 && tgInitData()) { await login(); return raw(method, path, body); }      // сессия кончилась — тихо входим заново
    throw e;
  }
}
const errText = e => (e && e.message) || 'Не получилось. Попробуйте ещё раз';

/* ---------------- мелкие элементы ---------------- */
function toast(text, opts = {}) {
  const t = $('toast'); t.className = 'show' + (opts.err ? ' err' : ''); t.replaceChildren(h('span', { class: 'grow', text }));
  if (opts.action) t.append(h('button', { onclick: () => { t.className = ''; opts.action.fn(); } }, opts.action.label));
  clearTimeout(S.tm); S.tm = setTimeout(() => { t.className = ''; }, opts.err ? 6000 : 6500);
}
function ask(text, ok = 'Да') {
  return new Promise(res => {
    const sh = $('sheet'); sh.hidden = false;
    const done = v => { sh.hidden = true; sh.replaceChildren(); res(v); };
    sh.replaceChildren(h('div', { class: 'box' }, h('p', { text }), h('div', { class: 'row' },
      h('button', { class: 'ghost grow', onclick: () => done(false) }, 'Нет'), h('button', { class: 'grow', onclick: () => done(true) }, ok))));
    sh.onclick = e => { if (e.target === sh) done(false); };
  });
}
async function act(btn, fn) {                       // кнопка действия: блокируется на время запроса, ошибка — в тосте
  btn.disabled = true;
  try { return await fn(); } catch (e) { toast(errText(e), { err: true }); } finally { btn.disabled = false; }
}
const stateOf = t => (t.plan_qty && t.fact_qty != null && t.fact_qty >= t.plan_qty) ? 'done' : (t.fact_qty != null ? 'part' : 'none');
function progress(workers) {
  const ts = workers.flatMap(w => w.tasks).filter(t => t.plan_qty);
  return { total: ts.length, done: ts.filter(t => t.fact_qty != null).length };
}

/* ---------------- карточка задания ---------------- */
function taskCard(w, t, canEnter, onChange) {
  const el = h('div', { class: 'card' });
  let busy = false, dirty = false, burstPrev, burstTouched = false, noReason = false, timer;
  // text + десятичная клавиатура: у type=number запятая («3,5») в части браузеров молча теряется — разбираем её сами
  const input = h('input', { type: 'text', inputmode: 'decimal', autocomplete: 'off', 'aria-label': 'Сделано' });
  const status = h('div', { class: 'status' });
  const reasonBox = h('div');
  const say = (txt, cls) => { status.textContent = txt || ''; status.className = 'status ' + (cls || ''); };

  function set(v) {
    v = Math.max(0, Math.min(1e6, Math.round(v * 1000) / 1000));
    t.fact_qty = v; input.value = String(v); draw(); onChange && onChange();
    clearTimeout(timer); timer = setTimeout(flush, 450);
  }
  async function flush() {
    if (busy) { dirty = true; return; }
    busy = true; dirty = false; say('сохраняю…');
    try {
      const r = await api('POST', '/fact', { order_line_id: t.order_line_id, operation_id: t.operation_id, worker_id: w.worker_id, qty: t.fact_qty });
      if (!burstTouched) { burstPrev = r.previous; burstTouched = true; }
      t.fact_id = r.fact_id; t.reason_id = null; t.fact_status = r.status;
      say(r.status === 'submitted' ? '✓ записано, бригадир подтвердит' : '✓ записано', 'ok');
    } catch (e) { say(errText(e), 'err'); toast(errText(e), { err: true }); dirty = false; }
    busy = false;
    if (dirty) return flush();
    if (burstTouched && t.fact_id) {
      const prev = burstPrev, fid = t.fact_id; burstTouched = false;
      S.undo = { fid, prev };
      toast('Записано: ' + fnum(t.fact_qty), { action: { label: '↩ Отменить', fn: () => undo(fid, prev) } });
    }
    draw();
  }
  async function undo(fid, prev) {
    try {
      await api('POST', `/fact/${fid}/undo`, { previous: prev == null ? null : prev });
      t.fact_qty = prev == null ? null : prev; if (prev == null) t.fact_id = null;
      input.value = t.fact_qty == null ? '' : String(t.fact_qty); say('↩ отменено'); draw(); onChange && onChange();
    } catch (e) { toast(errText(e), { err: true }); }
  }
  async function pickReason(id) {
    try {
      await api('POST', `/fact/${t.fact_id}/reason`, { reason_id: id });
      if (id) { t.reason_id = id; t.reason = (S.data.reasons[w.worker_id] || []).find(r => r.id === id)?.name; } else noReason = true;
      toast(id ? 'Причина сохранена' : 'Хорошо, без причины'); draw();
    } catch (e) { toast(errText(e), { err: true }); }
  }
  function draw() {
    el.className = 'card ' + stateOf(t);
    const meta = t.plan_qty ? `план ${fnum(t.plan_qty)}` : 'вне плана';
    const head = h('div', { class: 'row' }, h('div', { class: 'grow' }, h('h3', { text: `${t.operation} · ${t.product}` }),
      h('div', { class: 'meta', text: `${t.order} · ${meta}` })),
      stateOf(t) === 'done' ? h('span', { class: 'pill ok' }, '✔ готово') : null);
    const kids = [head];
    if (canEnter) {
      kids.push(h('div', { class: 'counter' },
        h('button', { class: 'sq ghost', 'aria-label': 'Минус один', onclick: () => set((t.fact_qty || 0) - 1), disabled: !t.fact_qty }, '−'), input,
        h('button', { class: 'sq', 'aria-label': 'Плюс один', onclick: () => set((t.fact_qty || 0) + 1) }, '+')));
      const chips = h('div', { class: 'row wrap', style: 'margin-top:10px' },
        h('button', { class: 'chip', onclick: () => set((t.fact_qty || 0) + 5) }, '+5'), h('button', { class: 'chip', onclick: () => set((t.fact_qty || 0) + 10) }, '+10'));
      if (t.plan_qty && !(t.fact_qty >= t.plan_qty)) chips.append(h('button', { class: 'chip on', onclick: () => set(t.plan_qty) }, `✔ Всё (${fnum(t.plan_qty)})`));
      if (t.fact_qty == null || t.fact_qty > 0) chips.append(h('button', { class: 'chip', onclick: () => set(0) }, '0 — не делал'));
      kids.push(chips, status);
      const rs = (S.data.reasons[w.worker_id] || []);
      reasonBox.replaceChildren();
      if (t.plan_qty && t.fact_qty != null && t.fact_qty < t.plan_qty && t.fact_id && !t.reason_id && !noReason && !busy) {
        reasonBox.append(h('div', { class: 'reasons' }, h('p', { text: `Меньше плана (${fnum(t.plan_qty)}). Что помешало?` }),
          h('div', { class: 'row wrap' }, rs.map(r => h('button', { class: 'chip', onclick: () => pickReason(r.id) }, r.name)),
            h('button', { class: 'chip', onclick: () => pickReason(null) }, 'Без причины'))));
      } else if (t.reason) reasonBox.append(h('div', { class: 'status', text: 'Причина: ' + t.reason }));
      kids.push(reasonBox);
    } else {
      kids.push(h('div', { class: 'status', text: t.fact_qty != null ? `Сделано: ${fnum(t.fact_qty)}` : 'Факт ещё не внесён' }));
    }
    el.replaceChildren(...kids);
  }
  input.value = t.fact_qty == null ? '' : String(t.fact_qty);
  input.addEventListener('change', () => { const v = parseFloat(String(input.value).replace(/\s/g, '').replace(',', '.')); if (isFinite(v) && v >= 0) set(v); else { input.value = t.fact_qty == null ? '' : String(t.fact_qty); } });
  draw();
  return el;
}

/* ---------------- экраны ---------------- */
const main = () => $('main');
function header(title, sub, prog) {
  const hd = $('hd');
  hd.replaceChildren(h('h1', { text: title }), sub ? h('div', { class: 'sub', text: sub }) : null,
    prog && prog.total ? h('div', null, h('div', { class: 'sub', text: `Внесено: ${prog.done} из ${prog.total}` }), h('div', { class: 'bar' }, h('i', { style: `width:${Math.round(prog.done / prog.total * 100)}%` }))) : null);
}
function tabsFor(role) {
  return role === 'foreman'
    ? [['people', '👥', 'Участок'], ['queue', '📥', 'Подтвердить'], ['absent', '🤒', 'Отсутствие'], ['tomorrow', '📅', 'Завтра']]
    : [['tasks', '📋', 'Задания'], ['tomorrow', '📅', 'Завтра'], ['problem', '⚠', 'Проблема'], ['absent', '🤒', 'Не выйду']];
}
function drawTabs() {
  $('tabs').replaceChildren(...tabsFor(S.me.role).map(([id, ic, label]) => h('button', { class: S.tab === id ? 'on' : '', onclick: () => go(id) },
    h('span', { class: 'ic', text: ic }), label, id === 'queue' && S.data && S.data.queue ? h('span', { class: 'badge', text: String(S.data.queue) }) : null)));
}
async function loadDay() { S.data = await api('GET', '/day'); }
async function go(tab, wid) {
  S.tab = tab; S.wid = wid || null; drawTabs();
  main().replaceChildren(h('p', { class: 'muted center', text: 'Загрузка…' }));
  window.scrollTo(0, 0);
  try {
    if (tab === 'tasks' || tab === 'people' || tab === 'queue' || !S.data) await loadDay();
    drawTabs();
    await ({ tasks: tasksScreen, people: peopleScreen, queue: queueScreen, tomorrow: tomorrowScreen, problem: problemScreen, absent: absentScreen }[tab])();
  } catch (e) { fatal(errText(e), e.status); }
}
function fatal(msg, status) {
  $('tabs').replaceChildren(); $('hd').replaceChildren(h('h1', { text: 'Plan 24' }));
  main().replaceChildren(h('div', { class: 'card' }, h('h3', { text: status === 403 ? 'Telegram не привязан' : 'Не удалось открыть' }), h('p', { text: msg }),
    status === 403 ? null : h('button', { class: 'wide', onclick: boot }, 'Повторить')));
}

function tasksList(workers, w, canEnter, onChange) {
  const ts = w.tasks.filter(t => t.plan_qty || t.fact_qty != null);
  return ts.length ? ts.map(t => taskCard(w, t, canEnter, onChange)) : [h('div', { class: 'empty', text: 'На сегодня заданий нет' })];
}
async function closeDay(wid, label) {
  const pend = S.data.workers.flatMap(w => w.tasks.filter(t => t.plan_qty && t.fact_qty == null && (!wid || w.worker_id === wid)));
  if (!pend.length) return toast('Заданий без факта нет ✔');
  if (!await ask(`Внести по плану ${pend.length} ${pend.length === 1 ? 'задание' : 'заданий'}${label ? ' — ' + label : ''}?\nВнесётся ровно плановое количество.`, 'Внести')) return;
  const r = await api('POST', '/closeday', { worker_id: wid || 0 });
  toast(`Внесено по плану: ${r.ok}` + (r.fails.length ? `. Не внесено: ${r.fails.length}` : ''), { err: r.fails.length > 0 });
  await go(S.tab, S.wid);
}

async function tasksScreen() {                                 // сборщик
  const d = S.data, w = d.workers[0] || { worker_id: d.worker_id, worker: d.name, tasks: [] };
  const upd = () => header('Мои задания', fday(d.date), progress(d.workers));
  upd();
  const pend = w.tasks.some(t => t.plan_qty && t.fact_qty == null);
  main().replaceChildren(...tasksList(d.workers, w, d.can_enter, upd),
    pend && d.can_enter ? h('button', { class: 'wide okb', onclick: e => act(e.currentTarget, () => closeDay(0)) }, '✅ Закрыть день по плану') : null);
}

async function peopleScreen() {                                // бригадир
  const d = S.data;
  if (S.wid) {
    const w = d.workers.find(x => x.worker_id === S.wid);
    if (!w) { S.wid = null; return peopleScreen(); }
    const upd = () => header(w.worker, 'Задания на ' + fday(d.date), progress([w]));
    upd();
    main().replaceChildren(h('button', { class: 'ghost wide', onclick: () => go('people') }, '◀ К участку'), ...tasksList(d.workers, w, d.can_enter, upd),
      w.tasks.some(t => t.plan_qty && t.fact_qty == null) ? h('button', { class: 'wide okb', onclick: e => act(e.currentTarget, () => closeDay(w.worker_id, w.worker)) }, `✅ Закрыть день: ${w.worker}`) : null);
    return;
  }
  header('Участок', fday(d.date), progress(d.workers));
  const list = d.workers.filter(w => w.tasks.some(t => t.plan_qty || t.fact_qty != null));
  const pend = list.some(w => w.tasks.some(t => t.plan_qty && t.fact_qty == null));
  main().replaceChildren(
    ...(list.length ? list.map(w => {
      const ts = w.tasks.filter(t => t.plan_qty), done = ts.filter(t => t.fact_qty != null).length;
      return h('button', { class: 'person', onclick: () => go('people', w.worker_id) },
        h('div', { class: 'row' }, h('b', { class: 'grow', text: w.worker }), h('span', { class: 'pill ' + (done === ts.length && ts.length ? 'ok' : done ? 'warn' : ''), text: `${done}/${ts.length}` })),
        h('div', { class: 'bar' }, h('i', { style: `width:${ts.length ? Math.round(done / ts.length * 100) : 0}%` })));
    }) : [h('div', { class: 'empty', text: 'На сегодня заданий на участке нет' })]),
    pend ? h('button', { class: 'wide okb', onclick: e => act(e.currentTarget, () => closeDay(0, 'весь участок')) }, '✅ Закрыть день по плану за всех') : null,
    pend ? h('button', { class: 'wide ghost', onclick: e => act(e.currentTarget, remind) }, '🔔 Напомнить тем, кто не внёс') : null,
    h('button', { class: 'wide ghost', onclick: e => act(e.currentTarget, replan) }, '🔄 Перепланировать участок'));
}
async function remind() {
  const r = await api('POST', '/remind');
  toast(r.status === 'throttled' ? `Напоминали ${r.minutes} мин назад — подождите` : r.status === 'nobody' ? 'У всех факт внесён ✔'
    : `Напоминание отправлено: ${r.sent} из ${r.total}` + (r.nolink && r.nolink.length ? `. Нет Telegram: ${r.nolink.slice(0, 4).join(', ')}` : ''));
}
async function replan() {
  if (!await ask('Пересчитать план участка сейчас?', 'Пересчитать')) return;
  const r = await api('POST', '/replan');
  toast(`План пересчитан. Сегодня: заданий — ${r.tasks}, работников — ${r.workers}`);
  await go('people');
}

async function queueScreen() {
  header('Подтверждение факта', 'Внесён сборщиками, ждёт вас');
  const q = (await api('GET', '/queue')).items;
  S.data.queue = q.length; drawTabs();
  const draw = items => main().replaceChildren(...(items.length ? [...items.map(it => h('div', { class: 'card row' },
    h('div', { class: 'grow' }, h('b', { text: it.worker }), h('div', { class: 'meta muted', text: `${fnum(it.qty)} · ${fday(it.date)}` })),
    h('button', { onclick: e => act(e.currentTarget, async () => { await api('POST', '/confirm', { fact_id: it.fact_id }); items.splice(items.indexOf(it), 1); S.data.queue = items.length; drawTabs(); draw(items); }) }, '✔'))),
    h('button', { class: 'wide okb', onclick: e => act(e.currentTarget, async () => { await api('POST', '/confirm', { fact_id: null }); toast('Подтверждено'); items.length = 0; S.data.queue = 0; drawTabs(); draw(items); }) }, '✔ Подтвердить все')]
    : [h('div', { class: 'empty', text: 'Неподтверждённого факта нет ✔' })]));
  draw(q);
}

async function tomorrowScreen() {
  const t = await api('GET', '/tomorrow');
  header(t.is_tomorrow ? 'Завтра' : 'Ближайший день', fday(t.date));
  if (!t.workers.length) return main().replaceChildren(h('div', { class: 'empty', text: 'На ближайшую неделю заданий в плане нет' }));
  main().replaceChildren(...(t.is_tomorrow ? [] : [h('div', { class: 'muted center', text: 'Завтра заданий нет — вот ближайший день с заданиями' })]),
    ...t.workers.map(w => h('div', { class: 'card' }, S.me.role === 'foreman' ? h('h3', { text: w.worker }) : null,
      ...w.tasks.map(x => h('div', { class: 'row', style: 'padding:6px 0' }, h('span', { class: 'grow', text: `${x.operation} · ${x.product} · ${x.order}` }), h('b', { text: fnum(x.plan_qty) }))))));
}

const TYPES = [['mat', '📦 Нет материала'], ['tool', '🔧 Поломка, инструмент'], ['bad', '✖ Брак'], ['oth', '💬 Другое']];
async function problemScreen() {
  header('Проблема', 'Бригадир получит сообщение сразу');
  let type = null;
  const chips = h('div', { class: 'row wrap' }), area = h('textarea', { placeholder: 'Опишите коротко (необязательно)', maxlength: '500' });
  const send = h('button', { class: 'wide', disabled: true, onclick: e => act(e.currentTarget, async () => {
    const r = await api('POST', '/problem', { type, comment: area.value.trim() });
    toast(r.delivered ? `Передал бригадиру (${r.delivered}). Если срочно — позвоните` : 'Бригадир не подключён к Telegram — позвоните ему или скажите лично', { err: !r.delivered });
    area.value = ''; type = null; draw();
  }) }, 'Отправить бригадиру');
  const draw = () => { chips.replaceChildren(...TYPES.map(([k, l]) => h('button', { class: 'chip' + (type === k ? ' on' : ''), onclick: () => { type = k; draw(); } }, l))); send.disabled = !type; };
  draw();
  main().replaceChildren(h('div', { class: 'card' }, h('label', { text: 'Что случилось?' }), chips, h('label', { text: 'Подробности' }), area),
    send, h('p', { class: 'muted center', text: 'Фото можно прислать боту в чате: кнопка «⚠ Проблема».' }));
}

const PERIODS = [['t', 'Сегодня'], ['m', 'Завтра'], ['tm', 'Сегодня и завтра'], ['w7', '7 дней']];
const REASONS = [['ill', 'Болезнь'], ['vac', 'Отпуск'], ['fam', 'Личные обстоятельства'], ['oth', 'Другое']];
async function absentScreen() {
  const foreman = S.me.role === 'foreman';
  header(foreman ? 'Отсутствие работника' : 'Не смогу выйти', foreman ? 'Отметить и перепланировать участок' : 'Бригадир будет предупреждён');
  if (!S.data) await loadDay();
  let period = null, reason = null;
  const sel = foreman ? h('select', null, (S.data.team || S.data.workers).map(w => h('option', { value: w.worker_id, text: w.worker }))) : null;
  const pc = h('div', { class: 'row wrap' }), rc = h('div', { class: 'row wrap' }), after = h('div');
  const go2 = h('button', { class: 'wide', disabled: true, onclick: e => act(e.currentTarget, async () => {
    const r = await api('POST', '/absence', { worker_id: sel ? +sel.value : null, period, reason });
    toast(foreman ? `Отмечено: ${r.worker} не выйдет ${r.when}` : `Записал: вы не выйдете ${r.when}. Бригадир предупреждён`);
    period = reason = null; draw();
    if (foreman) after.replaceChildren(h('button', { class: 'wide okb', onclick: ev => act(ev.currentTarget, async () => { await replan(); }) }, '🔄 Перепланировать участок сейчас'));
  }) }, 'Сохранить');
  const draw = () => {
    pc.replaceChildren(...PERIODS.map(([k, l]) => h('button', { class: 'chip' + (period === k ? ' on' : ''), onclick: () => { period = k; draw(); } }, l)));
    rc.replaceChildren(...REASONS.map(([k, l]) => h('button', { class: 'chip' + (reason === k ? ' on' : ''), onclick: () => { reason = k; draw(); } }, l)));
    go2.disabled = !(period && reason);
  };
  draw();
  main().replaceChildren(h('div', { class: 'card' }, foreman ? [h('label', { text: 'Кто не выйдет?' }), sel] : null, h('label', { text: 'На какой срок?' }), pc, h('label', { text: 'Причина' }), rc), go2, after);
}

/* ---------------- запуск ---------------- */
async function boot() {
  tgEvent('web_app_ready'); tgEvent('web_app_expand'); applyTheme();
  if (!tgInitData()) return fatal('Приложение открывается кнопкой в чате с ботом: нажмите «Приложение» рядом с полем ввода или кнопку «📱 Открыть приложение» в сообщении бота.', 0);
  try { await login(); } catch (e) { return fatal(errText(e), e.status); }
  await go(tabsFor(S.me.role)[0][0]);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden && S.me && ['tasks', 'people'].includes(S.tab) && !document.querySelector('input:focus')) go(S.tab, S.wid); });
boot();
