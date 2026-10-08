// ===== CONFIG =====
const SHEET_ORDERS   = 'Заказы';
const SHEET_OPS      = 'Операции';
const SHEET_WORKERS  = 'Сборщики';
const SHEET_OUT      = 'Задания на день';
const SHEET_LOAD     = 'Загрузка';
const SHEET_FACT     = 'Факт';
const SHEET_STATUS   = 'Статус заказов';
const SHEET_HOLIDAYS = 'Праздники';
const SHEET_GUIDE    = 'Инструкция';

const ORD  = { id: 1, project: 2, item: 3, qty: 4, due: 5, priority: 6 };
const OPS  = { item: 1, op: 2, norm: 3, skill: 4, perItem: 5, order: 6, blocked: 7 };
const WRK  = { name: 1, perf: 2, skill: 3, status: 4, projects: 5 };
const FACT = { date: 1, id: 2, project: 3, item: 4, op: 5, worker: 6, plan: 7, qty: 8, reason: 9 };
const FACT_REASON_HEADER = 'Причина отклонения';

const SHEET_SETTINGS = 'Настройки';
// Значения ниже — по умолчанию; фактические берутся с листа «Настройки» (см. applySettings)
let   MAX_WORKERS_PER_OP   = 4;
const HORIZON_WEEKS        = 8;
const WEEK_LENGTH          = 5;
let   TODAY_LOAD_THRESHOLD = 0.6;
let   PRIORITY_STRICTNESS  = 100;   // %, 100 = приоритет 2 ждёт весь приоритет 1
let   PRIORITY_STRICTNESS_BY_LEVEL = {};
const FREEZE_HEADERS      = false; // true — закреплять шапки листов (на границе закрепления появляется «ручка» для перетаскивания)
let   MIN_START_SHARE      = 0.5;   // доля одной операции, при которой её можно начать в конце дня
// ==================

// === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
function floorWithTolerance(cum, perItem) {
  if (perItem <= 0) return 0;
  const raw = cum / perItem;
  const floor = Math.floor(raw);
  if (raw - floor >= 0.9) {
    return floor + 1;
  }
  return floor;
}

function normId(v) {
  if (v === null || v === undefined) return '';
  return String(v).trim().replace(/\.0+$/, '');
}

function cleanRecs(rec) {
  const t = String(rec || '');
  if (!t || t === '—') return [];
  return t.split('\n')
    .map(l => l.replace(/^\d+\)\s*/, '').trim())
    .filter(l => l && l.indexOf('взять на контроль') < 0)
    .map((l, i) => (i + 1) + ') ' + l);
}

// «18шт. к» → 18, «6 шт.» → 6, «1 200» → 1200, «2,5» → 2.5
function parseQty(v) {
  if (typeof v === 'number') return v;
  const t = String(v == null ? '' : v).replace(/(\d)[\s\u00a0]+(?=\d)/g, '$1').replace(',', '.');
  const m = t.match(/-?\d+(?:\.\d+)?/);
  return m ? Number(m[0]) : 0;
}

// ---------- партии: заказы одного проекта и изделия ----------
// Факт считается по связке проект — изделие — операция. ID заказа (партии) для факта не важен:
// выполненный объём раскладывается по партиям в порядке сроков (ранние закрываются первыми).
const KSEP = '\u0001';
function batchKey(project, item) { return project + KSEP + item; }

function groupBatches(orders) {
  const m = {};
  orders.forEach(o => { (m[batchKey(o.project, o.item)] = m[batchKey(o.project, o.item)] || []).push(o); });
  const ts = o => { const d = parseAnyDate(o.due); return d ? d.getTime() : Infinity; };
  Object.keys(m).forEach(k => m[k].sort((a, b) => ts(a) - ts(b))); // сортировка устойчивая: при равных сроках — порядок в таблице
  return m;
}

// factByPIO: { 'проект␁изделие␁операция': выполнено операций } → { 'ID|операция': выполнено на партии }
function allocateFact(factByPIO, batchesByPI, opsByItem) {
  const done = {};
  Object.keys(factByPIO).forEach(key => {
    const p = key.split(KSEP);
    const batches = batchesByPI[batchKey(p[0], p[1])];
    if (!batches || !batches.length) return;
    const opInfo = (opsByItem[p[1]] || []).find(o => o.op === p[2]);
    const perItem = opInfo ? opInfo.perItem : 1;
    let left = factByPIO[key];
    batches.forEach((b, i) => {
      if (left <= 0) return;
      const cap = Math.ceil(b.qty * perItem - 1e-9);
      const give = (i === batches.length - 1) ? left : Math.min(left, cap); // излишек — на последнюю партию
      if (give > 0) {
        const k = b.id + '|' + p[2];
        done[k] = (done[k] || 0) + give;
        left -= give;
      }
    });
  });
  return done;
}

function applyFreeze(sh, rows, cols) {
  if (rows !== undefined) sh.setFrozenRows(FREEZE_HEADERS ? rows : 0);
  if (cols !== undefined) sh.setFrozenColumns(FREEZE_HEADERS ? cols : 0);
}

// Снимает закрепление на всех листах скрипта (в том числе созданных прежними версиями)
function resetFreeze(ss) {
  if (FREEZE_HEADERS) return;
  const names = [SHEET_FACT, SHEET_OUT, SHEET_LOAD, SHEET_STATUS, SHEET_SETTINGS];
  ss.getSheets().forEach(sh => {
    const n = sh.getName();
    if (names.indexOf(n) >= 0 || n.indexOf('Отчёт: ') === 0) {
      sh.setFrozenRows(0);
      sh.setFrozenColumns(0);
    }
  });
}

function writeStatusAdvice(ss, advice) {
  const sh = ss.getSheetByName(SHEET_STATUS);
  if (!sh || sh.getLastRow() < 1) return;
  const COL = 12; // L
  sh.getRange(1, COL).setValue('Что нужно, чтобы успеть').setFontWeight('bold');
  sh.setColumnWidth(COL, 360);
  const ids = readRows(sh, 1).map(r => String(r[0]).trim());
  ids.forEach((id, i) => {
    const text = (advice && advice[id]) || '';
    const cell = sh.getRange(i + 2, COL);
    cell.setValue(text).setWrap(true).setVerticalAlignment('top');
    if (text === 'В срок') cell.setBackground('#d9ead3');
    else if (text) cell.setBackground('#fce5cd');
  });
}

function readRows(sh, cols) {
  return sh && sh.getLastRow() > 1 ? sh.getRange(2, 1, sh.getLastRow() - 1, cols).getValues() : [];
}

function toNum(v, def) {
  const n = Number(String(v).replace(',', '.').trim());
  return isNaN(n) || String(v).trim() === '' ? def : n;
}

function ensureSettingsSheet(ss) {
  let sh = ss.getSheetByName(SHEET_SETTINGS);
  if (sh) return sh;
  sh = ss.insertSheet(SHEET_SETTINGS);
  sh.getRange(1, 1, 6, 3).setValues([
    ['Параметр', 'Значение', 'Пояснение'],
    ['Жёсткость приоритета, %', 100, '100 — приоритет 2 ждёт окончания всего приоритета 1. 0 — приоритет лишь задаёт очерёдность доступа к людям, следующий уровень занимает свободные мощности с первого дня. Между ними: следующий уровень стартует, когда предыдущий запланирован на указанный процент.'],
    ['Жёсткость по уровням', '', 'Необязательно. Формат «2:60; 3:30» — для приоритета 2 жёсткость 60%, для приоритета 3 — 30%. Остальные уровни берут значение выше.'],
    ['Макс. сборщиков на операцию в день', 4, 'Сколько людей одновременно можно поставить на одну операцию.'],
    ['Порог загрузки сегодня (0–1)', 0.6, 'Сборщик, уже сделавший по факту такую долю дня, новых заданий на сегодня не получит.'],
    ['Мин. доля операции для старта (0–1)', 0.5, 'Операции только целые. Если до конца дня времени не хватает на всю операцию, но хватает на эту долю — операция ставится целиком, а излишек переносится на следующий рабочий день сборщика.']
  ]);
  sh.getRange(1, 1, 1, 3).setFontWeight('bold').setBackground('#d9e2f3');
  sh.setColumnWidth(1, 260); sh.setColumnWidth(2, 90); sh.setColumnWidth(3, 620);
  sh.getRange(2, 3, 5, 1).setWrap(true);
  applyFreeze(sh, 1);
  return sh;
}

function applySettings(ss) {
  const sh = ensureSettingsSheet(ss);
  const v = readRows(sh, 2).map(r => r[1]);
  PRIORITY_STRICTNESS  = Math.min(100, Math.max(0, toNum(v[0], 100)));
  PRIORITY_STRICTNESS_BY_LEVEL = {};
  String(v[1] || '').split(/[;,]/).forEach(part => {
    const m = part.match(/^\s*(\d+)\s*:\s*([\d.,]+)\s*$/);
    if (m) PRIORITY_STRICTNESS_BY_LEVEL[Number(m[1])] = Math.min(100, Math.max(0, toNum(m[2], 100)));
  });
  MAX_WORKERS_PER_OP   = Math.max(1, Math.round(toNum(v[2], 4)));
  TODAY_LOAD_THRESHOLD = Math.min(1, Math.max(0, toNum(v[3], 0.6)));
  MIN_START_SHARE      = Math.min(1, Math.max(0.1, toNum(v[4], 0.5)));
}

function strictnessFor(p) {
  return (p in PRIORITY_STRICTNESS_BY_LEVEL) ? PRIORITY_STRICTNESS_BY_LEVEL[p] : PRIORITY_STRICTNESS;
}

function openSettings() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  ss.setActiveSheet(ensureSettingsSheet(ss));
}

function replanAll() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  ensureGuideSheet(ss);
  const shFact = ensureFactSheet(ss);

  if (shFact.getLastRow() > 1) {
    const data = shFact.getRange(2, 1, shFact.getLastRow() - 1, 9).getValues();
    for (let i = data.length - 1; i >= 0; i--) {
      const qty = Number(data[i][FACT.qty - 1]) || 0;
      const hasReason = String(data[i][FACT.reason - 1] || '').trim() !== '';
      if (qty === 0 && !hasReason) {
        shFact.deleteRow(i + 2);
      }
    }
  }

  sortFactSheet(shFact);
  const res = distributeTasks() || { ok: false, msg: 'Не удалось построить план.' };
  let msg = res.msg;
  if (res.ok) {
    const rep = buildProjectReports(ss);
    writeStatusAdvice(ss, rep ? rep.advice : {});
    if (rep) {
      msg += '\n\nОтчётов по проектам: ' + rep.created;
      if (rep.skipped.length) msg += '\nБез отчёта (нет плана работ): ' + rep.skipped.join(', ');
      if (rep.removed.length) msg += '\nУдалены устаревшие отчёты: ' + rep.removed.length;
    }
  }
  resetFreeze(ss);
  SpreadsheetApp.getUi().alert(msg); // единственное сообщение — после завершения всех шагов
}

function distributeTasks() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const shOrders  = ss.getSheetByName(SHEET_ORDERS);
  const shOps     = ss.getSheetByName(SHEET_OPS);
  const shWorkers = ss.getSheetByName(SHEET_WORKERS);
  let shOut       = ss.getSheetByName(SHEET_OUT);
  const shHol     = ss.getSheetByName(SHEET_HOLIDAYS);
  const tz        = Session.getScriptTimeZone();
  applySettings(ss);

  if (!shOut) {
    shOut = ss.insertSheet(SHEET_OUT);
    shOut.getRange(1, 1, 1, 7).setValues([[
      'Дата','ID','Проект','Изделие','Операция','Кол-во операций','Сборщик'
    ]]);
    shOut.getRange(1, 1, 1, 7).setFontWeight('bold');
    applyFreeze(shOut, 1);
  }

  const holidays = readHolidays(shHol);
  const workDays = buildWorkCalendar(holidays);
  const TOTAL_DAYS = workDays.length;
  const todayKeyStr = dateKey(workDays[0]);

  const workersData = readRows(shWorkers, 5);
  const workers = workersData
    .filter(r => String(r[WRK.status - 1]).trim().toLowerCase() === 'доступен')
    .map((r, idx) => ({
      id: idx,
      name: String(r[WRK.name - 1]).trim(),
      perf: Number(r[WRK.perf - 1]) || 1,
      skill: Number(r[WRK.skill - 1]) || 1,
      projects: String(r[WRK.projects - 1] || '').trim(),
      free: Array.from({ length: TOTAL_DAYS }, () => 1)
    }));

  if (!workers.length) {
    return { ok: false, msg: 'Нет доступных сборщиков.' };
  }

  const opsData = readRows(shOps, 7);
  const opsByItem = {};
  const normByOp = {};
  opsData.forEach(r => {
    const item = String(r[OPS.item - 1]).trim();
    if (!item) return;
    const opObj = {
      op: String(r[OPS.op - 1]).trim(),
      norm: Number(r[OPS.norm - 1]) || 1,
      skill: Number(r[OPS.skill - 1]) || 1,
      perItem: Number(r[OPS.perItem - 1]) || 1,
      order: Number(r[OPS.order - 1]) || 999,
      blocked: Number(r[OPS.blocked - 1]) || 0
    };
    (opsByItem[item] = opsByItem[item] || []).push(opObj);
    normByOp[item + '|' + opObj.op] = { norm: opObj.norm };
  });

  Object.keys(opsByItem).forEach(item => {
    opsByItem[item].sort((a, b) => a.order - b.order);
  });

  const ordData = readRows(shOrders, 6);
  const orders = ordData
    .filter(r => String(r[ORD.id - 1]).trim() && String(r[ORD.item - 1]).trim())
    .map(r => ({
      id: String(r[ORD.id - 1]).trim(),
      project: String(r[ORD.project - 1]).trim(),
      item: String(r[ORD.item - 1]).trim(),
      qty: parseQty(r[ORD.qty - 1]),
      due: r[ORD.due - 1],
      priority: Number(r[ORD.priority - 1]) || 999
    }))
    .sort((a, b) => {
      if (a.priority !== b.priority) return a.priority - b.priority;
      return new Date(a.due) - new Date(b.due);
    });

  const shFact = ensureFactSheet(ss);
  const factData = shFact.getLastRow() > 1
    ? shFact.getRange(2, 1, shFact.getLastRow() - 1, 8).getValues()
    : [];

  const doneMap = {};
  const factByPIO = {};
  const batchesByPI = groupBatches(orders);
  const orderById = {};
  orders.forEach(o => { orderById[o.id] = o; });
  const planByDayKey = {};
  const existingKeys = new Set();
  const todayFactLoadByWorker = {};

  factData.forEach(r => {
    const d       = r[FACT.date - 1];
    const id      = String(r[FACT.id - 1]).trim();
    const item    = String(r[FACT.item - 1]).trim();
    const op      = String(r[FACT.op - 1]).trim();
    const worker  = String(r[FACT.worker - 1]).trim();
    const plan    = Number(r[FACT.plan - 1]) || 0;
    const qty     = Number(r[FACT.qty - 1]) || 0;

    const project = String(r[FACT.project - 1]).trim();
    if (!d || !item || !op) return;

    const dKey = dateKey(d);
    const rowKey = dKey + '|' + id + '|' + op + '|' + worker;
    existingKeys.add(rowKey);

    if (qty > 0) {
      // факт учитывается по связке проект — изделие — операция; ID партии не важен
      let proj = project;
      if (!batchesByPI[batchKey(proj, item)] && orderById[id]) proj = orderById[id].project; // старые строки без проекта
      const pio = proj + KSEP + item + KSEP + op;
      factByPIO[pio] = (factByPIO[pio] || 0) + qty;

      if (dKey === todayKeyStr) {
        const opInfo = normByOp[item + '|' + op];
        if (opInfo) {
          const w = workers.find(w => w.name === worker);
          const perf = w ? w.perf : 1;
          const dayCapacity = opInfo.norm * perf;
          if (dayCapacity > 0) {
            const share = qty / dayCapacity;
            todayFactLoadByWorker[worker] = (todayFactLoadByWorker[worker] || 0) + share;
          }
        }
      }
    }
    if (qty === 0 && plan > 0) {
      planByDayKey[rowKey] = plan;
    }
  });

  Object.assign(doneMap, allocateFact(factByPIO, batchesByPI, opsByItem));

  const loadedToday = new Set();
  Object.keys(todayFactLoadByWorker).forEach(name => {
    if (todayFactLoadByWorker[name] >= TODAY_LOAD_THRESHOLD) {
      loadedToday.add(name);
    }
  });

  // --- время и целые операции ---
  // Норма — операций за полный день при производительности 1, поэтому 1 операция занимает 1/(норма×perf) дня.
  const opDays = (op, w) => 1 / (op.norm * w.perf);
  // Списывает время; если операция не влезла в остаток дня, излишек уходит на следующие дни сборщика.
  function spendTime(w, day, days) {
    w.free[day] -= days;
    if (w.free[day] < 1e-9) {
      let over = Math.max(0, -w.free[day]);
      w.free[day] = 0;
      for (let d = day + 1; over > 1e-9 && d < TOTAL_DAYS; d++) {
        const take = Math.min(over, w.free[d]);
        w.free[d] -= take;
        over -= take;
      }
    }
  }

  // --- жёсткость приоритета: прогресс планирования по уровням (в нормо-днях) ---
  const prioLoad = {};
  function addPrioLoad(p, day, normoDays) {
    const rec = prioLoad[p] = prioLoad[p] || { total: 0, byDay: new Array(TOTAL_DAYS).fill(0) };
    rec.total += normoDays;
    rec.byDay[day] += normoDays;
  }
  // День (индекс), к концу которого на уровне p запланирована доля share работ; -1 если share = 0
  function prioShareDay(p, share, fallbackDay) {
    if (share <= 0) return -1;
    const rec = prioLoad[p];
    if (!rec || rec.total <= 0) return fallbackDay;
    let cum = 0;
    for (let d = 0; d < TOTAL_DAYS; d++) {
      cum += rec.byDay[d];
      if (cum >= share * rec.total - 1e-9) return d;
    }
    return fallbackDay;
  }

  const result = [['Дата','ID','Проект','Изделие','Операция','Кол-во операций','Сборщик']];
  const newFactRows = [];
  const warnings = [];
  const statusMap = {};

  const priorityLastDay = {};

  orders.forEach(order => {
    const ops = opsByItem[order.item];
    const orderKey = order.id;

    if (!statusMap[orderKey]) {
      statusMap[orderKey] = {
        order: order,
        requiredOps: 0,
        doneOps: 0,
        plannedOps: 0,
        lastDayIdx: -1,
        noOps: false
      };
    }
    const st = statusMap[orderKey];

    if (!ops || !ops.length) {
      warnings.push(`${order.item} (ID ${order.id}): нет операций`);
      st.noOps = true;
      return;
    }

    let minStartDayForOrder = 0;
    Object.keys(priorityLastDay).forEach(p => {
      const pNum = Number(p);
      if (pNum < order.priority) {
        const S = strictnessFor(order.priority);
        const lastDay = S >= 100
          ? priorityLastDay[p]
          : prioShareDay(p, S / 100, priorityLastDay[p]);
        if (lastDay + 1 > minStartDayForOrder) {
          minStartDayForOrder = lastDay + 1;
        }
      }
    });
    if (minStartDayForOrder >= TOTAL_DAYS) {
      warnings.push(`${order.item} (ID ${order.id}): не хватает горизонта для приоритета ${order.priority}`);
      st.noOps = true;
      return;
    }

    const dueDate = new Date(order.due);
    let deadlineIdx = TOTAL_DAYS - 1;
    if (!isNaN(dueDate)) {
      if (dueDate < workDays[0]) deadlineIdx = 0;
      else {
        let idx = -1;
        for (let i = 0; i < TOTAL_DAYS; i++) {
          if (workDays[i] <= dueDate) idx = i; else break;
        }
        deadlineIdx = idx === -1 ? 0 : idx;
      }
    }
    if (deadlineIdx < minStartDayForOrder) {
      deadlineIdx = minStartDayForOrder;
    }

    const groups = [];
    {
      const byOrder = {};
      ops.forEach(op => {
        const o = op.order;
        (byOrder[o] = byOrder[o] || []).push(op);
      });
      Object.keys(byOrder)
        .map(Number)
        .sort((a, b) => a - b)
        .forEach(orderN => {
          groups.push({ order: orderN, ops: byOrder[orderN] });
        });
    }

    const groupProgress = [];
    let orderMaxDay = minStartDayForOrder;

    groups.forEach((group, groupIndex) => {
      let readyItems;
      if (groupIndex === 0) readyItems = order.qty;
      else readyItems = Math.min(order.qty, groupProgress[groupIndex - 1].readyItems);

      let minStartDay = minStartDayForOrder;
      if (groupIndex > 0) {
        minStartDay = Math.max(
          minStartDayForOrder,
          groupProgress[groupIndex - 1].lastDayIdx
        );
      }

      let groupLastDay = minStartDay;
      let groupMinReadyItems = order.qty;

      group.ops.forEach(op => {
        const totalRequired = Math.ceil(order.qty * op.perItem - 1e-9);
        const factKey = order.id + '|' + op.op;
        const doneOps = doneMap[factKey] || 0;
        const doneItems = Math.floor(doneOps / op.perItem);

        st.requiredOps += totalRequired;
        st.doneOps     += doneOps;

        const blockedForOrder = Math.min(op.blocked || 0, readyItems);
        const availableItems = Math.max(0, readyItems - blockedForOrder);

        let alreadyPlannedOps = 0;
        let alreadyPlannedLastDay = -1;
        for (let day = 0; day < TOTAL_DAYS; day++) {
          const dk = dateKey(workDays[day]);
          const wCandidates = workers.filter(w =>
            w.skill >= op.skill && workerAllowedForProject(w, order.id)
          );
          for (const w of wCandidates) {
            const rk = dk + '|' + order.id + '|' + op.op + '|' + w.name;
            if (planByDayKey[rk]) {
              const pl = Math.max(1, Math.round(planByDayKey[rk]));
              alreadyPlannedOps += pl;
              result.push([
                Utilities.formatDate(workDays[day], tz, 'dd.MM.yyyy'),
                order.id,
                order.project,
                order.item,
                op.op,
                pl,
                w.name
              ]);
              spendTime(w, day, pl * opDays(op, w));
              addPrioLoad(order.priority, day, pl / op.norm);
              if (day > st.lastDayIdx) st.lastDayIdx = day;
              if (day > alreadyPlannedLastDay) alreadyPlannedLastDay = day;
              if (day > orderMaxDay) orderMaxDay = day;
              planByDayKey[rk] = 0;
            }
          }
        }
        st.plannedOps += alreadyPlannedOps;

        const alreadyPlannedItems = Math.floor(alreadyPlannedOps / op.perItem);
        const doneItemsThisOp = doneItems + alreadyPlannedItems;
        const remainingItems = Math.max(0, availableItems - doneItemsThisOp);
        let remainingOps = Math.ceil(remainingItems * op.perItem - 1e-9);

        const before = remainingOps;
        let lastPlannedDay = alreadyPlannedLastDay;
        if (remainingOps > 0) {
          const candidates = workers.filter(w =>
            w.skill >= op.skill && workerAllowedForProject(w, order.id)
          );
          if (!candidates.length) {
            warnings.push(`${order.item} (ID ${order.id}) / ${op.op}: нет подходящих сборщиков`);
          } else {
            remainingOps = scheduleOp(order, op, remainingOps, candidates,
                                      minStartDay, deadlineIdx, loadedToday,
                                      (dayIdx) => {
                                        if (dayIdx > lastPlannedDay) lastPlannedDay = dayIdx;
                                        if (dayIdx > orderMaxDay) orderMaxDay = dayIdx;
                                      });

            if (remainingOps > 0 && deadlineIdx < TOTAL_DAYS - 1) {
              remainingOps = scheduleOp(order, op, remainingOps, candidates,
                                        deadlineIdx + 1, TOTAL_DAYS - 1, loadedToday,
                                        (dayIdx) => {
                                          if (dayIdx > lastPlannedDay) lastPlannedDay = dayIdx;
                                          if (dayIdx > orderMaxDay) orderMaxDay = dayIdx;
                                        });
            }

            if (remainingOps > 0) {
              warnings.push(`${order.item} (ID ${order.id}) / ${op.op}: не влезает в горизонт, остаток ${remainingOps}`);
            }
          }
        }

        const plannedOps = before - remainingOps;
        st.plannedOps += plannedOps;

        if (lastPlannedDay > groupLastDay) groupLastDay = lastPlannedDay;

        const totalPlannedOps = doneOps + alreadyPlannedOps + plannedOps;
        const totalPlannedItems = Math.floor(totalPlannedOps / op.perItem);
        const nextGroupReady = Math.min(totalPlannedItems, availableItems);
        if (nextGroupReady < groupMinReadyItems) groupMinReadyItems = nextGroupReady;
      });

      groupProgress[groupIndex] = {
        readyItems: Math.max(0, groupMinReadyItems),
        lastDayIdx: groupLastDay
      };
    });

    const pKey = order.priority;
    if (!(pKey in priorityLastDay)) {
      priorityLastDay[pKey] = orderMaxDay;
    } else {
      if (orderMaxDay > priorityLastDay[pKey]) {
        priorityLastDay[pKey] = orderMaxDay;
      }
    }
  });

  function scheduleOp(order, op, remainingOps, candidates, fromDay, toDay, loadedToday, onDayUsed) {
    for (let day = fromDay; day <= toDay && remainingOps > 0; day++) {
      let pool = candidates.filter(w => w.free[day] > 0.01);

      if (day === 0) pool = pool.filter(w => !loadedToday.has(w.name));

      pool = pool
        .sort((a, b) =>
          (b.free[day] * b.perf) - (a.free[day] * a.perf) ||
          b.perf - a.perf
        )
        .slice(0, MAX_WORKERS_PER_OP);

      for (const w of pool) {
        if (remainingOps <= 0) break;
        // Только целые операции. Сколько целых влезает в оставшееся время дня:
        const oneOp = opDays(op, w);
        let give = Math.floor(w.free[day] / oneOp + 1e-9);
        // Целая не влезает, но времени хватает на нужную долю (или операция дольше дня) — ставим 1,
        // излишек перенесётся на следующий рабочий день сборщика.
        if (give < 1 && w.free[day] >= Math.min(oneOp * MIN_START_SHARE, MIN_START_SHARE) - 1e-9) give = 1;
        give = Math.min(give, remainingOps);

        if (give <= 0) continue;

        spendTime(w, day, give * oneOp);
        addPrioLoad(order.priority, day, give / op.norm);
        remainingOps -= give;

        result.push([
          Utilities.formatDate(workDays[day], tz, 'dd.MM.yyyy'),
          order.id,
          order.project,
          order.item,
          op.op,
          give,
          w.name
        ]);

        if (day === 0 || day === 1) {
          const dk = dateKey(workDays[day]);
          const rowKey = dk + '|' + order.id + '|' + op.op + '|' + w.name;
          if (!existingKeys.has(rowKey)) {
            newFactRows.push([
              workDays[day],
              order.id,
              order.project,
              order.item,
              op.op,
              w.name,
              give,
              ''
            ]);
            existingKeys.add(rowKey);
          }
        }

        const st = statusMap[order.id];
        if (st && day > st.lastDayIdx) st.lastDayIdx = day;

        if (onDayUsed) onDayUsed(day);
      }
    }
    return remainingOps;
  }

  if (newFactRows.length) {
    const startRow = shFact.getLastRow() + 1;
    shFact.getRange(startRow, 1, newFactRows.length, 8).setValues(newFactRows);
  }

  sortFactSheet(shFact);

  shOut.clear();
  shOut.getRange(1, 1, result.length, result[0].length).setValues(result);
  shOut.getRange(1, 1, 1, result[0].length).setFontWeight('bold');
  applyFreeze(shOut, 1);

  writeLoadSheet(ss, workers, workDays, tz, result);
  writeStatusSheet(ss, statusMap, orders, workDays, tz);

  let msg = 'Готово. Строк в плане: ' + (result.length - 1);
  if (newFactRows.length) msg += '\nДобавлено план-строк в Факт: ' + newFactRows.length;
  if (loadedToday.size) {
    msg += '\n\nЗагружены на сегодня (≥' + Math.round(TODAY_LOAD_THRESHOLD * 100) + '%), новых заданий не получат:';
    msg += '\n• ' + [...loadedToday].join('\n• ');
  }
  if (warnings.length) msg += '\n\nПредупреждения:\n• ' + warnings.join('\n• ');
  return { ok: true, msg: msg };
}

function workerAllowedForProject(w, projectId) {
  const p = String(w.projects || '').trim();
  if (!p) return true;
  const list = p.split(',').map(s => normId(s)).filter(Boolean);
  return list.includes(normId(projectId));
}

function sortFactSheet(shFact) {
  const lastRow = shFact.getLastRow();
  if (lastRow <= 2) return;

  const range = shFact.getRange(2, 1, lastRow - 1, 9);
  const data = range.getValues();

  data.sort((a, b) => {
    const dA = new Date(a[FACT.date - 1]).getTime() || 0;
    const dB = new Date(b[FACT.date - 1]).getTime() || 0;
    if (dA !== dB) return dA - dB;
    const idA = String(a[FACT.id - 1]).trim();
    const idB = String(b[FACT.id - 1]).trim();
    if (idA !== idB) return idA.localeCompare(idB, 'ru');
    const oA = String(a[FACT.op - 1]).trim();
    const oB = String(b[FACT.op - 1]).trim();
    if (oA !== oB) return oA.localeCompare(oB, 'ru');
    const wA = String(a[FACT.worker - 1]).trim();
    const wB = String(b[FACT.worker - 1]).trim();
    return wA.localeCompare(wB, 'ru');
  });

  range.setValues(data);
}

function readHolidays(shHol) {
  const holidays = new Set();
  if (shHol && shHol.getLastRow() > 1) {
    shHol.getRange(2, 1, shHol.getLastRow() - 1, 1).getValues().forEach(r => {
      const d = r[0];
      if (d instanceof Date) holidays.add(toKey(d));
      else if (d) {
        const p = new Date(d);
        if (!isNaN(p)) holidays.add(toKey(p));
      }
    });
  }
  return holidays;
}

function buildWorkCalendar(holidays) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const shOrders = ss.getSheetByName(SHEET_ORDERS);

  let maxDue = new Date();
  maxDue.setHours(0, 0, 0, 0);

  if (shOrders && shOrders.getLastRow() > 1) {
    const data = shOrders.getRange(2, 1, shOrders.getLastRow() - 1, 6).getValues();
    data.forEach(r => {
      const d = r[ORD.due - 1];
      const parsed = parseAnyDate(d);
      if (parsed && parsed > maxDue) maxDue = parsed;
    });
  }

  const safetyDate = new Date(maxDue.getTime() + 14 * 86400000);

  const minDate = new Date();
  minDate.setHours(0, 0, 0, 0);
  minDate.setDate(minDate.getDate() + HORIZON_WEEKS * 7);

  const endDate = safetyDate > minDate ? safetyDate : minDate;

  const workDays = [];
  const cursor = new Date();
  cursor.setHours(0, 0, 0, 0);

  let guard = 0;
  while (cursor <= endDate && guard < 3650) {
    const dow = cursor.getDay();
    if (dow !== 0 && dow !== 6 && !holidays.has(toKey(cursor))) {
      workDays.push(new Date(cursor));
    }
    cursor.setDate(cursor.getDate() + 1);
    guard++;
  }

  return workDays;
}

function ensureFactSheet(ss) {
  let sh = ss.getSheetByName(SHEET_FACT);
  if (!sh) {
    sh = ss.insertSheet(SHEET_FACT);
    sh.getRange(1, 1, 1, 8).setValues([[
      'Дата', 'ID', 'Проект', 'Изделие', 'Операция', 'Сборщик', 'План', 'Кол-во операций'
    ]]);
    sh.getRange(1, 1, 1, 8).setFontWeight('bold');
    applyFreeze(sh, 1);
    sh.setColumnWidth(1, 100);
    sh.setColumnWidth(2, 90);
    sh.setColumnWidth(3, 90);
    sh.setColumnWidth(4, 120);
    sh.setColumnWidth(5, 160);
    sh.setColumnWidth(6, 120);
    sh.setColumnWidth(7, 80);
    sh.setColumnWidth(8, 130);
  }

  // Столбец I «Причина отклонения» (в том числе для листов, созданных прежними версиями)
  if (String(sh.getRange(1, FACT.reason).getValue() || '').trim() === '') {
    sh.getRange(1, FACT.reason).setValue(FACT_REASON_HEADER).setFontWeight('bold');
    sh.setColumnWidth(FACT.reason, 280);
  }

  const range = sh.getRange('A2:H1000');
  const rules = [];
  rules.push(SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied('=AND($A2<>""; $H2<>"")')
    .setBackground('#d9ead3')
    .setRanges([range])
    .build());
  rules.push(SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied('=AND($A2<>""; $H2="")')
    .setBackground('#f3f3f3')
    .setRanges([range])
    .build());
  // Жёлтым подсвечиваем пустую ячейку причины, если факт внесён и отличается от плана
  // (формула без разделителей аргументов — работает в любой локали таблицы)
  rules.push(SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied('=($A2<>"")*($H2<>"")*($H2<>$G2)*($I2="")')
    .setBackground('#ffff00')
    .setRanges([sh.getRange('I2:I1000')])
    .build());
  sh.setConditionalFormatRules(rules);

  return sh;
}

function ensureGuideSheet(ss) {
  const sh = ss.getSheetByName(SHEET_GUIDE);
  if (sh) return sh;

  const sheet = ss.insertSheet(SHEET_GUIDE, 0);

  const lines = [
    ['ИНСТРУКЦИЯ ДЛЯ МАСТЕРА', ''],
    ['', ''],
    ['Как работать с распределением заданий', ''],
    ['', ''],
    ['1. ЛИСТ «ФАКТ»', ''],
    ['   Ваш рабочий лист. В нём указано, кто что делает сегодня и завтра.', ''],
    ['   Строки создаются автоматически при нажатии «Перепланировать».', ''],
    ['   Операции всегда целые. Норма — выработка за полный день, без поправочных коэффициентов.', ''],
    ['2. КАК ВНЕСТИ ФАКТ', ''],
    ['   • Впишите число в столбец «Кол-во операций». Факт считается по связке проект — изделие — операция, ID партии не важен.', ''],
    ['   • Строка станет зелёной. Если факт отличается от плана, ячейка в столбце «Причина отклонения» станет жёлтой — впишите причину.', ''],
    ['', ''],
    ['3. ПОРЯДОК ОПЕРАЦИЙ', ''],
    ['   В листе «Операции» есть столбец «Порядок».', ''],
    ['   Одинаковый порядок — операции идут ПАРАЛЛЕЛЬНО.', ''],
    ['', ''],
    ['4. БЛОК ОПЕРАЦИИ', ''],
    ['   Столбец «Блок» — число изделий на паузе.', ''],
    ['', ''],
    ['5. ПРИОРИТЕТ И ПАРТИИ', ''],
    ['   Столбец «Приоритет» задаёт последовательность.', ''],
    ['   По умолчанию: сначала приоритет 1, затем 2, затем 3. Строгость — на листе «Настройки».', ''],
    ['', ''],
    ['6. КНОПКА «ПЕРЕПЛАНИРОВАТЬ»', ''],
    ['   • сохраняет факт;', ''],
    ['   • обновляет план, отчёты, загрузку, статусы.', ''],
    ['', ''],
    ['7. ЛИСТ «СБОРЩИКИ»', ''],
    ['   «ID проекта» — ID заказов, к которым прикреплён сборщик.', ''],
    ['', ''],
    ['8. ЛИСТ «ОТЧЁТ: <ID>»', ''],
    ['   Три блока: ПЛАН, ФАКТ, ОТКЛОНЕНИЕ (до сегодня).', ''],
    ['   Планирование ресурсов: успеем ли к сроку, что мешает, что делать.', ''],
    ['   Отчёты строятся только для проектов, по которым есть план работ. В них есть список причин отставания (из столбца «Причина отклонения» листа «Факт»).', ''],
    ['', ''],
    ['9. КОРОТКО', ''],
    ['   УТРОМ: план на сегодня в «Факте».', ''],
    ['   ДНЁМ: вписывайте числа в «Кол-во».', ''],
    ['   ВЕЧЕРОМ: «Перепланировать».', ''],
    ['', ''],
    ['Вопросы — к администратору таблицы.', '']
  ];

  sheet.getRange(1, 1, lines.length, 2).setValues(lines);
  sheet.setColumnWidth(1, 900);
  sheet.setColumnWidth(2, 20);
  sheet.getRange('A1').setFontSize(16).setFontWeight('bold').setBackground('#d9ead3');
  sheet.getRange('A3').setFontSize(13).setFontWeight('bold');
  [5, 9, 13, 18, 23, 28, 33, 38, 43].forEach(r => {
    if (r <= lines.length) {
      sheet.getRange(r, 1).setFontWeight('bold').setFontSize(12).setBackground('#f3f3f3');
    }
  });
  sheet.getRange(1, 1, lines.length, 1).setWrap(true).setVerticalAlignment('top');
  sheet.setTabColor('#f4cccc');

  return sheet;
}

function openGuide() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ensureGuideSheet(ss);
  ss.setActiveSheet(sh);
}

function dateKey(d) {
  const x = new Date(d);
  const y = x.getFullYear();
  const m = String(x.getMonth() + 1).padStart(2, '0');
  const day = String(x.getDate()).padStart(2, '0');
  return y + '-' + m + '-' + day;
}
function toKey(d) { return dateKey(d); }

function parseAnyDate(v) {
  if (!v) return null;
  if (v instanceof Date) return isNaN(v) ? null : v;
  const s = String(v).trim();
  if (!s) return null;
  const parts = s.split('.');
  if (parts.length === 3) {
    let year = +parts[2];
    if (year < 100) year += 2000;
    const d = new Date(year, +parts[1] - 1, +parts[0]);
    return isNaN(d) ? null : d;
  }
  const d2 = new Date(s);
  return isNaN(d2) ? null : d2;
}

function writeLoadSheet(ss, workers, workDays, tz, planRows) {
  let sh = ss.getSheetByName(SHEET_LOAD);
  if (!sh) sh = ss.insertSheet(SHEET_LOAD);
  sh.clear();

  // Сброс форматов, чтобы числа не превращались в даты
  sh.getRange(1, 1, sh.getMaxRows(), sh.getMaxColumns()).setNumberFormat('0.00');

  const shOps = ss.getSheetByName(SHEET_OPS);
  const opsData = readRows(shOps, 7);
  const normByOp = {};
  opsData.forEach(r => {
    const item = String(r[OPS.item - 1]).trim();
    const op   = String(r[OPS.op - 1]).trim();
    if (!item || !op) return;
    normByOp[item + '|' + op] = Number(r[OPS.norm - 1]) || 1;
  });

  const shWorkers = ss.getSheetByName(SHEET_WORKERS);
  const workersDataAll = readRows(shWorkers, 5);
  const perfByName = {};
  workersDataAll.forEach(r => {
    const name = String(r[WRK.name - 1]).trim();
    if (name) perfByName[name] = Number(r[WRK.perf - 1]) || 1;
  });

  const shFact = ensureFactSheet(ss);
  const factData = shFact.getLastRow() > 1
    ? shFact.getRange(2, 1, shFact.getLastRow() - 1, 8).getValues()
    : [];

  const planData = {};
  const factData2 = {};
  const allDates = new Set();
  const allWorkers = new Set();
  const todayKey = dateKey(workDays[0]);

  factData.forEach(r => {
    const d       = r[FACT.date - 1];
    const id      = String(r[FACT.id - 1]).trim();
    const item    = String(r[FACT.item - 1]).trim();
    const op      = String(r[FACT.op - 1]).trim();
    const worker  = String(r[FACT.worker - 1]).trim();
    const plan    = Number(r[FACT.plan - 1]) || 0;
    const qty     = Number(r[FACT.qty - 1]) || 0;

    if (!d || !id || !item || !op || !worker) return;
    const norm = normByOp[item + '|' + op];
    if (!norm) return;
    const perf = perfByName[worker] || 1;
    const dayCap = norm * perf;
    const dk = dateKey(d);

    allDates.add(dk);
    allWorkers.add(worker);

    if (plan > 0 && dk <= todayKey) {
      const key = dk + '|' + worker;
      planData[key] = (planData[key] || 0) + plan / dayCap;
    }
    if (qty > 0) {
      const key = dk + '|' + worker;
      factData2[key] = (factData2[key] || 0) + qty / dayCap;
    }
  });

  for (let i = 1; i < planRows.length; i++) {
    const row = planRows[i];
    const item    = String(row[3] || '').trim();
    const op      = String(row[4] || '').trim();
    const qty     = Number(row[5]) || 0;
    const worker  = String(row[6] || '').trim();

    if (!item || !op || !worker || qty <= 0) continue;

    const d = parseAnyDate(row[0]);
    if (!d) continue;
    const dk = dateKey(d);
    if (dk <= todayKey) continue;

    const norm = normByOp[item + '|' + op];
    if (!norm) continue;
    const perf = perfByName[worker] || 1;
    const dayCap = norm * perf;
    const share = qty / dayCap;

    const key = dk + '|' + worker;
    planData[key] = (planData[key] || 0) + share;
    allDates.add(dk);
    allWorkers.add(worker);
  }

  workDays.forEach(d => allDates.add(dateKey(d)));

  const sortedDates = [...allDates].sort((a, b) => new Date(a) - new Date(b));
  const workerList = [...new Set([...workers.map(w => w.name), ...allWorkers])];

  const planMatrix = {};
  const factMatrix = {};
  const devMatrix  = {};

  workerList.forEach(name => {
    planMatrix[name] = {};
    factMatrix[name] = {};
    devMatrix[name]  = {};
    sortedDates.forEach(dk => {
      const key = dk + '|' + name;
      const p = planData[key]  || 0;
      const f = factData2[key] || 0;
      if (p > 0) planMatrix[name][dk] = +p.toFixed(2);
      if (f > 0) factMatrix[name][dk] = +f.toFixed(2);
      if (p > 0 || f > 0) devMatrix[name][dk] = +(f - p).toFixed(2);
    });
  });

  const headers = ['Сборщик', ...sortedDates.map(dk => {
    const [y, m, d] = dk.split('-');
    return d + '.' + m;
  }), 'Итого'];

  const totalCols = headers.length;
  const lines = [];

  lines.push(['ЗАГРУЗКА — ПЛАН']);
  lines.push(headers.slice());
  const planTotalsByDay = new Array(sortedDates.length).fill(0);
  workerList.forEach(name => {
    const row = [name];
    let total = 0;
    sortedDates.forEach((dk, i) => {
      const v = planMatrix[name][dk] || 0;
      row.push(v || '');
      total += v;
      planTotalsByDay[i] += v;
    });
    row.push(+total.toFixed(2));
    lines.push(row);
  });
  {
    const row = ['Итого по дню'];
    let g = 0;
    planTotalsByDay.forEach(v => { row.push(+v.toFixed(2)); g += v; });
    row.push(+g.toFixed(2));
    lines.push(row);
  }

  lines.push([]);

  lines.push(['ЗАГРУЗКА — ФАКТ']);
  lines.push(headers.slice());
  const factTotalsByDay = new Array(sortedDates.length).fill(0);
  workerList.forEach(name => {
    const row = [name];
    let total = 0;
    sortedDates.forEach((dk, i) => {
      const v = factMatrix[name][dk] || 0;
      row.push(v || '');
      total += v;
      factTotalsByDay[i] += v;
    });
    row.push(+total.toFixed(2));
    lines.push(row);
  });
  {
    const row = ['Итого по дню'];
    let g = 0;
    factTotalsByDay.forEach(v => { row.push(+v.toFixed(2)); g += v; });
    row.push(+g.toFixed(2));
    lines.push(row);
  }

  lines.push([]);

  lines.push(['ОТКЛОНЕНИЕ (факт − план)']);
  lines.push(headers.slice());
  const devTotalsByDay = new Array(sortedDates.length).fill(0);
  workerList.forEach(name => {
    const row = [name];
    let total = 0;
    sortedDates.forEach((dk, i) => {
      if (dk > todayKey) {
        row.push('');
        return;
      }
      const v = devMatrix[name][dk];
      if (v === undefined) {
        row.push('');
      } else {
        row.push(v);
        total += v;
        devTotalsByDay[i] += v;
      }
    });
    row.push(+total.toFixed(2));
    lines.push(row);
  });
  {
    const row = ['Итого по дню'];
    let g = 0;
    devTotalsByDay.forEach((v, i) => {
      const dk = sortedDates[i];
      if (dk > todayKey) {
        row.push('');
      } else {
        row.push(+v.toFixed(2));
        g += v;
      }
    });
    row.push(+g.toFixed(2));
    lines.push(row);
  }

  sh.getRange(1, 1, lines.length, totalCols).setValues(
    lines.map(r => {
      const copy = r.slice();
      while (copy.length < totalCols) copy.push('');
      return copy.slice(0, totalCols);
    })
  );

  const blockHeaderRows = [];
  const subHeaderRows   = [];
  for (let i = 0; i < lines.length; i++) {
    const v = String(lines[i][0] || '');
    if (v.startsWith('ЗАГРУЗКА —') || v.startsWith('ОТКЛОНЕНИЕ')) blockHeaderRows.push(i + 1);
    if (v === 'Сборщик') subHeaderRows.push(i + 1);
  }
  blockHeaderRows.forEach(r => {
    sh.getRange(r, 1, 1, totalCols).setFontWeight('bold').setFontSize(12).setBackground('#d9ead3');
  });
  subHeaderRows.forEach(r => {
    sh.getRange(r, 1, 1, totalCols).setFontWeight('bold').setBackground('#f3f3f3');
  });

  if (blockHeaderRows[0]) {
    const planStart = blockHeaderRows[0] + 1;
    for (let r = 0; r < workerList.length; r++) {
      for (let c = 0; c < sortedDates.length; c++) {
        const v = planMatrix[workerList[r]][sortedDates[c]] || 0;
        const cell = sh.getRange(planStart + 1 + r, c + 2);
        if (v >= 1.0) cell.setBackground('#f4cccc');
        else if (v >= 0.8) cell.setBackground('#fff2cc');
      }
    }
  }
  if (blockHeaderRows[1]) {
    const factStart = blockHeaderRows[1] + 1;
    for (let r = 0; r < workerList.length; r++) {
      for (let c = 0; c < sortedDates.length; c++) {
        const v = factMatrix[workerList[r]][sortedDates[c]] || 0;
        const cell = sh.getRange(factStart + 1 + r, c + 2);
        if (v >= 1.0) cell.setBackground('#d9ead3');
        else if (v > 0) cell.setBackground('#e6f4ea');
      }
    }
  }
  if (blockHeaderRows[2]) {
    const devStart = blockHeaderRows[2] + 1;
    for (let r = 0; r < workerList.length; r++) {
      for (let c = 0; c < sortedDates.length; c++) {
        if (sortedDates[c] > todayKey) continue;
        const v = devMatrix[workerList[r]][sortedDates[c]];
        if (v === undefined) continue;
        const cell = sh.getRange(devStart + 1 + r, c + 2);
        if (v < -0.5)      cell.setBackground('#f4cccc');
        else if (v < 0)    cell.setBackground('#fce5cc');
        else if (v > 0.1)  cell.setBackground('#d9ead3');
      }
    }
  }

  // ===== ФИКС ФОРМАТА: числа вместо дат =====
  // Принудительно числовой формат для всех блоков, кроме шапок с датами
  sh.getRange(1, 1, lines.length, totalCols).setNumberFormat('0.00');
  // Вернуть формат даты для строк-шапок с датами (где в столбце A «Сборщик»)
  const dateHeaderRows = [];
  for (let i = 0; i < lines.length; i++) {
    const v = String(lines[i][0] || '');
    if (v === 'Сборщик') dateHeaderRows.push(i + 1);
  }
  dateHeaderRows.forEach(r => {
    sh.getRange(r, 2, 1, totalCols - 2).setNumberFormat('dd.MM');
  });

  sh.setColumnWidth(1, 140);
  for (let c = 2; c <= totalCols; c++) sh.setColumnWidth(c, 60);
  applyFreeze(sh, undefined, 1);
}

function writeStatusSheet(ss, statusMap, orders, workDays, tz) {
  let sh = ss.getSheetByName(SHEET_STATUS);
  if (!sh) sh = ss.insertSheet(SHEET_STATUS);
  sh.clear();

  const header = ['ID','Проект','Изделие','Всего изделий','Всего операций',
                  'Сделано операций','Осталось операций','Выполнено %',
                  'Прогноз завершения','Дата по заказу','Статус'];
  const rows = [header];

  orders.forEach(order => {
    const st = statusMap[order.id];
    if (!st) return;

    const required = st.requiredOps;
    const done     = st.doneOps;
    const planned  = st.plannedOps;
    const left     = required - done;
    const pct      = required ? Math.round((done / required) * 100) : 0;

    let forecast = '—';
    let status;

    if (st.noOps || required === 0) {
      status = 'Нет операций';
    } else if (left <= 0) {
      forecast = 'Выполнено';
      status = 'Готово';
    } else if (planned === 0) {
      forecast = 'Не запланировано';
      status = 'Нет плана';
    } else {
      forecast = Utilities.formatDate(workDays[st.lastDayIdx], tz, 'dd.MM.yyyy');
      status = 'В работе';

      const dueDate = new Date(order.due);
      if (!isNaN(dueDate)) {
        const [d, m, y] = forecast.split('.').map(Number);
        const fDate = new Date(y, m - 1, d);
        if (fDate > dueDate) status = 'Риск опоздания';
      }
    }

    rows.push([
      order.id,
      order.project, order.item, order.qty,
      required, done, left,
      required ? pct + '%' : '—',
      forecast, order.due, status
    ]);
  });

  sh.getRange(1, 1, rows.length, rows[0].length).setValues(rows);
  sh.getRange(1, 1, 1, rows[0].length).setFontWeight('bold');
  applyFreeze(sh, 1);
  for (let c = 1; c <= rows[0].length; c++) sh.setColumnWidth(c, 130);
  sh.setColumnWidth(3, 120);
  sh.setColumnWidth(11, 150);

  for (let r = 2; r <= rows.length; r++) {
    const status = rows[r - 1][10];
    if (status === 'Готово')              sh.getRange(r, 11).setBackground('#d9ead3');
    else if (status === 'Риск опоздания') sh.getRange(r, 11).setBackground('#f4cccc');
    else if (status === 'Нет плана')      sh.getRange(r, 11).setBackground('#fff2cc');
    else if (status === 'Нет операций')   sh.getRange(r, 11).setBackground('#fce5cd');
  }
}

// =========================================================
// ОТЧЁТ ПО ПРОЕКТАМ
// =========================================================
function buildProjectReports(ss) {
  const shOrders  = ss.getSheetByName(SHEET_ORDERS);
  const shOps     = ss.getSheetByName(SHEET_OPS);
  const shFact    = ss.getSheetByName(SHEET_FACT);
  const shWorkers = ss.getSheetByName(SHEET_WORKERS);
  const shOut     = ss.getSheetByName(SHEET_OUT);
  const shHol     = ss.getSheetByName(SHEET_HOLIDAYS);
  if (!shOrders || !shOps || !shFact || !shOut) return null;

  const tz = Session.getScriptTimeZone();
  applySettings(ss);

  const holidays = readHolidays(shHol);
  const workDays = buildWorkCalendar(holidays);
  const todayKey = dateKey(workDays[0]);

  const ordData = shOrders.getLastRow() > 1
    ? shOrders.getRange(2, 1, shOrders.getLastRow() - 1, 6).getValues()
    : [];
  const orders = ordData
    .filter(r => String(r[ORD.id - 1]).trim() && String(r[ORD.project - 1]).trim())
    .map(r => ({
      id: String(r[ORD.id - 1]).trim(),
      project: String(r[ORD.project - 1]).trim(),
      item: String(r[ORD.item - 1]).trim(),
      qty: parseQty(r[ORD.qty - 1]),
      due: r[ORD.due - 1],
      priority: Number(r[ORD.priority - 1]) || 999
    }));

  const opsData = shOps.getLastRow() > 1
    ? shOps.getRange(2, 1, shOps.getLastRow() - 1, 7).getValues()
    : [];
  const opsByItem = {};
  opsData.forEach(r => {
    const item = String(r[OPS.item - 1]).trim();
    if (!item) return;
    const op = String(r[OPS.op - 1]).trim();
    if (!op) return;
    const perItem = Number(r[OPS.perItem - 1]) || 1;
    const order = Number(r[OPS.order - 1]) || 999;
    const norm = Number(r[OPS.norm - 1]) || 1;
    const blocked = Number(r[OPS.blocked - 1]) || 0;
    (opsByItem[item] = opsByItem[item] || []).push({
      op: op,
      perItem: perItem,
      order: order,
      norm: norm,
      blocked: blocked
    });
  });
  Object.keys(opsByItem).forEach(item => {
    opsByItem[item].sort((a, b) => a.order - b.order);
  });

  const factData = shFact.getLastRow() > 1
    ? shFact.getRange(2, 1, shFact.getLastRow() - 1, 9).getValues()
    : [];

  const planRowsData = shOut.getLastRow() > 1
    ? shOut.getRange(2, 1, shOut.getLastRow() - 1, 7).getValues()
    : [];

  const workersAll = shWorkers.getLastRow() > 1
    ? shWorkers.getRange(2, 1, shWorkers.getLastRow() - 1, 5).getValues().map(r => ({
        name: String(r[WRK.name - 1]).trim(),
        status: String(r[WRK.status - 1]).trim().toLowerCase(),
        perf: Number(r[WRK.perf - 1]) || 1,
        projects: String(r[WRK.projects - 1] || '').trim()
      }))
    : [];

  const projects = {};
  orders.forEach(order => {
    (projects[order.project] = projects[order.project] || []).push(order);
  });

  const usedSheetNames = new Set();
  const adviceByOrder = {};
  const skippedProjects = [];
  const planQtyByOrder = {};
  planRowsData.forEach(r => {
    const id = String(r[1] || '').trim();
    if (id) planQtyByOrder[id] = (planQtyByOrder[id] || 0) + (Number(r[5]) || 0);
  });
  Object.keys(projects).forEach(projectName => {
    // Отчёт только если у проекта есть план работ: есть операции и в «Заданиях на день» есть строки
    const hasOps  = projects[projectName].some(o => (opsByItem[o.item] || []).length > 0);
    const hasPlan = projects[projectName].some(o => planQtyByOrder[o.id] > 0);
    if (!hasOps || !hasPlan) {
      skippedProjects.push(projectName);
      return;
    }

    const prefix = 'Отчёт: ';
    const maxLen = 31; // ограничение Google Sheets на длину имени листа
    const projectPart = String(projectName).replace(/[\[\]*?:\/\\]/g, '_');
    let sheetName = (prefix + projectPart).slice(0, maxLen);
    const baseName = sheetName;
    for (let n = 2; usedSheetNames.has(sheetName); n++) {
      const suf = ' (' + n + ')';
      sheetName = baseName.slice(0, maxLen - suf.length) + suf;
    }
    usedSheetNames.add(sheetName);

    let sh = ss.getSheetByName(sheetName);
    if (!sh) {
      sh = ss.insertSheet(sheetName);
    } else {
      sh.clear();
    }

    const projectOrders = projects[projectName];
    const orderIdsInProject = new Set(projectOrders.map(o => o.id));

    const projectDateSet = new Set();

    planRowsData.forEach(r => {
      if (String(r[2] || '').trim() !== projectName) return;
      const d = parseAnyDate(r[0]);
      if (!d) return;
      projectDateSet.add(dateKey(d));
    });

    factData.forEach(r => {
      if (String(r[FACT.project - 1] || '').trim() !== projectName) return;
      const d = parseAnyDate(r[FACT.date - 1]);
      if (!d) return;
      projectDateSet.add(dateKey(d));
    });

    let maxDue = null;
    projectOrders.forEach(o => {
      const d = parseAnyDate(o.due);
      if (!d) return;
      if (maxDue === null || d > maxDue) maxDue = d;
    });

    if (maxDue) {
      const maxDueKey = dateKey(new Date(maxDue.getTime() + 14 * 86400000));
      workDays.forEach(d => {
        const dk = dateKey(d);
        if (dk >= todayKey && dk <= maxDueKey) projectDateSet.add(dk);
      });
    } else {
      workDays.forEach(d => {
        const dk = dateKey(d);
        if (dk >= todayKey) projectDateSet.add(dk);
      });
    }

    const dateList = [...projectDateSet].sort((a, b) => new Date(a) - new Date(b));
    const dateSet = new Set(dateList);

    const reportRows = [];
    projectOrders.forEach(order => {
      const ops = opsByItem[order.item] || [];
      ops.forEach(op => {
        reportRows.push({
          orderId: order.id,
          item: order.item,
          op: op.op,
          perItem: op.perItem,
          order: op.order,
          norm: op.norm,
          required: order.qty * op.perItem,
          qty: order.qty,
          blocked: op.blocked
        });
      });
    });
    reportRows.sort((a, b) => {
      if (a.orderId !== b.orderId) return a.orderId.localeCompare(b.orderId, 'ru');
      return a.order - b.order;
    });

    const planMatrix = {};

    factData.forEach(r => {
      const dateRaw = r[FACT.date - 1];
      const id      = String(r[FACT.id - 1] || '').trim();
      const op      = String(r[FACT.op - 1] || '').trim();
      const plan    = Number(r[FACT.plan - 1]) || 0;
      if (!id || !op || plan <= 0) return;
      if (!orderIdsInProject.has(id)) return;
      const d = parseAnyDate(dateRaw);
      if (!d) return;
      const dk = dateKey(d);
      if (dk > todayKey) return;
      if (!dateSet.has(dk)) return;

      const k = id + '|' + op;
      (planMatrix[k] = planMatrix[k] || {});
      planMatrix[k][dk] = (planMatrix[k][dk] || 0) + plan;
    });

    planRowsData.forEach(r => {
      const dateRaw = r[0];
      const id      = String(r[1] || '').trim();
      const op      = String(r[4] || '').trim();
      const qty     = Number(r[5]) || 0;
      if (!id || !op || qty <= 0) return;
      if (!orderIdsInProject.has(id)) return;
      const d = parseAnyDate(dateRaw);
      if (!d) return;
      const dk = dateKey(d);
      if (dk <= todayKey) return;
      if (!dateSet.has(dk)) return;

      const k = id + '|' + op;
      (planMatrix[k] = planMatrix[k] || {});
      planMatrix[k][dk] = (planMatrix[k][dk] || 0) + qty;
    });

    // ---- отчёт по связке изделие — операция (ID партий не учитываем) ----
    const itemOrders = [];
    const seenItems = {};
    projectOrders.forEach(o => {
      let io = seenItems[o.item];
      if (!io) { io = seenItems[o.item] = { id: o.item, item: o.item, qty: 0, project: o.project }; itemOrders.push(io); }
      io.qty += o.qty;
    });
    const reportRowsI = [];
    itemOrders.forEach(io => {
      (opsByItem[io.item] || []).forEach(op => {
        reportRowsI.push({ orderId: io.id, item: io.item, op: op.op, perItem: op.perItem, order: op.order,
                           norm: op.norm, required: io.qty * op.perItem, qty: io.qty, blocked: op.blocked });
      });
    });
    reportRowsI.sort((a, b) => (a.orderId !== b.orderId) ? a.orderId.localeCompare(b.orderId, 'ru') : a.order - b.order);

    const planMatrixI = {};
    const factMatrixI = {};
    const factByPIOp = {};
    factData.forEach(r => {
      if (String(r[FACT.project - 1] || '').trim() !== projectName) return;
      const item = String(r[FACT.item - 1] || '').trim();
      const op   = String(r[FACT.op - 1] || '').trim();
      if (!item || !op) return;
      const d = parseAnyDate(r[FACT.date - 1]);
      if (!d) return;
      const dk = dateKey(d);
      if (!dateSet.has(dk)) return;
      const plan = Number(r[FACT.plan - 1]) || 0;
      const qty  = Number(r[FACT.qty - 1]) || 0;
      const k = item + '|' + op;
      if (plan > 0 && dk <= todayKey) {
        (planMatrixI[k] = planMatrixI[k] || {});
        planMatrixI[k][dk] = (planMatrixI[k][dk] || 0) + plan;
      }
      if (qty > 0) {
        (factMatrixI[k] = factMatrixI[k] || {});
        factMatrixI[k][dk] = (factMatrixI[k][dk] || 0) + qty;
        const pk = projectName + KSEP + item + KSEP + op;
        factByPIOp[pk] = (factByPIOp[pk] || 0) + qty;
      }
    });
    planRowsData.forEach(r => {
      if (String(r[2] || '').trim() !== projectName) return;
      const item = String(r[3] || '').trim();
      const op   = String(r[4] || '').trim();
      const qty  = Number(r[5]) || 0;
      if (!item || !op || qty <= 0) return;
      const d = parseAnyDate(r[0]);
      if (!d) return;
      const dk = dateKey(d);
      if (dk <= todayKey || !dateSet.has(dk)) return;
      const k = item + '|' + op;
      (planMatrixI[k] = planMatrixI[k] || {});
      planMatrixI[k][dk] = (planMatrixI[k][dk] || 0) + qty;
    });
    const factTotalsI = {};
    Object.keys(factMatrixI).forEach(k => {
      let sum = 0;
      Object.keys(factMatrixI[k]).forEach(dk => sum += factMatrixI[k][dk]);
      factTotalsI[k] = sum;
    });
    // Факт по партиям (для планирования ресурсов): раскладка по срокам, ID в факте не нужен
    const factTotals = allocateFact(factByPIOp, groupBatches(projectOrders), opsByItem);

    const header = ['Изделие — Операция',
      ...dateList.map(dk => {
        const [y, m, d] = dk.split('-');
        return d + '.' + m;
      }),
      'Итого'];

    const lines = [];
    const styles = [];

    lines.push(['ОТЧЁТ ПО ПРОЕКТУ ' + projectName]);
    styles.push('title');
    lines.push([]);
    styles.push('space');

    lines.push(['ПЛАН']);
    styles.push('section');
    lines.push(header.slice());
    styles.push('header');

    reportRowsI.forEach(rr => {
      const k = rr.orderId + '|' + rr.op;
      const byPlan = planMatrixI[k] || {};
      const row = [rr.orderId + ' — ' + rr.op];
      let total = 0;
      dateList.forEach(dk => {
        const v = byPlan[dk] || 0;
        row.push(v > 0 ? v : '');
        total += v;
      });
      row.push(total);
      lines.push(row);
      styles.push({ type: 'op', orderId: rr.orderId, op: rr.op });
    });

    {
      const row = ['Итого по дню'];
      let grand = 0;
      dateList.forEach(dk => {
        let s = 0;
        reportRowsI.forEach(rr => {
          const k = rr.orderId + '|' + rr.op;
          const byPlan = planMatrixI[k] || {};
          s += byPlan[dk] || 0;
        });
        row.push(s > 0 ? s : '');
        grand += s;
      });
      row.push(grand);
      lines.push(row);
      styles.push('total');
    }

    itemOrders.forEach(order => {
      const row = ['ГОТОВЫХ ' + order.id];
      dateList.forEach(dk => {
        const ops = opsByItem[order.item] || [];
        let orderReady = order.qty;
        ops.forEach(op => {
          const k = order.id + '|' + op.op;
          const byPlan = planMatrixI[k] || {};
          let cum = 0;
          for (const d2 of dateList) {
            if (d2 > dk) break;
            cum += byPlan[d2] || 0;
          }
          const items = floorWithTolerance(cum, op.perItem);
          if (items < orderReady) orderReady = items;
        });
        row.push(orderReady);
      });
      const ops = opsByItem[order.item] || [];
      let maxReady = order.qty;
      ops.forEach(op => {
        const k = order.id + '|' + op.op;
        const byPlan = planMatrixI[k] || {};
        let cum = 0;
        Object.keys(byPlan).forEach(d2 => { cum += byPlan[d2]; });
        const items = floorWithTolerance(cum, op.perItem);
        if (items < maxReady) maxReady = items;
      });
      row.push(maxReady);
      lines.push(row);
      styles.push('ready');
    });

    {
      const row = ['Ожидаемый выход'];
      const firstReadyByOrder = {};
      itemOrders.forEach(order => { firstReadyByOrder[order.id] = null; });
      dateList.forEach(dk => {
        itemOrders.forEach(order => {
          if (firstReadyByOrder[order.id]) return;
          const ops = opsByItem[order.item] || [];
          let minItems = order.qty;
          ops.forEach(op => {
            const k = order.id + '|' + op.op;
            const byPlan = planMatrixI[k] || {};
            let cum = 0;
            for (const d2 of dateList) {
              if (d2 > dk) break;
              cum += byPlan[d2] || 0;
            }
            const items = floorWithTolerance(cum, op.perItem);
            if (items < minItems) minItems = items;
          });
          if (minItems >= order.qty) firstReadyByOrder[order.id] = dk;
        });
      });
      dateList.forEach(dk => {
        let hit = false;
        Object.keys(firstReadyByOrder).forEach(id => {
          if (firstReadyByOrder[id] === dk) hit = true;
        });
        row.push(hit ? '✓' : '');
      });
      const cnt = Object.keys(firstReadyByOrder).filter(id => firstReadyByOrder[id]).length;
      row.push(cnt > 0 ? cnt + ' из ' + itemOrders.length : '—');
      lines.push(row);
      styles.push('expected');
    }

    lines.push([]);
    styles.push('space');

    lines.push(['ФАКТ']);
    styles.push('section');
    lines.push(header.slice());
    styles.push('header');

    reportRowsI.forEach(rr => {
      const k = rr.orderId + '|' + rr.op;
      const byFact = factMatrixI[k] || {};
      const row = [rr.orderId + ' — ' + rr.op];
      let total = 0;
      dateList.forEach(dk => {
        const v = byFact[dk] || 0;
        row.push(v > 0 ? v : '');
        total += v;
      });
      row.push(total);
      lines.push(row);
      styles.push({ type: 'op', orderId: rr.orderId, op: rr.op });
    });

    {
      const row = ['Итого по дню'];
      let grand = 0;
      dateList.forEach(dk => {
        let s = 0;
        reportRowsI.forEach(rr => {
          const k = rr.orderId + '|' + rr.op;
          const byFact = factMatrixI[k] || {};
          s += byFact[dk] || 0;
        });
        row.push(s > 0 ? s : '');
        grand += s;
      });
      row.push(grand);
      lines.push(row);
      styles.push('total');
    }

    itemOrders.forEach(order => {
      const row = ['ГОТОВЫХ ' + order.id];
      dateList.forEach(dk => {
        const ops = opsByItem[order.item] || [];
        let orderReady = order.qty;
        ops.forEach(op => {
          const k = order.id + '|' + op.op;
          const byFact = factMatrixI[k] || {};
          let cum = 0;
          for (const d2 of dateList) {
            if (d2 > dk) break;
            cum += byFact[d2] || 0;
          }
          const items = floorWithTolerance(cum, op.perItem);
          if (items < orderReady) orderReady = items;
        });
        row.push(orderReady);
      });
      const ops = opsByItem[order.item] || [];
      let maxReady = order.qty;
      ops.forEach(op => {
        const k = order.id + '|' + op.op;
        const byFact = factMatrixI[k] || {};
        let cum = 0;
        Object.keys(byFact).forEach(d2 => { cum += byFact[d2]; });
        const items = floorWithTolerance(cum, op.perItem);
        if (items < maxReady) maxReady = items;
      });
      row.push(maxReady);
      lines.push(row);
      styles.push('ready');
    });

    {
      const row = ['Фактический выход'];
      const firstReadyByOrder = {};
      itemOrders.forEach(order => { firstReadyByOrder[order.id] = null; });
      dateList.forEach(dk => {
        itemOrders.forEach(order => {
          if (firstReadyByOrder[order.id]) return;
          const ops = opsByItem[order.item] || [];
          let minItems = order.qty;
          ops.forEach(op => {
            const k = order.id + '|' + op.op;
            const byFact = factMatrixI[k] || {};
            let cum = 0;
            for (const d2 of dateList) {
              if (d2 > dk) break;
              cum += byFact[d2] || 0;
            }
            const items = floorWithTolerance(cum, op.perItem);
            if (items < minItems) minItems = items;
          });
          if (minItems >= order.qty) firstReadyByOrder[order.id] = dk;
        });
      });
      dateList.forEach(dk => {
        let hit = false;
        Object.keys(firstReadyByOrder).forEach(id => {
          if (firstReadyByOrder[id] === dk) hit = true;
        });
        row.push(hit ? '✓' : '');
      });
      const cnt = Object.keys(firstReadyByOrder).filter(id => firstReadyByOrder[id]).length;
      row.push(cnt > 0 ? cnt + ' из ' + itemOrders.length : '—');
      lines.push(row);
      styles.push('expected');
    }

    lines.push([]);
    styles.push('space');

    lines.push(['ОТКЛОНЕНИЕ (факт − план)']);
    styles.push('section');
    lines.push(header.slice());
    styles.push('header');

    reportRowsI.forEach(rr => {
      const k = rr.orderId + '|' + rr.op;
      const byPlan = planMatrixI[k] || {};
      const byFact = factMatrixI[k] || {};
      const row = [rr.orderId + ' — ' + rr.op];
      let total = 0;
      dateList.forEach(dk => {
        if (dk > todayKey) {
          row.push('');
          return;
        }
        const p = byPlan[dk];
        const f = byFact[dk];
        if (p === undefined && f === undefined) {
          row.push('');
        } else {
          const dev = (f || 0) - (p || 0);
          row.push(dev);
          total += dev;
        }
      });
      row.push(total);
      lines.push(row);
      styles.push({ type: 'dev', orderId: rr.orderId, op: rr.op });
    });

    {
      const row = ['Итого по дню'];
      let grand = 0;
      dateList.forEach(dk => {
        if (dk > todayKey) {
          row.push('');
          return;
        }
        let s = 0;
        reportRowsI.forEach(rr => {
          const k = rr.orderId + '|' + rr.op;
          const byPlan = planMatrixI[k] || {};
          const byFact = factMatrixI[k] || {};
          const p = byPlan[dk];
          const f = byFact[dk];
          if (p === undefined && f === undefined) return;
          s += (f || 0) - (p || 0);
        });
        row.push(s);
        grand += s;
      });
      row.push(grand);
      lines.push(row);
      styles.push('total');
    }

    lines.push([]);
    styles.push('space');

    // ===========================
    // ПРИЧИНЫ ОТСТАВАНИЯ (факт меньше плана; причины — из столбца I листа «Факт»)
    // ===========================
    {
      const groups = {};
      let noReason = 0, noReasonLost = 0;
      factData.forEach(r => {
        if (String(r[FACT.project - 1] || '').trim() !== projectName) return;
        const rawQty = r[FACT.qty - 1];
        if (rawQty === '' || rawQty === null || isNaN(Number(rawQty))) return; // факт не внесён
        const plan = Number(r[FACT.plan - 1]) || 0;
        const qty = Number(rawQty);
        if (qty >= plan) return;                                               // отставание = сделали меньше плана
        const d = parseAnyDate(r[FACT.date - 1]);
        if (!d) return;
        const lost = plan - qty;
        const reason = String(r[FACT.reason - 1] || '').trim();
        if (!reason) { noReason++; noReasonLost += lost; return; }
        const key = reason.toLowerCase().replace(/\s+/g, ' ');
        const g = groups[key] = groups[key] || { text: reason, cases: 0, lost: 0, items: [] };
        g.cases++;
        g.lost += lost;
        g.items.push({
          t: d.getTime(),
          text: Utilities.formatDate(d, tz, 'dd.MM') + ' ' + String(r[FACT.op - 1] || '').trim() +
                (String(r[FACT.worker - 1] || '').trim() ? ' (' + String(r[FACT.worker - 1]).trim() + ')' : '') +
                ' −' + lost
        });
      });
      const list = Object.keys(groups).map(k => groups[k]).sort((a, b) => b.lost - a.lost);
      if (list.length || noReason) {
        lines.push(['ПРИЧИНЫ ОТСТАВАНИЯ']);
        styles.push('section');
        list.forEach(g => {
          g.items.sort((a, b) => a.t - b.t);
          const shown = g.items.slice(0, 4).map(x => x.text).join('; ');
          const more = g.items.length > 4 ? '; …и ещё ' + (g.items.length - 4) : '';
          lines.push([g.text, 'случаев: ' + g.cases + ', недовыполнено: ' + g.lost + ' опер.\n' + shown + more]);
          styles.push({ type: 'reasons' });
        });
        if (noReason) {
          lines.push(['Причина не указана', 'случаев: ' + noReason + ', недовыполнено: ' + noReasonLost +
                      ' опер. Впишите причину на листе «Факт» (жёлтые ячейки).']);
          styles.push({ type: 'reasons', warn: true });
        }
        lines.push([]);
        styles.push('space');
      }
    }

    // ===========================
    // ПЛАНИРОВАНИЕ РЕСУРСОВ
    // ===========================
    lines.push(['ПЛАНИРОВАНИЕ РЕСУРСОВ']);
    styles.push('section');

    function workersForOrder(orderId) {
      const oid = normId(orderId);
      return workersAll.filter(w => {
        if (w.status !== 'доступен') return false;
        const p = String(w.projects || '').trim();
        if (!p) return true;
        const list = p.split(',').map(s => normId(s)).filter(Boolean);
        return list.includes(oid);
      });
    }

    const sortedOrdersForResources = [...projectOrders].sort((a, b) => {
      const da = parseAnyDate(a.due);
      const db = parseAnyDate(b.due);
      if (!da && !db) return 0;
      if (!da) return 1;
      if (!db) return -1;
      return da - db;
    });

    function getPlanEndDateKey(order) {
      let lastKey = null;
      const ops = opsByItem[order.item] || [];
      ops.forEach(op => {
        const k = order.id + '|' + op.op;
        const byPlan = planMatrix[k] || {};
        Object.keys(byPlan).forEach(dk => {
          if (byPlan[dk] > 0 && (lastKey === null || dk > lastKey)) {
            lastKey = dk;
          }
        });
      });
      return lastKey;
    }

    function getNormoDaysOnDay(order, dk) {
      let total = 0;
      const ops = opsByItem[order.item] || [];
      ops.forEach(op => {
        const k = order.id + '|' + op.op;
        const byPlan = planMatrix[k] || {};
        const v = byPlan[dk] || 0;
        if (v > 0 && op.norm > 0) {
          total += v / op.norm;
        }
      });
      return total;
    }

    const perOrderStats = [];
    let nextStartKey = todayKey;
    const workersBusyByDate = {};

    sortedOrdersForResources.forEach(order => {
      let remainingNormoDays = 0;
      reportRows.forEach(rr => {
        if (rr.orderId !== order.id) return;
        const k = rr.orderId + '|' + rr.op;
        const factTotal = factTotals[k] || 0;
        const availableItems = Math.max(0, rr.qty - (rr.blocked || 0));
        const availableOps = availableItems * rr.perItem;
        const remaining = Math.max(0, availableOps - factTotal);
        remainingNormoDays += remaining / rr.norm;
      });
      remainingNormoDays = +remainingNormoDays.toFixed(2);

      const orderWorkers = workersForOrder(order.id);
      const totalWorkersForOrder = orderWorkers.length;

      const dueDate = parseAnyDate(order.due);
      const startKey = nextStartKey;

      let workDaysUntilDue = 0;
      const dueDateKeys = [];
      if (dueDate) {
        const dueKey = dateKey(dueDate);
        workDays.forEach(d => {
          const dk = dateKey(d);
          if (dk >= startKey && dk <= dueKey) {
            workDaysUntilDue++;
            dueDateKeys.push(dk);
          }
        });
      }

      let workersBusyOnThisOrderDays = 0;
      if (dueDateKeys.length > 0) {
        // Среднее по ВСЕМ дням окна (дни без записей = 0 занятых). Раньше делили только на дни с записями,
        // и одного пересечения с прошлым заказом хватало, чтобы «занять» всю бригаду.
        let sumBusy = 0;
        dueDateKeys.forEach(dk => { sumBusy += workersBusyByDate[dk] || 0; });
        workersBusyOnThisOrderDays = Math.round(sumBusy / dueDateKeys.length);
      }

      const workersAvailable = Math.max(0, totalWorkersForOrder - workersBusyOnThisOrderDays);

      let shiftsRemaining = '—';
      let success = '—';
      let neededWorkers = '—';
      let needToAdd = '—';

      if (remainingNormoDays > 0 && workDaysUntilDue > 0) {
        // средняя выработка сборщика заказа, нормо-дней в день
        const avgPerfOrder = orderWorkers.length
          ? orderWorkers.reduce((sum, w) => sum + (w.perf || 1), 0) / orderWorkers.length
          : 1;
        const requiredWorkersForOrder = Math.ceil(
          remainingNormoDays / (avgPerfOrder * workDaysUntilDue) - 1e-9
        );
        neededWorkers = requiredWorkersForOrder;

        if (workersAvailable > 0) {
          shiftsRemaining = +(remainingNormoDays / (workersAvailable * avgPerfOrder)).toFixed(2);
        } else {
          shiftsRemaining = '∞';
        }

        if (workersAvailable >= requiredWorkersForOrder) {
          success = 'ДА';
          needToAdd = 0;
        } else {
          success = 'НЕТ';
          needToAdd = requiredWorkersForOrder - workersAvailable;
        }

        const workersUsedNow = Math.min(workersAvailable, requiredWorkersForOrder);
        dueDateKeys.forEach(dk => {
          workersBusyByDate[dk] = (workersBusyByDate[dk] || 0) + workersUsedNow;
        });
      } else if (remainingNormoDays === 0) {
        shiftsRemaining = 0;
        success = 'ВЫПОЛНЕНО';
        neededWorkers = 0;
        needToAdd = 0;
      }

      const planEndKey = getPlanEndDateKey(order);
      if (planEndKey) {
        const idx = workDays.findIndex(d => dateKey(d) === planEndKey);
        if (idx >= 0 && idx < workDays.length - 1) {
          nextStartKey = dateKey(workDays[idx + 1]);
        } else {
          nextStartKey = planEndKey;
        }
      } else if (dueDate) {
        nextStartKey = dateKey(dueDate);
      }

      // ===== КРИТИЧЕСКИЙ ПУТЬ =====
      const critPathEndKey = planEndKey;

      let critPathSuccess = '—';
      if (critPathEndKey && dueDate) {
        critPathSuccess = (critPathEndKey <= dateKey(dueDate)) ? 'ДА' : 'НЕТ';
      }

      let critPathDelayDays = 0;
      if (critPathEndKey && dueDate) {
        const dueKey = dateKey(dueDate);
        if (critPathEndKey > dueKey) {
          const wdSet = new Set(workDays.map(d => dateKey(d)));
          const pd = k => { const a = k.split('-').map(Number); return new Date(a[0], a[1] - 1, a[2]); };
          const cur = pd(dueKey), endD = pd(critPathEndKey);
          cur.setDate(cur.getDate() + 1);
          while (cur <= endD) {
            const k = dateKey(cur);
            const wd = cur.getDay();
            if (wdSet.has(k) || (k < todayKey && wd !== 0 && wd !== 6)) critPathDelayDays++;
            cur.setDate(cur.getDate() + 1);
          }
        }
      }

      // ===== ТОП-3 УЗКИХ МЕСТА =====
      let critPathBottlenecks = '';
      let critPathRecommendations = '';
      let critPathUrgency = 'green'; // green / yellow / red

      if (planEndKey) {
        const ops = opsByItem[order.item] || [];

        const dayToOrders = {};
        ops.forEach(op => {
          const k = order.id + '|' + op.op;
          const byPlan = planMatrix[k] || {};
          Object.keys(byPlan).forEach(dk => {
            if (byPlan[dk] > 0) {
              (dayToOrders[dk] = dayToOrders[dk] || new Set()).add(op.order);
            }
          });
        });

        let totalNormoAll = 0, opCount = 0;
        ops.forEach(op => {
          const k = order.id + '|' + op.op;
          const byPlan = planMatrix[k] || {};
          let s = 0;
          Object.keys(byPlan).forEach(dk => { s += byPlan[dk]; });
          if (s > 0 && op.norm > 0) { totalNormoAll += s / op.norm; opCount++; }
        });
        const avgNormo = opCount > 0 ? totalNormoAll / opCount : 0;

        const idxEnd = workDays.findIndex(d => dateKey(d) === planEndKey);
        const lastTwoDays = new Set();
        for (let i = Math.max(0, idxEnd - 1); i <= idxEnd; i++) {
          lastTwoDays.add(dateKey(workDays[i]));
        }

        const candidates = [];

        ops.forEach(op => {
          const k = order.id + '|' + op.op;
          const byPlan = planMatrix[k] || {};
          let totalQty = 0;
          let firstDay = null, lastDay = null;
          const activeDays = [];
          Object.keys(byPlan).forEach(dk => {
            const v = byPlan[dk];
            if (v > 0) {
              totalQty += v;
              activeDays.push(dk);
              if (!firstDay || dk < firstDay) firstDay = dk;
              if (!lastDay || dk > lastDay) lastDay = dk;
            }
          });
          if (totalQty <= 0 || op.norm <= 0) return;

          const normoDays = totalQty / op.norm;
          const daysCount = activeDays.length;

          let score = 0;
          const reasons = [];
          let recommendation = '';

          // 1. Наложение порядков
          const neighborOrders = new Set();
          activeDays.forEach(dk => {
            const oo = dayToOrders[dk];
            if (oo) for (const o of oo) if (o !== op.order) neighborOrders.add(o);
          });
          if (neighborOrders.size === 1) {
            score += 1;
            reasons.push('наложение с порядком ' + [...neighborOrders][0]);
          } else if (neighborOrders.size >= 2) {
            score += 3;
            reasons.push('наложение с порядками ' + [...neighborOrders].sort((a,b)=>a-b).join(','));
          }

          // 2. Одна в своём порядке
          const sameOrderOps = ops.filter(x => x.order === op.order);
          const isAlone = sameOrderOps.length === 1;
          if (isAlone && daysCount >= 3) {
            score += 4;
            reasons.push('одна в порядке, идёт ' + daysCount + ' дн.');
          } else if (isAlone && daysCount === 2) {
            score += 2;
            reasons.push('одна в порядке, идёт 2 дн.');
          }

          // 3. Трудоёмкость
          let ratio = 0;
          if (avgNormo > 0) {
            ratio = normoDays / avgNormo;
            if (ratio >= 3) { score += 5; reasons.push('трудоёмкость ×' + ratio.toFixed(1)); }
            else if (ratio >= 2) { score += 3; reasons.push('трудоёмкость ×' + ratio.toFixed(1)); }
          }

          // 4. Последние 2 дня
          let inLastTwo = false;
          activeDays.forEach(dk => { if (lastTwoDays.has(dk)) inLastTwo = true; });
          if (inLastTwo) { score += 3; reasons.push('в последние 2 дня'); }

          // 5. Поздний порядок
          if (op.order >= 8) score += 2;

          // ---- РЕКОМЕНДАЦИЯ ----
          // Кто уже стоит на операции
          const workersOnOp = new Set();
          factData.forEach(r => {
            const id = String(r[FACT.id - 1] || '').trim();
            const opName = String(r[FACT.op - 1] || '').trim();
            const worker = String(r[FACT.worker - 1] || '').trim();
            const plan = Number(r[FACT.plan - 1]) || 0;
            if (id === order.id && opName === op.op && plan > 0 && worker) {
              workersOnOp.add(worker);
            }
          });

          const alreadyWorkers = workersOnOp.size || 1;
          const targetDays = Math.max(1, Math.ceil(daysCount / 2));
          const wantWorkers = Math.ceil(normoDays / targetDays - 1e-9);
          const room = Math.max(0, MAX_WORKERS_PER_OP - alreadyWorkers);
          const addWorkers = Math.min(room, Math.max(0, wantWorkers - alreadyWorkers));

          if (ratio >= 2 && addWorkers > 0) {
            recommendation = 'добавить ' + addWorkers + ' чел. на ' +
                             firstDay.split('-').reverse().slice(0,2).join('.');
          } else if (isAlone && daysCount >= 2) {
            const parts = Math.min(daysCount, 5);
            recommendation = 'разбить партию на ' + parts + ' ' + (parts < 5 ? 'части' : 'частей') +
                             (addWorkers > 0 ? ' или добавить ' + addWorkers + ' чел.' : '');
          } else if (neighborOrders.size >= 2) {
            const firstNeighbor = [...neighborOrders].sort((a,b)=>a-b)[0];
            recommendation = 'перекинуть ' + Math.max(1, Math.ceil(addWorkers/2)) +
                             ' чел. с порядка ' + firstNeighbor + ' на ' +
                             firstDay.split('-').reverse().slice(0,2).join('.');
          } else if (inLastTwo) {
            const idxFirst = workDays.findIndex(d => dateKey(d) === firstDay);
            if (idxFirst > 0) {
              const prevDay = dateKey(workDays[idxFirst - 1]);
              recommendation = 'начать раньше: сдвинуть с ' +
                               firstDay.split('-').reverse().slice(0,2).join('.') + ' на ' +
                               prevDay.split('-').reverse().slice(0,2).join('.');
            }
          }
          if (!recommendation) recommendation = 'взять на контроль';

          if (score > 0) {
            candidates.push({
              firstDay, lastDay, op: op.op, orderNum: op.order,
              normoDays, daysCount, score, reasons, recommendation
            });
          }
        });

        candidates.sort((a, b) => b.score - a.score);
        const top = candidates.slice(0, 3);

        if (top.length) {
          critPathBottlenecks = top.map((b, i) => {
            const [y1, m1, d1] = b.firstDay.split('-');
            const [y2, m2, d2] = b.lastDay.split('-');
            const dateStr = (b.firstDay === b.lastDay)
              ? d1 + '.' + m1
              : d1 + '.' + m1 + '–' + d2 + '.' + m2;
            const mainReason = b.reasons[0] || '';
            return (i + 1) + ') ' + dateStr + ' — ' + b.op +
                   ' (порядок ' + b.orderNum + ')' +
                   ' — ' + b.normoDays.toFixed(2) + ' нормо-дн. [' + mainReason + ']';
          }).join('\n');

          critPathRecommendations = top.map((b, i) => {
            return (i + 1) + ') ' + b.recommendation;
          }).join('\n');

          // Цветовая метка: красный — есть отставание, жёлтый — есть узкие места
          if (critPathDelayDays > 0) critPathUrgency = 'red';
          else critPathUrgency = 'yellow';
        } else {
          critPathBottlenecks = '—';
          critPathRecommendations = '—';
          critPathUrgency = 'green';
        }
      } else {
        critPathBottlenecks = '—';
        critPathRecommendations = '—';
        critPathUrgency = 'green';
      }

      // Нужно добавить на критический путь (справочно)
      // Сколько людей добавить, чтобы уложить «хвост» (работы после срока) в срок:
      //   хвост (нормо-дни) / (средняя выработка × рабочих дней от сегодня до срока),
      // но не больше, чем можно поставить на операции хвоста (лимит на операцию).
      let critPathNeedToAdd = 0;
      let critPathLateNormoDays = 0;
      let critPathNote = '—';
      let critWindowDays = 0, critWindowStart = '', critWindowEnd = '';
      if (critPathEndKey && critPathDelayDays > 0 && dueDate) {
        const dueKey = dateKey(dueDate);
        let idxDueDay = -1;
        workDays.forEach((d, i) => { if (dateKey(d) <= dueKey) idxDueDay = i; });
        const idxEndDay = workDays.findIndex(d => dateKey(d) === critPathEndKey);
        for (let i = idxDueDay + 1; i <= idxEndDay; i++) {
          critPathLateNormoDays += getNormoDaysOnDay(order, dateKey(workDays[i]));
        }
        critPathLateNormoDays = +critPathLateNormoDays.toFixed(2);

        // окно, в котором дополнительные люди реально могут работать: от старта заказа в плане до срока
        let firstPlanKey = null;
        (opsByItem[order.item] || []).forEach(op => {
          const byPlan = planMatrix[order.id + '|' + op.op] || {};
          Object.keys(byPlan).forEach(dk => {
            if (byPlan[dk] > 0 && (firstPlanKey === null || dk < firstPlanKey)) firstPlanKey = dk;
          });
        });
        const windowStart = (firstPlanKey && firstPlanKey > todayKey) ? firstPlanKey : todayKey;
        const daysToDue = workDays.filter(d => { const k = dateKey(d); return k >= windowStart && k <= dueKey; }).length;
        critWindowDays = daysToDue; critWindowStart = windowStart; critWindowEnd = dueKey;
        const avgPerfCrit = orderWorkers.length
          ? orderWorkers.reduce((sum, w) => sum + (w.perf || 1), 0) / orderWorkers.length
          : 1;

        let tailOps = 0;
        (opsByItem[order.item] || []).forEach(op => {
          const byPlan = planMatrix[order.id + '|' + op.op] || {};
          if (Object.keys(byPlan).some(dk => dk > dueKey && byPlan[dk] > 0)) tailOps++;
        });
        const maxExtra = Math.max(1, tailOps) * MAX_WORKERS_PER_OP;

        if (daysToDue <= 0) {
          critPathNeedToAdd = '—';
          if (dueKey < todayKey) {
            critPathNote = 'Срок уже прошёл: людьми не исправить — сдвиньте срок минимум на ' +
                           critPathDelayDays + ' раб. дн.';
          } else {
            critPathNote = 'Заказ стартует ' + (firstPlanKey ? firstPlanKey.split('-').reverse().slice(0, 2).join('.') : '—') +
                           ', уже после срока: снизьте жёсткость приоритета (лист «Настройки») или сдвиньте срок на ' +
                           critPathDelayDays + ' раб. дн.';
          }
        } else {
          const need = Math.max(1, Math.ceil(critPathLateNormoDays / (avgPerfCrit * daysToDue) - 1e-9));
          critPathNeedToAdd = Math.min(need, maxExtra);
          const parts = [];
          if (firstPlanKey && firstPlanKey > todayKey && strictnessFor(order.priority) > 0) {
            parts.push('Заказ стартует только ' + firstPlanKey.split('-').reverse().slice(0, 2).join('.') +
                       ' из-за приоритета (жёсткость ' + strictnessFor(order.priority) + '%): снизьте жёсткость на листе «Настройки»');
          }
          if (need > maxExtra) {
            parts.push('Людьми не закрыть: сдвиньте срок на ' + critPathDelayDays + ' раб. дн. или разбейте партию');
          }
          if (parts.length) critPathNote = parts.join('\n');
        }
      }

      perOrderStats.push({
        order: order,
        workersOnOrder: totalWorkersForOrder,
        workersAvailable,
        workersBusyOnThisOrderDays,
        remainingNormoDays,
        shiftsRemaining,
        success,
        neededWorkers,
        needToAdd,
        workDaysUntilDue,
        dueDate,
        startKey,
        planEndKey,
        critPathEndKey,
        critPathSuccess,
        critPathNeedToAdd,
        critPathLateNormoDays,
        critPathNote,
        critWindowDays,
        critWindowStart,
        critWindowEnd,
        critPathDelayDays,
        critPathBottlenecks,
        critPathRecommendations,
        critPathUrgency
      });
    });

    // Рекомендация по заказу для листа «Статус заказов» (столбец L)
    const ddmm = k => k ? k.split('-').reverse().slice(0, 2).join('.') : '';
    perOrderStats.forEach(ps => {
      let text = '';
      if (ps.success === 'ВЫПОЛНЕНО') {
        text = '';
      } else if (ps.critPathSuccess === 'ДА') {
        text = 'В срок';
      } else if (ps.critPathSuccess === 'НЕТ') {
        const delay = Number(ps.critPathDelayDays) || 0;
        if (typeof ps.critPathNeedToAdd === 'number' && ps.critPathNeedToAdd > 0) {
          text = 'Добавить ' + ps.critPathNeedToAdd + ' чел. на ' + ps.critWindowDays + ' раб. дн. (' +
                 ddmm(ps.critWindowStart) + '–' + ddmm(ps.critWindowEnd) + '); отставание ' + delay + ' раб. дн.';
          if (ps.critPathNote && ps.critPathNote !== '—') text += ' ' + String(ps.critPathNote).replace(/\n/g, '. ');
        } else {
          text = (ps.critPathNote && ps.critPathNote !== '—')
            ? String(ps.critPathNote).replace(/\n/g, '. ')
            : 'Отставание ' + delay + ' раб. дн.';
        }
      }
      adviceByOrder[ps.order.id] = text;
    });

    perOrderStats.forEach((ps, idx) => {
      const dueStr = ps.dueDate ? Utilities.formatDate(ps.dueDate, tz, 'dd.MM.yyyy') : '—';
      lines.push([`ЗАКАЗ ${ps.order.id} (${ps.order.item}, срок ${dueStr})`]);
      styles.push('subtitle');

      let critPathEndStr = '—';
      if (ps.critPathEndKey) {
        const [y, m, d] = ps.critPathEndKey.split('-');
        critPathEndStr = d + '.' + m + '.' + y;
      }

      const verdict = ps.success === 'ВЫПОЛНЕНО' ? 'ВЫПОЛНЕНО'
        : ((ps.critPathSuccess === 'ДА' || ps.critPathSuccess === 'НЕТ') ? ps.critPathSuccess : ps.success);
      const note = String(ps.critPathNote || '—');
      const actions = cleanRecs(ps.critPathRecommendations).concat(note !== '—' ? [note] : []).join('\n');

      const rows = [['Успеем к сроку', verdict]];
      if (verdict !== 'ВЫПОЛНЕНО') {
        rows.push(['Прогноз завершения', critPathEndStr]);
        if (Number(ps.critPathDelayDays) > 0) {
          rows.push(['Отставание, раб. дней', ps.critPathDelayDays]);
          rows.push(['Нужно добавить людей', ps.critPathNeedToAdd]);
        }
        if (verdict === 'НЕТ') {
          if (ps.critPathBottlenecks && ps.critPathBottlenecks !== '—') rows.push(['Узкие места', ps.critPathBottlenecks]);
          if (actions) rows.push(['Что делать', actions]);
        }
      }
      rows.forEach(row => {
        lines.push(row);
        styles.push({ type: 'resource', label: row[0], urgency: ps.critPathUrgency });
      });

      if (idx < perOrderStats.length - 1) {
        lines.push([]);
        styles.push('space');
      }
    });

    if (perOrderStats.length > 1) {
      lines.push([]);
      styles.push('space');
      lines.push(['СВОДНО ПО ПРОЕКТУ']);
      styles.push('subtitle');

      const verdicts = perOrderStats.map(ps => (ps.success === 'ВЫПОЛНЕНО') ? 'ВЫПОЛНЕНО'
        : ((ps.critPathSuccess === 'ДА' || ps.critPathSuccess === 'НЕТ') ? ps.critPathSuccess : ps.success));
      const summaryVerdict = verdicts.indexOf('НЕТ') >= 0 ? 'НЕТ'
        : (verdicts.every(v => v === 'ВЫПОЛНЕНО') ? 'ВЫПОЛНЕНО' : 'ДА');
      // Люди общие для заказов, поэтому берём максимум, а не сумму
      const summaryAdd = perOrderStats.reduce((m, ps) => {
        const v = Number(ps.critPathNeedToAdd);
        return isNaN(v) ? m : Math.max(m, v);
      }, 0);
      const recLines = [];
      let summaryUrgency = 'green';
      perOrderStats.forEach((ps, k) => {
        if (verdicts[k] !== 'НЕТ') return;
        const recs = cleanRecs(ps.critPathRecommendations);
        if (recs.length) {
          recLines.push('Заказ ' + ps.order.id + ':');
          recs.forEach(l => recLines.push('  ' + l));
        }
      });
      if (recLines.length) {
        summaryUrgency = perOrderStats.some(ps => ps.critPathUrgency === 'red') ? 'red' : 'yellow';
      }

      const summaryRows = [['Успеем к сроку', summaryVerdict]];
      if (summaryAdd > 0) summaryRows.push(['Нужно добавить людей (макс. по заказам)', summaryAdd]);
      if (recLines.length) summaryRows.push(['Что делать (сводно)', recLines.join('\n')]);
      summaryRows.forEach(row => {
        lines.push(row);
        styles.push({ type: 'resource', label: row[0], isSummary: true, urgency: summaryUrgency });
      });
    }

    const totalCols = header.length;
    sh.getRange(1, 1, lines.length, totalCols).setValues(
      lines.map(r => {
        const copy = r.slice();
        while (copy.length < totalCols) copy.push('');
        return copy.slice(0, totalCols);
      })
    );

    applyFreeze(sh, 1);
    applyFreeze(sh, undefined, 1);

    sh.setColumnWidth(1, 330);
    sh.setColumnWidth(2, 95);
    for (let c = 3; c <= totalCols; c++) sh.setColumnWidth(c, 60);

    for (let i = 0; i < lines.length; i++) {
      const st = styles[i];
      const rowNum = i + 1;

      if (st === 'title') {
        sh.getRange(rowNum, 1, 1, totalCols).setFontWeight('bold').setFontSize(14).setBackground('#c9daf8');
      } else if (st === 'subtitle') {
        sh.getRange(rowNum, 1, 1, totalCols)
          .setFontWeight('bold')
          .setBackground('#e6f4ea')
          .setFontStyle('italic');
      } else if (st === 'section') {
        sh.getRange(rowNum, 1, 1, totalCols).setFontWeight('bold').setBackground('#c9daf8').setFontSize(12);
      } else if (st === 'header') {
        sh.getRange(rowNum, 1, 1, totalCols).setFontWeight('bold').setBackground('#d9ead3');
      } else if (st && st.type === 'op') {
        const k = st.orderId + '|' + st.op;
        const factTotal = factTotalsI[k] || 0;
        const rr = reportRowsI.find(x => x.orderId === st.orderId && x.op === st.op);
        if (rr && rr.required > 0 && factTotal >= rr.required - 0.1) {
          sh.getRange(rowNum, 1, 1, totalCols).setBackground('#d9ead3');
        }
      } else if (st === 'total') {
        sh.getRange(rowNum, 1, 1, totalCols).setFontWeight('bold').setBackground('#f3f3f3');
      } else if (st === 'ready') {
        sh.getRange(rowNum, 1, 1, totalCols).setFontWeight('bold').setBackground('#e6f4ea');
      } else if (st === 'expected') {
        sh.getRange(rowNum, 1, 1, totalCols).setFontWeight('bold').setBackground('#e6f4ea').setFontStyle('italic');
      } else if (st && st.type === 'dev') {
        for (let c = 2; c < totalCols; c++) {
          const v = Number(lines[i][c - 1]);
          if (isNaN(v)) continue;
          const dk = dateList[c - 2];
          if (dk > todayKey) continue;
          const cell = sh.getRange(rowNum, c);
          if (v < 0)      cell.setBackground('#f4cccc');
          else if (v > 0) cell.setBackground('#d9ead3');
        }
      } else if (st && st.type === 'reasons') {
        const span = Math.min(10, totalCols - 1);
        sh.getRange(rowNum, 1).setWrap(true).setVerticalAlignment('top').setFontWeight('bold');
        if (span >= 1) {
          const rng = sh.getRange(rowNum, 2, 1, span);
          rng.merge();
          rng.setWrap(true).setVerticalAlignment('top').setHorizontalAlignment('left');
        }
        if (st.warn) sh.getRange(rowNum, 1, 1, span + 1).setBackground('#fff2cc');
        const widthB = 95 + Math.max(0, span - 1) * 60;
        let n = Math.max(1, Math.ceil(String(lines[i][0] || '').length / (330 / 6.5)));
        let nb = 0;
        String(lines[i][1] || '').split('\n').forEach(t => { nb += Math.max(1, Math.ceil(t.length / (widthB / 6.5))); });
        sh.setRowHeight(rowNum, Math.max(21, Math.max(n, nb) * 16 + 4));
      } else if (st && st.type === 'resource') {
        const label = st.label;
        const urgency = st.urgency || 'green';
        const raw = lines[i][1];
        const valCell = sh.getRange(rowNum, 2);
        valCell.setHorizontalAlignment('left');

        if (st.isSummary) sh.getRange(rowNum, 1, 1, totalCols).setBackground('#fff2cc');

        if (label.indexOf('Успеем') === 0) {
          if (raw === 'ДА' || raw === 'ВЫПОЛНЕНО') valCell.setBackground('#d9ead3').setFontWeight('bold');
          else if (raw === 'НЕТ' || raw === 'СРОК ПРОШЁЛ') valCell.setBackground('#f4cccc').setFontWeight('bold');
        } else if (label.indexOf('Нужно добавить') === 0) {
          const v = Number(raw);
          if (!isNaN(v)) valCell.setBackground(v > 0 ? '#fce5cd' : '#d9ead3').setFontWeight('bold');
        } else if (label.indexOf('Отставание') === 0) {
          valCell.setBackground('#f4cccc').setFontWeight('bold');
        } else if (label === 'Узкие места' || label.indexOf('Что делать') === 0) {
          // длинный текст: объединяем B…K, чтобы читалось без раздвигания колонок
          const span = Math.min(10, totalCols - 1);
          const rng = sh.getRange(rowNum, 2, 1, span);
          rng.merge();
          rng.setWrap(true).setVerticalAlignment('top').setHorizontalAlignment('left');
          if (urgency === 'red') rng.setBackground('#f4cccc');
          else if (urgency === 'yellow') rng.setBackground('#fff2cc');
          else rng.setBackground('#d9ead3');
          const perLine = Math.floor((95 + (span - 1) * 60) / 6.5);
          let n = 0;
          String(raw || '').split('\n').forEach(t => { n += Math.max(1, Math.ceil(t.length / perLine)); });
          sh.setRowHeight(rowNum, Math.max(21, n * 16 + 4));
        }
      }
    }

    sh.setTabColor('#c9daf8');
  });

  // Устаревшие отчёты (проекта больше нет в плане) удаляем, чтобы не вводили в заблуждение старыми цифрами
  const removed = [];
  ss.getSheets().forEach(sh => {
    const n = sh.getName();
    if (n.indexOf('Отчёт: ') === 0 && !usedSheetNames.has(n) && ss.getSheets().length > 1) {
      removed.push(n);
      ss.deleteSheet(sh);
    }
  });

  return { created: usedSheetNames.size, skipped: skippedProjects, removed: removed, advice: adviceByOrder };
}

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Распределение')
    .addItem('▶ Перепланировать', 'replanAll')
    .addSeparator()
    .addItem('⚙ Настройки', 'openSettings')
    .addItem('📖 Инструкция для мастера', 'openGuide')
    .addToUi();
}