const {makeEnv,Sheet}=require('./harness');
const file=process.argv[2]||'../Code_v3.5.gs';
const {ctx,ss,sheets}=makeEnv(file);
const day=n=>{const d=new Date();d.setHours(0,0,0,0);d.setDate(d.getDate()+n);return d};
const mk=(name,rows)=>{const s=ss.insertSheet(name);s.getRange(1,1,rows.length,rows[0].length).setValues(rows)};
mk('Заказы',[['ID','Проект','Изделие','Кол-во','Срок','Приоритет'],
 ['1','Проект А','Шкаф',20,day(14),1],['2','Проект А','Шкаф',15,day(20),2],['3','Проект Б','Тумба',30,day(9),3],
 ['4','Проект В (нет операций)','Кресло',10,day(10),1],['5','Проект Г (нет плана)','Тумба',5,day(10),1]]);
mk('Операции',[['Изделие','Операция','Норма','Квал','На изделие','Порядок','Блок'],
 ['Шкаф','Раскрой',40,1,1,1,0],['Шкаф','Сборка',5,1,1,2,0],['Шкаф','Покраска',0.5,1,1,3,0],['Тумба','Сборка',8.5,1,2,1,0],['Тумба','Упаковка',12,1,0.5,2,0]]);
mk('Сборщики',[['Имя','Произв.','Квал','Статус','Проекты'],['Иванов',1,2,'доступен','1,2,3,4'],['Петров',1.2,2,'доступен','1,2,3,4'],['Сидоров',0.8,1,'доступен','1,2,3,4'],['Кузнецов',1,1,'доступен','1,2,3,4'],['Орлов',0.9,1,'доступен','1,2,3,4']]);
mk('Праздники',[['Дата']]);
// старые артефакты: устаревший отчёт и закреплённая шапка
const old=ss.insertSheet('Отчёт: Старый'); 
const f=ss.insertSheet('Факт'); f.getRange(1,1,1,8).setValues([['Дата','ID','Проект','Изделие','Операция','Сборщик','План','Кол-во операций']]); sheets['Факт'].fr=1; sheets['Факт'].fc=1;
if(process.env.S0){ctx.ensureSettingsSheet(ss);sheets['Настройки'].data[1][1]=0}ctx.replanAll();
console.log('ALERTS всего:',ctx.__alerts.length);
console.log('--- текст сообщения ---\n'+ctx.__alerts[0]);
console.log('--- листов на момент сообщения:',ctx.__alertSheets,'| в конце:',Object.keys(sheets).length,'(равно => сообщение после создания всех отчётов)');
console.log('листы:',Object.keys(sheets).join(' | '));
Object.keys(sheets).filter(n=>n.startsWith('Отчёт: ')||['Факт','Задания на день','Загрузка','Статус заказов','Настройки'].includes(n)).forEach(n=>{const sh=sheets[n];console.log(n.padEnd(22),'закреплено строк/столбцов:',sh.fr,'/',sh.fc, n.startsWith('Отчёт')?`| ширина A=${sh.widths[1]} B=${sh.widths[2]}`:'')});
for(const n of ['Отчёт: Проект А','Отчёт: Проект Б']){
  const sh=sheets[n]; const i=sh.data.findIndex(r=>String(r[0]).includes('ПЛАНИРОВАНИЕ РЕСУРСОВ')||String(r[0]).includes('Планирование ресурсов'));
  console.log('\n=== '+n+' — блок ресурсов ===');
  sh.data.slice(i).filter(r=>r[0]).forEach(r=>console.log(String(r[0]).padEnd(40),'|',String(r[1]??'').replace(/\n/g,' ⏎ ')));
}
