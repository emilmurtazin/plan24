const {makeEnv}=require('./harness');
const file=process.argv[2]||'../Code_v3.5.gs';
const {ctx,ss,sheets}=makeEnv(file);
const day=n=>{const d=new Date();d.setHours(0,0,0,0);d.setDate(d.getDate()+n);return d};
const mk=(name,rows)=>{const s=ss.insertSheet(name);s.getRange(1,1,rows.length,rows[0].length).setValues(rows)};
mk('Заказы',[['ID','Проект','Изделие','Кол-во','Срок','Приоритет'],['1','Проект А','Шкаф',20,day(14),1]]);
mk('Операции',[['Изделие','Операция','Норма','Квал','На изделие','Порядок','Блок'],['Шкаф','Раскрой',40,1,1,1,0],['Шкаф','Сборка',5,1,1,2,0]]);
mk('Сборщики',[['Имя','Произв.','Квал','Статус','Проекты'],['Иванов',1,2,'доступен',''],['Петров',1.2,2,'доступен','']]);
mk('Праздники',[['Дата']]);
// «старый» лист Факт без столбца I (как у пользователя)
const f=ss.insertSheet('Факт'); f.getRange(1,1,1,8).setValues([['Дата','ID','Проект','Изделие','Операция','Сборщик','План','Кол-во операций']]);
ctx.replanAll();
const F=sheets['Факт']; console.log('Заголовок I1:',F.data[0][8],'| ширина I:',F.widths[9]);
const rule=F.rules.find(r=>r.ranges&&r.ranges[0]==='I2:I1000'); console.log('Правило:',rule.formula,'| цвет',rule.bg,'| диапазон',rule.ranges[0]);
// вычисление формулы правила для строки
function yellow(row){const cell=c=>{const v=row[c.charCodeAt(0)-65];return v===undefined||v===null?'':v};
  const js=rule.formula.slice(1).replace(/<>/g,'!==').replace(/([^!<>=])=(?!=)/g,'$1===').replace(/\$([A-Z])2/g,(_,c)=>JSON.stringify(cell(c)));
  return !!eval(js)}
const T=(name,row,exp)=>console.log((yellow(row)===exp?'OK  ':'FAIL'),name);
const d='01.10.2026';
T('план 12, факт 9, причины нет  → жёлтая',[d,'1','','','','',12,9,''],true);
T('план 12, факт 12              → нет',[d,'1','','','','',12,12,''],false);
T('факт не внесён                → нет',[d,'1','','','','',12,'',''],false);
T('план 12, факт 9, причина есть → нет',[d,'1','','','','',12,9,'ждали фурнитуру'],false);
T('факт 14 > плана 12            → жёлтая',[d,'1','','','','',12,14,''],true);
T('внеплановая работа (план пуст)→ жёлтая',[d,'1','','','','','',5,''],true);
T('явный 0 при плане 12          → жёлтая',[d,'1','','','','',12,0,''],true);
T('пустая строка                 → нет',['','','','','','','','',''],false);
// причины не «уезжают» при сортировке, нули с причиной не удаляются
const rows=F.data.slice(1); console.log('строк в Факте:',rows.length);
const target=rows[1]; const key=x=>x.slice(0,6).join('|');
target[7]=target[6]-1; target[8]='Ждали материал'; const keep=key(target);
const zero=rows[2]; zero[7]=0; zero[8]='Отсутствовал (больничный)'; const zkey=key(zero);
const dele=rows[3]; dele[7]=0; const dkey=key(dele); // ноль без причины → удаляется как раньше
ctx.replanAll();
const after=sheets['Факт'].data.slice(1);
const f1=after.find(r=>key(r)===keep); console.log('причина осталась у своей строки:',f1&&f1[8]==='Ждали материал'?'OK':'FAIL');
const f2=after.find(r=>key(r)===zkey); console.log('ноль с причиной сохранён:',f2&&f2[8]?'OK':'FAIL');
console.log('ноль без причины удалён (или пересоздан планом):',after.filter(r=>key(r)===dkey).every(r=>r[7]===''||r[7]===undefined)?'OK':'FAIL');
console.log('alerts:',ctx.__alerts.length);
