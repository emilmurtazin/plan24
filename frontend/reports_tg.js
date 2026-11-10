/* «Организация → Отчёты в Telegram»: регулярные отчёты в группы (только администратор).
   Для каждого отчёта: группа, время по Москве, дни недели, метрики, участки (пусто — вся организация). Отчётов может быть несколько. */
const RT_WD = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const rtDays = d => d.length === 7 ? 'каждый день' : (d.join() === '0,1,2,3,4' ? 'по будням' : d.map(i => RT_WD[i]).join(', '));
const rtWhen = iso => iso ? new Date(iso).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) + ' МСК' : '';

SCREENS.tgreports = async v => {
  const d = await GET('/report-schedules');
  const secName = id => d.sections.find(s => s.id === id)?.name || 'участок удалён';
  const status = r => !r.enabled ? '<span class="badge b-none">выключен</span>'
    : r.last_status === 'error' ? `<span class="badge b-risk">⚠ ошибка</span><div class="muted" style="font-size:13px;max-width:260px">${esc(r.last_error)}</div>`
    : r.last_status === 'ok' ? `<span class="badge b-done">✔ отправлен</span><div class="muted" style="font-size:13px">${rtWhen(r.last_sent_at)}</div>`
    : '<span class="muted">ещё не отправлялся</span>';
  v.innerHTML = `<div class="row" style="margin-bottom:10px"><h2 class="grow" style="margin:0">Отчёты в Telegram</h2><button id="rt-add" ${d.items.length >= d.limit ? 'disabled' : ''}>+ Отчёт</button></div>
    <p class="hint">Сервис сам публикует отчёт в группу Telegram в выбранное время (московское). Выберите метрики и, если нужно, участки — по каждому участку отчёт будет отдельным блоком. Отчётов можно создать несколько: например, утренний по всей организации и вечерний по сборке.</p>
    ${d.bot.configured ? '' : '<div class="card err" role="alert">Telegram-бот не настроен администратором сервиса — отчёты не будут отправляться.</div>'}
    <div class="card flush scroll">${table([
    { label: 'Отчёт', html: r => `<b>${esc(r.name)}</b>` },
    { label: 'Группа', get: r => r.chat_ref },
    { label: 'Когда', html: r => `${esc(r.send_time)} МСК<div class="muted" style="font-size:13px">${esc(rtDays(r.weekdays))}</div>` },
    { label: 'Участки', get: r => r.section_ids.length ? r.section_ids.map(secName).join(', ') : 'вся организация' },
    { label: 'Метрики', html: r => `<span title="${esc(r.metrics.map(k => d.metrics.find(m => m.key === k)?.title || k).join(', '))}">${r.metrics.length} из ${d.metrics.length}</span>` },
    { label: 'Статус', html: status }], d.items,
    r => `<button class="small secondary" data-e="${r.id}">Изменить</button> <button class="small secondary" data-p="${r.id}">Предпросмотр</button> <button class="small" data-s="${r.id}">Отправить сейчас</button> <button class="small secondary" data-t="${r.id}">${r.enabled ? 'Выключить' : 'Включить'}</button> <button class="small danger" data-d="${r.id}">Удалить</button>`)}</div>`;
  const find = el => d.items.find(r => r.id == el.dataset.e || r.id == el.dataset.p || r.id == el.dataset.s || r.id == el.dataset.t || r.id == el.dataset.d);
  const reload = () => SCREENS.tgreports(v);
  $('#rt-add', v).onclick = () => rtForm(d, null, reload);
  $$('[data-e]', v).forEach(b => b.onclick = () => rtForm(d, find(b), reload));
  $$('[data-p]', v).forEach(b => b.onclick = async () => { try { rtShow((await POST(`/report-schedules/${find(b).id}/preview`)).messages); } catch (e) { toast(errText(e), true); } });
  $$('[data-s]', v).forEach(b => b.onclick = async () => {
    const r = find(b);
    if (!await confirmBox(`Отправить отчёт «${r.name}» в группу ${r.chat_ref} прямо сейчас?`)) return;
    try { await POST(`/report-schedules/${r.id}/send`); toast('Отчёт отправлен'); } catch (e) { toast(errText(e), true); }
    reload();
  });
  $$('[data-t]', v).forEach(b => b.onclick = async () => { const r = find(b); try { await PATCH(`/report-schedules/${r.id}`, { enabled: !r.enabled }); reload(); } catch (e) { toast(errText(e), true); } });
  $$('[data-d]', v).forEach(b => b.onclick = async () => { const r = find(b); if (!await confirmBox(`Удалить отчёт «${r.name}»?`)) return; try { await DEL(`/report-schedules/${r.id}`); toast('Отчёт удалён'); reload(); } catch (e) { toast(errText(e), true); } });
};

/* Текст отчёта показываем как есть (textContent — без разметки) */
function rtShow(messages) {
  const m = modal(`<h2>Так отчёт выглядит в Telegram</h2><div id="rt-pv"></div><div class="row"><button class="secondary" data-x>Закрыть</button></div>`, true);
  messages.forEach(t => { const p = document.createElement('pre'); p.className = 'card'; p.style.cssText = 'white-space:pre-wrap;margin:0 0 8px;font-family:inherit'; p.textContent = t; $('#rt-pv', m.el).appendChild(p); });
  $('[data-x]', m.el).onclick = m.close;
}

function rtForm(d, r, reload) {
  const val = r || { name: '', chat_ref: '', send_time: '08:30', weekdays: [0, 1, 2, 3, 4, 5, 6], metrics: d.default_metrics, section_ids: [], enabled: true };
  const chip = (name, value, label, on) => `<label class="chip"><input type="checkbox" name="${name}" value="${value}" ${on ? 'checked' : ''}><span>${esc(label)}</span></label>`;
  const bot = d.bot.username ? '@' + d.bot.username.replace(/^@/, '') : 'бота Plan 24';
  const m = modal(`<h2>${r ? esc(r.name) : 'Новый отчёт'}</h2><form novalidate>
    <label class="f">Название *<input name="name" value="${esc(val.name)}" maxlength="120" placeholder="Например: Утренняя сводка" required></label>
    <label class="f">Группа Telegram *<input name="chat_ref" value="${esc(val.chat_ref)}" placeholder="-1001234567890 или @имя_группы" required>
      <span style="font-size:13px">1) Добавьте ${esc(bot)} в группу. 2) Отправьте в группе команду /chatid — бот ответит номером группы. 3) Вставьте номер сюда. Для публичной группы можно указать @имя.</span></label>
    <label class="f">Время публикации (московское) *<input name="send_time" type="time" value="${esc(val.send_time)}" required style="max-width:160px"></label>
    <fieldset style="border:1px solid var(--line);border-radius:6px;margin-bottom:10px"><legend class="muted">Дни недели</legend>
      <div class="row">${RT_WD.map((l, i) => chip('wd', i, l, val.weekdays.includes(i))).join('')}</div>
      <div class="row" style="margin-top:6px"><button type="button" class="small secondary" data-wd="all">Каждый день</button><button type="button" class="small secondary" data-wd="work">Будни</button></div></fieldset>
    <fieldset style="border:1px solid var(--line);border-radius:6px;margin-bottom:10px"><legend class="muted">Участки</legend>
      ${d.sections.length ? `<div class="row">${d.sections.map(s => chip('sec', s.id, s.name, val.section_ids.includes(s.id))).join('')}</div>
      <div class="muted" style="font-size:13px;margin-top:6px">Ничего не отмечено — отчёт по всей организации. Если отмечено несколько участков, в отчёте будет отдельный блок по каждому.</div>` : '<div class="muted">Участков пока нет — отчёт будет по всей организации.</div>'}</fieldset>
    <fieldset style="border:1px solid var(--line);border-radius:6px;margin-bottom:10px"><legend class="muted">Что публиковать *</legend>
      ${d.metrics.map(x => `<label class="chk"><input type="checkbox" name="mt" value="${x.key}" ${val.metrics.includes(x.key) ? 'checked' : ''}><span>${esc(x.title)}<span class="muted" style="display:block;font-size:13px">${esc(x.hint)}</span></span></label>`).join('')}</fieldset>
    <label class="chk"><input type="checkbox" name="enabled" ${val.enabled ? 'checked' : ''}> <span>Отчёт включён</span></label>
    <div class="err" data-err role="alert"></div>
    <div class="row"><button type="submit">Сохранить</button><button type="button" class="secondary" data-pv>Предпросмотр</button><button type="button" class="secondary" data-x>Отмена</button></div></form>`, true);
  const form = $('form', m.el), checked = n => $$(`[name=${n}]:checked`, form).map(i => +i.value || i.value);
  const body = () => ({ name: form.elements.name.value, chat_ref: form.elements.chat_ref.value, send_time: form.elements.send_time.value,
    weekdays: $$('[name=wd]:checked', form).map(i => +i.value), section_ids: $$('[name=sec]:checked', form).map(i => +i.value),
    metrics: $$('[name=mt]:checked', form).map(i => i.value), enabled: form.elements.enabled.checked });
  $('[data-x]', m.el).onclick = m.close;
  $$('[data-wd]', m.el).forEach(b => b.onclick = () => $$('[name=wd]', form).forEach(i => { i.checked = b.dataset.wd === 'all' || +i.value < 5; }));
  $('[data-pv]', m.el).onclick = async () => {
    const er = $('[data-err]', m.el); er.textContent = '';
    try { rtShow((await POST('/report-schedules/preview', body())).messages); } catch (e) { er.textContent = errText(e); }
  };
  form.onsubmit = async e => {
    e.preventDefault(); const er = $('[data-err]', m.el); er.textContent = '';
    const btn = $('button[type=submit]', m.el); btn.disabled = true;
    try { if (r) await PATCH(`/report-schedules/${r.id}`, body()); else await POST('/report-schedules', body()); m.close(); toast('Отчёт сохранён'); reload(); }
    catch (x) { er.textContent = errText(x); btn.disabled = false; }
  };
}
