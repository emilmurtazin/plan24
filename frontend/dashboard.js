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

/* ---------------- Дашборд руководителя ----------------
   Сверху — четыре главных показателя, затем «Требует внимания» и ближайшие сроки, загрузка и исполнение плана.
   Подробные таблицы и графики по участкам убраны в раздел «Подробнее». */
const ST_BADGE = { 'Риск опоздания': 'b-risk', 'В работе': 'b-work', 'Готово': 'b-done' };
const nf = v => v == null ? '—' : num(v);
const dd = iso => iso ? fmtD(iso) : '—';

function ring(pct, color) { return `<div class="ring" style="--p:${pct == null ? 0 : Math.min(100, pct)};--c:${color}" aria-hidden="true"><b>${pct == null ? '—' : pct + '%'}</b></div>`; }
const goodColor = (v, hi, mid) => v == null ? 'var(--grey)' : v >= hi ? 'var(--green)' : v >= mid ? 'var(--amber)' : 'var(--red)';

function tilesHtml(sm) {
  const onTime = sm.on_time_pct, active = sm.active;
  const chips = [sm.at_risk ? `<span class="chip-s s-red">риск срыва: ${sm.at_risk}</span>` : '', sm.no_plan ? `<span class="chip-s s-amber">без плана: ${sm.no_plan}</span>` : '', sm.done ? `<span class="chip-s s-green">готово: ${sm.done}</span>` : ''].join('');
  const t1 = `<div class="tile" title="Доля заказов в работе, которые по прогнозу укладываются в срок">
    <div class="tile-h">Заказы в срок</div>
    <div class="tile-row">${ring(onTime, goodColor(onTime, 90, 70))}<div><div class="tile-big">${active ? `${sm.on_track}<small> из ${active}</small>` : '—'}</div><div class="tile-sub">${active ? 'заказов укладываются в срок' : 'нет заказов в работе'}</div></div></div>
    <div class="chips">${chips}</div></div>`;
  const p = sm.progress_pct;
  const t2 = `<div class="tile" title="Доля выполненной работы по заказам, с учётом трудоёмкости операций (нормо-дни). Сделанная быстрая операция весит меньше сборки">
    <div class="tile-h">Выполнение</div>
    <div class="tile-big">${nf(p)}<small>%</small></div>
    <div class="bar lg"><i style="width:${Math.min(100, p || 0)}%;background:var(--blue)"></i></div>
    <div class="tile-sub">${sm.active || sm.done ? `осталось ≈ ${nf(sm.work_left_days)} нормо-дн. работы` : 'заказов пока нет'}</div></div>`;
  const l = sm.load_pct, stateTxt = { ok: 'есть запас', high: 'высокая', over: 'перегрузка' }[sm.load_state] || '', lc = { ok: 'var(--green)', high: 'var(--amber)', over: 'var(--red)' }[sm.load_state] || 'var(--grey)';
  const t3 = `<div class="tile" title="Средняя загрузка работников по плану на ближайшие 3 месяца, % рабочего дня">
    <div class="tile-h">Загрузка · 3 месяца</div>
    <div class="tile-big">${nf(l)}<small>%</small>${stateTxt ? `<span class="chip-s" style="background:${lc};color:#fff">${stateTxt}</span>` : ''}</div>
    <div class="bar lg marked"><i style="width:${Math.min(100, l || 0)}%;background:${lc}"></i></div>
    <div class="tile-sub">${l == null ? 'план ещё не построен' : l > 100 ? 'работы больше, чем людей' : 'в среднем по всем участкам'}</div></div>`;
  const ad = sm.adherence || {}, ap = ad.percent, pv = ad.prev_percent;
  const delta = ap != null && pv != null ? Math.round((ap - pv) * 10) / 10 : null;
  const t4 = `<div class="tile" title="Выполнение плана по заданиям за последние 4 недели: внесённый факт к запланированному. Если факт не внесён — задание считается невыполненным">
    <div class="tile-h">Исполнение плана · 4 недели</div>
    <div class="tile-big" style="color:${goodColor(ap, 90, 75)}">${nf(ap)}<small>%</small>${delta == null || delta === 0 ? '' : `<span class="trend ${delta > 0 ? 'up' : 'down'}">${delta > 0 ? '▲' : '▼'} ${num(Math.abs(delta))}</span>`}</div>
    <div class="bar lg"><i style="width:${Math.min(100, ap || 0)}%;background:${goodColor(ap, 90, 75)}"></i></div>
    <div class="tile-sub">${ap == null ? 'история копится после первого плана' : pv == null ? 'факт к плану' : 'к предыдущим 4 неделям: ' + (delta > 0 ? 'лучше' : delta < 0 ? 'хуже' : 'без изменений')}</div></div>`;
  return `<div class="tiles">${t1}${t2}${t3}${t4}</div>`;
}

function attentionHtml(sm) {
  const items = sm.attention || [];
  if (!items.length) return `<div class="card"><h2>Требует внимания</h2><div class="allgood"><span>✓</span><div><b>Всё под контролем</b><small>заказы укладываются в сроки, невыполненных заданий нет</small></div></div></div>`;
  const staff = STAFF.includes(S.me.role);
  return `<div class="card"><h2>Требует внимания <small class="muted">${items.length}</small></h2><ul class="att">${items.map(a => `<li class="${a.level}" ${a.order_line_id ? `data-ord="${a.order_line_id}" tabindex="0"` : ''} ${a.kind === 'resource' ? 'data-more="res" tabindex="0"' : ''} ${a.kind === 'reason' ? 'data-more="rsn" tabindex="0"' : ''}>
    <i class="dot"></i><div class="att-t"><b>${esc(a.title)}</b><span>${esc(a.text)}</span></div>
    ${a.progress != null && a.order_line_id ? `<div class="att-p" title="Выполнено"><div class="bar"><i style="width:${Math.min(100, a.progress)}%"></i></div><small>${num(a.progress)}%</small></div>` : ''}
    ${a.kind === 'facts' && staff ? `<button class="small" id="goMiss" data-first="${a.first}">Внести факт</button>` : ''}</li>`).join('')}</ul></div>`;
}

function soonHtml(d) {
  const rows = d.orders_month.slice(0, 8), more = d.orders_month.length - rows.length;
  return `<div class="card"><h2>Ближайшие сроки <small class="muted">на 30 дней</small></h2>${rows.length ? `<ul class="soon">${rows.map(r => `<li data-ord="${r.order_line_id}" tabindex="0">
    <div class="soon-m"><b>${esc(r.id)}</b> ${esc(r.product)}<small class="muted">${esc(r.project || '')}</small></div>
    <div class="soon-p"><div class="bar"><i style="width:${Math.min(100, r.progress)}%"></i></div><small>${num(r.progress)}%</small></div>
    <div class="soon-d"><span class="${r.overdue ? 'err' : ''}">до ${dd(r.due)}</span><span class="badge ${ST_BADGE[r.status] || 'b-grey'}">${esc(r.status)}</span></div></li>`).join('')}</ul>${more > 0 ? `<p class="muted" style="margin:8px 0 0">и ещё ${more} — в разделе «Статус заказов»</p>` : ''}` : '<p class="muted">Нет заказов со сроком в ближайший месяц.</p>'}</div>`;
}

/* загрузка участков по неделям: строка на участок, ячейка на неделю, цвет по уровню загрузки */
function heatHtml(L) {
  const has = L.series.length && L.series.some(s => s.values.some(v => v != null));
  if (!has) return `<div class="card"><h2>Загрузка участков по неделям</h2><p class="muted">Нет данных: план ещё не построен.</p></div>`;
  const cls = v => v == null ? 'h-none' : v >= 100 ? 'h-over' : v >= 85 ? 'h-high' : v >= 50 ? 'h-ok' : 'h-low';
  const n = L.weeks.length;
  return `<div class="card"><h2>Загрузка участков по неделям <small class="muted">3 месяца</small></h2>
    <div class="heat" style="--n:${n}">
      <div class="heat-h"></div>${L.weeks.map((w, i) => `<div class="heat-h">${i % 2 === 0 ? fmtD(w) : ''}</div>`).join('')}
      ${L.series.map(s => `<div class="heat-l" title="${esc(s.section)}">${esc(s.section)}</div>${s.values.map((v, i) => `<div class="heat-c ${cls(v)}" title="${esc(s.section)} · неделя с ${fmtD(L.weeks[i])}: ${v == null ? 'нет плана' : num(v) + '%'}">${v == null ? '' : Math.round(v)}</div>`).join('')}`).join('')}
    </div>
    <div class="heat-leg"><span><i class="h-low"></i>до 50%</span><span><i class="h-ok"></i>50–85%</span><span><i class="h-high"></i>85–100%</span><span><i class="h-over"></i>перегрузка</span><span><i class="h-none"></i>нет плана</span></div></div>`;
}

/* исполнение плана по неделям + главные причины отклонений */
function adherenceHtml(U, reasons) {
  const vals = (U.total_percent || []).map(p => p == null ? null : Math.max(0, Math.round((100 - p) * 10) / 10));
  const has = U.has_data && vals.some(v => v != null);
  const W = 520, H = 170, L = 34, R = 8, T = 10, B = 24, slot = (W - L - R) / Math.max(vals.length, 1), bw = Math.min(26, slot * .64), y = v => T + (1 - v / 100) * (H - T - B);
  let g = '';
  for (const v of [0, 50, 90, 100]) g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)" ${v === 90 ? 'stroke-dasharray="4 4"' : ''}/><text x="${L - 5}" y="${y(v) + 4}" text-anchor="end" font-size="10" fill="var(--muted)">${v}</text>`;
  vals.forEach((v, i) => {
    const cx = L + slot * (i + .5);
    if (i % 2 === 0) g += `<text x="${cx}" y="${H - 7}" text-anchor="middle" font-size="10" fill="var(--muted)">${fmtD(U.weeks[i])}</text>`;
    if (v != null) g += `<rect x="${(cx - bw / 2).toFixed(1)}" y="${y(v).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, H - B - y(v)).toFixed(1)}" rx="3" fill="${v >= 90 ? 'var(--green)' : v >= 75 ? 'var(--amber)' : 'var(--red)'}"><title>неделя с ${fmtD(U.weeks[i])}: выполнено ${num(v)}% плана</title></rect>`;
  });
  const top = reasons.slice(0, 4), mx = Math.max(1, ...top.map(r => r.count));
  return `<div class="card"><h2>Исполнение плана <small class="muted">по неделям, % выполненных заданий</small></h2>
    ${has ? `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="Исполнение плана по неделям, %">${g}</svg>` : '<p class="muted">Пока нет данных за прошедшие дни: история планов начнёт копиться с первого построенного плана.</p>'}
    ${top.length ? `<h3 class="sub">Главные причины отклонений <small class="muted">за 90 дней</small></h3><ul class="rsn">${top.map(r => `<li><span>${r.repeating ? '🔁 ' : ''}${esc(r.reason)}</span><div class="bar"><i style="width:${r.count / mx * 100}%;background:var(--amber)"></i></div><b>${r.count}</b></li>`).join('')}</ul>` : ''}</div>`;
}

async function dashboardView(plan) {
  const d = await GET('/reports/overview'), sm = d.summary, w = d.workers;
  const R = d.resources, L = d.load, U = d.underperformance;
  let h = `<div class="dash"><div class="dash-h"><h1>Обзор производства</h1><span class="muted">${fmtDY(d.today)}${plan && plan.created_at ? ' · план от ' + fmtDY(plan.created_at) : ''}</span></div>${tilesHtml(sm)}
    <div class="dash-2">${attentionHtml(sm)}${soonHtml(d)}</div>
    <div class="dash-2">${heatHtml(L)}${adherenceHtml(U, d.reasons)}</div>
    <details class="more" id="more"><summary>Подробнее: ресурсы, причины, работники, графики по участкам</summary><div class="more-body">`;
    /* потребность в ресурсах */
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
  h += `<div class="card"><h2>Загрузка участков на 3 месяца вперёд</h2>${L.series.length && L.series.some(s => s.values.some(v => v != null)) ? `${lineChart({ weeks: L.weeks, series: L.series, ref: 100, label: 'Загрузка участков по неделям, %' })}${legend(L.series)}${dataTable(L.weeks, L.series, 'values')}
    <p class="muted" style="font-size:14px;margin-bottom:0">Средняя загрузка работников участка по неделям, % от рабочего дня. План построен до ${fmtDY(L.horizon_end)}: недели дальше горизонта планирования не показаны.</p>` : '<p class="muted">Нет данных: план ещё не построен.</p>'}</div>
  <div class="card"><h2>Недовыполнение плана по участкам за 3 месяца</h2>${U.has_data ? `${barChart({ weeks: U.weeks, series: U.series, field: 'percent', label: 'Недовыполнение плана по неделям, %' })}${legend(U.series)}${dataTable(U.weeks, U.series, 'percent')}` : '<p class="muted">Пока нет данных за прошедшие дни.</p>'}
    <p class="muted" style="font-size:14px;margin-bottom:0">Недовыполнение = запланированное на день минус внесённый факт по каждому заданию; если факт не внесён — задание считается невыполненным. ${U.since ? `История планов копится с ${fmtDY(U.since)}.` : 'История планов начнёт копиться с первого построенного плана.'}</p></div>`;

  h += `</div></details></div>`;
  return h;
}
function wireDashboard(v) {
  const gm = $('#goMiss', v); if (gm) gm.onclick = e => { e.stopPropagation(); S.dayOpen = gm.dataset.first; show('day'); };
  const open = el => { const t = e => { if (e.type === 'click' || e.key === 'Enter') el.onclick(e); }; el.onkeydown = t; };
  $$('[data-ord]', v).forEach(el => { el.onclick = () => { S.stOrder = +el.dataset.ord; S.stProduct = ''; show('status'); }; open(el); });
  $$('[data-more]', v).forEach(el => { el.onclick = () => { const m = $('#more', v); m.open = true; m.scrollIntoView({ behavior: 'smooth', block: 'start' }); }; open(el); });
}
