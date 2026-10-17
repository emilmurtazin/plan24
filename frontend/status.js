/* Статус заказов: таблица всех заказов сверху; выбираем заказ — видим операции: выполнено, в плане, человеко-смены
   до завершения, дата завершения, узкое место, проблемы и рекомендации. */
'use strict';

/* простой: те же пороги, что в «Загрузке» — до 5% норма, 5–30% внимание, больше 30% плохо */
const idleCls = p => p == null ? 'muted' : p > 30 ? 'err' : p >= 5 ? 'warn-t' : 'ok-t';

SCREENS.status = async (v, pick) => {
  S.nav++;
  const { rows } = await GET('/reports/status');
  if (!rows.length) { v.innerHTML = '<h1>Статус заказов</h1><div class="card empty">План ещё не построен.</div>'; return; }
  let oid = pick || S.stOrder; if (!rows.some(r => r.order_line_id === oid)) oid = null;
  v.innerHTML = `<h1>Статус заказов</h1><h2>Все заказы</h2><div class="card flush scroll" id="all"></div><div id="od" class="od"></div>`;
  const drawAll = () => {
    $('#all').innerHTML = `<table><tr><th>Заказ</th><th>Проект</th><th>Изделие</th><th class="num">Кол-во</th><th class="num" title="Доля выполненного объёма заказа">Выполнено, %</th><th class="num" title="Недовыполнение плана за прошедшие дни: (план − факт) / план. Считается по дням, по которым есть план">Простой, % (план − факт)</th><th>Прогноз</th><th>Срок</th><th>Статус</th></tr>${rows.map(r => `<tr data-o="${r.order_line_id}" style="cursor:pointer" ${r.order_line_id === oid ? 'aria-selected="true"' : ''}><td><b>${esc(r.id)}</b></td><td>${esc(r.project)}</td><td>${esc(r.product)}</td><td class="num">${num(r.qty)}</td><td class="num">${num(r.progress)}%<div class="bar"><i style="width:${Math.min(100, r.progress)}%"></i></div></td><td class="num ${idleCls(r.idle_pct)}" title="${r.idle_pct == null ? 'За прошедшие дни плана ещё нет' : 'Недовыполнение плана за прошедшие дни'}">${r.idle_pct == null ? '—' : num(r.idle_pct) + '%'}</td><td>${esc(r.forecast_text)}</td><td>${fmtDY(r.due)}</td><td>${stBadge(r.status)}</td></tr>`).join('')}</table>`;
    $$('#all tr[data-o]').forEach(tr => tr.onclick = () => { oid = +tr.dataset.o; drawAll(); load(true); });
  };
  const load = async scroll => {
    if (oid == null) { $('#od').innerHTML = '<p class="muted">Выберите заказ в таблице — ниже появятся его операции, узкое место, проблемы и рекомендации.</p>'; return; }
    S.stOrder = oid;
    try { $('#od').innerHTML = orderView(await GET('/reports/order-status?order_line_id=' + oid)); if (scroll && $('#od').scrollIntoView) $('#od').scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    catch (e) { if (!(e instanceof Stale)) $('#od').innerHTML = `<div class="card err">${esc(errText(e))}</div>`; }
  };
  drawAll(); load(!!pick);
};

function orderView(s) {
  const o = s.order, ops = s.operations;
  const kpi = (t, b, extra = '', cls = 'k-grey') => `<div class="kpi ${cls}"><small class="muted">${t}</small><b style="font-size:22px">${b}</b>${extra}</div>`;
  const late = o.late_days ? `<span class="err">позже срока на ${o.late_days} дн.</span>` : (o.forecast_end && o.due_date ? '<span class="ok">укладывается в срок</span>' : '');
  return `<div class="card"><div class="row" style="margin-bottom:6px"><h2 class="grow" style="margin:0">Заказ ${esc(o.order)} · ${esc(o.product)} × ${num(o.qty)}</h2>${stBadge(o.status)}</div>
    <p class="muted" style="margin:0 0 12px">${esc(o.project)}${o.customer ? ' · заказчик ' + esc(o.customer) : ''} · версия изделия v${o.version}${o.version_name ? ' «' + esc(o.version_name) + '»' : ''}</p>
    <div class="grid4">${kpi('Выполнено', num(o.progress) + '%', `<div class="bar"><i style="width:${Math.min(100, o.progress)}%"></i></div>`, 'k-blue')}
    ${kpi('Срок заказа', fmtDY(o.due_date), '', 'k-grey')}${kpi('Прогноз завершения', o.forecast_end ? fmtDY(o.forecast_end) : (o.status === 'Готово' ? 'выполнен' : 'не определён'), late, o.late_days ? 'k-red' : (o.forecast_end && o.due_date ? 'k-green' : 'k-grey'))}
    ${o.bottleneck ? kpi('Узкое место', esc(o.bottleneck), '<small class="muted">определяет срок заказа</small>', 'k-amber') : ''}</div></div>
    ${ops.length ? `<div class="card flush scroll" style="margin-top:12px"><table class="stt"><tr><th>Операция</th><th class="num">Выполнено</th><th class="num">В плане</th><th class="num">Не в плане</th><th class="num" title="Сколько полных рабочих дней одного работника нужно, чтобы закончить операцию">Человеко-смен до завершения</th><th>Дата завершения</th><th>Узкое место</th><th>Статус</th></tr>
    ${ops.map(opRow).join('')}</table></div>` : '<div class="card empty" style="margin-top:12px">В вашем участке у этого заказа нет операций.</div>'}
`;
}
function opRow(x) {
  const st = { 'Готово': 'b-done', 'В плане': 'b-work', 'Не распределена': 'b-risk', 'Не весь объём в плане': 'b-risk' }[x.status] || 'b-noop';
  const bn = x.is_bottleneck ? `<span class="badge ${x.bottleneck_critical ? 'b-risk' : 'b-none'}" title="Определяет срок завершения заказа">${x.bottleneck_critical ? '⚠ узкое место' : 'узкое место'}</span>` : '<span class="muted">—</span>';
  const pct = x.required ? Math.min(100, x.done / x.required * 100) : 100;
  const det = (x.problems.length || x.recommendations.length) ? `<tr class="det"><td colspan="8"><div class="grid2">
      <div><b>Проблемы</b>${x.problems.length ? `<ul>${x.problems.map(p => `<li class="${p.code === 'REASONS' ? '' : 'err'}">${esc(p.text)}</li>`).join('')}</ul>` : '<p class="muted">нет</p>'}</div>
      <div><b>Рекомендации</b>${x.recommendations.length ? `<ul>${x.recommendations.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : '<p class="muted">нет</p>'}</div></div></td></tr>` : '';
  return `<tr class="${x.is_bottleneck ? 'bn' : ''}"><td><b>${esc(x.name)}</b><small class="muted" style="display:block">${x.sections.length ? 'участок: ' + esc(x.sections.join(', ')) : ''}${x.workers.length ? ' · на операции: ' + esc(x.workers.join(', ')) : ''}</small></td>
    <td class="num">${num(x.done)} из ${num(x.required)}<div class="bar"><i style="width:${pct}%"></i></div></td><td class="num">${num(x.planned)}</td>
    <td class="num ${x.unplanned > 0 ? 'err' : ''}">${x.unplanned > 0 ? '<b>' + num(x.unplanned) + '</b>' : '0'}</td>
    <td class="num">${num(x.person_shifts_left)}</td>
    <td class="${x.late ? 'err' : ''}">${x.end_date ? fmtDY(x.end_date) : '<span class="err">не определена</span>'}${x.late ? ' ⚠' : ''}</td><td>${bn}</td><td><span class="badge ${st}">${esc(x.status)}</span></td></tr>${det}`;
}
