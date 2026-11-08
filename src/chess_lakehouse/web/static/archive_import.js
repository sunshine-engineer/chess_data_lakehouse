'use strict';
window.ArchiveImports={mount:async function(panel){
 const A=window.Atlas,{el,button,link}=A,box=el('div'),status=el('p','Sign in to manage imports.','muted');panel.querySelector('#live-archive-controls')?.remove();box.id='live-archive-controls';panel.append(box);box.append(el('h3','Fetch a monthly archive'),el('p','Choose the latest standard archive, a published month, or an official PGN/torrent link. Each job publishes one complete month. Archives, frames, parsed data and database tables need working disk space.'),status);
 async function openControls(){
 let session;try{session=await A.request('/api/personal/session');}catch(e){status.textContent=e.message;return;}if(!session.authenticated){box.append(link('Sign in →','#personal'));return;}
 const post=(path,value)=>A.request('/api/personal/archives/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-Study-Action':'1','X-CSRF-Token':session.user.csrf},body:JSON.stringify(value)});
 const form=el('form',null,'compact-form'),mode=el('select'),month=el('select'),url=el('input'),ceiling=el('input'),engine=el('select'),auto=el('input'),list=el('div'),detail=el('p',null,'muted');let timer,rows=[],subscriptionLoaded=false;
 for(const [v,t] of [['latest','Latest available month'],['month','Choose published month'],['url','Official PGN / torrent link']]){const o=el('option',t);o.value=v;mode.append(o);}
 for(const [v,t] of [['python_mp','Parallel Python'],['python','Python'],['spark','Spark']]){const o=el('option',t);o.value=v;engine.append(o);}
 mode.id='archive-source-mode';month.id='archive-month';url.id='archive-source-url';url.type='url';url.placeholder='https://database.lichess.org/standard/…pgn.zst';ceiling.id='archive-download-ceiling';ceiling.type='number';ceiling.min='1';ceiling.max=String(2*1024*1024);ceiling.value='100';ceiling.required=true;engine.id='archive-import-engine';auto.id='archive-auto-latest';auto.type='checkbox';list.id='archive-job-list';
 const label=(title,input)=>{const n=el('label',title);n.append(input);return n;},ml=label('Published month',month),ul=label('Official link',url);ml.hidden=ul.hidden=true;
 const options=()=>({engine:engine.value,max_download_bytes:Number(ceiling.value)*1048576});
 function describe(){const r=rows.find(r=>r.month===month.value)||rows[0];detail.textContent=r?`${mode.value==='latest'?'Latest: ':''}${r.month} · ${r.display_size} compressed · ${A.number(r.expected_games)} games. The published SHA256 is verified before loading. Torrent links use the matching official HTTPS PGN.`:'';}
 mode.onchange=()=>{ml.hidden=mode.value!=='month';ul.hidden=mode.value!=='url';url.required=mode.value==='url';describe();};month.onchange=describe;
 async function load(){try{status.textContent='Reading official catalog…';rows=(await A.request('/api/personal/archives/catalog')).archives;month.replaceChildren(...rows.map(r=>{const o=el('option',`${r.month} · ${r.display_size} · ${A.number(r.expected_games)} games`);o.value=r.month;return o;}));describe();status.textContent='Choose a month and compressed-download ceiling before starting.';}catch(e){status.textContent=e.message;}}
 const submit=el('button','Import selected month');submit.type='submit';form.append(label('Selection',mode),ml,ul,label('Maximum compressed download (MiB)',ceiling),label('Pipeline',engine),submit,button('Refresh official catalog',load));box.append(form,detail,label('Automatically import each new latest month within this ceiling',auto),button('Save automatic latest settings',async()=>{try{await post('subscription',{enabled:auto.checked,...options()});status.textContent=auto.checked?'Latest-month import enabled; worker checks hourly.':'Automatic latest import paused.';await jobs();}catch(e){status.textContent=e.message;}}),list);
 form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;try{const value=options();if(mode.value==='url')value.url=url.value.trim();if(mode.value==='month')value.month=month.value;const job=await post('jobs',value);status.textContent=`Queued ${job.source.month}.`;await jobs();}catch(e){status.textContent=e.message;}finally{submit.disabled=false;}};
 async function jobs(){
  clearTimeout(timer);if(!box.isConnected)return;
  try{const data=await A.request('/api/personal/archives/jobs');if(A.state.view!=='pipelines'||!box.isConnected)return;list.replaceChildren();
   if(data.subscription){if(!subscriptionLoaded){auto.checked=data.subscription.enabled;ceiling.value=String(data.subscription.max_download_bytes/1048576);engine.value=data.subscription.engine;subscriptionLoaded=true;}if(data.subscription.error)list.append(el('p','Automatic latest: '+data.subscription.error,'callout'));}
   for(const job of data.jobs){
    const card=el('article',null,'panel');card.append(el('h4',`${job.source.month} · ${job.engine} · ${job.status}`),el('p',job.phase),el('p',job.progress.bytes_received?`${A.number(job.progress.bytes_received/1048576)} MiB received${job.progress.total_bytes?' / '+A.number(job.progress.total_bytes/1048576)+' MiB':''}`:JSON.stringify(job.progress),'muted'));
    if(job.error)card.append(el('p',job.error,'callout'));
    for(const [action,title,allowed] of [['pause','Pause',['queued','running']],['resume','Resume / retry',['paused','failed']],['cancel','Cancel',['queued','running','paused','failed']]])if(allowed.includes(job.status))card.append(button(title,async()=>{try{await post('control',{id:job.id,action});await jobs();}catch(e){status.textContent=e.message;}}));list.append(card);
   }
  }catch(e){status.textContent=e.message;}
  timer=setTimeout(()=>{if(A.state.view==='pipelines'&&box.isConnected)jobs();},5000);
 }
 await load();await jobs();
 }
 const openButton=button('Open archive import controls',async()=>{openButton.disabled=true;try{await openControls();}finally{openButton.disabled=false;openButton.hidden=!!box.querySelector('form');}});box.append(openButton);
}};
