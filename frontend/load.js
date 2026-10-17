/* Загрузка: по участкам, в процентах рабочего дня. Цвет — выполнение плана за день:
   зелёный — выполнено или перевыполнено; жёлтый — недовыполнение от 5 до 30%; красный — недовыполнение более чем на 30%.
   Дни, по которым факта ещё нет (будущее и сегодня до внесения факта), — нейтральные: показывают только плановую загрузку. */
'use strict';

const LOAD_SHORT = { warn: 0.05, bad: 0.30 };       // доля недовыполнения плана
const LOAD_COLS = 14;                               // дней в окне (две недели)

/* класс ячейки по плану и факту (в долях рабочего дня); past — день уже можно оценивать */
function loadClass(plan, fact, past) {
  if (!past) return plan ? 'hc-plan' : 'hc-none';
  if (!plan) return fact ? 'hc-ok' : 'hc-none';                 // плана не было, а работа выполнена — это не недовыполнение
  const short = (plan - (fact || 0)) / plan;
  if (short > LOAD_SHORT.bad + 1e-9) return 'hc-bad';
  if (short >= LOAD_SHORT.warn - 1e-9) return 'hc-warn';
  return 'hc-ok';
}
const pctOf = x => x == null ? null : Math.round(x * 100);
const monday0 = iso => { const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return d; };
const isoOf = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');

SCREENS.load = async v => {
  S.nav++;
  const L = await GET('/reports/load');
  if (!L.plan_id) { v.innerHTML = '<h2>Загрузка</h2><div class="card empty">План ещё не построен.</div>'; return; }
  const today = L.today, dates = L.dates;
  const draw = () => {
    const from = monday0(today); from.setDate(from.getDate() + 7 * (S.loadWeek || 0));
    const to = new Date(from); to.setDate(to.getDate() + LOAD_COLS - 1);
    const lo = isoOf(from), hi = isoOf(to), cols = dates.filter(d => d >= lo && d <= hi);
    /* участки → работники */
    const info = L.worker_info || {}, groups = new Map();
    for (const n of L.workers) {
      const cid = info[n] ? info[n].work_center_id : null, key = cid == null ? 0 : cid;
      if (!groups.has(key)) groups.set(key, { id: key, name: key ? (L.centers[key] || 'Участок') : 'Без участка', names: [] });
      groups.get(key).names.push(n);
    }
    const secs = [...groups.values()].sort((a, b) => (a.id === 0) - (b.id === 0) || a.name.localeCompare(b.name, 'ru'));
    const avg = names => { let s = 0, c = 0; for (const n of names) for (const d of cols) { s += (L.plan[n] && L.plan[n][d]) || 0; c++; } return c ? Math.round(s / c * 100) : null; };
    const get = (n, d) => ({ p: L.plan[n] && L.plan[n][d], f: L.fact[n] && L.fact[n][d] });
    const isPast = (d, f) => d < today || (d === today && f != null);
    /* клетка: число «план / факт», верхняя полоса — план, нижняя — факт; цвет — выполнение плана */
    const cell = (n, d) => {
      const { p, f } = get(n, d), past = isPast(d, f), cls = loadClass(p, f, past);
      if (p == null && f == null) return `<td><span class="lc hc-none"></span></td>`;
      const w = x => Math.min(100, Math.round((x || 0) * 100));
      const label = past ? `${p != null ? pctOf(p) : 0}<i>/</i><b>${pctOf(f || 0)}</b>` : `<b>${pctOf(p)}</b>`;
      const tip = `${n}, ${fmtD(d)}: план ${p != null ? pctOf(p) + '%' : '—'}${past ? ', факт ' + pctOf(f || 0) + '%' : ', факта ещё нет'}${past && p ? ' (отставание ' + Math.max(0, pctOf(p) - pctOf(f || 0)) + ' п.п.)' : ''}`;
      return `<td><span class="lc ${cls}" title="${esc(tip)}"><span class="lt">${label}</span><span class="lb"><i class="bp" style="width:${w(p)}%"></i></span>${past ? `<span class="lb"><i class="bf" style="width:${w(f)}%"></i></span>` : ''}</span></td>`;
    };
    /* итоги за окно по прошедшим дням: план, факт и отставание (план − факт) в % рабочего дня */
    const totals = names => {
      let sp = 0, sf = 0, nd = 0, short = 0;
      for (const n of names) for (const d of cols) { const { p, f } = get(n, d); if (!isPast(d, f) || !(p || f)) continue; sp += p || 0; sf += f || 0; nd++; short += Math.max(0, (p || 0) - (f || 0)); }
      if (!nd) return null;
      return { plan: Math.round(sp / nd * 100), fact: Math.round(sf / nd * 100), lag: sp > 0 ? Math.round(short / sp * 100) : 0 };
    };
    const lagCls = lag => lag > 30 ? 'hc-bad' : lag >= 5 ? 'hc-warn' : 'hc-ok';
    const tcells = t => t ? `<td class="tt">${t.plan}%</td><td class="tt">${t.fact}%</td><td class="tt"><span class="hc ${lagCls(t.lag)}" title="Недовыполнение плана за прошедшие дни периода: доля невыполненного от плана">${t.lag === 0 ? '0' : '−' + t.lag}%</span></td>` : '<td class="tt muted">—</td><td class="tt muted">—</td><td class="tt muted">—</td>';
    const all = totals(L.workers);
    const range = `${fmtD(lo)} – ${fmtD(hi)}`;
    v.innerHTML = `<div class="row" style="margin-bottom:6px"><h2 class="grow" style="margin:0">Загрузка</h2>
      <span class="row noprint"><button class="secondary small" id="lp" aria-label="Предыдущие две недели">‹</button><b class="lrange">${range}</b><button class="secondary small" id="ln" aria-label="Следующие две недели">›</button><button class="small secondary" id="lt">Сегодня</button></span></div>
      ${all ? `<div class="cards5 lsum"><div class="kpi k-grey" title="Средняя плановая загрузка за прошедшие дни периода"><b>${all.plan}%</b>план</div><div class="kpi k-blue" title="Средняя фактическая загрузка за прошедшие дни периода"><b>${all.fact}%</b>факт</div><div class="kpi ${all.lag > 30 ? 'k-red' : all.lag >= 5 ? 'k-amber' : 'k-green'}" title="Какая доля запланированного не выполнена"><b>${all.lag === 0 ? '0' : '−' + all.lag}%</b>отставание от плана</div></div>` : '<p class="muted" style="margin:0 0 8px">В выбранном периоде ещё нет прошедших дней с планом — показан только план.</p>'}
      <div class="legend lg"><span><i class="hc hc-ok"></i>план выполнен или перевыполнен</span><span><i class="hc hc-warn"></i>отставание 5–30%</span><span><i class="hc hc-bad"></i>отставание больше 30%</span><span><i class="hc hc-plan"></i>день впереди — только план</span></div>
      <p class="muted" style="margin:0 0 10px">В клетке: <b>план / факт</b> в % рабочего дня. Верхняя полоса — план, нижняя — факт. Справа — итоги за период по прошедшим дням.</p>
      ${cols.length && secs.length ? secs.map(sec => { const st = totals(sec.names); return `<div class="card lsec"><div class="lh"><h2>${esc(sec.name)}</h2><span class="muted">работников: ${sec.names.length}${avg(sec.names) != null ? ' · средний план ' + avg(sec.names) + '%' : ''}${st ? ` · факт ${st.fact}% · отставание <b class="${st.lag > 30 ? 'err' : st.lag >= 5 ? 'warn-t' : 'ok-t'}">${st.lag === 0 ? '0' : '−' + st.lag}%</b>` : ''}</span></div>
        <div class="scroll"><table class="heat"><tr><th></th>${cols.map(d => `<th class="${d === today ? 'tday' : ''}">${fmtD(d)}<small>${wd(d)}</small></th>`).join('')}<th class="tt">План</th><th class="tt">Факт</th><th class="tt">Отстав.</th></tr>${sec.names.map(n => `<tr><td class="name">${esc(n)}</td>${cols.map(d => cell(n, d)).join('')}${tcells(totals([n]))}</tr>`).join('')}</table></div></div>`; }).join('')
        : '<div class="card empty">В этом периоде рабочих дней нет в плане. Переключите период стрелками.</div>'}`;
    $('#lp').onclick = () => { S.loadWeek = (S.loadWeek || 0) - 2; draw(); };
    $('#ln').onclick = () => { S.loadWeek = (S.loadWeek || 0) + 2; draw(); };
    $('#lt').onclick = () => { S.loadWeek = 0; draw(); };
  };
  draw();
};
