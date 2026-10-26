'use strict';
const fs=require('node:fs'),path=require('node:path');
const repo=path.resolve(__dirname,'../..');
const root=path.resolve(process.env.DSF_TEST_TEMP_ROOT||path.join(repo,'artifacts/test-temp'));
const relative=path.relative(repo,root);
if(relative.startsWith('..')||path.isAbsolute(relative))throw Error('TEST_TEMP_OUTSIDE_PROJECT');
fs.mkdirSync(root,{recursive:true});const actualRoot=fs.realpathSync(root);const created=new Set();
function temporaryDir(prefix){const target=fs.mkdtempSync(path.join(actualRoot,prefix));created.add(fs.realpathSync(target));return target;}
function removeOwnedDirectory(target){if(!fs.existsSync(target))return;const actual=fs.realpathSync(target),rel=path.relative(actualRoot,actual);if(!created.has(actual)||!rel||rel.startsWith('..')||path.isAbsolute(rel))throw Error('UNOWNED_TEST_DIRECTORY_DELETE_REFUSED');fs.rmSync(actual,{recursive:true,force:true});created.delete(actual);}
module.exports={temporaryDir,removeOwnedDirectory};
