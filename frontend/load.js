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
    const cell = (n, d) => {
      const p = L.plan[n] && L.plan[n][d], f = L.fact[n] && L.fact[n][d], past = d < today || (d === today && f != null);
      const shown = p != null ? pctOf(p) : (f != null ? pctOf(f) : null);
      const tip = `${n}, ${fmtD(d)}: план ${p != null ? pctOf(p) + '%' : '—'}${f != null ? ', факт ' + pctOf(f) + '%' : ''}`;
      return `<td><span class="hc ${loadClass(p, f, past)}" title="${esc(tip)}">${shown == null ? '' : shown}</span></td>`;
    };
    const avg = names => { let s = 0, c = 0; for (const n of names) for (const d of cols) { s += (L.plan[n] && L.plan[n][d]) || 0; c++; } return c ? Math.round(s / c * 100) : null; };
    const range = `${fmtD(lo)} – ${fmtD(hi)}`;
    v.innerHTML = `<div class="row" style="margin-bottom:6px"><h2 class="grow" style="margin:0">Загрузка</h2>
      <span class="row noprint"><button class="secondary small" id="lp" aria-label="Предыдущие две недели">‹</button><b class="lrange">${range}</b><button class="secondary small" id="ln" aria-label="Следующие две недели">›</button><button class="small secondary" id="lt">Сегодня</button></span></div>
      <p class="muted" style="margin:0 0 10px">Число в клетке — загрузка работника за день по плану, % рабочего дня. Цвет — выполнение плана за день.</p>
      <div class="legend lg"><span><i class="hc hc-ok"></i>выполнено или перевыполнено</span><span><i class="hc hc-warn"></i>недовыполнено на 5–30%</span><span><i class="hc hc-bad"></i>недовыполнено более чем на 30%</span><span><i class="hc hc-plan"></i>по плану, факта ещё нет</span></div>
      ${cols.length && secs.length ? secs.map(sec => `<div class="card lsec"><div class="lh"><h2>${esc(sec.name)}</h2><span class="muted">работников: ${sec.names.length}${avg(sec.names) != null ? ' · средняя загрузка ' + avg(sec.names) + '%' : ''}</span></div>
        <div class="scroll"><table class="heat"><tr><th></th>${cols.map(d => `<th class="${d === today ? 'tday' : ''}">${fmtD(d)}<small>${wd(d)}</small></th>`).join('')}</tr>${sec.names.map(n => `<tr><td class="name">${esc(n)}</td>${cols.map(d => cell(n, d)).join('')}</tr>`).join('')}</table></div></div>`).join('')
        : '<div class="card empty">В этом периоде рабочих дней нет в плане. Переключите период стрелками.</div>'}`;
    $('#lp').onclick = () => { S.loadWeek = (S.loadWeek || 0) - 2; draw(); };
    $('#ln').onclick = () => { S.loadWeek = (S.loadWeek || 0) + 2; draw(); };
    $('#lt').onclick = () => { S.loadWeek = 0; draw(); };
  };
  draw();
};
