/* Панель суперадмина в jsdom: вход, список организаций, блокировка, вход под организацией (режим поддержки). */
const { JSDOM, VirtualConsole } = require('jsdom');
const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const PASS = 'ui-superadmin-pass-123', EMAIL = 'root-ui@plan24.test';
const errors = [], vc = new VirtualConsole();
vc.on('jsdomError', e => errors.push('jsdomError: ' + (e.detail || e.message)));
vc.on('error', e => errors.push('console.error: ' + e));
const sleep = ms => new Promise(r => setTimeout(r, ms));
let win, doc, opened = [];
const $ = s => doc.querySelector(s), $$ = s => [...doc.querySelectorAll(s)], byText = (sel, t) => $$(sel).find(e => e.textContent.trim().includes(t));
async function until(fn, what, t = 8000) { const s = Date.now(); while (Date.now() - s < t) { try { const r = fn(); if (r) return r; } catch (e) {} await sleep(40); } throw new Error('Не дождались: ' + what + '\nDOM: ' + doc.body.textContent.slice(0, 300)); }
const click = el => el.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true }));
const setv = (el, v) => { el.value = v; el.dispatchEvent(new win.Event('input', { bubbles: true })); };
const step = n => console.log('✔', n);
async function open(url) {
  const dom = await JSDOM.fromURL(url, { runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE), o); w.TextDecoder = TextDecoder; w.prompt = () => 'ui-тест'; w.confirm = () => true; w.open = u => { opened.push(u); return null; }; w.close = () => { }; } });
  win = dom.window; doc = win.document; return dom;
}
const api = (path, body) => fetch(BASE + '/api/v1' + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

(async () => {
  const reg = await api('/auth/register', { org_name: 'Цех <b>Админ</b>', name: 'Владелец', email: 'owner-ui-admin@x.ru', password: 'password-1', accept_terms: true });
  if (reg.status !== 201) throw new Error('регистрация: ' + reg.status);
  await open(BASE + '/admin');
  await until(() => $('form input[name=password]'), 'форма входа суперадмина'); step('страница /admin: форма входа');
  setv($('input[name=email]'), EMAIL); setv($('input[name=password]'), 'wrong-wrong-wrong-1');
  $('form').dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => /Неверный/.test($('.err').textContent), 'сообщение о неверном пароле'); step('неверный пароль отклонён');
  setv($('input[name=password]'), PASS); $('form').dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => $('tr.clickable'), 'таблица организаций'); step('вход выполнен, список организаций');
  const row = byText('tr.clickable', 'Админ');
  if (!row || row.querySelector('b b') || !row.cells[0].textContent.includes('<b>Админ</b>')) throw new Error('название организации должно выводиться текстом, без HTML: ' + (row && row.innerHTML));
  if (!$$('.kpi').length) throw new Error('нет сводных показателей'); step('сводные показатели и название без разметки (XSS)');
  click(row); await until(() => $('.adm-modal') && byText('.adm-modal button', 'Заблокировать'), 'карточка организации'); step('карточка организации открыта');
  click(byText('.adm-modal button', 'Заблокировать'));
  await until(() => $('.adm-modal .warnbox'), 'организация заблокирована'); step('организация заблокирована');
  const lg = await api('/auth/login', { email: 'owner-ui-admin@x.ru', password: 'password-1' });
  if (lg.status !== 403) throw new Error('вход заблокированной организации: ' + lg.status); step('заблокированная организация не входит');
  click(byText('.adm-modal button', 'Разблокировать')); await until(() => !$('.adm-modal .warnbox') && byText('.adm-modal button', 'Заблокировать'), 'разблокировка'); step('разблокирована');
  click(byText('.adm-modal button', 'Войти под организацией'));
  await until(() => opened.length, 'ссылка входа поддержки'); const url = opened[0]; step('ссылка входа под организацией создана');
  if (!/\/#imp=/.test(url)) throw new Error('неверная ссылка: ' + url);
  const adminDoc = [win, doc];
  await open(BASE + url);
  await until(() => $('.supportbar') && $('header.top'), 'режим поддержки'); step('режим поддержки: баннер и приложение организации');
  if (win.localStorage.getItem('p24_rt')) throw new Error('в режиме поддержки не должно быть сохранённой сессии');
  if (/imp=/.test(win.location.href)) throw new Error('токен остался в адресной строке');
  click($('.supportbar button')); await until(() => /Режим поддержки завершён/.test(doc.body.textContent), 'завершение поддержки'); step('режим поддержки завершён');
  [win, doc] = adminDoc;
  console.log('\nОшибок JS в консоли:', errors.length); errors.forEach(e => console.log('  ', e));
  process.exit(errors.length ? 1 : 0);
})().catch(e => { console.error('✘ ПАДЕНИЕ:', e.message); errors.forEach(x => console.error('  ', x)); process.exit(1); });
