const {makeEnv}=require('./harness');
const file=process.argv[2]||'../Code_v3.5.gs';
const {ctx,ss,sheets}=makeEnv(file);
const day=n=>{const d=new Date();d.setHours(0,0,0,0);d.setDate(d.getDate()+n);return d};
const mk=(name,rows)=>{const s=ss.insertSheet(name);s.getRange(1,1,rows.length,rows[0].length).setValues(rows)};
mk('Заказы',[['ID','Проект','Изделие','Кол-во','Срок','Приоритет'],['1','Проект А','Шкаф',20,day(14),1],['2','Проект А','Шкаф',15,day(20),2]]);
mk('Операции',[['Изделие','Операция','Норма','Квал','На изделие','Порядок','Блок'],['Шкаф','Раскрой',40,1,1,1,0],['Шкаф','Сборка',5,1,1,2,0]]);
mk('Сборщики',[['Имя','Произв.','Квал','Статус','Проекты'],['Иванов',1,2,'доступен',''],['Петров',1.2,2,'доступен','']]);
mk('Праздники',[['Дата']]);
ctx.replanAll();
const F=sheets['Факт']; const rows=F.data.slice(1);
console.log('строк в Факте:',rows.length);
const plan=r=>r[6];
// сценарии: разные написания одной причины, одна без причины, одно перевыполнение, ноль с причиной, не внесённый факт
rows[0][7]=plan(rows[0])-3; rows[0][8]='Ждали фурнитуру';
rows[1][7]=plan(rows[1])-2; rows[1][8]='ждали  фурнитуру ';
rows[2][7]=0;               rows[2][8]='Больничный';
rows[3][7]=plan(rows[3])-1; rows[3][8]='';
rows[4][7]=plan(rows[4])+2; rows[4][8]='';   // перевыполнение — не отставание
ctx.replanAll();
const sh=sheets['Отчёт: Проект А'];
const i=sh.data.findIndex(r=>r[0]==='ПРИЧИНЫ ОТСТАВАНИЯ');
console.log('раздел найден в строке',i+1);
sh.data.slice(i,i+6).forEach(r=>console.log('|',String(r[0]).padEnd(20),'|',String(r[1]??'').replace(/\n/g,' ⏎ ')));
const j=sh.data.findIndex(r=>r[0]==='ПЛАНИРОВАНИЕ РЕСУРСОВ'); console.log('раздел ресурсов ниже причин:',j>i);
// чистый проект без отклонений
const rowsB=sheets['Факт'].data.slice(1);
console.log('alerts:',ctx.__alerts.length);
