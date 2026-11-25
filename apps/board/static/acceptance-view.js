'use strict';
function acceptanceBadge(asset) {
  const runs=new Set((asset.copies||[]).filter(c=>c.arrived&&c.run).map(c=>c.run));
  const rows=(asset.manual_acceptance||[]).slice().sort((a,b)=>b.id-a.id);
  const current=rows.find(row=>runs.has(row.record.backup_run));
  if(!current)return {text:rows.length?'人工：仅有历史批次记录':'人工：尚未验收',kind:'warn'};
  return {text:'人工：'+(current.record.status==='passed'?'通过':'未通过')+' · '+current.record.backup_run,
    kind:current.record.status==='passed'?'good':'bad'};
}
if(typeof module!=='undefined')module.exports=acceptanceBadge;
