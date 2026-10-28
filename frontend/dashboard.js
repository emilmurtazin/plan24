/* Дашборд: выполнение, статусы заказов, потребность в ресурсах, повторяющиеся причины, работники, графики на 3 месяца. */
'use strict';

const PAL = ['#0a84ff', '#30d158', '#ff9f0a', '#ff453a', '#bf5af2', '#40c8e0', '#ac8e68', '#8e8e93'];
const ST_ORDER = ['Риск опоздания', 'В работе', 'Готово'];

function niceMax(v) { if (v <= 0) return 10; const steps = [10, 20, 25, 40, 50, 100, 150, 200, 300, 500, 1000]; return steps.find(s => s >= v) || Math.ceil(v / 100) * 100; }
function legend(series) { return `<div class="legend">${series.map((s, i) => `<span><i style="background:${PAL[i % PAL.length]}"></i>${esc(s.section)}</span>`).join('')}</div>`; }

/* линии по неделям; null — разрыв (недели за горизонтом плана) */
function lineChart({ weeks, series, ref, label }) {
  const W = 720, H = 250, L = 46, R = 12, T = 12, B = 30, vals = series.flatMap(s => s.values).filter(v => v != null);
  const max = niceMax(Math.max(ref || 0, ...vals, 0) * 1.05), x = i => L + i * (W - L - R) / Math.max(weeks.length - 1, 1), y = v => T + (1 - v / max) * (H - T - B);
  let g = '';
  for (let k = 0; k <= 4; k++) { const v = max * k / 4; g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--muted)">${num(Math.round(v))}%</text>`; }
  weeks.forEach((w, i) => { if (i % 2 === 0) g += `<text x="${x(i)}" y="${H - 10}" text-anchor="middle" font-size="11" fill="var(--muted)">${fmtD(w)}</text>`; });
  if (ref) g += `<line x1="${L}" x2="${W - R}" y1="${y(ref)}" y2="${y(ref)}" stroke="var(--red)" stroke-dasharray="5 4"/><text x="${W - R}" y="${y(ref) - 4}" text-anchor="end" font-size="11" fill="var(--red)">${ref}% — полная загрузка</text>`;
  series.forEach((s, si) => {
    const c = PAL[si % PAL.length]; let path = '';
    s.values.forEach((v, i) => { if (v == null) { path += ' '; return; } path += (i > 0 && s.values[i - 1] != null ? 'L' : 'M') + `${x(i).toFixed(1)},${y(v).toFixed(1)}`; });
    g += `<path d="${path.trim()}" fill="none" stroke="${c}" stroke-width="2.5"/>`;
    s.values.forEach((v, i) => { if (v != null) g += `<circle cx="${x(i)}" cy="${y(v)}" r="3.5" fill="${c}"><title>${esc(s.section)} · неделя с ${fmtD(weeks[i])}: ${num(v)}%</title></circle>`; });
  });
  return `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="${esc(label)}">${g}</svg>`;
}
/* столбцы по неделям, по участкам рядом */
function barChart({ weeks, series, field, label }) {
  const W = 720, H = 250, L = 46, R = 12, T = 12, B = 30, vals = series.flatMap(s => s[field]).filter(v => v != null);
  const max = niceMax(Math.max(...vals, 0) * 1.05), slot = (W - L - R) / weeks.length, bw = Math.max(3, Math.min(18, (slot - 6) / Math.max(series.length, 1))), y = v => T + (1 - v / max) * (H - T - B);
  let g = '';
  for (let k = 0; k <= 4; k++) { const v = max * k / 4; g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--muted)">${num(Math.round(v))}%</text>`; }
  weeks.forEach((w, i) => {
    const cx = L + slot * (i + .5); if (i % 2 === 0) g += `<text x="${cx}" y="${H - 10}" text-anchor="middle" font-size="11" fill="var(--muted)">${fmtD(w)}</text>`;
    series.forEach((s, si) => { const v = s[field][i]; if (v == null) return; const bx = cx - bw * series.length / 2 + si * bw; g += `<rect x="${bx.toFixed(1)}" y="${y(v).toFixed(1)}" width="${(bw - 1).toFixed(1)}" height="${Math.max(1, H - B - y(v)).toFixed(1)}" fill="${PAL[si % PAL.length]}"><title>${esc(s.section)} · неделя с ${fmtD(weeks[i])}: ${num(v)}% (недовыполнено ${num(s.under[i])} из ${num(s.planned[i])})</title></rect>`; });
  });
  return `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="${esc(label)}">${g}</svg>`;
}
const dataTable = (weeks, series, f, unit = '%') => `<details class="noprint"><summary>Данные таблицей</summary><div class="scroll"><table><tr><th>Участок</th>${weeks.map(w => `<th class="num">${fmtD(w)}</th>`).join('')}</tr>${series.map(s => `<tr><td>${esc(s.section)}</td>${s[f].map(v => `<td class="num">${v == null ? '—' : num(v) + unit}</td>`).join('')}</tr>`).join('')}</table></div></details>`;

/* Показатель: крупное число, подпись и пояснение по наведению */
const kpiCard = (value, label, cls, hint, extra = '') => `<div class="kpi ${cls}" title="${esc(hint)}"><b>${value}</b>${esc(label)}${extra}</div>`;

async function dashboardView(plan) {
  const d = await GET('/reports/overview'), k = d.kpi, w = d.workers;
  const pct = v => v == null ? '—' : num(v) + '%';
  let h = `<div class="cards5">
    ${kpiCard(k.projects_in_work, 'проектов в работе', 'k-blue', 'Есть незавершённые позиции, по которым уже внесён факт')}
    ${kpiCard(k.projects_queued, 'проектов в очереди', 'k-grey', 'Есть незавершённые позиции, по которым факта ещё нет')}
    ${kpiCard(`<span class="${k.projects_at_risk ? 'err' : ''}">${k.projects_at_risk}</span>`, 'проектов с риском срыва сроков', k.projects_at_risk ? 'k-red' : 'k-grey', 'Есть позиция со статусом «Риск опоздания»: прогноз завершения позже срока')}
    ${kpiCard(`${k.workers_with_tasks}<small class="muted"> из ${k.workers_total}</small>`, 'работников с заданиями сегодня', 'k-green', 'Работники, у которых на сегодня есть задание или работа вне перечня, из общего числа работников')}
    ${kpiCard(pct(k.load_3m), 'загрузка на ближайшие 3 месяца', k.load_3m >= 100 ? 'k-red' : k.load_3m >= 80 ? 'k-amber' : 'k-grey', 'Средняя загрузка работников по плану, % рабочего дня' + (k.load_horizon_end ? '. План построен до ' + fmtDY(k.load_horizon_end) : ''), `<div class="bar"><i style="width:${Math.min(100, k.load_3m || 0)}%"></i></div>`)}</div>`;

  /* факт за прошедшие дни, который ещё не внесён */
  const mf = d.missing_fact;
  if (mf && mf.days && STAFF.includes(S.me.role)) h += `<div class="banner warn"><span><b>Не внесён факт за прошедшие дни:</b> дней — ${mf.days}, заданий — ${mf.tasks} (с ${fmtDY(mf.first)}).</span><button class="small" id="goMiss" data-first="${mf.first}">Внести факт</button></div>`;

  /* статус заказов: одна таблица, только заказы с плановым сроком завершения в ближайший месяц */
  h += `<div class="card"><h2>Статус заказов <small class="muted">срок завершения — ближайший месяц</small></h2>${d.orders_month.length ? `<div class="scroll"><table><tr><th>Заказ</th><th>Изделие</th><th>Проект</th><th>Выполнено</th><th>Срок</th><th>Прогноз</th><th>Статус</th></tr>${d.orders_month.map(r => `<tr data-ord="${r.order_line_id}" style="cursor:pointer" title="Открыть статус заказа"><td><b>${esc(r.id)}</b></td><td>${esc(r.product)}</td><td>${esc(r.project)}</td><td class="num">${num(r.progress)}%<div class="bar"><i style="width:${Math.min(100, r.progress)}%"></i></div></td><td class="${r.overdue ? 'err' : ''}">${fmtDY(r.due)}${r.overdue ? ' <small>просрочен</small>' : ''}</td><td class="${r.status === 'Риск опоздания' ? 'err' : ''}">${esc(r.forecast_text || '—')}</td><td>${stBadge(r.status)}</td></tr>`).join('')}</table></div>` : '<p class="muted">Нет заказов со сроком завершения в ближайший месяц.</p>'}</div>`;

  /* потребность в ресурсах */
  const R = d.resources;
  h += `<div class="card"><h2>Потребность в ресурсах</h2><div class="banner ${R.any_need ? 'warn' : 'info'}" style="margin-bottom:10px">${R.any_need ? '<b>Нужно усиление.</b> Ниже — где и насколько.' : `Усиление не требуется${R.orders_checked ? '' : ': проблемных заказов нет'}.`} <span class="muted">Проверено проблемных заказов: ${R.orders_checked}.</span></div>
    <div class="scroll"><table><tr><th>Участок</th><th class="num">Работников</th><th class="num">Пик загрузки</th><th class="num">Недель перегрузки</th><th>Что нужно</th></tr>
    ${R.sections.map(s => `<tr><td><b>${esc(s.section)}</b></td><td class="num">${s.workers}</td><td class="num ${s.peak_load > 100 ? 'err' : ''}">${s.peak_load == null ? '—' : num(s.peak_load) + '%'}</td><td class="num ${s.overload_weeks ? 'err' : ''}">${s.overload_weeks}</td>
      <td>${s.needs_more ? [s.add_workers ? `<b class="err">+${s.add_workers} работн.</b> — ${s.items.slice(0, 3).map(i => `${esc(i.operation)} (заказ ${esc(i.order)}: сейчас ${i.current}, нужно ~${i.need})`).join('; ')}` : '', ...s.qualification.map(q => `${esc(q.text)} — ${esc(q.operation)}, заказ ${esc(q.order)}`), ...(s.late_ops || []).slice(0, 3).map(l => `${esc(l.operation)}, заказ ${esc(l.order)}: ${esc(l.reason)}${l.unplanned ? ' (' + l.unplanned + ' шт)' : ''} — нужно усиление или перенос срока`), s.overload_weeks ? `загрузка выше 100% в течение ${s.overload_weeks} нед.` : ''].filter(Boolean).join('<br>') : '<span class="ok">усиление не требуется</span>'}</td></tr>`).join('')}</table></div>
    <p class="muted" style="font-size:14px;margin-bottom:0">Оценка по заказам в риске: сколько работников нужно, чтобы закончить к сроку при текущих нормах и производительности, минус уже занятые. Подробности по операциям — в «Статусе заказов».</p></div>`;

  /* повторяющиеся причины */
  h += `<div class="card"><h2>Повторяющиеся проблемы и причины отклонений <small class="muted">за 90 дней</small></h2>${d.reasons.length ? `<div class="scroll"><table><tr><th>Причина</th><th class="num">Раз</th><th class="num">Дней</th><th>Операции</th><th>Участки</th><th>Последний раз</th><th>Комментарии</th></tr>
    ${d.reasons.map(r => `<tr><td>${r.repeating ? '🔁 ' : ''}<b>${esc(r.reason)}</b></td><td class="num ${r.repeating ? 'err' : ''}">${r.count}</td><td class="num">${r.days}</td><td>${r.operations.map(o => `${esc(o.name)} ×${o.count}`).join(', ')}</td><td>${r.sections.map(o => `${esc(o.name)} ×${o.count}`).join(', ')}</td><td>${fmtDY(r.last_date)}</td><td class="muted">${r.notes.map(esc).join('; ')}</td></tr>`).join('')}</table></div>
    <p class="muted" style="font-size:14px;margin-bottom:0">🔁 — причина повторялась два раза и больше. Причины попадают сюда, когда их указывают при внесении факта.</p>` : '<p class="muted">Отклонений с указанной причиной пока нет.</p>'}</div>`;

  /* работники */
  h += `<div class="card"><h2>Работники</h2><div class="grid2"><div class="scroll"><h3 style="margin-top:0">По участкам</h3><table><tr><th>Участок</th><th class="num">Всего</th><th class="num">Доступно</th><th class="num">Временных</th></tr>
    ${w.by_section.map(s => `<tr><td>${esc(s.section)}</td><td class="num">${s.total}</td><td class="num">${s.available}</td><td class="num">${s.temporary}</td></tr>`).join('')}<tr><td><b>Итого</b></td><td class="num"><b>${w.total}</b></td><td class="num"><b>${w.available}</b></td><td class="num"><b>${w.by_section.reduce((a, s) => a + s.temporary, 0)}</b></td></tr></table></div>
    <div class="scroll"><h3 style="margin-top:0">По квалификациям</h3><table><tr><th>Уровень</th>${w.sections.map(s => `<th class="num">${esc(s.name)}</th>`).join('')}<th class="num">Всего</th></tr>
    ${w.by_qualification.map(l => `<tr><td>${l.level} — ${esc(l.name)}</td>${w.sections.map(s => `<td class="num">${l.by_section[s.id] || 0}</td>`).join('')}<td class="num"><b>${l.total}</b></td></tr>`).join('')}</table></div></div></div>`;

  /* графики */
  const L = d.load, U = d.underperformance;
  h += `<div class="card"><h2>Загрузка участков на 3 месяца вперёд</h2>${L.series.length && L.series.some(s => s.values.some(v => v != null)) ? `${lineChart({ weeks: L.weeks, series: L.series, ref: 100, label: 'Загрузка участков по неделям, %' })}${legend(L.series)}${dataTable(L.weeks, L.series, 'values')}
    <p class="muted" style="font-size:14px;margin-bottom:0">Средняя загрузка работников участка по неделям, % от рабочего дня. План построен до ${fmtDY(L.horizon_end)}: недели дальше горизонта планирования не показаны.</p>` : '<p class="muted">Нет данных: план ещё не построен.</p>'}</div>
  <div class="card"><h2>Недовыполнение плана по участкам за 3 месяца</h2>${U.has_data ? `${barChart({ weeks: U.weeks, series: U.series, field: 'percent', label: 'Недовыполнение плана по неделям, %' })}${legend(U.series)}${dataTable(U.weeks, U.series, 'percent')}` : '<p class="muted">Пока нет данных за прошедшие дни.</p>'}
    <p class="muted" style="font-size:14px;margin-bottom:0">Недовыполнение = запланированное на день минус внесённый факт по каждому заданию; если факт не внесён — задание считается невыполненным. ${U.since ? `История планов копится с ${fmtDY(U.since)}.` : 'История планов начнёт копиться с первого построенного плана.'}</p></div>`;
  return h;
}
function wireDashboard(v) { const gm = $('#goMiss', v); if (gm) gm.onclick = () => { S.dayOpen = gm.dataset.first; show('day'); };
  $$('[data-ord]', v).forEach(tr => tr.onclick = () => { S.stOrder = +tr.dataset.ord; S.stProduct = ''; show('status'); }); }
