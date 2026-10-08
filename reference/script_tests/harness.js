// Мини-эмулятор Apps Script API для запуска скрипта планирования в Node
const vm = require('vm'), fs = require('fs');
function chain(){ const f=function(){return p}; const p=new Proxy(f,{get:(t,k)=>k===Symbol.toPrimitive?()=>'':p,apply:()=>p}); return p; }
class Sheet {
  constructor(name){this.name=name;this.data=[];this.widths={};this.fr=0;this.fc=0;}
  setColumnWidth(c,w){this.widths[c]=w}
  setFrozenRows(n){this.fr=n}
  setFrozenColumns(n){this.fc=n}
  getName(){return this.name}
  setConditionalFormatRules(r){this.rules=r}
  getLastRow(){return this.data.length}
  getLastColumn(){return Math.max(0,...this.data.map(r=>r.length))}
  clear(){this.data=[];return this}
  deleteRow(i){this.data.splice(i-1,1)}
  getRange(r,c=1,nr=1,nc=1){
    const sh=this; const rng={a1:(typeof r==='string'?r:null),
      getValues:()=>Array.from({length:nr},(_,i)=>Array.from({length:nc},(_,j)=>{const v=(sh.data[r-1+i]||[])[c-1+j];return v===undefined?'':v})),
      setValues:(vals)=>{vals.forEach((row,i)=>{sh.data[r-1+i]=sh.data[r-1+i]||[];row.forEach((v,j)=>{sh.data[r-1+i][c-1+j]=v})});return proxy},
      setValue:(v)=>{sh.data[r-1]=sh.data[r-1]||[];sh.data[r-1][c-1]=v;return proxy},
      getValue:()=>((sh.data[r-1]||[])[c-1]??''),
      sort:()=>proxy,
    };
    const proxy=new Proxy(rng,{get:(t,k)=>k in t?t[k]:()=>proxy});
    return proxy;
  }
}
const sheetProxy=(sh)=>new Proxy(sh,{get:(t,k)=>k in t?t[k]:()=>chain()});
function makeEnv(scriptPath, today){
  const sheets={};
  const ss={
    getSheetByName:n=>sheets[n]?sheetProxy(sheets[n]):null,
    insertSheet:n=>{sheets[n]=new Sheet(n);return sheetProxy(sheets[n])},
    getSheets:()=>Object.values(sheets).map(sheetProxy),
    deleteSheet:(p)=>{delete sheets[p.getName()]},
    setActiveSheet:()=>{}, getSpreadsheetTimeZone:()=>'UTC',
  };
  const fmt=(d,tz,f)=>{const p=n=>String(n).padStart(2,'0');d=new Date(d);
    return f.replace('yyyy',d.getFullYear()).replace('MM',p(d.getMonth()+1)).replace('dd',p(d.getDate())).replace('HH',p(d.getHours())).replace('mm',p(d.getMinutes()))};
  const RealDate=Date;
  const ctx={console,Math,JSON,Set,Map,Array,Object,Number,String,isNaN,Date:RealDate,
    SpreadsheetApp:new Proxy({getActiveSpreadsheet:()=>ss,newConditionalFormatRule:()=>{const b={};const api={whenFormulaSatisfied:f=>{b.formula=f;return api},setBackground:c=>{b.bg=c;return api},setRanges:r=>{b.ranges=r.map(x=>x.a1);return api},build:()=>b};return api},getUi:()=>({alert:m=>{ctx.__alerts.push(m);ctx.__alertSheets=Object.keys(sheets).length},createMenu:()=>chain()}),newDataValidation:()=>chain(),BandingTheme:{},BorderStyle:{}},{get:(t,k)=>k in t?t[k]:(typeof k==='string'&&k.startsWith('new')?()=>chain():chain())}),
    Session:{getScriptTimeZone:()=>'UTC'}, Utilities:{formatDate:fmt}, __alerts:[]};
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(scriptPath,'utf8'),ctx);
  return {ctx,ss,sheets};
}
module.exports={makeEnv,Sheet};
