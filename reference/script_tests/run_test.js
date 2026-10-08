const {makeEnv,Sheet}=require('./harness');
const file=process.argv[2]; const S=process.argv[3]; const byLevel=process.argv[4]||'';
const {ctx,ss,sheets}=makeEnv(file);
const day=n=>{const d=new Date();d.setHours(0,0,0,0);d.setDate(d.getDate()+n);return d};
const mk=(name,rows)=>{const s=ss.insertSheet(name);s.getRange(1,1,rows.length,rows[0].length).setValues(rows)};
mk('Заказы',[['ID','Проект','Изделие','Кол-во','Срок','Приоритет'],
 ['1','Проект А','Шкаф',20,day(14),1],['2','Проект А','Шкаф',15,day(20),2],['3','Проект Б','Тумба',30,day(9),3]]);
mk('Операции',[['Изделие','Операция','Норма','Квал','На изделие','Порядок','Блок'],
 ['Шкаф','Раскрой',40,1,1,1,0],['Шкаф','Сборка',5,1,1,2,0],['Шкаф','Покраска',0.5,1,1,3,0],
 ['Тумба','Сборка',8.5,1,2,1,0],['Тумба','Упаковка',12,1,0.5,2,0]]);
mk('Сборщики',[['Имя','Произв.','Квал','Статус','Проекты'],['Иванов',1,2,'доступен',''],['Петров',1.2,2,'доступен',''],['Сидоров',0.8,1,'Доступен',''],['Кузнецов',1,1,'доступен',''],['Орлов',0.9,1,'доступен','']]);
mk('Праздники',[['Дата']]);
if(S!==undefined&&S!==''){ if(file.includes('v3')){const st=new Sheet('x');} }
try{
  if(file.includes('v3')){ ctx.ensureSettingsSheet(ss); const st=sheets['Настройки']; st.data[1][1]=Number(S||100); st.data[2][1]=byLevel; }
  ctx.replanAll();
}catch(e){console.log('ERROR',e.stack.split('\n').slice(0,4).join('\n'));process.exit(1)}
const out=sheets['Задания на день'].data.slice(1);
const qtys=out.map(r=>r[5]);
const frac=qtys.filter(q=>!Number.isInteger(q));
console.log('строк плана:',out.length,'| дробных значений:',frac.length,frac.slice(0,5));
const byOrder={};out.forEach(r=>{const [d,m,y]=r[0].split('.');const k=`${y}-${m}-${d}`;(byOrder[r[1]]=byOrder[r[1]]||[]).push(k)});
Object.keys(byOrder).forEach(id=>{const a=byOrder[id].sort();console.log('заказ',id,'с',a[0],'по',a[a.length-1])});
console.log('листы:',Object.keys(sheets).join(' | '));
console.log('alerts:',ctx.__alerts.map(a=>a.slice(0,200)));
module.exports={out,sheets,ctx};
if(process.env.DUMP){const r=sheets[Object.keys(sheets).find(n=>n.startsWith('Отчёт: Проект А'))];console.log(r.data.filter(x=>x[0]&&/критическ|нехват|Просроч|Как закрыть|Нужно добавить|Требуется|Успеем/i.test(String(x[0]))).map(x=>x.slice(0,2).join(' = ').replace(/\n/g,' / ')).join('\n'))}
