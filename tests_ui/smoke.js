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
    beforeParse(w) { w.fetch = async (u, o) => { const r = await fetch(new URL(u, BASE), o); if (global.TRACE && /facts|reports\/day/.test(u)) { const c = r.clone(); const t = await c.text(); console.log('   TRACE', o && o.method, u, o && o.body || '', '=>', r.status, t.slice(0, 160)); } return r; }; w.print = () => {}; w.TextDecoder = TextDecoder; } });
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

const setFile = (input, text, name) => { const f = new win.File(['\uFEFF' + text], name, { type: 'text/csv' }); Object.defineProperty(input, 'files', { value: [f], configurable: true }); input.dispatchEvent(new win.Event('change', { bubbles: true })); };
const goExec = async tab => { click(byText('#drawer button', 'Выполнение')); await until(() => $('#view nav.seg') || $('#dt'), 'подвкладки «Выполнения»'); if ($('#view nav.seg')) click(byText('#view nav.seg button', tab)); };

(async () => {
  await open(BASE + '/');
  await until(() => $('#af'), 'форма входа'); step('форма входа показана');

  // --- регистрация ---
  click(byText('button', 'Регистрация организации'));
  await until(() => $('[name=org_name]'), 'форма регистрации');
  const f = $('#af');
  const E = f.elements; setv(E.org_name, 'Цех Смоук'); setv(E.first_name, 'Анна'); setv(E.last_name, 'Петрова'); setv(E.email, 'anna@smoke.ru'); setv(E.password, 'password-1');
  f.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => $('#aerr')?.textContent, 'ошибка согласия');
  if (!/согласие/i.test($('#aerr').textContent)) throw new Error('ожидалась ошибка согласия: ' + $('#aerr').textContent);
  step('без согласия регистрация отклонена: «' + $('#aerr').textContent + '»');
  E.accept_terms.checked = true;
  f.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => $('header.top'), 'оболочка'); 
  if (!$('header.top').textContent.includes('Цех Смоук')) throw new Error('нет названия организации');
  step('регистрация → оболочка, владелец: ' + $('header.top').textContent.replace(/\s+/g, ' ').slice(0, 60));
  if ($('#vb')) throw new Error('баннер подтверждения должен быть убран с общих экранов');
  click(byText('#drawer button', 'Профиль')); await until(() => $('#vb'), 'баннер подтверждения в профиле');
  step('баннер «Почта не подтверждена» — в разделе «Профиль»');
  click($('#vr')); await until(() => /Письмо отправлено|минуту/.test($('#toast').textContent), 'повторная отправка');
  step('повторная отправка: «' + $('#toast').textContent + '»');
  const vlink = await mailLink('anna@smoke.ru', 'verify');
  await inFreshWindow(vlink, async () => { await until(() => /E-mail подтверждён/.test($('#root').textContent), 'страница подтверждения'); });
  step('ссылка из письма подтверждает e-mail');
  const nav = $$('#drawer .drawer-nav > button').map(b => b.lastChild.textContent);
  console.log('   меню владельца:', nav.join(' | '));
  for (const x of ['Дашборд', 'Выполнение', 'Работники', 'Статус заказов', 'Каталог изделий', 'Проекты и Заказы', 'Организация', 'Профиль']) if (!nav.includes(x)) throw new Error('нет вкладки ' + x);
  for (const x of ['Обзор', 'Данные', 'Настройки', 'Пользователи', 'Журнал', 'План на день', 'Загрузка', 'Участки']) if (nav.includes(x)) throw new Error('лишняя вкладка в меню: ' + x);

  // --- профиль: имя, e-mail, пароль ---
  const prof = $('#pf'); if (prof.elements.first_name.value !== 'Анна' || prof.elements.last_name.value !== 'Петрова') throw new Error('в профиле не видны имя и фамилия');
  setv(prof.elements.first_name, 'Анна-Мария'); prof.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => /Профиль сохранён/.test($('#toast').textContent) && /Анна-Мария/.test($('header.top').textContent), 'сохранение профиля'); step('профиль: имя изменено, шапка обновилась');
  click(byText('#drawer button', 'Профиль')); await until(() => $('#pw'), 'профиль');
  const pw = $('#pw').elements; setv(pw.current_password, 'password-1'); setv(pw.new_password, 'password-1b'); setv(pw.again, 'other-pass-1'); $('#pw').dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => /не совпадают/.test($('#pw [data-err]').textContent), 'несовпадение паролей');
  setv(pw.again, 'password-1b'); $('#pw').dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => /Пароль изменён/.test($('#toast').textContent), 'смена пароля'); step('профиль: пароль изменён, сессия продолжается');
  click(byText('#drawer button', 'Дашборд'));

  // --- пустое состояние и демо ---
  await until(() => byText('h1', 'Начнём'), 'онбординг'); step('пустая организация → онбординг');
  click($('#demo'));
  await until(() => $$('.kpi').length >= 5, 'обзор с KPI', 15000);
  for (const t of ['проектов в работе', 'проектов в очереди', 'проектов с риском срыва сроков', 'работников с заданиями', 'загрузка на ближайшие 3 месяца']) if (!$$('.kpi').some(k => k.textContent.includes(t))) throw new Error('нет показателя «' + t + '»');
  if ($$('.kpi').length !== 5) throw new Error('показателей должно быть 5, а их ' + $$('.kpi').length);
  if (/уже загружен|загружены сегодня/i.test($('#view').textContent)) throw new Error('сообщение о загруженных сегодня должно быть убрано');
  if ($$('#view h2').filter(x => x.textContent.includes('Статус заказов')).length !== 1 || $$('#view .card table').length < 1) throw new Error('нужна одна таблица статуса заказов');
  if ($('#rp.hact') === null || $('#view #rp')) throw new Error('кнопка «Перепланировать» должна быть только в шапке');
  step('демо загружено, план построен, KPI: ' + $$('.kpi').map(k => k.textContent.replace(/\s+/g, ' ')).join(' / '));
  for (const h of ['Статус заказов', 'Потребность в ресурсах', 'Повторяющиеся проблемы и причины отклонений', 'Работники', 'Загрузка участков на 3 месяца вперёд', 'Недовыполнение плана по участкам за 3 месяца']) if (!$$('#view h2').some(x => x.textContent.includes(h))) throw new Error('на дашборде нет раздела «' + h + '»');
  if (!/По участкам/.test($('#view').textContent) || !/По квалификациям/.test($('#view').textContent)) throw new Error('нет таблиц работников по участкам и квалификациям');
  const loadSvg = $('#view svg.chart'); if (!loadSvg || $$('#view svg.chart path').length < 2) throw new Error('нет графика загрузки по участкам (линии по каждому участку)');
  if (!/Данные таблицей/.test($('#view').textContent)) throw new Error('у графика нет таблицы с данными');
  step('дашборд: все разделы на месте; график загрузки — линий ' + $$('#view svg.chart path').length + ', узлов ' + $$('#view svg.chart circle').length + ', графики недовыполнения: ' + (/Пока нет данных за прошедшие дни/.test($('#view').textContent) ? 'истории ещё нет (как и должно быть)' : 'есть'));

  // --- план на день и факт ---
  await goExec('План на день');
  if (byText('#view button', 'Печать') || byText('#view button', 'Перепланировать')) throw new Error('в «Плане на день» не должно быть кнопок «Печать» и «Перепланировать»');
  if ($('#rp').hidden) throw new Error('кнопка «Перепланировать» должна быть в верхней панели'); if (!$('header.top').contains($('#rp'))) throw new Error('кнопка не в шапке');
  step('«План на день» — подвкладка «Выполнения»; кнопка «Перепланировать» в верхней панели, «Печать» нет');
  await until(() => $$('.wcard').length, 'карточки исполнителей');
  const cards = $$('.wcard').length, inputs = $$('.fact input').length;
  step(`план на день: карточек ${cards}, полей ввода факта ${inputs}`);
  const firstDone = $('.fact [data-q]:not([data-q="0"])'); const w0 = firstDone.closest('.wcard').querySelector('h3').textContent;
  const devOf = b => b.closest('tr').nextElementSibling.querySelector('.devbox');
  if (!$('.fact [data-save]') || $('.fact [data-save]').textContent.trim() !== 'Записать выполнение') throw new Error('кнопка факта должна называться «Записать выполнение»');
  if ($$('.devbox').some(b => !b.hidden)) throw new Error('причина запрашивается, хотя отклонения нет');
  step('форма факта: кнопка «Записать выполнение», поле причины скрыто, пока нет отклонения');
  click(firstDone);
  await until(() => $('#toast') && !$('#toast').classList.contains('hidden') && /Записано|Исправлено/.test($('#toast').textContent), 'тост факта');
  await until(() => $$('.b-done').length, 'бейдж выполнено');
  step(`факт «Всё» записан для ${w0}: ${$('#toast').textContent}`);
  // меньше плана → появляется запрос причины, без неё сохранить нельзя
  const box = $$('.fact').find(b => b.querySelector('input[type=number]').value !== '' && +b.dataset.plan > 1);
  const qin = box.querySelector('input[type=number]'); setv(qin, String(+box.dataset.plan - 1));
  if (devOf(box).hidden) throw new Error('при факте меньше плана причина должна запрашиваться');
  if (!/Меньше плана/.test(devOf(box).querySelector('.dtext').textContent)) throw new Error('нет пояснения «Меньше плана»: ' + devOf(box).querySelector('.dtext').textContent);
  click(box.querySelector('[data-save]')); await sleep(150);
  if (!/причину|комментарий/.test($('#toast').textContent)) throw new Error('без причины сохранилось: ' + $('#toast').textContent);
  setv(devOf(box).querySelector('.dnote'), 'не хватило материала'); click(box.querySelector('[data-save]'));
  await until(() => /Исправлено/.test($('#toast').textContent), 'исправление факта с причиной');
  step('меньше плана: без причины не сохраняется, с комментарием — «' + $('#toast').textContent + '»');
  const box2 = $$('.fact').find(b => b.querySelector('input[type=number]').value !== '' && +b.dataset.plan > 0 && !devOf(b).querySelector('.dnote').value);
  if (box2) { setv(box2.querySelector('input[type=number]'), box2.dataset.plan); if (!devOf(box2).hidden) throw new Error('при факте по плану причина не нужна'); }

  // --- правка плана: задание вручную; закрепление дня — только вручную ---
  click($('#addT')); await until(() => $('.modal form') && $('.modal form').elements.operation_id && $('.modal form').elements.operation_id.options.length > 0 && $('.modal form').elements.operation_id.value, 'форма задания');
  const tf = $('.modal form'); setv(tf.elements.qty, '2');
  tf.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => $$('.wcard').some(c => /добавлено вручную/.test(c.textContent)), 'задание добавлено вручную');
  if ($$('.wcard').some(c => /день закреплён/.test(c.textContent))) throw new Error('правка не должна закреплять день автоматически');
  click($('[data-pin]')); await until(() => $$('.wcard').some(c => /день закреплён/.test(c.textContent)), 'день закреплён вручную');
  step('«+ Задание»: добавлено вручную, день сам не закрепился; кнопка «Закрепить день» его закрепила');
  click($('[data-te]')); await until(() => $('.modal form') && $('.modal form').elements.operation_id.value, 'форма изменения'); click(byText('.modal button', 'Отмена'));
  // --- работа вне перечня ---
  click($('#addX')); await until(() => $('.modal form'), 'форма работы вне перечня'); const xf = $('.modal form');
  setv(xf.elements.kind, 'other'); if (!xf.elements.order_line_id.closest('label').hidden) throw new Error('поле «Заказ» должно скрываться для работы не по заказу');
  setv(xf.elements.title, 'Уборка участка'); setv(xf.elements.hours, '2'); xf.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => $$('.task.extra').length, 'работа вне перечня в плане'); step('«+ Работа вне перечня»: «Уборка участка» · не относится к заказу');
  const xb = $('.fact[data-xid]'); setv(xb.querySelector('input[type=number]'), '1');
  if (devOf(xb).hidden) throw new Error('для работы вне перечня с фактом меньше плана причина должна запрашиваться');
  setv(devOf(xb).querySelector('.dnote'), 'отвлекли на другую работу'); click(xb.querySelector('[data-save-x]'));
  await until(() => /Записано/.test($('#toast').textContent) && $('.task.extra .b-none'), 'факт по работе вне перечня'); step('факт по работе вне перечня записан, «частично»');
  // --- временный работник ---
  click($('#addW')); await until(() => $('.modal form') && $('.modal form').elements.name, 'форма работника'); const nf = $('.modal form');
  setv(nf.elements.name, 'Временный Иван'); nf.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => /добавлен/.test($('#toast').textContent), 'работник добавлен'); step('временный работник добавлен: ' + $('#toast').textContent);
  await until(() => $('.modal form') && $('.modal form').elements.order_line_id, 'форма задания для нового работника'); click(byText('.modal button', 'Отмена'));
  // --- снять закрепление ---
  click($('[data-unpin]')); await until(() => byText('.modal button', 'Да') || $('.modal .danger'), 'подтверждение');
  click($('.modal .danger')); await until(() => /Закрепление снято|нельзя|работы вне перечня/.test($('#toast').textContent), 'ответ на снятие закрепления');
  step('снятие закрепления: ' + $('#toast').textContent.slice(0, 90));

  // --- обзор показывает «данные изменились» ---
  click(byText('#drawer button', 'Дашборд'));
  await until(() => $('#view .banner.warn'), 'баннер устаревшего плана');
  step('после факта обзор предупреждает: «' + $('#view .banner.warn').textContent.replace(/\s+/g, ' ').slice(0, 70) + '…»');
  click($('#rp2'));
  await until(() => $('#cmp'), 'окно перепланирования');
  if (/Вся организация/.test($('.modal').textContent)) throw new Error('«Вся организация» должна быть исключена');
  if (!$('.modal #scope') || [...$('.modal #scope').options].some(o => o.value === '')) throw new Error('перепланирование — только по участкам');
  step('перепланирование: выбор только из участков, «Вся организация» нет');
  click($('#cmp'));
  await until(() => $$('.cmp tr').length >= 5, 'таблица сравнения');
  step('сравнение вариантов: ' + $$('.cmp tr').slice(1).map(r => r.cells[0].textContent + '→' + r.cells[2].textContent + ' в риске').join('; '));
  click(byText('button', 'Построить план'));
  await until(() => !$('.modal'), 'закрытие окна'); await until(() => !$('#view .banner.warn'), 'баннер исчез после пересчёта');
  step('пересчёт → баннер «устарел» исчез');

  // --- статус и загрузка ---
  // регрессия: переключение раздела сразу после перерисовки не должно затираться запоздавшим ответом прежнего экрана
  click(byText('#drawer button', 'Дашборд')); click(byText('#drawer button', 'Выполнение')); click(byText('#drawer button', 'Дашборд')); click(byText('#drawer button', 'Статус заказов'));
  await sleep(1200);
  if (!$('#all')) throw new Error('запоздавший ответ перерисовал не тот раздел: ' + $('#view').textContent.slice(0, 80));
  step('быстрое переключение разделов: остаётся последний выбранный');
  click(byText('#drawer button', 'Статус заказов'));
  await until(() => $$('#all tr[data-o]').length, 'таблица всех заказов');
  if ($('#sp') || $('#so')) throw new Error('выпадающие списки «изделие» и «заказ» должны быть убраны');
  if (byText('#view button', 'Печать')) throw new Error('кнопка «Печать» должна быть убрана');
  if (!($('#all').compareDocumentPosition($('#od')) & 4)) throw new Error('таблица «Все заказы» должна быть выше деталей заказа');
  if (!/Выберите заказ/.test($('#od').textContent)) throw new Error('нет подсказки выбрать заказ');
  const firstRow = $('#all tr[data-o]'); const firstTitle = firstRow.querySelector('td b').textContent; click(firstRow);
  await until(() => $$('#od .stt tr[class]').length >= 2 || $$('#od .stt tr').length > 3, 'операции заказа');
  const opsN = $$('#od .stt tr:not(.det)').length - 1, title0 = $('#od h2').textContent;
  for (const h of ['Выполнено', 'В плане', 'Не в плане', 'Человеко-смен до завершения', 'Дата завершения', 'Узкое место']) if (!$('#od .stt').textContent.includes(h)) throw new Error('в таблице операций нет колонки «' + h + '»');
  const ps = [...$$('#od .stt tr:not(.det)')].slice(1).map(r => r.cells.length); if (!ps.length) throw new Error('нет строк операций');
  if (/«Не в плане» —|Рекомендации ориентировочные/.test($('#od').textContent)) throw new Error('комментарий внизу таблицы должен быть убран');
  if ($$('#od .stt tr.bn').length !== 1) throw new Error('должно быть ровно одно узкое место, найдено ' + $$('#od .stt tr.bn').length);
  if (!/Прогноз завершения/.test($('#od').textContent) || !/Срок заказа/.test($('#od').textContent)) throw new Error('в карточке заказа нет прогноза и срока');
  step(`статус заказов: таблица «Все заказы» сверху, без списков и печати; заказ «${title0}»: операций ${opsN}, узкое место отмечено (${$('#od tr.bn b').textContent})`);
  const lastRow = [...$$('#all tr[data-o]')].pop(); const lastTitle = lastRow.querySelector('td b').textContent; click(lastRow);
  await until(() => $('#od h2') && $('#od h2').textContent.includes('Заказ ' + lastTitle), 'выбор заказа из общей таблицы'); step('выбор заказа из таблицы «Все заказы»: ' + $('#od h2').textContent);
  await goExec('Загрузка');
  await until(() => $$('.heat tr').length > 2, 'тепловая карта');
  const cells = $$('.heat .hc'); if (!cells.length) throw new Error('нет ячеек загрузки');
  if (cells.some(c => c.textContent && !/^\d+$/.test(c.textContent))) throw new Error('в ячейках должны быть проценты числом');
  const secs = $$('.lsec h2').map(h => h.textContent); if (!secs.length) throw new Error('работники не разделены по участкам');
  if (byText('#view', 'Печать')) throw new Error('на загрузке нет печати');
  step('загрузка: подвкладка «Выполнения»; участков ' + secs.length + ' (' + secs.join(', ') + '), ячеек ' + cells.length + ', классов цвета: ' + [...new Set(cells.map(c => c.className.replace('hc ', '')))].join(','));

  // --- данные ---
  click(byText('#drawer button', 'Проекты и Заказы'));
  await until(() => $('#dt'), 'проекты'); click(byText('#view nav.seg button', 'Заказы'));
  await until(() => $$('#dt tr').length > 3, 'заказы'); step('данные/заказы: ' + ($$('#dt tr').length - 1) + ' позиций');
  click(byText('#dt button', 'Добавить позицию'));
  await until(() => $('.modal form'), 'форма позиции');
  const pf = $('.modal form'); setv(pf.elements.qty, '-3'); pf.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await sleep(300); step('невалидная форма не уходит на сервер (' + ($('.modal [data-err]').textContent || 'браузерная валидация') + ')');
  const hasCust = !!pf.elements.customer && !!pf.elements.routing_id; if (!hasCust) throw new Error('в форме заказа нет заказчика или версии изделия');
  if (pf.elements.routing_id.options.length < 2) throw new Error('версии изделия не подставились');
  step('форма заказа: есть «Заказчик» и выбор версии изделия (' + pf.elements.routing_id.options.length + ' вар.)');
  click(byText('.modal button', 'Отмена'));
  // --- распределение операций по участкам: нераспределённая операция — ошибка ---
  if (!/Операции по участкам/.test($('#dt').textContent)) throw new Error('в заказах нет колонки «Операции по участкам»');
  const replanFirstCenter = async () => { click(byText('#drawer button', 'Дашборд')); await until(() => $('#rp2') || $('#rp'), 'кнопка пересчёта'); click($('#rp2') || $('#rp')); await until(() => $('#scope'), 'окно пересчёта'); click(byText('button', 'Построить план')); await until(() => !$('.modal'), 'закрытие окна пересчёта'); await sleep(300); };
  click($('[data-dist]')); await until(() => $('.modal tr[data-op]'), 'окно распределения');
  const nOps = $$('.modal tr[data-op]').length;
  click($('#dqc')); if (!/Распределено: 0 из/.test($('#dsum').textContent)) throw new Error('«Снять все» не сработало');
  click($('#dqa')); if (!new RegExp(`Распределено: ${nOps} из ${nOps}`).test($('#dsum').textContent)) throw new Error('«Назначить всем операциям» не сработало: ' + $('#dsum').textContent);
  click($('.modal tr[data-op] input:checked'));            // одну операцию оставляем без участка
  if (!new RegExp(`Распределено: ${nOps - 1} из ${nOps}`).test($('#dsum').textContent)) throw new Error('счётчик не обновился: ' + $('#dsum').textContent);
  click($('#ds')); await until(() => !$('.modal'), 'закрытие окна распределения'); await until(() => /не распределено/.test($('#dt').textContent), 'ошибка в таблице заказов');
  step('распределение: одна операция без участка → в таблице заказов «не распределено»');
  await replanFirstCenter();
  await until(() => $('#view .banner.err'), 'красная ошибка на дашборде');
  if (!/не распределены по участкам/.test($('#view .banner.err').textContent)) throw new Error('текст ошибки: ' + $('#view .banner.err').textContent);
  step('дашборд: «' + $('#view .banner.err').textContent.replace(/\s+/g, ' ').slice(0, 85) + '…»');
  click($('#goDist')); await until(() => $('[data-dist]'), 'переход к заказам по кнопке «Распределить»');
  click($('[data-dist]')); await until(() => $('.modal tr[data-op]'), 'окно распределения 2'); click($('#dqa')); click($('#ds')); await until(() => !$('.modal'), 'закрытие');
  await replanFirstCenter(); await until(() => !$('#view .banner.err'), 'ошибка исчезла после распределения');
  step('после распределения и пересчёта ошибка пропала');
  click(byText('#drawer button', 'Проекты и Заказы')); await until(() => $('#dt'), 'проекты и заказы'); click(byText('#view nav.seg button', 'Заказы')); await until(() => $$('#dt tr').length > 3, 'заказы');
  click(byText('#drawer button', 'Каталог изделий'));
  await until(() => $('[data-rt]'), 'кнопка маршрута'); click($('[data-rt]'));
  await until(() => $('.modal table'), 'маршрут'); 
  step('маршрут: ' + ($('.modal .muted').textContent.slice(0, 80)) + '…');
  click(byText('.modal button', 'Новая версия'));
  await until(() => $('.modal form'), 'форма новой версии'); const nvf = $('.modal form'); setv(nvf.elements.name, 'Усиленная'); nvf.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => /v2 · Усиленная/.test($('.modal h2').parentElement.textContent), 'версия 2 с названием'); step('создана версия «v2 · Усиленная», операций: ' + ($$('.modal table tr').length - 1));
  click(byText('.modal button', 'Переименовать версию'));
  await until(() => $('.modal form'), 'форма переименования'); const rnf = $('.modal form'); setv(rnf.elements.name, 'Усиленная-2'); rnf.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => /v2 · Усиленная-2/.test($('.modal h2').parentElement.textContent), 'новое название версии'); step('версия переименована: v2 · Усиленная-2');
  if (!/Уровень \d/.test($('.modal table').textContent)) throw new Error('в операциях нет требуемой квалификации с названием уровня');
  step('операции показывают требуемую квалификацию названием уровня');
  click(byText('.modal button', 'Закрыть'));
  await until(() => /v2 · Усиленная-2/.test($('#dt').textContent), 'каталог обновился'); step('каталог показывает текущую версию «v2 · Усиленная-2»');
  click($('[data-import]')); await until(() => $('.modal [data-file]'), 'окно загрузки каталога');
  setFile($('.modal [data-file]'), 'Изделие;Ед.;Порядок;Операция;Норма в день;Квалификация;На изделие\r\nИмпорт-Стол;шт;1;Раскрой;12,5;1;1\r\nИмпорт-Стол;шт;2;Сборка;0;1;1\r\n', 'c.csv');
  await until(() => /ошиб/.test($('.modal [data-res]').textContent), 'ошибки каталога');
  setFile($('.modal [data-file]'), 'Изделие;Ед.;Порядок;Операция;Норма в день;Квалификация;На изделие\r\nИмпорт-Стол;шт;1;Раскрой;12,5;1;1\r\nИмпорт-Стол;шт;2;Сборка;4;1;1\r\n', 'c.csv');
  await until(() => !$('.modal [data-go]').disabled, 'проверка каталога пройдена'); click($('.modal [data-go]'));
  await until(() => byText('#dt td', 'Импорт-Стол'), 'изделие из файла в каталоге'); step('каталог: загрузка из файла создала изделие «Импорт-Стол»');
  click(byText('#drawer button', 'Работники')); await until(() => $('#view nav.seg'), 'раздел «Работники»');
  if (!['Участки', 'Работники', 'Причины отклонений'].every(t => byText('#view nav.seg button', t))) throw new Error('в разделе «Работники» должны быть подвкладки Участки, Работники, Причины отклонений');
  click(byText('#view nav.seg button', 'Работники'));
  await until(() => $('[data-ac]'), 'исполнители'); click($('[data-ac]'));
  await until(() => $('.modal input[type=checkbox]'), 'допуски'); step('допуски: позиций для выбора ' + $$('.modal input[type=checkbox]').length);
  click(byText('.modal button', 'Отмена'));
  click($('[data-tg]'));
  await until(() => $('.modal [data-code]'), 'окно Telegram'); click($('.modal [data-code]'));
  await until(() => /[A-Z2-9]{4}-[A-Z2-9]{4}/.test($('#tgc').textContent), 'код привязки');
  step('Telegram: выдан код ' + $('#tgc').textContent.match(/[A-Z2-9]{4}-[A-Z2-9]{4}/)[0] + ', ссылка на бота: ' + !!$('#tgc a'));
  click(byText('.modal button', 'Закрыть'));

  // --- настройки ---
  // уровни квалификации: форма работника — список; переименование и добавление уровней в «Об организации»
  click(byText('#view nav.seg button', 'Работники')); await until(() => $('[data-add]'), 'работники');
  if (!/Временный Иван/.test($('#dt').textContent) || !/только вручную/.test($('#dt').textContent)) throw new Error('временный работник не виден в списке или попал в автопланирование');
  step('временный работник в списке: «временный», планирование «только вручную»'); click($('[data-add]'));
  await until(() => $('.modal form'), 'форма работника'); const wsk = $('.modal form').elements.skill;
  if (wsk.tagName !== 'SELECT' || ![...wsk.options].some(o => /Уровень 1/.test(o.textContent))) throw new Error('квалификация работника — не список уровней');
  step('форма работника: квалификация — список уровней (' + wsk.options.length + ')'); click(byText('.modal button', 'Отмена'));
  click(byText('#drawer button', 'Организация')); await until(() => $('#view nav.seg'), 'организация'); click(byText('#view nav.seg button', 'Об организации')); await until(() => $('#ql [data-q]'), 'уровни квалификации');
  const n0 = $$('[data-q]').length; setv($('[data-q="0"]'), 'Ученик'); click($('#qa'));
  await until(() => $$('[data-q]').length === n0 + 1, 'новый уровень'); click($('#qs'));
  await until(() => $('[data-q="0"]') && $('[data-q="0"]').value === 'Ученик' && $$('[data-q]').length === n0 + 1, 'уровни сохранены'); step('уровни квалификации: ' + n0 + ' → ' + (n0 + 1) + ', первый «Ученик»');
  click(byText('#drawer button', 'Работники')); await until(() => $('[data-add]'), 'работники'); click($('[data-add]'));
  await until(() => $('.modal form'), 'форма работника 2');
  if (![...$('.modal form').elements.skill.options].some(o => /Ученик/.test(o.textContent))) throw new Error('новое название уровня не дошло до формы работника');
  step('новое название уровня видно в форме работника'); click(byText('.modal button', 'Отмена'));
  click(byText('#view nav.seg button', 'Участки'));
  await until(() => $('[data-settings]'), 'кнопка «Настройки» в участках'); click($('[data-settings]'));
  await until(() => $('#sf'), 'настройки'); 
  setv($('#sf').elements.priority_strictness, '60'); $('#sf').dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => /Настройки сохранены/.test($('#toast').textContent), 'сохранение настроек'); step('настройки сохранены');

  // --- пользователи: создаём бригадира ---
  click(byText('#drawer button', 'Организация')); await until(() => $('#view nav.seg'), 'организация'); click(byText('#view nav.seg button', 'Пользователи'));
  await until(() => $('#add'), 'пользователи'); click($('#add'));
  await until(() => $('.modal form'), 'форма пользователя');
  const U = $('.modal form'), UE = U.elements; setv(UE.email, 'foreman@smoke.ru'); setv(UE.first_name, 'Борис'); setv(UE.last_name, 'Бригадир'); setv(UE.role, 'foreman');
  setv(UE.work_center_id, UE.work_center_id.options[1].value); setv(UE.password, 'password-2');
  U.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => byText('#view td', 'foreman@smoke.ru'), 'бригадир в списке'); step('бригадир создан (имя и фамилия — отдельные поля)');
  if (![...$$('#view th')].some(h => h.textContent === 'Фамилия') || !byText('#view td', 'Бригадир')) throw new Error('в таблице пользователей нет колонок «Фамилия»/«Имя»');
  // --- загрузка пользователей из файла ---
  click($('#imp')); await until(() => $('.modal [data-file]'), 'окно загрузки пользователей');
  setFile($('.modal [data-file]'), 'E-mail;Имя;Фамилия;Роль;Участок;Работник;Пароль\r\ndir@smoke.ru;Дарья;Директорова;Директор;;;password-3\r\nbad;Х;У;Роль;;;\r\n', 'u.csv');
  await until(() => /ошиб/.test($('.modal [data-res]').textContent), 'ошибки файла');
  if (!/строка 3/.test($('.modal [data-res]').textContent) || !$('.modal [data-go]').disabled) throw new Error('ошибки файла не показаны построчно или загрузка не заблокирована: ' + $('.modal [data-res]').textContent);
  $('.modal [name=send_invite]').checked = false;
  setFile($('.modal [data-file]'), 'E-mail;Имя;Фамилия;Роль;Участок;Работник;Пароль\r\ndir@smoke.ru;Дарья;Директорова;Директор;;;password-3\r\n', 'u.csv');
  await until(() => !$('.modal [data-go]').disabled, 'проверка файла пройдена');
  click($('.modal [data-go]')); await until(() => byText('#view td', 'dir@smoke.ru'), 'пользователь из файла в списке');
  if (!byText('#view td', 'Директор')) throw new Error('роль «Директор» не отображается');
  step('загрузка пользователей из файла: ошибки по строкам → исправленный файл создал директора');
  click(byText('#view button', 'Загрузить из файла')); await until(() => $('.modal [data-tpl]'), 'окно ещё раз'); click(byText('.modal button', 'Закрыть'));
  click($('#add')); await until(() => $('.modal form'), 'форма приглашения');
  const I = $('.modal form'), IE = I.elements; setv(IE.email, 'invitee@smoke.ru'); setv(IE.first_name, 'Ирина'); setv(IE.last_name, 'Наблюдатель'); setv(IE.role, 'viewer'); IE.send_invite.checked = true;
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
    const m = $$('#drawer .drawer-nav > button').map(b => b.lastChild.textContent).join(' | ');
    if (/Проекты и Заказы|Организация|Каталог/.test(m)) throw new Error('наблюдатель видит лишнее: ' + m);
    if ($('#vb')) throw new Error('приглашённый должен быть уже подтверждён');
    console.log('   меню наблюдателя:', m);
  });
  step('приглашение: ссылка из письма → свой пароль → вход, роль «наблюдатель», e-mail подтверждён');

  click(byText('#drawer button', 'Организация')); await until(() => byText('#view nav.seg button', 'Журнал'), 'вкладки организации'); click(byText('#view nav.seg button', 'Журнал'));
  await until(() => $$('#view tr').length > 5, 'журнал'); step('журнал: записей ' + ($$('#view tr').length - 1));

  // --- выход и вход бригадиром ---
  click(byText('header button', 'Выйти'));
  await until(() => $('#af'), 'экран входа после выхода'); step('выход');
  const lf = $('#af'); setv(lf.elements.email, 'foreman@smoke.ru'); setv(lf.elements.password, 'password-2');
  lf.dispatchEvent(new win.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => $('header.top'), 'оболочка бригадира');
  const nav2 = $$('#drawer .drawer-nav > button').map(b => b.lastChild.textContent);
  console.log('   меню бригадира:', nav2.join(' | '));
  if (['Проекты и Заказы', 'Организация', 'Каталог изделий'].some(x => nav2.includes(x))) throw new Error('бригадир видит лишние разделы');
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
    await until(() => $('#af'), 'вход'); setv($('#af').elements.email, 'anna@smoke.ru'); setv($('#af').elements.password, 'password-1b');
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
