'use strict';
const $=id=>document.getElementById(id);
$('fixture-input').value=JSON.stringify({source:'test_fixture',candidate_origin:'https://fixture.invalid',publish:false,selected_skus:[{goods_id:'s1',offer_id:'fixture-1',price:'100.00',old_price:'160.00',min_price:'50.00',currency:'RUB'}]},null,2);
async function message(value){return await chrome.runtime.sendMessage(value);}
$('set-backend').addEventListener('click',async()=>{try{$('result').textContent=JSON.stringify(await message({type:'set_fixture_backend',url:$('backend').value}),null,2);}catch{$('result').textContent='MESSAGE_FAILED';}});
$('preview').addEventListener('click',async()=>{$('preview').disabled=true;try{const body=JSON.parse($('fixture-input').value);$('result').textContent=JSON.stringify(await message({type:'preview_fixture',body}),null,2);}catch{$('result').textContent='INVALID_JSON_OR_MESSAGE_FAILED';}finally{$('preview').disabled=false;}});
