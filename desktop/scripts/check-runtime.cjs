'use strict';
const fs=require('node:fs');const path=require('node:path');
const root=path.resolve(__dirname,'..');const expected=require('../package.json').devDependencies.electron;
const projectExecutable=path.join(root,'node_modules','electron','dist',process.platform==='win32'?'electron.exe':'electron');
const metadata=path.join(root,'node_modules','electron','package.json');
let installed=null;try{installed=JSON.parse(fs.readFileSync(metadata,'utf8')).version;}catch{}
const result={status:fs.existsSync(projectExecutable)&&installed===expected?'AVAILABLE_REQUIRES_APPROVAL':'BLOCKED',expectedElectron:expected,projectPackageVersion:installed,projectExecutablePresent:fs.existsSync(projectExecutable),runtimeExecuted:false,installationPerformed:false,internalCodexRuntimeUsed:false,source:'official Electron/npm only',requirements:{node:'>=24.19.0',python:'>=3.12',windows:'Windows x64; exact supported OS minimum not yet verified',nativeTest:'npm run test:native after approved runtime placement'}};
console.log(JSON.stringify(result,null,2));
