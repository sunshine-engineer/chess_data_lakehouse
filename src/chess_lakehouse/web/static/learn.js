'use strict';
(()=>{
 const A=window.Atlas,{$,el,button,link}=A;
 const state={lessons:[],current:null,flipped:false,session:null,progress:[],libraries:[],examples:[],index:0,source:'historical',library:null,epoch:0};
 const post=(url,value)=>A.request(url,{method:'POST',headers:{'Content-Type':'application/json','X-Study-Action':'1',...(state.session?.user?{'X-CSRF-Token':state.session.user.csrf}:{})},body:JSON.stringify(value)});
 const current=epoch=>A.state.view==='learn'&&A.state.epoch===epoch;
 function attemptId(){
  // randomUUID needs a secure origin; getRandomValues also supports the Docker
  // browser's internal HTTP hostname. This ID is for idempotence, not a secret.
  const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const h=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');return [h.slice(0,8),h.slice(8,12),h.slice(12,16),h.slice(16,20),h.slice(20)].join('-');
 }
 function progress(){const row=state.progress.find(p=>p.lesson_id===state.current.id);$('#lesson-progress').textContent=state.session?.authenticated?row?`${row.attempts} saved attempt${row.attempts===1?'':'s'} · ${row.successes} goal${row.successes===1?'':'s'} met.`:'Your attempts will be saved privately.':'Guest practice. Sign in to save progress.';}
 async function reset(){const item=state.current,epoch=A.state.epoch;$('#lesson-feedback').textContent='Select a piece, then a destination. Keyboard: arrows to navigate, Enter to select.';const board=await post('/api/learn/board',{lesson_id:item.id});if(!current(epoch)||state.current!==item)return;
  window.ChessBoard.render($('#lesson-board'),board,{flipped:state.flipped,onMove:async move=>{try{const value={lesson_id:item.id,move,attempt_id:attemptId()};const result=await post(state.session?.authenticated?'/api/personal/learning/attempt':'/api/learn/attempt',value);if(!current(epoch)||state.current!==item)return;$('#lesson-feedback').textContent=result.feedback+(result.saved?' Saved to your account.':'');window.ChessBoard.render($('#lesson-board'),{fen:result.fen},{flipped:state.flipped});if(result.saved){const p=await A.request('/api/personal/learning/progress');if(!current(epoch)||state.current!==item)return;state.progress=p.lessons;progress();}}catch(e){if(current(epoch))A.notice(e.message);}}});
 }
 async function choose(item){state.current=item;state.index=0;$('#lesson-heading').replaceChildren(el('h2',item.title));$('#lesson-prompt').textContent=item.prompt;$('#lesson-ideas').replaceChildren();const list=el('ul',null,'lesson-ideas');item.ideas.forEach(s=>list.append(el('li',s)));$('#lesson-ideas').append(list);$('#lesson-menu').querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.lesson===item.id));progress();example();await reset();}
 function example(){const item=state.current,examples=state.examples.filter(p=>p.phase===item.phase),box=$('#learn-example');box.replaceChildren();
  if(!examples.length){box.append(el('p',state.source==='personal'?'No '+item.phase+' example in the selected 30-game sample. Try another library or the historical sample.':'No historical '+item.phase+' examples are published yet. Import the teaching sample using the archive guide.','muted'));return;}
  const p=examples[state.index%examples.length],title=el('p',`${state.source==='personal'?'Your game':'Archive game'} ${p.game_id} · before half-move ${p.ply+1} · ${p.fen.split(' ')[1]==='w'?'White':'Black'} to move`,'learn-example-details');
  const board=el('div',null,'chess-board learn-example-board'),reflection=el('p',item.reflection);box.append(title,reflection,board);window.ChessBoard.render(board,{fen:p.fen});
  let shown=false;const replay=button('Reveal the recorded move',()=>{shown=!shown;window.ChessBoard.render(board,{fen:shown?p.next_fen:p.fen});replay.textContent=shown?'Recorded: '+p.played_san+' · return to position':'Reveal the recorded move';});box.append(replay,el('p','This is an observed move, not an engine recommendation. Consider the opponent’s response.','learn-source-note'));
  box.append(state.source==='personal'?link('Open this game in Study →',`#personal/${p.library_id}/games/${encodeURIComponent(p.game_id)}?snapshot=${p.snapshot_id}&ply=${p.ply}`):link('Open source game ↗','https://lichess.org/'+p.game_id));
 }
 function focus(data){const box=$('#learn-focus');box.replaceChildren();box.hidden=!data.recommendations?.length;for(const r of data.recommendations||[]){const card=el('article',null,'panel');card.append(el('p',r.source,'eyebrow'),el('h3',r.title),el('p',r.explanation),button('Open related lesson',()=>choose(state.lessons.find(l=>l.id===r.lesson_id))),link('Review the evidence game →',`#personal/${data.library_id}/games/${r.game_id}?snapshot=${data.snapshot_id}&ply=${r.ply}`));box.append(card);}}
 async function source(){const epoch=A.state.epoch;state.source=$('#learn-source').value;state.index=0;$('#learn-library-label').hidden=state.source!=='personal';$('#learn-example').replaceChildren(el('p','Loading examples…'));A.notice('');
  $('#learn-focus').hidden=true;
  try{
   if(state.source==='personal'){
    if(!state.session?.authenticated){state.examples=[];$('#learn-source-note').textContent='Sign in to connect lessons to your own library.';$('#learn-example').replaceChildren(link('Open Personal Study to sign in →','#personal'));return;}
    if(!state.libraries.length){state.examples=[];$('#learn-source-note').textContent='Import a game library in Personal Study first.';example();return;}
    const library=$('#learn-library').value||state.libraries[0].id;const data=await post('/api/personal/learning/examples',{library_id:library});if(!current(epoch)||state.source!=='personal'||$('#learn-library').value!==library)return;state.examples=data.examples;focus(data);$('#learn-source-note').textContent=`${data.games_examined} example games examined from ${A.number(data.library_games)} games in this collection.`;$('#learn-method').textContent=data.method;
   }else{
    const data=await A.request('/api/learn/examples');if(!current(epoch)||state.source!=='historical')return;state.examples=data.examples.flatMap(r=>r.payload.positions.map(p=>({...p,game_id:r.game_id,month:r.source_month})));$('#learn-source-note').textContent=data.datasets.map(d=>`${d.source_month.slice(0,7)}: ${A.number(d.sample_games)} teaching games sampled from ${A.number(d.input_games)} source records.`).join(' ')+(data.truncated?` Showing ${A.number(data.returned_games)} of ${A.number(data.published_sample_games)} published teaching games; newest source months first.`:'');$('#learn-method').textContent=data.datasets.map(d=>d.source_month.slice(0,7)+': '+d.method).join(' ');
   }example();
  }catch(e){if(current(epoch)){state.examples=[];$('#learn-example').replaceChildren(el('p','Examples could not be loaded. Retry with the source selector.','callout'));A.notice(e.message);}}
 }
 A.pages.learn=()=>A.load(async signal=>{
  const lessons=await A.request('/api/learn',{},signal);let session={authenticated:false},libraries=[],p=[];
  try{session=await A.request('/api/personal/session',{},signal);if(session.authenticated){libraries=(await A.request('/api/personal/libraries',{},signal)).libraries;p=(await A.request('/api/personal/learning/progress',{},signal)).lessons;}}catch(e){if(e.name==='AbortError')throw e;}
  return {lessons:lessons.lessons,session,libraries,progress:p};
 },d=>{Object.assign(state,d);state.examples=[];$('#learn-focus').hidden=true;state.source=$('#learn-source').value;const menu=$('#lesson-menu');menu.replaceChildren();for(const phase of ['opening','middlegame','endgame']){menu.append(el('h3',phase));for(const item of state.lessons.filter(l=>l.phase===phase)){const b=button(item.title,()=>choose(item));b.dataset.lesson=item.id;menu.append(b);}}
  $('#learn-library').replaceChildren(...state.libraries.map(l=>{const o=el('option',l.player+' · '+A.number(l.games)+' games');o.value=l.id;return o;}));
  choose(state.lessons[0]).catch(e=>A.notice(e.message));source();
 });
 $('#lesson-reset').addEventListener('click',()=>reset().catch(e=>A.notice(e.message)));$('#lesson-flip').addEventListener('click',()=>{state.flipped=!state.flipped;reset().catch(e=>A.notice(e.message));});
 $('#learn-source').addEventListener('change',source);$('#learn-library').addEventListener('change',source);$('#learn-next').addEventListener('click',()=>{++state.index;example();});
})();
