const {makeEnv}=require('./harness');
const file=process.argv[2]||'../Code_v3.5.gs';
const {ctx,ss,sheets}=makeEnv(file);
const day=n=>{const d=new Date();d.setHours(0,0,0,0);d.setDate(d.getDate()+n);return d};
const mk=(name,rows)=>{const s=ss.insertSheet(name);s.getRange(1,1,rows.length,rows[0].length).setValues(rows)};
const P='307 | Москва, ИТЭЛМА, БЦ Ньютон Плаза (1 этап)', I='Флагман Смарт';
mk('Заказы',[['ID','Проект','Изделие','Кол-во','Срок','Приоритет'],
 ['307-71',P,I,'18шт. к',day(1),1],['307-72',P,I,'6шт.',day(13),1],['307-73',P,I,'7шт.',day(20),1]]);
mk('Операции',[['Изделие','Операция','Норма','Квал','На изделие','Порядок','Блок'],[I,'Раскрой',10,1,1,1,0],[I,'Сборка',4,1,1,2,0],[I,'Упаковка',12,1,1,3,0]]);
mk('Сборщики',[['Имя','Произв.','Квал','Статус','Проекты'],['Иванов',1,2,'доступен',''],['Петров',1.2,2,'доступен',''],['Сидоров',0.8,1,'доступен','']]);
mk('Праздники',[['Дата']]);
// факт: внесён на ДРУГУЮ партию (307-72) и с пустым ID — партии должны закрываться по срокам
const f=ss.insertSheet('Факт');
f.getRange(1,1,3,9).setValues([['Дата','ID','Проект','Изделие','Операция','Сборщик','План','Кол-во операций','Причина отклонения'],
 [day(-1),'307-72',P,I,'Раскрой','Иванов',20,20,''],
 [day(-1),'',P,I,'Сборка','Петров',5,3,'Ждали материал']]);
ctx.replanAll();
const out=sheets['Задания на день'].data.slice(1);
const sum=(id,op)=>out.filter(r=>r[1]===id&&r[4]===op).reduce((a,r)=>a+r[5],0);
console.log('qty распознаны: партии 18/6/7 → план Раскрой = (18-18 факт)=0 / (6-2)=4 / 7');
for(const id of ['307-71','307-72','307-73']) console.log(id,'Раскрой в плане:',sum(id,'Раскрой'),'| Сборка в плане:',sum(id,'Сборка'),'| Упаковка:',sum(id,'Упаковка'));
console.log('ожидание Сборка: 71 → 18-3=15, 72 → 6, 73 → 7');
const rn=Object.keys(sheets).find(n=>n.startsWith('Отчёт: '));
console.log('\nлист отчёта:',rn);
const R=sheets[rn].data;
const sec=(name)=>{const i=R.findIndex(r=>r[0]===name);return R.slice(i,i+6).map(r=>String(r[0]).padEnd(34)+'| итого='+r[r.findIndex((x,k)=>k>0&&false)||0]).join('\n')};
const rowsOf=(title,n)=>{const i=R.findIndex(r=>r[0]===title);return R.slice(i+1,i+1+n).map(r=>String(r[0]).padEnd(34)+'| '+String(r[r.length-1]??'')).join('\n')};
const lastCol=r=>{let k=r.length-1;while(k>0&&(r[k]===''||r[k]===undefined))k--;return r[k]};
['ПЛАН','ФАКТ','ОТКЛОНЕНИЕ (факт − план)'].forEach(t=>{const i=R.findIndex(r=>r[0]===t);console.log('\n'+t);R.slice(i+1,i+9).forEach(r=>{if(r[0])console.log('  '+String(r[0]).padEnd(36),'итого:',lastCol(r))})});
const j=R.findIndex(r=>r[0]==='ПЛАНИРОВАНИЕ РЕСУРСОВ');
console.log('\nПЛАНИРОВАНИЕ РЕСУРСОВ (по партиям):');R.slice(j,j+14).filter(r=>r[0]).forEach(r=>console.log('  '+String(r[0]).padEnd(46),'|',String(r[1]??'').replace(/\n/g,' ⏎ ').slice(0,70)));
const k=R.findIndex(r=>r[0]==='ПРИЧИНЫ ОТСТАВАНИЯ');console.log('\nПричины:',k>=0?R.slice(k+1,k+3).map(r=>r[0]+' → '+String(r[1]).split('\n')[0]).join(' / '):'нет');
console.log('alerts:',ctx.__alerts.length,'|',ctx.__alerts[0].split('\n')[0]);
