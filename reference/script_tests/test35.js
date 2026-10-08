const {makeEnv}=require('./harness');
const file=process.argv[2]||'../Code_v3.5.gs';
const {ctx,ss,sheets}=makeEnv(file);
const day=n=>{const d=new Date();d.setHours(0,0,0,0);d.setDate(d.getDate()+n);return d};
const mk=(name,rows)=>{const s=ss.insertSheet(name);s.getRange(1,1,rows.length,rows[0].length).setValues(rows)};
mk('Заказы',[['ID','Проект','Изделие','Кол-во','Срок','Приоритет'],
 ['1','Проект А','Шкаф',20,day(14),1],['2','Проект А','Шкаф',15,day(20),2],['3','Проект Б','Тумба',30,day(9),3],['4','Проект В','Кресло',10,day(10),1],['5','Проект Г','Тумба',5,day(-3),1]]);
mk('Операции',[['Изделие','Операция','Норма','Квал','На изделие','Порядок','Блок'],['Шкаф','Раскрой',40,1,1,1,0],['Шкаф','Сборка',5,1,1,2,0],['Шкаф','Покраска',0.5,1,1,3,0],['Тумба','Сборка',8.5,1,2,1,0],['Тумба','Упаковка',12,1,0.5,2,0]]);
mk('Сборщики',[['Имя','Произв.','Квал','Статус','Проекты'],['Иванов',1,2,'доступен',''],['Петров',1.2,2,'доступен',''],['Сидоров',0.8,1,'доступен',''],['Кузнецов',1,1,'доступен',''],['Орлов',0.9,1,'доступен','']]);
mk('Праздники',[['Дата']]);
ctx.replanAll();
const S=sheets['Статус заказов'].data;
console.log(S[0].slice(8).join(' | '));
S.slice(1).forEach(r=>console.log(String(r[0]).padEnd(3),String(r[1]).padEnd(9),String(r[10]).padEnd(15),'|',r[11]===undefined?'':r[11]));
for (const n of Object.keys(sheets).filter(n=>n.startsWith('Отчёт: Проект Б')||n.startsWith('Отчёт: Проект Г'))){
  const R=sheets[n].data; const j=R.findIndex(r=>r[0]==='ПЛАНИРОВАНИЕ РЕСУРСОВ'); console.log('\n'+n);
  R.slice(j,j+12).filter(r=>r[0]).forEach(r=>console.log('  ',String(r[0]).padEnd(36),'|',String(r[1]??'').replace(/\n/g,' ⏎ ').slice(0,90)));
}
