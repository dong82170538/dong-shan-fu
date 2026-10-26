'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {randomUUID}=require('node:crypto');
function inside(root,candidate){const relative=path.relative(root,candidate);return relative===''||(!relative.startsWith('..'+path.sep)&&relative!=='..'&&!path.isAbsolute(relative));}
function exportWriter(dataDir){
 const directory=path.join(dataDir,'exports');fs.mkdirSync(directory,{recursive:true});
 if(!inside(fs.realpathSync(dataDir),fs.realpathSync(directory)))throw Error('INVALID_EXPORT_DIRECTORY');
 return async(taskId,format,bytes,assertCurrent)=>{
  if(!/^[a-f0-9]{32}$/.test(taskId)||!['json','csv'].includes(format)||!Buffer.isBuffer(bytes)||bytes.length>12_000_000)throw Error('INVALID_EXPORT');
  const name='task-'+taskId+'-'+randomUUID()+'.'+format;const target=path.join(directory,name);const temp=target+'.partial';
  try{assertCurrent();await fs.promises.writeFile(temp,bytes,{flag:'wx'});assertCurrent();fs.renameSync(temp,target);return {fileName:name,format,bytes:bytes.length,directory:'managed exports'};}
  finally{try{await fs.promises.unlink(temp);}catch(error){if(error.code!=='ENOENT')throw Error('EXPORT_CLEANUP_FAILED');}}
 };
}
module.exports={exportWriter,inside};
