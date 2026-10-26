'use strict';
// This never invokes npx or the Electron npm wrapper; no implicit binary download.
const fs=require('node:fs');const path=require('node:path');const {spawn}=require('node:child_process');
const {inside}=require('../managed-export.cjs');const root=path.resolve(__dirname,'..');const repo=path.dirname(root);
const mode=process.argv[2];
function blocked(code){const report={status:'BLOCKED',code,requiredElectron:require('../package.json').devDependencies.electron,nativePassed:false,installationPerformed:false};if(mode==='native-test'){const out=path.resolve(repo,process.env.DSF_ARTIFACT_DIR||'artifacts/stage3');fs.mkdirSync(out,{recursive:true});fs.writeFileSync(path.join(out,'desktop-native-result.json'),JSON.stringify(report,null,2));}console.log(JSON.stringify(report));process.exitCode=2;}
if(!['app','native-test'].includes(mode)||process.argv.length!==3){blocked('INVALID_LAUNCH_MODE');}
else if(process.env.DSF_ELECTRON_RUN_APPROVED!=='1'){blocked('ELECTRON_RUN_REQUIRES_PARENT_APPROVAL');}
else{
 const local=path.join(root,'node_modules','electron','dist');const approved=path.join(root,'approved-runtime');
 const candidate=process.env.DSF_ELECTRON_EXECUTABLE||path.join(local,process.platform==='win32'?'electron.exe':'electron');
 if(!path.isAbsolute(candidate)||!fs.existsSync(candidate)||![local,approved].some(directory=>fs.existsSync(directory)&&inside(fs.realpathSync(directory),fs.realpathSync(candidate))))blocked('PROJECT_ELECTRON_RUNTIME_MISSING');
 else{
  const data=path.join(repo,'data-desktop');const temp=path.join(data,'tmp');fs.mkdirSync(temp,{recursive:true});
  const env={...process.env,TMP:temp,TEMP:temp,TMPDIR:temp};delete env.NODE_OPTIONS;delete env.NODE_PATH;delete env.ELECTRON_RUN_AS_NODE;
  const entry=mode==='app'?root:path.join(root,'test','native-runner.cjs');
  const child=spawn(candidate,['--user-data-dir='+path.join(data,'electron-profile'),entry],{cwd:repo,shell:false,stdio:'inherit',env});
  child.on('error',()=>blocked('ELECTRON_PROCESS_START_FAILED'));child.on('exit',code=>{process.exitCode=code===null?1:code;});
 }
}
