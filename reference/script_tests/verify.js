const {makeEnv,Sheet}=require('./harness');
const {ctx,ss,sheets}=makeEnv('../Code_v3.5.gs');
const day=n=>{const d=new Date();d.setHours(0,0,0,0);d.setDate(d.getDate()+n);return d};
const mk=(name,rows)=>{const s=ss.insertSheet(name);s.getRange(1,1,rows.length,rows[0].length).setValues(rows)};
const norms={'Шкаф|Раскрой':40,'Шкаф|Сборка':5,'Шкаф|Покраска':0.5,'Тумба|Сборка':8.5,'Тумба|Упаковка':12};
const perf={Иванов:1,Петров:1.2,Сидоров:0.8,Кузнецов:1,Орлов:0.9};
mk('Заказы',[['ID','Проект','Изделие','Кол-во','Срок','Приоритет'],['1','Проект А','Шкаф',20,day(14),1],['2','Проект А','Шкаф',15,day(20),2],['3','Проект Б','Тумба',30,day(9),3]]);
mk('Операции',[['Изделие','Операция','Норма','Квал','На изделие','Порядок','Блок'],['Шкаф','Раскрой',40,1,1,1,0],['Шкаф','Сборка',5,1,1,2,0],['Шкаф','Покраска',0.5,1,1,3,0],['Тумба','Сборка',8.5,1,2,1,0],['Тумба','Упаковка',12,1,0.5,2,0]]);
mk('Сборщики',[['Имя','Произв.','Квал','Статус','Проекты'],['Иванов',1,2,'доступен',''],['Петров',1.2,2,'доступен',''],['Сидоров',0.8,1,'доступен',''],['Кузнецов',1,1,'доступен',''],['Орлов',0.9,1,'доступен','']]);
mk('Праздники',[['Дата']]);
function check(label){
  const out=sheets['Задания на день'].data.slice(1);
  const item={ '1':'Шкаф','2':'Шкаф','3':'Тумба'};
  const load={}; // worker -> date -> days
  out.forEach(r=>{const [d,m,y]=r[0].split('.');const k=`${y}-${m}-${d}`;const t=r[5]/(norms[r[3]+'|'+r[4]]*perf[r[6]]);const w=load[r[6]]=load[r[6]]||{};w[k]=(w[k]||0)+t});
  let worst=0,worstInfo='';
  Object.keys(load).forEach(w=>{const ds=Object.keys(load[w]).sort();let cum=0,cap=0;
    // ёмкость считаем по индексам рабочих дней от первого дня плана
    const all=[...new Set(out.map(r=>{const [d,m,y]=r[0].split('.');return `${y}-${m}-${d}`}))].sort();
    all.forEach(k=>{cap+=1;cum+=(load[w][k]||0);const over=cum-cap;if(over>worst){worst=over;worstInfo=w+' '+k}})});
  console.log(label,'| строк:',out.length,'| дробных:',out.filter(r=>!Number.isInteger(r[5])).length,'| макс. накопленный перебор времени, дней:',worst.toFixed(2),worstInfo);
}
ctx.replanAll(); check('прогон 1');
// вносим факт: частично выполнено по первым строкам сегодняшнего/завтрашнего плана
const f=sheets['Факт']; console.log('строк в Факте:',f.data.length-1);
let n=0;for(let i=1;i<f.data.length&&n<3;i++){const plan=f.data[i][6];if(plan>=2){f.data[i][7]=plan-1;n++}}
ctx.replanAll(); check('прогон 2 (с фактом)');
const out=sheets['Задания на день'].data.slice(1).filter(r=>Number.isInteger(r[5])===false);console.log('дробных после факта:',out.length);
console.log('предупреждения:',ctx.__alerts.slice(-1)[0].split('\n').slice(0,6));

// ---- строгая проверка: никто не двойно занят (модель непрерывного времени, дни = рабочие дни)
function wdIndex(dstr){const [d,m,y]=dstr.split('.').map(Number);const t=new Date(y,m-1,d);let c=new Date();c.setHours(0,0,0,0);let i=0;while(c<t){c.setDate(c.getDate()+1);if(c.getDay()!==0&&c.getDay()!==6)i++}return i}
function strict(label){
  const rows=sheets['Задания на день'].data.slice(1);
  const byW={};rows.forEach(r=>{(byW[r[6]]=byW[r[6]]||[]).push({d:wdIndex(r[0]),t:r[5]/(norms[r[3]+'|'+r[4]]*perf[r[6]])})});
  let bad=0,maxDayLoad=0;
  Object.keys(byW).forEach(w=>{const days={};byW[w].forEach(x=>{const o=days[x.d]=days[x.d]||{T:0,mx:0};o.T+=x.t;o.mx=Math.max(o.mx,x.t)});
    let backlog=0;Object.keys(days).map(Number).sort((a,b)=>a-b).forEach((d,i,arr)=>{
      const prev=i?arr[i-1]:d;backlog=Math.max(0,backlog-(d-prev)); // за пропущенные дни долг гасится
      if(backlog>=1+1e-6)bad++;
      const load=backlog+days[d].T; if(load-days[d].mx>1+1e-6)bad++;
      maxDayLoad=Math.max(maxDayLoad,load);backlog=Math.max(0,load-1);});});
  console.log(label,'| нарушений двойной занятости:',bad,'| макс. загрузка дня с долгом:',maxDayLoad.toFixed(2));
}
strict('строгая проверка (прогон 2)');
