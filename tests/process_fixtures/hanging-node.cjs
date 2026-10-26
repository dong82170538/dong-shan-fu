'use strict';
// A synthetic Node test runner that owns a Python child/grandchild tree and
// never settles. The test harness, not this finally block, must clean it up.
const {test}=require('node:test');const {spawn}=require('node:child_process');const path=require('node:path');
test('synthetic owned heartbeat tree never settles',async()=>{
 const child=spawn(process.env.DSF_PYTHON||'python3',[path.join(__dirname,'heartbeat_tree.py'),'--folder',process.env.DSF_HEARTBEAT_DIR,'--nonce',process.env.DSF_HEARTBEAT_NONCE],{shell:false,stdio:'ignore'});
 const timer=setInterval(()=>{},1000);
 try{await new Promise(()=>{});}finally{clearInterval(timer);child.kill('SIGTERM');}
});
