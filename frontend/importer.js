/* Загрузка из файла (пользователи, каталог изделий). Формат — CSV/TSV/TXT: в Excel «Сохранить как → CSV UTF-8».
   Файл разбирается в браузере, на сервер уходят уже разобранные строки; сначала идёт проверка без сохранения,
   сохранение — только если в файле нет ошибок (всё или ничего). */
'use strict';

/* разбор текста с разделителями (; , или табуляция), кавычки "…" и "" внутри */
function parseDelimited(text) {
  text = text.replace(/^\uFEFF/, '');
  const first = text.split(/\r?\n/).find(l => l.trim()) || '';
  const cnt = ch => first.split(ch).length - 1;
  const delim = [';', '\t', ','].sort((a, b) => cnt(b) - cnt(a))[0];
  const rows = []; let row = [], cell = '', q = false, line = 1, start = 1;
  const endRow = () => { row.push(cell); cell = ''; if (row.some(c => c.trim() !== '')) rows.push({ cells: row.map(c => c.trim()), line: start }); row = []; start = line + 1; };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) { if (c === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; } else { if (c === '\n') line++; cell += c; } }
    else if (c === '"') q = true;
    else if (c === delim) { row.push(cell); cell = ''; }
    else if (c === '\n') { endRow(); line++; start = line; }
    else if (c !== '\r') cell += c;
  }
  if (cell !== '' || row.length) endRow();
  return rows;
}
async function readTextFile(file) {
  const buf = file.arrayBuffer ? await file.arrayBuffer() : await new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = no; r.readAsArrayBuffer(file); });
  let t = new TextDecoder('utf-8').decode(buf);
  if (t.includes('\uFFFD')) t = new TextDecoder('windows-1251').decode(buf);       // Excel часто сохраняет CSV в Windows-1251
  return t;
}
const normHead = h => h.toLowerCase().replace(/ё/g, 'е').replace(/[^a-zа-я0-9№]/g, '');

/* spec: { ключ: ['синонимы заголовка', …] } → строки-объекты; missing — обязательные столбцы, которых нет */
function mapTable(parsed, spec, required) {
  if (!parsed.length) return { rows: [], missing: required, header: [] };
  const head = parsed[0].cells.map(normHead), idx = {};
  for (const [k, syn] of Object.entries(spec)) { const i = head.findIndex(h => syn.includes(h)); if (i >= 0) idx[k] = i; }
  const missing = required.filter(k => !(k in idx));
  const rows = parsed.slice(1).map(r => { const o = { _line: r.line }; for (const k of Object.keys(spec)) o[k] = k in idx ? (r.cells[idx[k]] || '') : ''; return o; });
  return { rows, missing };
}
function downloadCsv(name, lines) {
  const csv = '\uFEFF' + lines.map(r => r.map(c => /[;"\n]/.test(c) ? '"' + String(c).replace(/"/g, '""') + '"' : c).join(';')).join('\r\n');
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); a.download = name; document.body.appendChild(a); a.click(); a.remove();
}

/* cfg: title, hint, spec, required, labels (названия столбцов для сообщения), template {name, lines}, path, optionsHtml, readOptions(el), summary(res) */
function importModal(cfg) {
  const m = modal(`<h2>${esc(cfg.title)}</h2><p class="muted" style="margin-top:0">${cfg.hint}</p>
    <p><button type="button" class="secondary small" data-tpl>Скачать шаблон</button></p>
    <label class="f">Файл (CSV, TSV или TXT)<input type="file" data-file accept=".csv,.tsv,.txt,text/csv,text/plain"></label>
    ${cfg.optionsHtml || ''}<div data-res aria-live="polite"></div>
    <div class="row"><button type="button" data-go disabled>Загрузить</button><button type="button" class="secondary" data-x>Закрыть</button></div>`, true);
  const el = m.el, res = $('[data-res]', el), go = $('[data-go]', el); let rows = [];
  $('[data-x]', el).onclick = m.close;
  $('[data-tpl]', el).onclick = () => downloadCsv(cfg.template.name, cfg.template.lines);
  const body = dry => ({ rows: rows.map(({ _line, ...r }) => r), dry_run: dry, ...(cfg.readOptions ? cfg.readOptions(el) : {}) });
  const check = async () => {
    go.disabled = true; if (!rows.length) return;
    res.innerHTML = '<p class="muted">Проверяем файл…</p>';
    try {
      const r = await POST(cfg.path, body(true));
      if (r.errors.length) res.innerHTML = `<div class="card err" role="alert"><b>В файле ${r.errors.length} ${r.errors.length === 1 ? 'ошибка' : 'ошибок'} — ничего не загружено. Исправьте файл и выберите его снова.</b><ul class="errlist">${r.errors.slice(0, 50).map(e => `<li>строка ${rows[e.row - 1]?._line ?? e.row}: ${esc(e.message)}</li>`).join('')}${r.errors.length > 50 ? '<li>…</li>' : ''}</ul></div>`;
      else { res.innerHTML = `<div class="banner" style="margin:8px 0"><span>${esc(cfg.summary(r, true))}</span></div>`; go.disabled = false; }
    } catch (e) { res.innerHTML = `<div class="card err">${esc(errText(e))}</div>`; }
  };
  $('[data-file]', el).onchange = async e => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const t = mapTable(parseDelimited(await readTextFile(f)), cfg.spec, cfg.required);
      if (t.missing.length) { rows = []; go.disabled = true; res.innerHTML = `<div class="card err" role="alert">В первой строке файла не найдены столбцы: <b>${t.missing.map(k => esc(cfg.labels[k])).join(', ')}</b>. Возьмите шаблон.</div>`; return; }
      if (!t.rows.length) { rows = []; res.innerHTML = '<div class="card err">В файле нет строк с данными.</div>'; return; }
      rows = t.rows; check();
    } catch (x) { res.innerHTML = `<div class="card err">${esc(errText(x))}</div>`; }
  };
  el.addEventListener('change', e => { if (e.target.matches('[data-opt]')) check(); });
  go.onclick = async () => {
    go.disabled = true;
    try { const r = await POST(cfg.path, body(false)); if (r.errors.length) { check(); return; } m.close(); toast(cfg.summary(r, false)); cfg.onDone(); }
    catch (e) { res.innerHTML = `<div class="card err">${esc(errText(e))}</div>`; go.disabled = false; }
  };
}

function importUsers(onDone) {
  importModal({
    title: 'Загрузить пользователей из файла',
    hint: 'По строке на пользователя. Столбцы: <b>E-mail, Имя, Фамилия, Роль</b>; для бригадира — <b>Участок</b>, для сборщика — <b>Работник</b> (как в списках системы); <b>Пароль</b> — если не отправляете приглашения. Роли: ' + Object.values(ROLE_RU).join(', ') + '.',
    spec: { email: ['email', 'почта', 'эпочта', 'электроннаяпочта'], first_name: ['имя'], last_name: ['фамилия'], role: ['роль'], work_center: ['участок'], worker: ['работник', 'исполнитель'], password: ['пароль', 'временныйпароль'] },
    required: ['email', 'first_name', 'last_name', 'role'],
    labels: { email: 'E-mail', first_name: 'Имя', last_name: 'Фамилия', role: 'Роль' },
    template: { name: 'пользователи-шаблон.csv', lines: [['E-mail', 'Имя', 'Фамилия', 'Роль', 'Участок', 'Работник', 'Пароль'], ['ivan@example.com', 'Иван', 'Петров', 'Руководитель проекта', '', '', ''], ['olga@example.com', 'Ольга', 'Смирнова', 'Бригадир', 'Сборка', '', ''], ['petr@example.com', 'Пётр', 'Орлов', 'Директор', '', '', '']] },
    path: '/users/import',
    optionsHtml: '<label class="chk"><input type="checkbox" data-opt name="send_invite" checked> <span>Отправить каждому приглашение по e-mail (пароль в файле не нужен)</span></label>',
    readOptions: el => ({ send_invite: $('[name=send_invite]', el).checked }),
    summary: (r, dry) => dry ? `Файл в порядке: будет создано пользователей — ${r.would_create}.` : `Создано пользователей: ${r.created}`,
    onDone,
  });
}

function importCatalog(refs, onDone) {
  importModal({
    title: 'Загрузить изделия из файла',
    hint: 'По строке на операцию. Столбцы: <b>Изделие, Операция, Норма в день</b>; необязательные: Ед., Порядок, Квалификация (число или название уровня), На изделие, Версия. Нового изделия ещё нет — создаётся версия 1.',
    spec: { product: ['изделие', 'продукт'], unit: ['ед', 'едизм', 'единица'], version_name: ['версия', 'названиеверсии'], operation: ['операция'], sequence: ['порядок', '№', 'номер'], norm_per_day: ['норма', 'нормадень', 'нормавдень', 'нормазадень', 'нормавсмену'], min_skill: ['квалификация', 'требуемаяквалификация', 'уровень'], per_item: ['наизделие', 'нашт', 'кол-вонаизделие', 'колвонаизделие'] },
    required: ['product', 'operation', 'norm_per_day'],
    labels: { product: 'Изделие', operation: 'Операция', norm_per_day: 'Норма в день' },
    template: { name: 'каталог-шаблон.csv', lines: [['Изделие', 'Ед.', 'Порядок', 'Операция', 'Норма в день', 'Квалификация', 'На изделие'], ['Стол', 'шт', '1', 'Раскрой', '20', '1', '1'], ['Стол', 'шт', '2', 'Сборка', '8', '2', '1'], ['Стул', 'шт', '1', 'Покраска', '15', '1', '1']] },
    path: '/products/import',
    optionsHtml: '<label class="f">Если изделие уже есть в каталоге<select data-opt name="on_existing"><option value="skip">пропустить</option><option value="version">добавить новую версию маршрута</option></select></label>',
    readOptions: el => ({ on_existing: $('[name=on_existing]', el).value }),
    summary: (r, dry) => `${dry ? 'Файл в порядке: будет' : 'Готово:'} новых изделий — ${r.created}, новых версий — ${r.versions}${r.skipped.length ? ', пропущено существующих — ' + r.skipped.length : ''}.`,
    onDone,
  });
}
