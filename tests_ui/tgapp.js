/* Сквозная проверка мини-приложения Telegram: настоящий сервер, настоящая подпись данных запуска (как у Telegram), настоящие нажатия. */
const { JSDOM, VirtualConsole } = require('jsdom');
const crypto = require('crypto');
const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const TG_TOKEN = '1:TEST', TG_SECRET = 'ui-secret';
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', e => errors.push('jsdomError: ' + (e.detail || e.message)));
vc.on('error', e => errors.push('console.error: ' + e));
const sleep = ms => new Promise(r => setTimeout(r, ms));
let win, doc;
const $ = s => doc.querySelector(s), $$ = s => [...doc.querySelectorAll(s)];
const byText = (sel, txt) => $$(sel).find(e => e.textContent.trim().includes(txt));
const step = n => console.log('✔', n);
async function until(fn, what, t = 8000) { const s = Date.now(); while (Date.now() - s < t) { try { const r = fn(); if (r) return r; } catch (e) { /* ждём */ } await sleep(40); } throw new Error('Не дождались: ' + what + '\n  DOM: ' + (doc.getElementById('app') ? doc.getElementById('app').textContent.replace(/\s+/g, ' ').slice(0, 400) : '')); }
function click(el) { el.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true })); }
function setv(el, v) { el.value = v; el.dispatchEvent(new win.Event('input', { bubbles: true })); el.dispatchEvent(new win.Event('change', { bubbles: true })); }

async function J(method, path, body, token) {
  const r = await fetch(BASE + '/api/v1' + path, { method, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}),
    ...(path.includes('telegram/webhook') ? { 'X-Telegram-Bot-Api-Secret-Token': crypto.createHash('sha256').update('hdr:' + TG_SECRET).digest('hex').slice(0, 48) } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
  const t = await r.text(); let d = null; try { d = JSON.parse(t); } catch (e) { /* не JSON */ }
  if (!r.ok) throw new Error(`${method} ${path} → ${r.status} ${t.slice(0, 200)}`);
  return d;
}
function initData(userId, { token = TG_TOKEN, authDate = Math.floor(Date.now() / 1000) } = {}) {
  const d = { auth_date: String(authDate), query_id: 'AAH-ui', user: JSON.stringify({ id: userId, first_name: 'UI' }) };
  const check = Object.keys(d).sort().map(k => `${k}=${d[k]}`).join('\n');
  const secret = crypto.createHmac('sha256', 'WebAppData').update(token).digest();
  d.hash = crypto.createHmac('sha256', secret).update(check).digest('hex');
  return new URLSearchParams(d).toString();
}
async function openApp(hash) {
  const dom = await JSDOM.fromURL(BASE + '/tg/' + (hash ? '#' + hash : ''), { runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) { w.fetch = async (u, o) => fetch(new URL(u, BASE), o); w.scrollTo = () => {}; } });
  win = dom.window; doc = win.document; return dom;
}
const appHash = (userId, theme) => 'tgWebAppData=' + encodeURIComponent(initData(userId)) + (theme ? '&tgWebAppThemeParams=' + encodeURIComponent(JSON.stringify(theme)) : '') + '&tgWebAppVersion=7.0';
const cards = () => $$('#main .card');
const toastText = () => $('#toast').className.includes('show') ? $('#toast').textContent : '';
async function untilFact(pred, what, t = 8000) { const s0 = Date.now(); let last; while (Date.now() - s0 < t) { last = await factsOf(); if (pred(last)) return last; await sleep(80); } throw new Error('Не дождались факта: ' + what + ' — сейчас ' + JSON.stringify(last)); }
let factsOf;

(async () => {
  // ---------- подготовка: организация, демо, план, привязки ----------
  const email = `tgapp-${Date.now()}@tgui.ru`;
  const reg = await J('POST', '/auth/register', { org_name: 'Мини-приложение', first_name: 'Анна', last_name: 'Админ', email, password: 'password-1', accept_terms: true });
  const T = reg.access_token;
  await J('POST', '/demo/load?kind=furniture', undefined, T);
  await J('POST', '/plans/replan', {}, T);
  const day = (await J('GET', '/reports/day', undefined, T)).workers;
  const w = day.find(x => x.tasks.filter(t => t.plan_qty >= 2).length >= 2);
  if (!w) throw new Error('в демо нет работника с двумя заданиями');
  const planned = w.tasks.filter(t => t.plan_qty);
  const WCHAT = 880001, FCHAT = 880002;
  // имя с разметкой: приложение обязано показать его как текст
  await J('PATCH', `/workers/${w.worker_id}`, { name: 'Иванов <img src=x onerror="window.__xss=1">' }, T);
  const code = (await J('POST', '/telegram/codes', { worker_id: w.worker_id }, T)).code;
  await J('POST', `/integrations/telegram/webhook/${TG_SECRET}`, { update_id: 880001, message: { chat: { id: WCHAT, type: 'private' }, text: '/start ' + code } });
  const wk = (await J('GET', '/workers', undefined, T)).items.find(x => x.id === w.worker_id);
  await J('POST', '/users', { email: `f-${Date.now()}@tgui.ru`, first_name: 'Борис', last_name: 'Бригадир', role: 'foreman', work_center_id: wk.work_center_id, password: 'password-2' }, T);
  const fu = (await J('GET', '/users', undefined, T)).find(u => u.role === 'foreman');
  const fcode = (await J('POST', '/telegram/codes', { user_id: fu.id }, T)).code;
  await J('POST', `/integrations/telegram/webhook/${TG_SECRET}`, { update_id: 880002, message: { chat: { id: FCHAT, type: 'private' }, text: '/start ' + fcode } });
  factsOf = async () => (await J('GET', '/facts', undefined, T)).items.filter(f => f.worker_id === w.worker_id);
  step(`подготовка: демо, план, привязан сборщик (${planned.length} заданий) и бригадир`);

  // ---------- без данных Telegram приложение не пускает ----------
  await openApp('');
  await until(() => /открывается кнопкой в чате/.test($('#main').textContent), 'экран «откройте из Telegram»');
  if ($('#tabs').children.length) throw new Error('без входа не должно быть меню');
  step('без данных запуска из Telegram — понятное сообщение, вкладок нет');

  // ---------- подделанная подпись ----------
  await openApp('tgWebAppData=' + encodeURIComponent(initData(WCHAT, { token: '9:OTHER' })));
  await until(() => /Не удалось подтвердить вход/.test($('#main').textContent), 'отказ по чужой подписи');
  await openApp('tgWebAppData=' + encodeURIComponent(initData(999999)));
  await until(() => /не привязан/i.test($('#main').textContent), 'отказ непривязанному Telegram');
  step('чужая подпись и непривязанный Telegram — отказ с пояснением');

  // ---------- сборщик ----------
  await openApp(appHash(WCHAT, { bg_color: '#112233', text_color: '#eeeeee', button_color: '#ff8800', button_text_color: '#000000' }));
  await until(() => $$('#main .card').length >= planned.length && $('#hd h1'), 'экран заданий');
  if ($('#hd h1').textContent !== 'Мои задания') throw new Error('заголовок: ' + $('#hd h1').textContent);
  if (win.document.documentElement.style.getPropertyValue('--btn') !== '#ff8800') throw new Error('тема Telegram не применилась');
  const names = $$('#main h3').map(x => x.textContent).join('|');
  if (win.__xss || $('#app img')) throw new Error('разметка из имени попала в страницу (XSS)');
  step(`сборщик: ${cards().length} карточек заданий, тема Telegram применена, разметка в данных не исполняется`);
  const tabsTxt = $$('#tabs button').map(b => b.textContent.replace(/\s+/g, ' ').trim()).join(' | ');
  for (const t of ['Задания', 'Завтра', 'Проблема', 'Не смогу выйти'.replace('Не смогу выйти', 'Не выйду')]) if (!tabsTxt.includes(t) && !(t === 'Не выйду' && tabsTxt.includes('Не смогу выйти'))) throw new Error('нет вкладки ' + t + ': ' + tabsTxt);

  // «+»: факт сохраняется (после паузы-дебаунса), всплывает «Отменить»
  const first = cards()[0], plus = [...first.querySelectorAll('.counter button')].find(b => b.textContent === '+');
  click(plus); click(plus);
  await until(() => /Записано: 2/.test(toastText()), 'сохранение двух нажатий «+»');
  let fs = await untilFact(f => f.length === 1 && f[0].qty === 2, 'факт = 2'); if (fs[0].source !== 'telegram') throw new Error('источник факта: ' + fs[0].source);
  step('«+» дважды → одна запись факта = 2 (источник telegram), тост «Записано: 2»');
  click(byText('#toast button', 'Отменить'));
  await untilFact(f => f.length === 0, 'отмена убрала запись');
  step('«Отменить» убирает запись (прежнего значения не было)');

  // «Всё» → план; затем «−» ниже плана → причина
  const c0 = () => cards()[0];
  const plan0 = planned[0].plan_qty;
  click([...c0().querySelectorAll('.chip')].find(b => b.textContent.includes('Всё')));
  await untilFact(f => f.length === 1 && f[0].qty === plan0, `«Всё» вносит план ${plan0}`);
  step(`«Всё» вносит плановое количество (${plan0}), карточка помечена выполненной`);
  if (!c0().classList.contains('done')) throw new Error('карточка не стала зелёной');
  click([...c0().querySelectorAll('.counter button')].find(b => b.textContent === '−'));
  await untilFact(f => f.length === 1 && f[0].qty === plan0 - 1, 'факт на единицу меньше плана');
  await until(() => $('.reasons'), 'вопрос о причине при факте ниже плана', 6000);
  click($$('.reasons .chip')[0]);
  await until(() => /Причина сохранена/.test(toastText()), 'причина сохранена');
  step('меньше плана → вопрос «Что помешало?» → причина сохраняется');

  // ввод числа руками
  setv(c0().querySelector('.counter input'), '3,5');
  await untilFact(f => f.length === 1 && f[0].qty === 3.5, 'ручной ввод 3,5');
  step('число можно ввести руками («3,5» → 3.5)');

  // закрыть день
  const closeBtn = byText('#main button', 'Закрыть день по плану');
  if (planned.length > 1) {
    click(closeBtn);
    await until(() => !$('#sheet').hidden, 'окно подтверждения');
    if (!/Внести по плану/.test($('#sheet').textContent)) throw new Error('нет текста подтверждения');
    click(byText('#sheet button', 'Внести'));
    await untilFact(f => f.length === planned.length, 'факт по всем заданиям после «Закрыть день»');
    step('«Закрыть день по плану»: подтверждение → факт по всем заданиям без факта');
  }

  // другие вкладки
  click(byText('#tabs button', 'Завтра')); await until(() => /Завтра|Ближайший день/.test($('#hd').textContent), 'вкладка «Завтра»');
  step('вкладка «Завтра» открывается: ' + $('#hd h1').textContent);
  click(byText('#tabs button', 'Проблема')); await until(() => $('textarea'), 'форма проблемы');
  if (!byText('#main button', 'Отправить бригадиру').disabled) throw new Error('кнопка отправки должна быть выключена, пока не выбран тип');
  click(byText('#main .chip', 'Нет материала')); setv($('textarea'), 'нет ленты');
  click(byText('#main button', 'Отправить бригадиру'));
  await until(() => /Передал бригадиру|не подключён к Telegram/.test(toastText()), 'отправка проблемы');
  step('проблема отправляется: «' + toastText().slice(0, 60) + '»');
  click(byText('#tabs button', 'Не смогу выйти') || byText('#tabs button', 'Не выйду')); await until(() => byText('#main .chip', 'Болезнь'), 'форма отсутствия');
  click(byText('#main .chip', 'Завтра')); click(byText('#main .chip', 'Болезнь'));
  click(byText('#main button', 'Сохранить'));
  await until(() => /Записал: вы не выйдете завтра/.test(toastText()), 'отсутствие записано');
  const ab = (await J('GET', '/absences', undefined, T)).items.filter(a => a.worker_id === w.worker_id);
  if (!ab.length || ab[0].reason !== 'Болезнь') throw new Error('отсутствие не создано');
  step('«Не выйду»: отсутствие создано в системе');
  if (byText('#tabs button', 'Участок')) throw new Error('у сборщика не должно быть экрана участка');

  // ---------- бригадир ----------
  await J('DELETE', `/absences/${ab[0].id}`, undefined, T);
  await openApp(appHash(FCHAT));
  await until(() => $('#hd h1') && $('#hd h1').textContent === 'Участок', 'экран участка бригадира');
  if (!byText('#main .person', 'Иванов')) throw new Error('в списке участка нет работника');
  if (win.__xss || $('#app img')) throw new Error('XSS через имя на экране бригадира');
  const tabsF = $$('#tabs button').map(b => b.textContent.replace(/\s+/g, ' ').trim()).join(' | ');
  for (const t of ['Участок', 'Подтвердить', 'Отсутствие', 'Завтра']) if (!tabsF.includes(t)) throw new Error('нет вкладки ' + t + ': ' + tabsF);
  if (!byText('#main button', 'Перепланировать участок')) throw new Error('нет кнопки перепланирования');
  step('бригадир: участок по людям, вкладки «Подтвердить/Отсутствие/Завтра», кнопка перепланирования');
  click(byText('#main .person', 'Иванов'));
  await until(() => byText('#main button', 'К участку') && $$('#main .card').length, 'задания работника у бригадира');
  step('бригадир открывает работника и видит его задания');
  click(byText('#tabs button', 'Подтвердить')); await until(() => /Неподтверждённого факта нет|Подтвердить все/.test($('#main').textContent), 'очередь подтверждений');
  click(byText('#tabs button', 'Отсутствие')); await until(() => $('select') && byText('#main .chip', 'Болезнь'), 'форма отсутствия бригадира');
  if (![...$('select').options].some(o => /Иванов/.test(o.textContent))) throw new Error('в выборе работника нет членов участка');
  step('бригадир: очередь подтверждений и форма отсутствия открываются, работник выбирается из своего участка');

  if (errors.length) throw new Error('Ошибки в консоли страницы:\n' + errors.join('\n'));
  console.log('\nОшибок JS в консоли: 0');
})().catch(e => { console.error('\n✘', e.message); process.exit(1); });
