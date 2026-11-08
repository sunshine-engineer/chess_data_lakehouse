'use strict';
(()=>{
 const A=window.Atlas,{$,el,button,link}=A;
 const state={lessons:[],current:null,flipped:false,session:null,progress:[],libraries:[],examples:[],index:0,source:'historical',library:null,epoch:0};
 let build=null,repertoireOffset=0,challenge=null,repertoireTimer=null,repertoireEpoch=0;
 const post=(url,value)=>A.request(url,{method:'POST',headers:{'Content-Type':'application/json','X-Study-Action':'1',...(state.session?.user?{'X-CSRF-Token':state.session.user.csrf}:{})},body:JSON.stringify(value)});
 const current=epoch=>A.state.view==='learn'&&A.state.epoch===epoch;
 function attemptId(){
  // randomUUID needs a secure origin; getRandomValues also supports the Docker
  // browser's internal HTTP hostname. This ID is for idempotence, not a secret.
  const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const h=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');return [h.slice(0,8),h.slice(8,12),h.slice(12,16),h.slice(16,20),h.slice(20)].join('-');
 }
 const mine=(name,value)=>post('/api/personal/repertoire/'+name,value);
 async function repertoireStatus(){
  clearTimeout(repertoireTimer);if(!state.session?.authenticated||!$('#repertoire-library').value)return;
  const library=$('#repertoire-library').value,epoch=repertoireEpoch;
  try{const data=await mine('status',{library_id:library});if(epoch!==repertoireEpoch||A.state.view!=='learn')return;build=data.build;
   $('#repertoire-status').textContent=build?`${build.status} · ${build.source_stage} · `+data.counts.map(r=>`${r.source_month||'your collection'}: ${A.number(r.games)} ${r.status}`).join(' · ')+` · `+data.phases.map(r=>`${r.phase}: ${r.exercises} exercises`).join(' · ')+(build.error?' · '+build.error:''):'No build yet. Start to scan every game in this collection.';
   $('#repertoire-method').textContent=(data.method||'')+(build?.partial_reviews?` ${build.partial_reviews} reviews reached their time budget before checking every decision.`:'');$('#repertoire-pause').disabled=!build||build.status!=='running';await repertoireList();
   if(build?.status==='running')repertoireTimer=setTimeout(()=>{if(A.state.view==='learn')repertoireStatus();},5000);
  }catch(e){A.notice(e.message);}
 }
 const exerciseSource=item=>item.source_month?`${item.source_month} · archive record ${item.source_key.split(':').slice(-2).join(' · ')}`:`your game ${item.source_key}`;
 async function repertoireList(){const box=$('#repertoire-list');if(!build){box.replaceChildren();return;}const id=String(build.id),epoch=repertoireEpoch;const data=await mine('list',{build_id:id,phase:$('#repertoire-phase').value,offset:repertoireOffset});if(epoch!==repertoireEpoch||String(build?.id)!==id)return;box.replaceChildren();for(const item of data.exercises)box.append(button(`${item.phase} · ${exerciseSource(item)} · position ${item.ply} · ${item.successes}/${item.attempts} accepted`,()=>openExercise(item.id)));if(!data.exercises.length)box.append(el('p','No exercises in this phase yet. The build continues through the collection; phases are shown only when qualifying decisions are found.','muted'));$('#repertoire-prev').disabled=!repertoireOffset;$('#repertoire-next').disabled=!data.has_more;}
 async function openExercise(id){
  const epoch=repertoireEpoch;challenge=null;$('#repertoire-board').replaceChildren();$('#repertoire-feedback').textContent='Loading position…';
  const item=await mine('exercise',{exercise_id:id});if(epoch!==repertoireEpoch||A.state.view!=='learn')return;
  challenge=item;$('#repertoire-challenge').hidden=false;$('#repertoire-title').textContent=`${item.phase} · ${exerciseSource(item)} · position ${item.ply}`;$('#repertoire-solution').replaceChildren();$('#repertoire-feedback').textContent='Your move.';
  $('#repertoire-source').replaceChildren();if(item.game_id)$('#repertoire-source').append(link(item.source_month?'Open archive source game ↗':'Review the original game →',item.source_month?'https://lichess.org/'+item.game_id:`#personal/${item.library_id}/game/${item.game_id}?snapshot=${item.snapshot_id}&ply=${item.ply}`));
  window.ChessBoard.render($('#repertoire-board'),item,{flipped:item.turn==='black',onMove:async move=>{
   try{const result=await mine('attempt',{exercise_id:id,move,attempt_id:attemptId()});if(challenge?.id!==item.id||epoch!==repertoireEpoch)return;
    $('#repertoire-feedback').textContent=result.feedback+' Attempt saved.';window.ChessBoard.render($('#repertoire-board'),{fen:result.fen},{flipped:item.turn==='black'});showSolution(result.evidence);await repertoireList();
   }catch(e){A.notice(e.message);}
  }});
 }
 function showSolution(e){const box=$('#repertoire-solution');box.replaceChildren(el('p',e.explanation||e.summary),el('p',`Played: ${e.played_san}. Searched solution: ${e.suggested_san}. Estimated deterioration: ${(e.loss_cp/100).toFixed(2)} pawn units.`));for(const line of e.alternatives||[]){const card=el('div',null,'panel');card.append(el('p',`${line.score_label}: ${line.pv_san.join(' ')}`),el('p',line.explanation));let step=0;const board=el('div',null,'chess-board learn-example-board'),draw=()=>window.ChessBoard.render(board,{fen:line.positions[step]},{flipped:e.player_color==='black'});card.append(board,button('← Back',()=>{step=Math.max(0,step-1);draw();}),button('Next →',()=>{step=Math.min(line.positions.length-1,step+1);draw();}));box.append(card);draw();}box.append(el('p','Engine search estimates. The solution set is not exhaustive; a rejected move is not proof that it loses. '+(e.coverage||''),'muted'));}
 $('#repertoire-start').addEventListener('click',async()=>{try{await mine('start',{library_id:$('#repertoire-library').value,archive_fallback:$('#repertoire-fallback').checked});await repertoireStatus();}catch(e){A.notice(e.message);}});
 $('#repertoire-pause').addEventListener('click',async()=>{try{if(build)await mine('control',{build_id:build.id,action:'pause'});await repertoireStatus();}catch(e){A.notice(e.message);}});
 $('#repertoire-refresh').addEventListener('click',repertoireStatus);$('#repertoire-library').addEventListener('change',()=>{repertoireEpoch++;repertoireOffset=0;challenge=null;$('#repertoire-challenge').hidden=true;repertoireStatus();});$('#repertoire-phase').addEventListener('change',()=>{repertoireOffset=0;repertoireList().catch(e=>A.notice(e.message));});
 $('#repertoire-prev').addEventListener('click',()=>{repertoireOffset=Math.max(0,repertoireOffset-20);repertoireList().catch(e=>A.notice(e.message));});$('#repertoire-next').addEventListener('click',()=>{repertoireOffset+=20;repertoireList().catch(e=>A.notice(e.message));});
 $('#repertoire-reset').addEventListener('click',()=>challenge&&openExercise(challenge.id).catch(e=>A.notice(e.message)));$('#repertoire-reveal').addEventListener('click',async()=>{try{if(!challenge)return;const id=challenge.id,data=await mine('reveal',{exercise_id:id});if(challenge?.id===id)showSolution(data.evidence);}catch(e){A.notice(e.message);}});
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
  box.append(state.source==='personal'?link('Open this game in Study →',`#personal/${p.library_id}/game/${encodeURIComponent(p.game_id)}?snapshot=${p.snapshot_id}&ply=${p.ply}`):link('Open source game ↗','https://lichess.org/'+p.game_id));
 }
 function focus(data){const box=$('#learn-focus');box.replaceChildren();box.hidden=!data.recommendations?.length;for(const r of data.recommendations||[]){const card=el('article',null,'panel');card.append(el('p',r.source,'eyebrow'),el('h3',r.title),el('p',r.explanation),button('Open related lesson',()=>choose(state.lessons.find(l=>l.id===r.lesson_id))),link('Review the evidence game →',`#personal/${data.library_id}/game/${r.game_id}?snapshot=${data.snapshot_id}&ply=${r.ply}`));box.append(card);}}
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
  clearTimeout(repertoireTimer);repertoireEpoch++;build=null;challenge=null;$('#repertoire-challenge').hidden=true;$('#repertoire-library').replaceChildren(...state.libraries.map(l=>{const o=el('option',l.player+' · '+A.number(l.games)+' games');o.value=l.id;return o;}));$('#repertoire-start').disabled=!state.session?.authenticated||!state.libraries.length;$('#repertoire-status').textContent=state.session?.authenticated?'Choose a library and build your repertoire.':'Sign in to build a private repertoire.';repertoireStatus();
  choose(state.lessons[0]).catch(e=>A.notice(e.message));source();
 });
 $('#lesson-reset').addEventListener('click',()=>reset().catch(e=>A.notice(e.message)));$('#lesson-flip').addEventListener('click',()=>{state.flipped=!state.flipped;reset().catch(e=>A.notice(e.message));});
 $('#learn-source').addEventListener('change',source);$('#learn-library').addEventListener('change',source);$('#learn-next').addEventListener('click',()=>{++state.index;example();});
})();
