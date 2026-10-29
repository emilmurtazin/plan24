/* Панель суперадмина. Все тексты из базы (названия организаций, имена) выводятся только через textContent. */
'use strict';
const API = '/api/v1/superadmin';
const KEY = 'p24_admin_token';
const root = document.getElementById('root');
let token = sessionStorage.getItem(KEY) || '';
let tenantsCache = [];
let reloadMain = () => { };          // обновление списка организаций на главном экране

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v; else if (k.startsWith('on')) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
function toast(msg) { const t = document.getElementById('toast'); t.textContent = msg; t.classList.remove('hidden'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add('hidden'), 3500); }
function fmt(iso, withTime) {
  if (!iso) return '—';
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  return d.toLocaleString('ru-RU', withTime ? { dateStyle: 'short', timeStyle: 'short' } : { dateStyle: 'medium' });
}
function ago(iso) {
  if (!iso) return '—';
  const days = Math.floor((Date.now() - new Date(iso.endsWith('Z') ? iso : iso + 'Z')) / 864e5);
  return days <= 0 ? 'сегодня' : days === 1 ? 'вчера' : days + ' дн. назад';
}

async function api(method, path, body) {
  const r = await fetch(API + path, { method, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
  let d = null; try { d = await r.json(); } catch (e) { /* пустой ответ */ }
  if (r.status === 401 && token) { logout('Сессия истекла. Войдите снова'); throw new Error('401'); }
  if (!r.ok) throw new Error((d && (d.title || d.detail)) || 'Ошибка ' + r.status);
  return d;
}
function logout(msg) { token = ''; sessionStorage.removeItem(KEY); document.querySelector('.modal')?.remove(); renderLogin(msg); }

/* ---------------- вход ---------------- */
function renderLogin(msg) {
  const err = h('div', { class: 'err', role: 'alert' }, msg || '');
  const email = h('input', { type: 'email', name: 'email', autocomplete: 'username', required: true });
  const pw = h('input', { type: 'password', name: 'password', autocomplete: 'current-password', required: true });
  const form = h('form', { novalidate: true, onsubmit: async e => {
    e.preventDefault(); err.textContent = '';
    try {
      const r = await fetch(API + '/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: email.value, password: pw.value }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { err.textContent = d.title || d.detail || 'Ошибка входа'; return; }
      token = d.access_token; sessionStorage.setItem(KEY, token); renderMain(d.email);
    } catch (x) { err.textContent = 'Нет связи с сервером'; }
  } }, h('label', { class: 'f' }, 'E-mail', email), h('label', { class: 'f' }, 'Пароль', pw), err, h('button', { type: 'submit' }, 'Войти'));
  root.replaceChildren(h('div', { class: 'adm adm-login card' }, h('h1', null, 'Plan 24 · суперадмин'), h('p', { class: 'muted' }, 'Вход для администратора платформы'), form));
  email.focus();
}

/* ---------------- главный экран ---------------- */
async function renderMain(email) {
  const kpis = h('div', { class: 'kpis' });
  const search = h('input', { type: 'search', placeholder: 'Поиск: название, e-mail владельца или номер', 'aria-label': 'Поиск', oninput: () => load() });
  const tbody = h('tbody');
  const note = h('p', { class: 'muted' });
  root.replaceChildren(h('div', { class: 'adm' },
    h('div', { class: 'adm-top' }, h('h1', null, 'Plan 24 · суперадмин'), h('span', { class: 'muted' }, email || ''), h('button', { class: 'secondary', onclick: () => logout() }, 'Выйти')),
    kpis, h('div', { class: 'row', style: 'margin-bottom:12px' }, h('div', { class: 'grow' }, search), h('button', { class: 'secondary', onclick: () => load(true) }, 'Обновить')),
    h('div', { class: 'card flush scroll' }, h('table', null, h('thead', null, h('tr', null, ['Организация', 'Владелец', 'Регистрация', 'Польз.', 'Работн.', 'Заказы', 'Telegram', 'Активность', 'Статус'].map((t, i) => h('th', { class: i >= 3 && i <= 6 ? 'num' : '' }, t)))), tbody)), note));
  async function load(force) {
    try {
      if (force || !kpis.childNodes.length) {
        const o = await api('GET', '/overview');
        kpis.replaceChildren(...[['Организаций', o.tenants], ['Активны за 7 дней', o.active_7d], ['Новые за 30 дней', o.new_30d], ['Заблокировано', o.blocked], ['Пользователей', o.users], ['Работников', o.workers], ['Заказов', o.orders]]
          .map(([l, v]) => h('div', { class: 'kpi' }, h('b', null, v), h('span', null, l))));
      }
      const d = await api('GET', '/tenants?q=' + encodeURIComponent(search.value));
      tenantsCache = d.items;
      tbody.replaceChildren(...d.items.map(t => h('tr', { class: 'clickable', tabindex: 0, onclick: () => openTenant(t.id), onkeydown: e => { if (e.key === 'Enter') openTenant(t.id); } },
        h('td', null, h('b', null, t.name), h('small', null, '№ ' + t.id)),
        h('td', null, t.owner ? [t.owner.name, h('small', null, t.owner.email + (t.owner.verified ? '' : ' · не подтверждён'))] : '—'),
        h('td', null, fmt(t.created_at)), h('td', { class: 'num' }, t.users), h('td', { class: 'num' }, t.workers), h('td', { class: 'num' }, t.orders), h('td', { class: 'num' }, t.telegram),
        h('td', null, ago(t.last_activity_at)),
        h('td', null, t.blocked ? h('span', { class: 'badge b-off' }, 'заблокирована') : h('span', { class: 'badge b-ok' }, 'работает')))));
      note.textContent = d.items.length ? '' : 'Ничего не найдено';
    } catch (x) { if (x.message !== '401') note.textContent = x.message; }
  }
  reloadMain = () => load(true);
  await load(true);
}

/* ---------------- организация ---------------- */
async function openTenant(id) {
  let t;
  try { t = await api('GET', '/tenants/' + id); } catch (x) { if (x.message !== '401') toast(x.message); return; }
  const close = () => { m.remove(); };
  async function act(label, fn, confirmText) {
    if (confirmText && !confirm(confirmText)) return;
    try { await fn(); toast(label); close(); openTenant(id); reloadMain(); } catch (x) { if (x.message !== '401') toast(x.message); }
  }
  async function enter(userId) {
    try {
      const r = await api('POST', `/tenants/${id}/impersonate`, userId ? { user_id: userId } : {});
      window.open('/#imp=' + encodeURIComponent(r.access_token), '_blank', 'noopener');
      toast(`Вход открыт в новой вкладке: ${r.user.name} (${r.user.role}), действует ${Math.round(r.expires_in / 60)} мин.`);
    } catch (x) { if (x.message !== '401') toast(x.message); }
  }
  const users = h('table', null, h('thead', null, h('tr', null, ['Пользователь', 'Роль', 'Статус', ''].map(x => h('th', null, x)))),
    h('tbody', null, t.users_list.map(u => h('tr', null,
      h('td', null, u.name, h('small', null, u.email + (u.verified ? '' : ' · e-mail не подтверждён'))), h('td', null, u.role),
      h('td', null, u.active ? h('span', { class: 'badge b-ok' }, 'активен') : h('span', { class: 'badge b-grey' }, 'отключён')),
      h('td', { class: 'row', style: 'justify-content:flex-end' },
        u.active ? h('button', { class: 'small secondary', onclick: () => enter(u.id) }, 'Войти под ним') : null,
        u.active ? h('button', { class: 'small secondary', onclick: () => act('Письмо со ссылкой отправлено на ' + u.email, () => api('POST', `/users/${u.id}/reset-password`), `Отправить ${u.email} ссылку для смены пароля?`) }, 'Сбросить пароль') : null,
        h('button', { class: 'small ' + (u.active ? 'danger' : 'secondary'), onclick: () => act(u.active ? 'Пользователь отключён' : 'Пользователь включён', () => api('POST', `/users/${u.id}/${u.active ? 'deactivate' : 'activate'}`), u.active ? `Отключить ${u.email}? Его сессии будут закрыты.` : null) }, u.active ? 'Отключить' : 'Включить'))))));
  const m = h('div', { class: 'modal adm-modal', onclick: e => { if (e.target === m) close(); } }, h('div', { role: 'dialog', 'aria-label': t.name },
    h('div', { class: 'row' }, h('h2', { class: 'grow', style: 'margin:0' }, t.name), t.blocked ? h('span', { class: 'badge b-off' }, 'заблокирована') : h('span', { class: 'badge b-ok' }, 'работает'), h('button', { class: 'secondary small', onclick: close, 'aria-label': 'Закрыть' }, '✕')),
    t.blocked ? h('div', { class: 'warnbox' }, `Заблокирована ${fmt(t.blocked_at, true)}${t.blocked_reason ? ': ' + t.blocked_reason : ''}. Пользователи не могут войти, бот молчит, рассылки не отправляются.`) : null,
    h('div', { class: 'info' }, [['Номер', t.id], ['Регистрация', fmt(t.created_at, true)], ['Часовой пояс', t.timezone], ['Работников', t.workers], ['Заказов', t.orders], ['Привязок Telegram', t.telegram],
      ['Последний план', fmt(t.last_plan_at, true)], ['Последняя активность', fmt(t.last_activity_at, true)]].map(([k, v]) => h('div', null, h('span', null, k), String(v)))),
    h('div', { class: 'row', style: 'margin:12px 0' },
      h('button', { onclick: () => enter() }, 'Войти под организацией (владелец)'),
      t.blocked ? h('button', { class: 'secondary', onclick: () => act('Организация разблокирована', () => api('POST', `/tenants/${id}/unblock`)) }, 'Разблокировать')
        : h('button', { class: 'danger', onclick: () => { const reason = prompt('Причина блокировки (необязательно):'); if (reason === null) return; act('Организация заблокирована', () => api('POST', `/tenants/${id}/block`, { reason }), `Заблокировать «${t.name}»? Все пользователи потеряют доступ.`); } }, 'Заблокировать')),
    h('p', { class: 'muted', style: 'margin:0 0 12px' }, 'Вход под организацией действует 30 минут, в нём нельзя менять пароль и e-mail; действия отмечаются в журнале организации.'),
    h('h3', null, 'Пользователи'), h('div', { class: 'scroll' }, users),
    t.history.length ? [h('h3', null, 'Действия суперадмина'), h('ul', { class: 'hist' }, t.history.map(e => h('li', null, `${fmt(e.at, true)} — ${({ platform_block: 'блокировка', platform_unblock: 'разблокировка', platform_user_off: 'отключён пользователь', platform_user_on: 'включён пользователь', platform_reset_link: 'ссылка сброса пароля', platform_login: 'вход под организацией' })[e.action] || e.action}${e.target ? ' · ' + e.target : ''}${e.reason ? ' · ' + e.reason : ''}`)))] : null));
  document.body.append(m);
}

if (token) { api('GET', '/me').then(d => renderMain(d.email)).catch(() => renderLogin()); } else renderLogin();
