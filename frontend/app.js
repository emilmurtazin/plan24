/* Plan 24 — интерфейс. Без сборки, без внешних зависимостей. Экраны данных — в data.js. */
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const MANAGERS = ['owner', 'manager'], STAFF = ['owner', 'manager', 'foreman'], READERS = ['owner', 'manager', 'technologist', 'foreman', 'viewer'], CATALOG = ['owner', 'manager', 'technologist'];
const ROLE_RU = { owner: 'Владелец', manager: 'Руководитель', technologist: 'Технолог', foreman: 'Бригадир', worker: 'Сборщик', viewer: 'Наблюдатель' };
const WD = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
const fmtD = iso => iso ? iso.slice(8, 10) + '.' + iso.slice(5, 7) : '';
const fmtDY = iso => iso ? fmtD(iso) + '.' + iso.slice(0, 4) : '—';
const wd = iso => WD[new Date(iso + 'T00:00:00').getDay()];
const fmtDT = iso => iso ? new Date(iso + 'Z').toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
const num = v => v == null ? '—' : (Math.round(v * 100) / 100).toString().replace('.', ',');
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());

const S = { me: null, screen: null, access: null, refresh: localStorage.getItem('p24_rt'), plan: undefined, nav: 0 };

function toast(m, bad) {
  if (!m) return;
  const t = $('#toast'); t.textContent = m; t.style.background = bad ? '#b3261e' : ''; t.classList.remove('hidden');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add('hidden'), bad ? 6000 : 3000);
}

/* ---------------- API ---------------- */
/* Ответ на чтение, начатое до смены экрана, отбрасывается — иначе запоздавший экран перерисует чужую вкладку. */
class Stale extends Error { }
class ApiError extends Error { constructor(status, title, errors) { super(title); this.status = status; this.errors = errors || []; } }
let refreshing = null;
async function doRefresh() {
  if (!S.refresh) return false;
  refreshing = refreshing || fetch('/api/v1/auth/refresh', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh_token: S.refresh }) })
    .then(async r => { if (!r.ok) return false; const d = await r.json(); setTokens(d); return true; }).catch(() => false).finally(() => { refreshing = null; });
  return refreshing;
}
function setTokens(d) { S.access = d.access_token; S.refresh = d.refresh_token; localStorage.setItem('p24_rt', d.refresh_token); }
async function api(method, path, body, extra = {}) {
  const gen = S.nav;
  const go = () => fetch('/api/v1' + path, { method, headers: { 'Content-Type': 'application/json', ...(S.access ? { Authorization: 'Bearer ' + S.access } : {}), ...(extra.headers || {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
  let r = await go();
  if (r.status === 401 && !path.startsWith('/auth/') && await doRefresh()) r = await go();
  if (r.status === 401 && !path.startsWith('/auth/login')) { logout(true); throw new ApiError(401, 'Сессия истекла. Войдите снова'); }
  if (method === 'GET' && gen !== S.nav) throw new Stale();
  if (r.status === 204) return null;
  let d = null; try { d = await r.json(); } catch (e) { /* пустое тело */ }
  if (!r.ok) throw new ApiError(r.status, (d && d.title) || 'Ошибка ' + r.status, d && d.errors);
  return d;
}
const GET = (p) => api('GET', p), POST = (p, b) => api('POST', p, b ?? {}), PATCH = (p, b) => api('PATCH', p, b), PUT = (p, b) => api('PUT', p, b), DEL = (p) => api('DELETE', p);
const listAll = async p => (await GET(p + (p.includes('?') ? '&' : '?') + 'limit=1000')).items;
const errText = e => e instanceof Stale ? '' : e instanceof ApiError ? (e.errors.length ? e.errors.map(x => (x.field ? x.field + ': ' : '') + x.message).join('; ') : e.message) : 'Нет связи с сервером';

/* ---------------- вход / регистрация ---------------- */
function logout(silent) {
  if (!silent && S.access) api('POST', '/auth/logout', {}).catch(() => { });
  S.me = null; S.access = null; S.refresh = null; localStorage.removeItem('p24_rt'); renderAuth('login');
}
function renderAuth(mode) {
  $('#root').innerHTML = `<div class="auth"><h1>Plan 24</h1><p class="muted">Планирование производства: заказы, люди, нормы — и план по дням на каждого сборщика.</p>
  <div class="card"><div class="row" style="margin-bottom:12px"><button class="${mode === 'login' ? '' : 'secondary'}" data-m="login">Вход</button><button class="${mode === 'register' ? '' : 'secondary'}" data-m="register">Регистрация организации</button></div>
  <form id="af" novalidate>${mode === 'login' ? `
    <label class="f">E-mail<input name="email" type="email" autocomplete="username" required></label>
    <label class="f">Пароль<input name="password" type="password" autocomplete="current-password" required></label>` : `
    <label class="f">Название организации<input name="org_name" required minlength="2"></label>
    <label class="f">Ваше имя<input name="name" autocomplete="name" required minlength="2"></label>
    <label class="f">E-mail<input name="email" type="email" autocomplete="username" required></label>
    <label class="f">Пароль (не короче 8 символов)<input name="password" type="password" autocomplete="new-password" minlength="8" required></label>
    <label class="chk"><input type="checkbox" name="accept_terms"> <span>Согласен на обработку персональных данных и с условиями оферты</span></label>`}
    <div id="aerr" class="err" role="alert"></div>
    <button type="submit" style="width:100%;justify-content:center">${mode === 'login' ? 'Войти' : 'Создать организацию'}</button></form>
    ${mode === 'login' ? '<p style="margin:12px 0 0"><a href="#" id="fg">Забыли пароль?</a></p>' : ''}</div>
  ${mode === 'register' ? '<p class="muted">14 дней — полный доступ без карты. Данные вашей организации не видны другим.</p>' : ''}</div>`;
  $$('[data-m]').forEach(b => b.onclick = () => renderAuth(b.dataset.m));
  if ($('#fg')) $('#fg').onclick = e => { e.preventDefault(); renderForgot(); };
  $('#af').onsubmit = async e => {
    e.preventDefault(); const f = new FormData(e.target), err = $('#aerr'); err.textContent = '';
    const body = Object.fromEntries(f.entries());
    if (mode === 'register') body.accept_terms = !!f.get('accept_terms');
    try { const d = await api('POST', mode === 'login' ? '/auth/login' : '/auth/register', body); setTokens(d); S.me = d.user; start(); }
    catch (x) { err.textContent = errText(x); }
  };
}

function renderForgot() {
  $('#root').innerHTML = `<div class="auth"><h1>Смена пароля</h1><div class="card"><p class="muted">Укажите e-mail — отправим ссылку для создания нового пароля.</p>
    <form id="ff" novalidate><label class="f">E-mail<input name="email" type="email" autocomplete="username" required></label>
    <div id="fmsg" role="status"></div><div class="row"><button type="submit">Отправить ссылку</button><button type="button" class="secondary" id="fb">Назад</button></div></form></div></div>`;
  $('#fb').onclick = () => renderAuth('login');
  $('#ff').onsubmit = async e => {
    e.preventDefault(); const m = $('#fmsg'); m.className = '';
    try { const d = await api('POST', '/auth/forgot-password', { email: e.target.elements.email.value }); m.className = 'ok'; m.textContent = d.message; }
    catch (x) { m.className = 'err'; m.textContent = errText(x); }
  };
}
/* Страницы по ссылкам из писем: #/verify?token=…, #/reset?token=…, #/invite?token=… */
function renderTokenPage(kind, token) {
  const home = () => { location.href = '/'; };
  const wrap = body => { $('#root').innerHTML = `<div class="auth"><h1>Plan 24</h1><div class="card">${body}</div></div>`; };
  if (kind === 'verify') {
    wrap('<p>Подтверждаем e-mail…</p>');
    api('POST', '/auth/verify', { token }).then(() => { wrap('<h2 class="ok">E-mail подтверждён</h2><p>Спасибо! Теперь всё в порядке.</p><button id="go">Открыть Plan 24</button>'); $('#go').onclick = home; })
      .catch(e => { wrap(`<h2 class="err">Не получилось</h2><p>${esc(errText(e))}</p><button id="go">Открыть Plan 24</button>`); $('#go').onclick = home; });
    return;
  }
  const invite = kind === 'invite';
  wrap(`<h2>${invite ? 'Добро пожаловать!' : 'Новый пароль'}</h2><p class="muted">${invite ? 'Задайте пароль — и можно входить.' : 'Придумайте новый пароль (не короче 8 символов).'}</p>
    <form id="rf" novalidate><label class="f">Пароль<input name="p1" type="password" autocomplete="new-password" minlength="8" required></label>
    <label class="f">Повторите пароль<input name="p2" type="password" autocomplete="new-password" minlength="8" required></label>
    <div id="rerr" class="err" role="alert"></div><button type="submit">Сохранить пароль</button></form>`);
  $('#rf').onsubmit = async e => {
    e.preventDefault(); const f = e.target.elements, er = $('#rerr'); er.textContent = '';
    if (f.p1.value.length < 8) { er.textContent = 'Пароль — не короче 8 символов'; return; }
    if (f.p1.value !== f.p2.value) { er.textContent = 'Пароли не совпадают'; return; }
    try { await api('POST', '/auth/reset-password', { token, new_password: f.p1.value }); wrap('<h2 class="ok">Пароль сохранён</h2><p>Теперь можно войти.</p><button id="go">Войти</button>'); $('#go').onclick = home; }
    catch (x) { er.textContent = errText(x); }
  };
}

/* ---------------- оболочка ---------------- */
const NAV = [
  { id: 'home', label: 'Дашборд', roles: READERS },
  { id: 'day', label: 'План на день', roles: STAFF, alt: { worker: 'Мои задания' } },
  { id: 'status', label: 'Статус заказов', roles: READERS },
  { id: 'load', label: 'Загрузка', roles: READERS },
  { id: 'catalog', label: 'Каталог изделий', roles: CATALOG },
  { id: 'data', label: 'Проекты и Заказы', roles: MANAGERS },
  { id: 'org', label: 'Организация', roles: MANAGERS },
];
function navFor(role) { return NAV.filter(n => n.roles.includes(role) || (n.alt && n.alt[role])).map(n => ({ ...n, label: (n.alt && n.alt[role]) || n.label })); }
function start() {
  const me = S.me, nav = navFor(me.role);
  $('#root').innerHTML = `<header class="top"><b>Plan 24</b><span class="org">${esc(me.tenant.name)}</span><span class="sp"></span>
    <span>${esc(me.name)} · ${ROLE_RU[me.role]}</span><button id="lo">Выйти</button></header>
    <nav class="tabs" aria-label="Разделы">${nav.map(n => `<button data-s="${n.id}">${esc(n.label)}</button>`).join('')}</nav>
    ${me.email_verified ? '' : `<div class="banner warn" id="vb" style="margin:12px 16px 0"><span><b>E-mail не подтверждён.</b> Мы отправили письмо на ${esc(me.email)} — перейдите по ссылке в нём.</span><button class="small" id="rv">Отправить ещё раз</button></div>`}<main id="view"></main>`;
  $('#lo').onclick = () => logout();
  if ($('#rv')) $('#rv').onclick = async () => { try { const r = await POST('/auth/resend-verification'); toast(r.already_verified ? 'E-mail уже подтверждён' : 'Письмо отправлено'); } catch (e) { toast(errText(e), true); } };
  $$('nav.tabs button').forEach(b => b.onclick = () => show(b.dataset.s));
  show(['worker', 'foreman'].includes(me.role) ? 'day' : me.role === 'technologist' ? 'catalog' : 'home');
}
const SCREENS = {};
async function show(id) {
  S.screen = id; S.nav++;
  $$('nav.tabs button').forEach(b => b.removeAttribute('aria-current')); const cur = $(`nav.tabs button[data-s="${id}"]`); if (cur) cur.setAttribute('aria-current', 'page');
  const v = $('#view'); v.innerHTML = '<p class="muted">Загрузка…</p>';
  try { await SCREENS[id](v); } catch (e) { if (e instanceof Stale) return; v.innerHTML = `<div class="card err" role="alert">${esc(errText(e))}</div>`; }
}

/* ---------------- модальные окна и формы ---------------- */
function modal(html, wide) {
  const m = document.createElement('div'); m.className = 'modal'; m.setAttribute('role', 'dialog'); m.setAttribute('aria-modal', 'true');
  m.innerHTML = `<div style="${wide ? 'max-width:960px' : ''}">${html}</div>`; document.body.appendChild(m);
  const close = () => m.remove(); m.addEventListener('mousedown', e => { if (e.target === m) close(); });
  m.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  const f = $('input,select,button', m); if (f) f.focus();
  return { el: m, close };
}
function fieldHtml(f, v) {
  const val = v == null ? '' : v, req = f.required ? 'required' : '';
  let inp;
  if (f.type === 'select') inp = `<select name="${f.name}" ${req}>${f.optional !== false && !f.required ? `<option value="">${esc(f.blank || '—')}</option>` : ''}${f.options.map(o => `<option value="${esc(o.v)}" ${String(o.v) === String(val) ? 'selected' : ''}>${esc(o.l)}</option>`).join('')}</select>`;
  else if (f.type === 'checkbox') return `<label class="chk"><input type="checkbox" name="${f.name}" ${v ? 'checked' : ''}> <span>${esc(f.label)}</span></label>${f.help ? `<div class="muted" style="margin:-4px 0 8px 28px;font-size:14px">${esc(f.help)}</div>` : ''}`;
  else inp = `<input name="${f.name}" type="${f.type || 'text'}" value="${esc(val)}" ${req} ${f.step ? `step="${f.step}"` : ''} ${f.min != null ? `min="${f.min}"` : ''} ${f.max != null ? `max="${f.max}"` : ''} ${f.type === 'password' ? 'autocomplete="new-password"' : ''}>`;
  return `<label class="f">${esc(f.label)}${f.required ? ' *' : ''}${inp}${f.help ? `<span style="font-size:13px">${esc(f.help)}</span>` : ''}</label>`;
}
function readForm(form, fields) {
  const o = {};
  for (const f of fields) {
    const el = form.elements[f.name]; if (!el) continue;
    if (f.type === 'checkbox') o[f.name] = el.checked;
    else if (el.value === '') o[f.name] = f.type === 'password' ? undefined : (f.required ? '' : null);
    else if (f.type === 'number' || (f.type === 'select' && f.num)) o[f.name] = Number(el.value);
    else o[f.name] = el.value;
  }
  return o;
}
function formModal({ title, fields, values = {}, submit = 'Сохранить', onSubmit, extra = '', onOpen }) {
  const m = modal(`<h2>${esc(title)}</h2><form novalidate>${fields.map(f => fieldHtml(f, values[f.name])).join('')}${extra}
    <div class="err" data-err role="alert"></div><div class="row"><button type="submit">${esc(submit)}</button><button type="button" class="secondary" data-x>Отмена</button></div></form>`);
  const form = $('form', m.el); $('[data-x]', m.el).onclick = m.close;
  form.onsubmit = async e => {
    e.preventDefault(); const er = $('[data-err]', m.el); er.textContent = '';
    if (!form.checkValidity()) { form.reportValidity(); return; }
    const btn = $('button[type=submit]', m.el); btn.disabled = true;
    try { await onSubmit(readForm(form, fields), m); }
    catch (x) { er.textContent = errText(x); btn.disabled = false; }
  };
  if (onOpen) onOpen(form);
  return m;
}
const confirmBox = (text) => new Promise(res => {
  const m = modal(`<p>${esc(text)}</p><div class="row"><button class="danger" data-y>Да</button><button class="secondary" data-n>Отмена</button></div>`);
  $('[data-y]', m.el).onclick = () => { m.close(); res(true); }; $('[data-n]', m.el).onclick = () => { m.close(); res(false); };
});

const WARN_RU = { NOT_DISTRIBUTED: 'Не распределено по участкам', NO_WORKERS: 'Нет подходящих работников', HORIZON_EXCEEDED: 'Не влезает в горизонт', PRIORITY_HORIZON: 'Горизонт для приоритета', NO_OPERATIONS: 'Нет операций', DATA_INVALID: 'Некорректные данные', WORKER_NO_SECTION: 'Работник без участка' };

/* ---------------- Дашборд ---------------- */
const ST = { 'Готово': ['b-done', '✔'], 'В работе': ['b-work', '▶'], 'Риск опоздания': ['b-risk', '⚠'], 'Нет плана': ['b-none', '…'], 'Нет операций': ['b-noop', '—'] };
const stBadge = s => { const x = ST[s] || ['b-noop', '?']; return `<span class="badge ${x[0]}"><span aria-hidden="true">${x[1]}</span>${esc(s)}</span>`; };

SCREENS.home = async v => {
  const can = MANAGERS.includes(S.me.role), staff = STAFF.includes(S.me.role);
  const [pl, prods, workers, orders] = await Promise.all([GET('/plans/active'), GET('/products?limit=1'), GET('/workers?limit=1'), GET('/order-lines?limit=1')]);
  const empty = !prods.items.length && !workers.items.length && !orders.items.length;
  if (empty) {
    let kinds = []; if (can) kinds = (await GET('/demo/kinds')).kinds;
    const KR = { furniture: 'Мебельный цех', windows: 'Окна и витражи', metal: 'Металлоконструкции', panels: 'Сборка щитов', sewing: 'Швейный цех' };
    v.innerHTML = `<h1>Начнём</h1><div class="card"><p>Чтобы получить план, нужны четыре вещи:</p><ol class="steps">
      <li><b>Каталог изделий</b> — что делаете, из каких операций, какая норма за день.</li><li><b>Организация → Работники</b> — кто работает, производительность и квалификация.</li>
      <li><b>Проекты и Заказы</b> — сколько изделий, к какому сроку, какой приоритет.</li><li>Нажмите <b>«Построить план»</b> — получите задания по дням.</li></ol>
      ${can ? `<div class="row"><button id="goData">Заполнить данные</button><span class="muted">или посмотреть на примере:</span>
      <select id="dk" aria-label="Пример">${kinds.map(k => `<option value="${k}">${KR[k] || k}</option>`).join('')}</select><button class="secondary" id="demo">Загрузить пример</button></div>` : '<p class="muted">Данные добавляет руководитель.</p>'}</div>`;
    if (can) { $('#goData').onclick = () => show('catalog'); $('#demo').onclick = async () => { try { await POST('/demo/load?kind=' + $('#dk').value); toast('Пример загружен'); S.plan = undefined; await POST('/plans/replan'); show('home'); } catch (e) { toast(errText(e), true); } }; }
    return;
  }
  const plan = pl.plan;
  let h = `<div class="row" style="margin-bottom:12px"><h1 class="grow" style="margin:0">Дашборд</h1>${staff ? '<button id="rp">Перепланировать</button>' : ''}</div>`;
  if (!plan) h += `<div class="banner info">План ещё не построен.${staff ? ' Нажмите «Перепланировать».' : ''}</div>`;
  else {
    if (plan.stale) h += `<div class="banner warn"><span><b>Данные изменились после расчёта.</b> План может не учитывать новый факт, заказы или людей.</span>${staff ? '<button id="rp2" class="small">Пересчитать</button>' : ''}</div>`;
    const nd = (plan.warnings || []).filter(w => w.code === 'NOT_DISTRIBUTED'), ndOrders = new Set(nd.map(w => w.order_line_id)).size;
    if (nd.length) h += `<div class="banner err" role="alert"><span><b>Ошибка: операции не распределены по участкам (${nd.length} в ${ndOrders} поз.).</b> Такие операции не попадают в план, а следующие за ними не планируются, пока их не распределят.</span>${MANAGERS.includes(S.me.role) ? '<button id="goDist" class="small">Распределить</button>' : ''}</div>`;
    const d = await GET('/reports/dashboard'), by = d.by_status || {};
    h += `<div class="cards3"><div class="kpi"><b class="${by['Риск опоздания'] ? 'err' : ''}">${by['Риск опоздания'] || 0}</b>позиций в риске</div><div class="kpi"><b>${by['В работе'] || 0}</b>в работе</div>
      <div class="kpi"><b class="ok">${by['Готово'] || 0}</b>готово</div><div class="kpi"><b>${num(d.week_peak_load)}</b>пик загрузки на неделе</div></div>`;
    if (d.risk.length) h += `<div class="card"><h2>Риск опоздания</h2><div class="scroll"><table><tr><th>Заказ</th><th>Изделие</th><th>Проект</th><th>Срок</th><th>Прогноз</th></tr>${d.risk.map(r => `<tr><td>${esc(r.id)}</td><td>${esc(r.product)}</td><td>${esc(r.project)}</td><td>${fmtDY(r.due)}</td><td class="err">${esc(r.forecast_text)}</td></tr>`).join('')}</table></div></div>`;
    if (d.loaded_today.length) h += `<div class="card"><b>Уже загружены сегодня</b> и не получают новых заданий: ${d.loaded_today.map(esc).join(', ')}</div>`;
    h += `<p class="muted">План от ${fmtDT(plan.created_at)}${plan.params.scope_center_id ? ' (последним пересчитан участок)' : ''} · строк плана: ${plan.items} · строгость приоритета ${num(plan.params.strictness)}%</p>`;
    if (plan.warnings && plan.warnings.length) h += `<div class="card"><h2>Предупреждения (${plan.warnings.length})</h2><div class="scroll"><table>${plan.warnings.slice(0, 30).map(w => `<tr><td class="${w.code === 'NOT_DISTRIBUTED' ? 'err' : 'warn'}">${esc(WARN_RU[w.code] || w.code)}</td><td>${esc(w.text)}</td></tr>`).join('')}</table></div></div>`;
  }
  v.innerHTML = h;
  for (const id of ['rp', 'rp2']) if ($('#' + id)) $('#' + id).onclick = openReplan;
  if ($('#goDist')) $('#goDist').onclick = () => { S.dataTab = 'orders'; show('data'); };
};

/* ---------------- План на день + факт ---------------- */
let REASONS = null;
/* ---------------- Статус и загрузка ---------------- */
SCREENS.load = async v => {
  const L = await GET('/reports/load');
  if (!L.plan_id) { v.innerHTML = '<h1>Загрузка</h1><div class="card empty">План ещё не построен.</div>'; return; }
  const cls = x => x == null ? 'c0' : x >= 1 ? 'c3' : x >= 0.8 ? 'c2' : 'c1', dates = L.dates.slice(0, 30);
  const cell = (n, d) => { const x = L.plan[n][d]; return `<td><div class="cell ${cls(x)}" title="план ${num(x)}${L.fact[n][d] != null ? ', факт ' + num(L.fact[n][d]) : ''}">${x == null ? '' : (x >= 1 ? '!' : '') + num(x)}</div></td>`; };
  v.innerHTML = `<h1>Загрузка исполнителей</h1><div class="legend"><span><span class="badge b-work">&lt; 0,8</span> есть запас</span><span><span class="badge b-none">0,8–1</span> почти полная</span><span><span class="badge b-risk">! ≥ 1</span> перегруз</span><span class="muted">Доля рабочего дня исполнителя</span></div>
    <div class="card scroll"><table class="heat"><tr><th></th>${dates.map(d => `<th>${fmtD(d)}<br><small>${wd(d)}</small></th>`).join('')}</tr>${L.workers.map(n => `<tr><td class="name">${esc(n)}</td>${dates.map(d => cell(n, d)).join('')}</tr>`).join('')}</table></div>`;
};

/* ---------------- Перепланирование ---------------- */
async function openReplan() {
  const foreman = S.me.role === 'foreman';
  const [st, centers, act] = await Promise.all([GET('/settings').catch(() => ({ priority_strictness: 100 })), listAll('/work-centers'), GET('/plans/active')]);
  const first = !act.plan;              // активного плана нет — первый расчёт идёт сразу по всем участкам; дальше только по участкам
  const hist = MANAGERS.includes(S.me.role) ? (await GET('/plans')).items : [];
  const myCenter = centers.find(c => c.id === S.me.work_center_id);
  const scopeHtml = foreman
    ? `<p class="banner info">Вы перепланируете <b>свой участок${myCenter ? ' «' + esc(myCenter.name) + '»' : ''}</b>. Задания других участков не меняются.</p>`
    : first ? `<p class="banner info">Плана ещё нет. Первый расчёт строится сразу <b>по всем участкам</b>; дальше план пересчитывается по одному участку.</p>`
    : (centers.length ? `<label class="f">Участок<select id="scope">${centers.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select><span style="font-size:13px">Перепланирование выполняется по участкам. Задания других участков не меняются.</span></label>`
      : `<p class="banner warn">Перепланирование выполняется по участкам, а участков нет. Добавьте их в разделе «Организация → Участки».</p>`);
  const m = modal(`<h2>Перепланирование</h2>${scopeHtml}
    <label class="f"><b>Жёсткость приоритета: <span id="sv">${num(st.priority_strictness)}</span>%</b><input class="slider" id="sl" type="range" min="0" max="100" step="10" value="${st.priority_strictness}"></label>
    <p class="muted" style="margin-top:-6px">100% — следующий приоритет начинается, когда закончен предыдущий. 0% — приоритет задаёт только очерёдность доступа к людям. Значение влияет на этот запуск; постоянное — в «Настройках».</p>
    <div class="row"><button class="secondary" id="cmp">Сравнить варианты</button><button id="run">Построить план</button><button class="secondary" data-x>Закрыть</button></div>
    <div id="cr" class="scroll" style="margin-top:12px"></div>
    ${hist.length ? `<h3>История планов</h3><div class="scroll"><table>${hist.slice(0, 8).map(p => `<tr><td>${fmtDT(p.created_at)}</td><td>${num(p.params.strictness)}%</td><td>${p.params.scope_center_id ? esc(centers.find(c => c.id === p.params.scope_center_id)?.name || 'участок') : 'первый расчёт, все участки'}</td><td>${p.status === 'active' ? '<span class="badge b-done">активный</span>' : `<button class="small secondary" data-act="${p.id}">Вернуть этот</button>`}</td></tr>`).join('')}</table></div>` : ''}`, true);
  const sl = $('#sl', m.el); sl.oninput = () => $('#sv', m.el).textContent = sl.value; $('[data-x]', m.el).onclick = m.close;
  const scopeBody = () => { const el = $('#scope', m.el); return el && el.value ? { work_center_id: +el.value } : {}; };
  const run = async s => { try { await POST('/plans/replan', { strictness: +s, ...scopeBody() }); REASONS = null; m.close(); toast('План построен'); show(S.screen); } catch (e) { toast(errText(e), true); } };
  $('#run', m.el).onclick = () => run(sl.value);
  $$('[data-act]', m.el).forEach(b => b.onclick = async () => { try { await POST(`/plans/${b.dataset.act}/activate`); m.close(); toast('План возвращён'); show(S.screen); } catch (e) { toast(errText(e), true); } });
  $('#cmp', m.el).onclick = async () => {
    const box = $('#cr', m.el); box.innerHTML = '<p class="muted">Считаю варианты…</p>';
    try {
      const r = await POST('/plans/compare', { strictness: +sl.value, ...scopeBody() });
      box.innerHTML = `<table class="cmp"><tr><th>Вариант</th><th class="num">Жёсткость</th><th class="num">В риске</th><th class="num">Сдвиг прогноза, дн.</th><th class="num">Пик загрузки</th><th>Сильнее всего сдвинутся</th><th></th></tr>${r.variants.map(x => `<tr class="${x.current ? 'sel' : ''}"><td><b>${esc(x.label)}</b></td><td class="num">${x.strictness}%</td><td class="num">${x.at_risk}</td><td class="num">${x.avg_shift_days > 0 ? '+' : ''}${x.avg_shift_days}</td><td class="num">${x.max_load}</td><td>${x.most_affected.map(a => esc(a.product) + ' ' + esc(a.id) + ' (+' + a.shift_days + ')').join(', ') || '—'}</td><td>${x.current ? '' : `<button class="small" data-use="${x.strictness}">Применить</button>`}</td></tr>`).join('')}</table><p class="muted">Сдвиг — в рабочих днях относительно текущей настройки. Активный план не меняется, пока вы не нажмёте «Применить».</p>`;
      $$('[data-use]', box).forEach(b => b.onclick = () => run(b.dataset.use));
    } catch (e) { box.innerHTML = `<p class="err">${esc(errText(e))}</p>`; }
  };
}

/* ---------------- старт ---------------- */
(async function boot() {
  const m = location.hash.match(/^#\/(verify|reset|invite)\?token=([\w-]+)$/);
  if (m) { history.replaceState(null, '', location.pathname); return renderTokenPage(m[1], m[2]); }
  if (S.refresh && await doRefresh()) { try { S.me = await GET('/auth/me'); return start(); } catch (e) { /* падаем на вход */ } }
  renderAuth('login');
})();
