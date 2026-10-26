'use strict';
const fs=require('node:fs');const path=require('node:path');const {spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),repo=path.dirname(root);
function runTests({files,timeoutMs=60000,out=path.resolve(repo,process.env.DSF_ARTIFACT_DIR||'artifacts/stage4')}={}){
 fs.mkdirSync(out,{recursive:true});
 if(!files)files=fs.readdirSync(path.join(root,'test')).filter(name=>name.endsWith('.test.cjs')).sort().map(name=>path.join(root,'test',name));
 if(files.length===0)throw Error('NO_TEST_FILES');
 const ownershipFile=path.join(out,'desktop-process-ownership.json');fs.rmSync(ownershipFile,{force:true});
 // The Python supervisor owns the deadline and all descendants. Killing only
 // this Node runner with spawnSync(timeout) would bypass its finally blocks.
 const python=process.env.DSF_PYTHON||(process.platform==='win32'?'python':'python3');
 const result=spawnSync(python,[path.join(repo,'scripts','owned_process.py'),'--timeout',String(timeoutMs/1000),'--report',ownershipFile,'--cwd',root,'--owner-pid',String(process.pid),'--',process.execPath,'--test','--test-reporter=tap',...files],{cwd:repo,encoding:'utf8',env:{...process.env,DSF_ARTIFACT_DIR:path.relative(repo,out),PYTHONUTF8:'1'},maxBuffer:20_000_000});
 const output=(result.stdout||'')+(result.stderr||'');process.stdout.write(output);fs.writeFileSync(path.join(out,'desktop-unit-output.txt'),output);
 let ownership;try{ownership=JSON.parse(fs.readFileSync(ownershipFile,'utf8'));}catch{ownership={status:'FAIL',reason:'SUPERVISOR_REPORT_MISSING',cleanup_unconfirmed:true};}
 const count=Number(output.match(/^# tests (\d+)$/m)?.[1]||0),status=ownership.status==='BLOCKED'?'BLOCKED':result.status===0&&ownership.status==='PASS'&&count>0?'PASS':'FAIL';
 const report={status,tests:count,scope:'Node protocol/preload doubles plus actual owned Python lifecycle/SQLite; not native Electron IPC',nativePassed:false,ownership,files:files.map(file=>path.basename(file))};
 fs.writeFileSync(path.join(out,'desktop-unit-result.json'),JSON.stringify(report,null,2));return report;
}
if(require.main===module){const result=runTests();process.exitCode=result.status==='PASS'?0:result.status==='BLOCKED'?2:1;}
module.exports={runTests};
