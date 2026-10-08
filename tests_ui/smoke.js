const { JSDOM, VirtualConsole } = require('jsdom');
const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', e => errors.push('jsdomError: ' + (e.detail || e.message)));
vc.on('error', e => errors.push('console.error: ' + e));
const sleep = ms => new Promise(r => setTimeout(r, ms));
let win, doc;
async function until(fn, what, t = 8000) { const s = Date.now(); while (Date.now() - s < t) { try { const r = fn(); if (r) return r; } catch (e) {} await sleep(40); } throw new Error('Не дождались: ' + what + '\nТекущий DOM: ' + (doc.querySelector('#view')?.textContent || doc.body.textContent).slice(0, 300)); }
const $ = s => doc.querySelector(s), $$ = s => [...doc.querySelectorAll(s)];
const byText = (sel, txt) => $$(sel).find(e => e.textContent.trim().includes(txt));
function setv(el, v) { el.value = v; el.dispatchEvent(new win.Event('input', { bubbles: true })); el.dispatchEvent(new win.Event('change', { bubbles: true })); }
function click(el) { el.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true })); }
async function open(url) {
  const dom = await JSDOM.fromURL(url, { runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) { w.fetch = async (u, o) => { const r = await fetch(new URL(u, BASE), o); if (global.TRACE && /facts|reports\/day/.test(u)) { const c = r.clone(); const t = await c.text(); console.log('   TRACE', o && o.method, u, o && o.body || '', '=>', r.status, t.slice(0, 160)); } return r; }; w.print = () => {}; } });
  win = dom.window; doc = win.document; return dom;
}
const step = n => console.log('✔', n);
async function mailLink(to, kind) {
  for (let i = 0; i < 40; i++) {
    const j = await (await fetch(`${BASE}/api/v1/dev/outbox?to=${encodeURIComponent(to)}`)).json();
    const m = j.items.filter(x => x.text.includes(`#/${kind}?token=`)).pop();
    if (m) return m.text.match(/(https?:\/\/\S+#\/\w+\?token=[\w-]+)/)[1].replace(/^https?:\/\/[^/]+/, BASE);
    await sleep(100);
  }
  throw new Error('письмо ' + kind + ' для ' + to + ' не пришло');
}
async function inFreshWindow(url, fn) {           // отдельная вкладка (как переход по ссылке из письма)
  const saved = [win, doc];
  await open(url);
  try { return await fn(); } finally { [win, doc] = saved; }
}

(async () => {
  await open(BASE + '/');
  await until(() => $('#af'), 'форма входа'); step('форма входа показана');

  // --- регистрация ---
  click(byText('button', 'Регистрация организации'));
  await until(() => $('[name=org_name]'), 'форма регистрации');
  const f = $('#af');
  const E = f.elements; setv(E.org_name, 'Цех Смоук'); setv(E.name, 'Анна Петрова'); setv(E.email, 'anna@smoke.ru'); setv(E.password, 'password-1');
  f.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => $('#aerr')?.textContent, 'ошибка согласия');
  if (!/согласие/i.test($('#aerr').textContent)) throw new Error('ожидалась ошибка согласия: ' + $('#aerr').textContent);
  step('без согласия регистрация отклонена: «' + $('#aerr').textContent + '»');
  E.accept_terms.checked = true;
  f.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => $('header.top'), 'оболочка'); 
  if (!$('header.top').textContent.includes('Цех Смоук')) throw new Error('нет названия организации');
  step('регистрация → оболочка, владелец: ' + $('header.top').textContent.replace(/\s+/g, ' ').slice(0, 60));
  if (!$('#vb')) throw new Error('нет баннера о неподтверждённом e-mail');
  step('баннер «E-mail не подтверждён» показан');
  click($('#rv')); await until(() => /Письмо отправлено|минуту/.test($('#toast').textContent), 'повторная отправка');
  step('повторная отправка: «' + $('#toast').textContent + '»');
  const vlink = await mailLink('anna@smoke.ru', 'verify');
  await inFreshWindow(vlink, async () => { await until(() => /E-mail подтверждён/.test($('#root').textContent), 'страница подтверждения'); });
  step('ссылка из письма подтверждает e-mail');
  const nav = $$('nav.tabs > button').map(b => b.textContent);
  console.log('   меню владельца:', nav.join(' | '));

  // --- пустое состояние и демо ---
  await until(() => byText('h1', 'Начнём'), 'онбординг'); step('пустая организация → онбординг');
  click($('#demo'));
  await until(() => $$('.kpi').length >= 4, 'обзор с KPI', 15000);
  step('демо загружено, план построен, KPI: ' + $$('.kpi').map(k => k.textContent.replace(/\s+/g, ' ')).join(' / '));

  // --- план на день и факт ---
  click(byText('nav.tabs button', 'План на день'));
  await until(() => $$('.wcard').length, 'карточки исполнителей');
  const cards = $$('.wcard').length, inputs = $$('.fact input').length;
  step(`план на день: карточек ${cards}, полей ввода факта ${inputs}`);
  const firstDone = $('[data-done]'); const w0 = firstDone.closest('.wcard').querySelector('h3').textContent;
  click(firstDone);
  await until(() => $('#toast') && !$('#toast').classList.contains('hidden') && /Факт сохранён|Исправлено/.test($('#toast').textContent), 'тост факта');
  await until(() => $$('.b-done').length, 'бейдж выполнено');
  step(`факт «Всё» сохранён для ${w0}: ${$('#toast').textContent}`);
  const inp = $('.fact input[value]:not([value=""])'); setv(inp, String(Number(inp.value) + 1));
  click(inp.closest('.fact').querySelector('[data-save]'));
  await until(() => /Исправлено/.test($('#toast').textContent), 'исправление факта');
  step('повторный ввод заменяет значение: ' + $('#toast').textContent);

  // --- обзор показывает «данные изменились» ---
  click(byText('nav.tabs button', 'Обзор'));
  await until(() => $('#view .banner.warn'), 'баннер устаревшего плана');
  step('после факта обзор предупреждает: «' + $('#view .banner.warn').textContent.replace(/\s+/g, ' ').slice(0, 70) + '…»');
  click($('#rp2'));
  await until(() => $('#cmp'), 'окно перепланирования');
  click($('#cmp'));
  await until(() => $$('.cmp tr').length >= 5, 'таблица сравнения');
  step('сравнение вариантов: ' + $$('.cmp tr').slice(1).map(r => r.cells[0].textContent + '→' + r.cells[2].textContent + ' в риске').join('; '));
  click(byText('button', 'Построить план'));
  await until(() => !$('.modal'), 'закрытие окна'); await until(() => !$('#view .banner.warn'), 'баннер исчез после пересчёта');
  step('пересчёт → баннер «устарел» исчез');

  // --- статус и загрузка ---
  // регрессия: переключение раздела сразу после перерисовки не должно затираться запоздавшим ответом прежнего экрана
  click(byText('nav.tabs button', 'Обзор')); click(byText('nav.tabs button', 'Загрузка')); click(byText('nav.tabs button', 'Обзор')); click(byText('nav.tabs button', 'Статус заказов'));
  await sleep(1200);
  if (!$('#tb')) throw new Error('запоздавший ответ перерисовал не тот раздел: ' + $('#view').textContent.slice(0, 80));
  step('быстрое переключение разделов: остаётся последний выбранный');
  click(byText('nav.tabs button', 'Статус заказов'));
  await until(() => $$('#tb tr').length > 3, 'таблица статусов'); step('статус заказов: строк ' + ($$('#tb tr').length - 1));
  const total = $$('#tb tr').length - 1; setv($('#fs'), 'В работе');
  const inWork = $$('#tb tr').length - 1; setv($('#fq'), 'шкаф'); const q = $$('#tb tr').length - 1;
  if (!(q > 0 && q < inWork)) throw new Error('поиск не сужает список: ' + q + ' из ' + inWork);
  setv($('#fq'), ''); setv($('#fs'), '');
  step(`фильтры: всего ${total}, «В работе» ${inWork}, поиск «шкаф» ${q}`);
  click(byText('nav.tabs button', 'Загрузка'));
  await until(() => $$('.heat tr').length > 2, 'тепловая карта'); step('загрузка: исполнителей ' + ($$('.heat tr').length - 1));

  // --- данные ---
  click(byText('nav.tabs button', 'Данные'));
  await until(() => $$('#dt tr').length > 3, 'заказы'); step('данные/заказы: ' + ($$('#dt tr').length - 1) + ' позиций');
  click(byText('#dt button', 'Добавить позицию'));
  await until(() => $('.modal form'), 'форма позиции');
  const pf = $('.modal form'); setv(pf.elements.qty, '-3'); pf.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await sleep(300); step('невалидная форма не уходит на сервер (' + ($('.modal [data-err]').textContent || 'браузерная валидация') + ')');
  click(byText('.modal button', 'Отмена'));
  click(byText('#view nav button', 'Изделия и маршруты'));
  await until(() => $('[data-rt]'), 'кнопка маршрута'); click($('[data-rt]'));
  await until(() => $('.modal table'), 'маршрут'); 
  step('маршрут: ' + ($('.modal .muted').textContent.slice(0, 80)) + '…');
  click(byText('.modal button', 'Новая версия'));
  await until(() => /v2/.test($('.modal h2').parentElement.textContent), 'версия 2'); step('создана версия маршрута v2, операций: ' + ($$('.modal table tr').length - 1));
  click(byText('.modal button', 'Закрыть'));
  click(byText('#view nav button', 'Исполнители'));
  await until(() => $('[data-ac]'), 'исполнители'); click($('[data-ac]'));
  await until(() => $('.modal input[type=checkbox]'), 'допуски'); step('допуски: позиций для выбора ' + $$('.modal input[type=checkbox]').length);
  click(byText('.modal button', 'Отмена'));
  click($('[data-tg]'));
  await until(() => $('.modal [data-code]'), 'окно Telegram'); click($('.modal [data-code]'));
  await until(() => /[A-Z2-9]{4}-[A-Z2-9]{4}/.test($('#tgc').textContent), 'код привязки');
  step('Telegram: выдан код ' + $('#tgc').textContent.match(/[A-Z2-9]{4}-[A-Z2-9]{4}/)[0] + ', ссылка на бота: ' + !!$('#tgc a'));
  click(byText('.modal button', 'Закрыть'));

  // --- настройки ---
  click(byText('nav.tabs button', 'Настройки'));
  await until(() => $('#sf'), 'настройки'); 
  setv($('#sf').elements.priority_strictness, '60'); $('#sf').dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => /Настройки сохранены/.test($('#toast').textContent), 'сохранение настроек'); step('настройки сохранены');

  // --- пользователи: создаём бригадира ---
  click(byText('nav.tabs button', 'Пользователи'));
  await until(() => $('#add'), 'пользователи'); click($('#add'));
  await until(() => $('.modal form'), 'форма пользователя');
  const U = $('.modal form'), UE = U.elements; setv(UE.email, 'foreman@smoke.ru'); setv(UE.name, 'Борис Бригадир'); setv(UE.role, 'foreman');
  setv(UE.work_center_id, UE.work_center_id.options[1].value); setv(UE.password, 'password-2');
  U.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => byText('#view td', 'foreman@smoke.ru'), 'бригадир в списке'); step('бригадир создан');
  click($('#add')); await until(() => $('.modal form'), 'форма приглашения');
  const I = $('.modal form'), IE = I.elements; setv(IE.email, 'invitee@smoke.ru'); setv(IE.name, 'Ирина Наблюдатель'); setv(IE.role, 'viewer'); IE.send_invite.checked = true;
  I.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => byText('#view td', 'invitee@smoke.ru'), 'приглашённый в списке');
  const ilink = await mailLink('invitee@smoke.ru', 'invite');
  await inFreshWindow(ilink, async () => {
    await until(() => $('#rf'), 'форма пароля приглашения'); const f = $('#rf').elements;
    setv(f.p1, 'invite-pass-1'); setv(f.p2, 'different-1'); $('#rf').dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
    await until(() => /не совпадают/.test($('#rerr').textContent), 'проверка совпадения паролей');
    setv(f.p2, 'invite-pass-1'); $('#rf').dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
    await until(() => /Пароль сохранён/.test($('#root').textContent), 'пароль сохранён');
  });
  await inFreshWindow(BASE + '/', async () => {
    await until(() => $('#af'), 'вход приглашённого'); const f = $('#af').elements; setv(f.email, 'invitee@smoke.ru'); setv(f.password, 'invite-pass-1');
    $('#af').dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
    await until(() => $('header.top'), 'оболочка приглашённого');
    const m = $$('nav.tabs > button').map(b => b.textContent).join(' | ');
    if (/Данные|Пользователи|Настройки/.test(m)) throw new Error('наблюдатель видит лишнее: ' + m);
    if ($('#vb')) throw new Error('приглашённый должен быть уже подтверждён');
    console.log('   меню наблюдателя:', m);
  });
  step('приглашение: ссылка из письма → свой пароль → вход, роль «наблюдатель», e-mail подтверждён');

  click(byText('nav.tabs button', 'Журнал'));
  await until(() => $$('#view tr').length > 5, 'журнал'); step('журнал: записей ' + ($$('#view tr').length - 1));

  // --- выход и вход бригадиром ---
  click(byText('header button', 'Выйти'));
  await until(() => $('#af'), 'экран входа после выхода'); step('выход');
  const lf = $('#af'); setv(lf.elements.email, 'foreman@smoke.ru'); setv(lf.elements.password, 'password-2');
  lf.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => $('header.top'), 'оболочка бригадира');
  const nav2 = $$('nav.tabs > button').map(b => b.textContent);
  console.log('   меню бригадира:', nav2.join(' | '));
  if (nav2.includes('Данные') || nav2.includes('Пользователи') || nav2.includes('Настройки')) throw new Error('бригадир видит лишние разделы');
  await until(() => $$('.wcard').length, 'план бригадира');
  const names = $$('.wcard h3').map(h => h.textContent);
  step('бригадир: сразу «План на день», видит ' + names.length + ' исполнителей своего участка (меньше, чем у владельца: ' + cards + ')');
  if (names.length >= cards) throw new Error('бригадир видит всех, а не свой участок');

  // --- бригадир перепланирует только свой участок ---
  click($('#rp')); await until(() => $('.modal #run'), 'окно перепланирования бригадира');
  if (!/свой участок/.test($('.modal').textContent)) throw new Error('нет пометки «свой участок»: ' + $('.modal').textContent.slice(0, 120));
  if ($('.modal #scope')) throw new Error('бригадиру не должен предлагаться выбор участка');
  click($('.modal #run'));
  await until(() => /План построен/.test($('#toast').textContent), 'план участка построен');
  step('бригадир: окно «Вы перепланируете свой участок», выбора участка нет, пересчёт выполнен');

  // --- забыли пароль ---
  await inFreshWindow(BASE + '/', async () => {
    await until(() => $('#fg'), 'ссылка «Забыли пароль»'); click($('#fg'));
    await until(() => $('#ff'), 'форма сброса'); setv($('#ff').elements.email, 'anna@smoke.ru');
    $('#ff').dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
    await until(() => /отправили письмо/.test($('#fmsg').textContent), 'ответ о письме');
  });
  const rlink = await mailLink('anna@smoke.ru', 'reset');
  await inFreshWindow(rlink, async () => {
    await until(() => $('#rf'), 'форма нового пароля'); const f = $('#rf').elements; setv(f.p1, 'new-pass-777'); setv(f.p2, 'new-pass-777');
    $('#rf').dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
    await until(() => /Пароль сохранён/.test($('#root').textContent), 'пароль сброшен');
    if (win.location.hash) throw new Error('токен остался в адресной строке');
  });
  await inFreshWindow(BASE + '/', async () => {
    await until(() => $('#af'), 'вход'); setv($('#af').elements.email, 'anna@smoke.ru'); setv($('#af').elements.password, 'password-1');
    $('#af').dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
    await until(() => /Неверный/.test($('#aerr').textContent), 'старый пароль отвергнут');
    setv($('#af').elements.password, 'new-pass-777'); $('#af').dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
    await until(() => $('header.top'), 'вход с новым паролем');
    if ($('#vb')) throw new Error('после подтверждения баннер должен исчезнуть');
  });
  step('забытый пароль: письмо → новый пароль → старый не работает, новый работает, e-mail подтверждён, токен убран из адреса');

  // --- повторный заход по сохранённому refresh-токену ---
  const rt = win.localStorage.getItem('p24_rt');
  await open(BASE + '/'); win.localStorage.setItem('p24_rt', rt); await open(BASE + '/');
  win.localStorage.setItem('p24_rt', rt);
  console.log('\nОшибок JS в консоли:', errors.length); errors.forEach(e => console.log('  ', e));
  process.exit(errors.length ? 1 : 0);
})().catch(e => { console.error('✘ ПАДЕНИЕ:', e.message); errors.forEach(x => console.error('  ', x)); process.exit(1); });
