// Эмулятор Apps Script для оригинального Code_v3.5.gs (ТЗ 8.4, п. 1).
// Вход (stdin): JSON { today:'YYYY-MM-DD', orders, ops, workers, holidays, facts, settings }
// Выход (stdout): JSON { plan, status, load, alerts, newFact }
// Запуск: TZ=UTC node tools/emulate.js < input.json
const vm = require('vm'), fs = require('fs'), path = require('path');
const SCRIPT = path.join(__dirname, '..', 'reference', 'Code_v3.5.gs');

function chain() { const f = function () { return p; }; const p = new Proxy(f, { get: (t, k) => k === Symbol.toPrimitive ? () => '' : p, apply: () => p }); return p; }
class Sheet {
  constructor(name) { this.name = name; this.data = []; }
  setColumnWidth() {} setFrozenRows() {} setFrozenColumns() {}
  getName() { return this.name; }
  setConditionalFormatRules() {}
  getLastRow() { return this.data.length; }
  getLastColumn() { return Math.max(0, ...this.data.map(r => r.length)); }
  clear() { this.data = []; return this; }
  deleteRow(i) { this.data.splice(i - 1, 1); }
  getRange(r, c = 1, nr = 1, nc = 1) {
    const sh = this;
    const rng = {
      getValues: () => Array.from({ length: nr }, (_, i) => Array.from({ length: nc }, (_, j) => { const v = (sh.data[r - 1 + i] || [])[c - 1 + j]; return v === undefined ? '' : v; })),
      setValues: (vals) => { vals.forEach((row, i) => { sh.data[r - 1 + i] = sh.data[r - 1 + i] || []; row.forEach((v, j) => { sh.data[r - 1 + i][c - 1 + j] = v; }); }); return proxy; },
      setValue: (v) => { sh.data[r - 1] = sh.data[r - 1] || []; sh.data[r - 1][c - 1] = v; return proxy; },
      getValue: () => ((sh.data[r - 1] || [])[c - 1] ?? ''),
      sort: () => proxy,
    };
    const proxy = new Proxy(rng, { get: (t, k) => k in t ? t[k] : () => proxy });
    return proxy;
  }
}
const sheetProxy = (sh) => new Proxy(sh, { get: (t, k) => k in t ? t[k] : () => chain() });

function run(input) {
  const [Y, M, D] = input.today.split('-').map(Number);
  const FIXED = new Date(Y, M - 1, D, 9, 30, 0).getTime();   // «сейчас» — 09:30 заданного дня
  const RealDate = Date;
  class FakeDate extends RealDate {
    constructor(...a) { if (a.length === 0) super(FIXED); else super(...a); }
    static now() { return FIXED; }
  }
  const sheets = {};
  const ss = {
    getSheetByName: n => sheets[n] ? sheetProxy(sheets[n]) : null,
    insertSheet: n => { sheets[n] = new Sheet(n); return sheetProxy(sheets[n]); },
    getSheets: () => Object.values(sheets).map(sheetProxy),
    deleteSheet: (p) => { delete sheets[p.getName()]; },
    setActiveSheet: () => {}, getSpreadsheetTimeZone: () => 'UTC',
  };
  const fmt = (d, tz, f) => { const p = n => String(n).padStart(2, '0'); d = new Date(d); return f.replace('yyyy', d.getFullYear()).replace('MM', p(d.getMonth() + 1)).replace('dd', p(d.getDate())).replace('HH', p(d.getHours())).replace('mm', p(d.getMinutes())); };
  const alerts = [];
  const ctx = {
    console, Math, JSON, Set, Map, Array, Object, Number, String, isNaN, Date: FakeDate,
    SpreadsheetApp: new Proxy({
      getActiveSpreadsheet: () => ss,
      newConditionalFormatRule: () => { const b = {}; const api = { whenFormulaSatisfied: f => { b.formula = f; return api; }, setBackground: c => { b.bg = c; return api; }, setRanges: r => api, build: () => b }; return api; },
      getUi: () => ({ alert: m => alerts.push(m), createMenu: () => chain() }),
      newDataValidation: () => chain(), BandingTheme: {}, BorderStyle: {},
    }, { get: (t, k) => k in t ? t[k] : (typeof k === 'string' && k.startsWith('new') ? () => chain() : chain()) }),
    Session: { getScriptTimeZone: () => 'UTC' }, Utilities: { formatDate: fmt },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(SCRIPT, 'utf8'), ctx);

  const toDate = s => { if (s == null || s === '') return ''; if (typeof s !== 'string') return s; const m = s.match(/^(\d{4})-(\d\d)-(\d\d)$/); return m ? new FakeDate(+m[1], +m[2] - 1, +m[3]) : s; };
  const mk = (name, rows) => { const s = ss.insertSheet(name); s.getRange(1, 1, rows.length, rows[0].length).setValues(rows); return s; };

  mk('Заказы', [['ID', 'Проект', 'Изделие', 'Кол-во', 'Срок', 'Приоритет']].concat(
    input.orders.map(o => [o.id, o.project, o.item, o.qty, toDate(o.due), o.priority])));
  mk('Операции', [['Изделие', 'Операция', 'Норма', 'Квал', 'На изделие', 'Порядок', 'Блок']].concat(
    input.ops.map(o => [o.item, o.op, o.norm, o.skill, o.perItem, o.order, o.blocked || 0])));
  mk('Сборщики', [['Имя', 'Произв.', 'Квал', 'Статус', 'Проекты']].concat(
    input.workers.map(w => [w.name, w.perf, w.skill, w.status || 'доступен', w.projects || ''])));
  mk('Праздники', [['Дата']].concat((input.holidays || []).map(d => [toDate(d)])));

  ctx.ensureSettingsSheet(ss);
  const st = sheets['Настройки'];
  const s = input.settings || {};
  st.data[1][1] = s.strictness ?? 100;
  st.data[2][1] = s.byLevel ?? '';
  st.data[3][1] = s.maxWorkers ?? 4;
  st.data[4][1] = s.todayThreshold ?? 0.6;
  st.data[5][1] = s.minStartShare ?? 0.5;

  const fact = ctx.ensureFactSheet(ss);
  (input.facts || []).forEach((f, i) => {
    fact.data[i + 1] = [toDate(f.date), f.id, f.project, f.item, f.op, f.worker, f.plan || 0, f.qty === 0 ? 0 : (f.qty ?? ''), f.reason || ''];
  });

  ctx.replanAll();
  const ds = d => d instanceof RealDate ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` : d;
  const out = {
    plan: (sheets['Задания на день'].data.slice(1)).map(r => r.slice(0, 7)),
    status: (sheets['Статус заказов'] ? sheets['Статус заказов'].data.slice(1) : []).map(r => r.slice(0, 11).map(ds)),
    load: sheets['Загрузка'] ? sheets['Загрузка'].data : [],
    newFact: sheets['Факт'].data.slice(1).filter(r => r[7] === '' || r[7] === undefined).map(r => r.slice(0, 8).map(ds)),
    alerts,
  };
  return out;
}

let buf = '';
process.stdin.on('data', c => buf += c);
process.stdin.on('end', () => {
  try { process.stdout.write(JSON.stringify(run(JSON.parse(buf)))); }
  catch (e) { process.stdout.write(JSON.stringify({ error: String(e.stack).split('\n').slice(0, 5).join('\n') })); process.exit(0); }
});
