'use strict';

window.Study = (() => {
  const state = {user:null, signup:false, source:'file', library:null, snapshot:null, versions:[], report:null,
    selected:{}, offset:0, replay:null, ply:0, flipped:false, preview:null, job:null, timer:null, routeSequence:0,
    preferences:{},trial:null,boardKey:null,boardSequence:0,positionTimer:null,gameOrder:'newest',gameRows:[],gamesRequest:0,reportRequest:0};
  const $ = selector => document.querySelector(selector);
  const num = value => value == null ? '—' : Number(value).toLocaleString(undefined,{maximumFractionDigits:1});
  const pct = value => value == null ? '—' : `${(100*value).toFixed(1)}%`;
  function node(tag,text,cls) { const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n; }
  function button(text,action,cls='secondary') { const b=node('button',text,cls);b.type='button';b.addEventListener('click',()=>Promise.resolve(action()).catch(error));return b; }
  function link(text,href,cls='text-link') { const a=node('a',text,cls);a.href=href;return a; }
  function alert(text,success=false) { const box=$('#study-alert');box.hidden=!text;box.className=success?'study-success':'callout';box.textContent=text||''; }
  function error(e) { alert(e.message||String(e)); }
  async function api(path,body,method) {
    const options={method:method||(body==null?'GET':'POST')};
    if(body!=null) { options.headers={'X-Study-Action':'1','X-CSRF-Token':state.user?.csrf||''};
      if(body instanceof FormData) options.body=body;
      else { options.headers['Content-Type']='application/json';options.body=JSON.stringify(body); }
    }
    const response=await fetch('/api/personal/'+path,options);
    const value=await response.json();
    if(response.status===401) { state.user=null;showAuth(); }
    if(!response.ok)throw new Error(value.error||'The request could not be completed');
    return value;
  }
  function table(headers,rows) {
    const wrap=node('div',null,'table-wrap'),t=node('table'),head=node('thead'),tr=node('tr');
    for(const label of headers){const th=node('th',label);th.scope='col';tr.append(th);}head.append(tr);t.append(head);
    const body=node('tbody');for(const values of rows){const row=node('tr');for(const value of values){const cell=node('td');if(value instanceof Node)cell.append(value);else cell.textContent=value??'—';row.append(cell);}body.append(row);}t.append(body);wrap.append(t);return wrap;
  }
  function page(id) { document.querySelectorAll('.study-page').forEach(n=>n.hidden=n.id!==id); }
  function showAuth() { $('#study-auth').hidden=false;$('#study-signed-in').hidden=true;clearTimeout(state.timer); }
  function showSignedIn() { $('#study-auth').hidden=true;$('#study-signed-in').hidden=false;$('#study-account-name').textContent=state.user.username; }
  function route() { const raw=location.hash.slice(1)||'personal';const [path,query='']=raw.split('?');return {parts:path.split('/'),query:new URLSearchParams(query)}; }
  function href(section='overview',gameId=null,ply=0,filters=state.selected,offset=state.offset) {
    const path=`#personal/${state.library}/${gameId?'game/'+encodeURIComponent(gameId):section}`;
    const q=new URLSearchParams();if(state.snapshot)q.set('snapshot',state.snapshot);if(ply)q.set('ply',ply);
    if(Object.keys(filters).length)q.set('filters',JSON.stringify(filters));
    if(offset)q.set('offset',offset);if(state.gameOrder!=='newest')q.set('order',state.gameOrder);
    return path+(q.size?'?'+q:'');
  }
  function go(hash) { if(location.hash===hash)open().catch(error);else location.hash=hash; }
  function selection() { return {library_id:state.library,snapshot_id:state.snapshot,filters:state.selected}; }
  function gameLink(value,text) { return link(text||value.game_id,href('games',value.game_id,value.ply||0)); }
  function rememberLocation(){history.replaceState(null,'',href(route().parts[2]||'games'));document.querySelectorAll('[data-study-tab]').forEach(a=>a.href=href(a.dataset.studyTab));}
  function fillFilters(){
    $('#study-filters').reset();
    for(const [key,value] of Object.entries(state.selected)){const field=$('#study-filters').elements[key];if(field){if(field.tagName==='SELECT'&&![...field.options].some(o=>o.value===value)){const option=node('option','Latest '+value);option.value=value;field.append(option);}field.value=value;}}
    if(state.selected.from_date||state.selected.to_date)$('#study-period').value='custom';updatePeriod();
  }
  function filterValues() {
    const entries=Object.fromEntries(new FormData($('#study-filters')));
    if(entries.period!=='custom') { delete entries.from_date;delete entries.to_date; }
    if(state.selected.opening)entries.opening=state.selected.opening;
    return Object.fromEntries(Object.entries(entries).filter(([,v])=>v));
  }
  function updatePeriod() { document.querySelectorAll('.study-custom-date').forEach(n=>n.hidden=$('#study-period').value!=='custom'); }
  function resetLibrary(library) {
    state.library=library;state.snapshot=null;state.selected={};state.offset=0;state.replay=null;state.report=null;state.gameOrder='newest';state.gameRows=[];
    $('#study-filters').reset();updatePeriod();
  }
  async function open() {
    const sequence=++state.routeSequence;clearTimeout(state.timer);
    if(!state.user) { const session=await api('session');state.user=session.user;if(!session.authenticated){showAuth();return;} }
    showSignedIn();const r=route(),sub=r.parts[1];
    if(sub==='import') { page('study-import-page');if(r.query.get('job'))await pollJob(r.query.get('job'),false);return; }
    if(sub==='account') { page('study-account-page');return; }
    if(!sub) { page('study-home');await loadHome(sequence);return; }
    if(sub!==state.library)resetLibrary(sub);
    if(r.query.has('filters')){const selected=JSON.parse(r.query.get('filters'));if(!selected||Array.isArray(selected)||typeof selected!=='object')throw new Error('Invalid study filter link');state.selected=selected;state.offset=0;}
    if(r.query.has('offset'))state.offset=Math.max(0,Math.min(100000000,Number(r.query.get('offset'))||0));
    if(r.query.has('order'))state.gameOrder=r.query.get('order')==='oldest'?'oldest':'newest';
    fillFilters();$('#study-game-order').value=state.gameOrder;
    if(r.query.get('snapshot'))state.snapshot=r.query.get('snapshot');
    const versions=await api(`libraries/${encodeURIComponent(sub)}/versions`);
    if(sequence!==state.routeSequence)return;
    state.versions=versions.versions;
    if(!state.snapshot)state.snapshot=versions.library.active_snapshot;
    if(!state.snapshot){go('#personal/import');return;}
    $('#study-version').replaceChildren(...versions.versions.map(v=>{const o=node('option',`v${v.version} · ${num(v.game_count)} games`);o.value=v.id;return o;}));$('#study-version').value=state.snapshot;
    if(r.parts[2]==='game') {
      $('#study-game-back').href=href('games');$('#study-game-page').setAttribute('aria-busy','true');$('#study-game-loading').hidden=false;
      $('#study-copy-link').disabled=true;$('#study-previous-game').disabled=true;$('#study-next-game').disabled=true;page('study-game-page');
      await loadGame(decodeURIComponent(r.parts[3]||''),Number(r.query.get('ply')||0),sequence);
      if(sequence!==state.routeSequence)return;
      await loadSwitcher(sequence);
      if(sequence===state.routeSequence){$('#study-game-page').setAttribute('aria-busy','false');$('#study-game-loading').hidden=true;$('#study-copy-link').disabled=false;}
      return;
    }
    const section=['overview','openings','games','questions','saved'].includes(r.parts[2])?r.parts[2]:'overview';
    page('study-analysis-page');document.querySelectorAll('.study-section').forEach(n=>n.hidden=n.id!==`study-${section}-section`);
    $('#study-analysis-title').textContent=versions.library.player;
    $('#study-analysis-description').textContent=`${num(versions.versions.find(v=>v.id===state.snapshot)?.game_count)} games in this collection. Choose a period or follow a finding to a game.`;
    document.querySelectorAll('[data-study-tab]').forEach(a=>{a.href=href(a.dataset.studyTab);a.classList.toggle('active',a.dataset.studyTab===section);});
    if(section==='games')await loadGames(sequence);
    else if(section==='saved')await loadSaved();
    else { await loadReport(sequence);if(section==='questions')renderPrompts(); }
    window.scrollTo(0,0);
  }
  async function loadHome(sequence) {
    const [data,jobs]=await Promise.all([api('libraries'),api('imports')]);if(sequence!==state.routeSequence)return;
    const box=$('#study-library-list');box.replaceChildren();
    if(!data.libraries.length) {const card=node('article',null,'panel study-empty');card.append(node('h3','Your first study starts with a game.'),node('p','Upload your PGN or paste a Lichess games API link. You can add more dates later.'),link('Import my games ↗','#personal/import','primary study-link-button'));box.append(card);}
    for(const library of data.libraries) {
      const card=node('article',null,'panel study-library-card'),actions=node('div',null,'study-actions');
      if(library.active_snapshot)actions.append(link('Review recent games →',`#personal/${library.id}/games?filters=%7B%7D`,'primary study-link-button'),link('Study overview',`#personal/${library.id}/overview`));
      actions.append(button('Sync new games',async()=>{const route=state.routeSequence;const result=await api(`libraries/${library.id}/sync`,{});if(route===state.routeSequence)go(`#personal/import?job=${result.job_id}`);}));
      card.append(node('p','PLAYER LIBRARY','eyebrow'),node('h3',library.player),node('strong',`${num(library.games)} games`),node('p',library.version?`Collection v${library.version} · all imported dates`:'No completed games imported yet','muted'),actions);box.append(card);
    }
    const recent=$('#study-recent-imports');recent.replaceChildren();
    if(!jobs.jobs.length)recent.append(node('p','No imports yet.','muted'));
    for(const job of jobs.jobs.slice(0,5)){const row=node('div',null,'study-import-row');row.append(node('strong',job.player||'Detecting player'),node('span',`${job.status} · ${num(job.new_games)} new games`, 'muted'),link('Import details',`#personal/import?job=${job.id}`));if(job.status==='complete'&&job.snapshot_id)row.append(link('Open study',`#personal/${job.library_id}/overview?snapshot=${job.snapshot_id}`));recent.append(row);}
  }
  async function loadReport(sequence=state.routeSequence) {
    const request=++state.reportRequest,report=await api('report',selection());if(sequence!==state.routeSequence||request!==state.reportRequest)return;
    state.report=report;state.snapshot=report.snapshot_id;
    $('#study-filter-context').textContent=`Version ${report.version} · ${num(report.summary.games)} matching games · ${report.summary.first_date||'Date unavailable'} to ${report.summary.last_date||'date unavailable'}${state.selected.opening?' · '+state.selected.opening:''}`;
    const metrics=$('#study-metrics');metrics.replaceChildren();
    for(const [label,value,note] of [['Games in this selection',num(report.summary.games),'Use the filters to focus a study session'],['Your score',pct(report.summary.score_rate),'A win is 1 point; a draw is ½'],['Wins · draws · losses',`${report.summary.wins} · ${report.summary.draws} · ${report.summary.losses}`,'Results from your perspective']]) {const card=node('article',null,'metric');card.append(node('span',label,'label'),node('strong',value),node('small',note));metrics.append(card);}
    $('#study-progress').textContent=`${report.progress.reviewed} reviewed · ${report.progress.practiced} practiced`;
    const findings=$('#study-findings');findings.replaceChildren();
    if(!report.summary.games)findings.append(node('article','No games match these filters. Try all imported dates or another clock class.','panel study-empty'));
    report.findings.forEach((finding,index)=>{const card=node('article',null,'panel study-finding');card.append(node('p',`${String(index+1).padStart(2,'0')} / ${finding.kind==='engine'?'POSITION TO PRACTICE':finding.kind==='opening'?'OPENING FOCUS':'DECISION REVIEW'}`,'eyebrow'),node('h3',finding.title),node('p',finding.detail));const action=node('div',null,'study-next-action');action.append(node('strong','Try this next'),node('p',finding.action));card.append(action);const links=node('div',null,'study-actions');for(const game of finding.games.slice(0,3))links.append(gameLink(game,game.opponent?`Review vs ${game.opponent}`:`Review ${game.game_id}`));card.append(links);const evidence=node('details');evidence.append(node('summary','Why this finding?'),node('p',finding.evidence));card.append(evidence);findings.append(card);});
    $('#study-openings').replaceChildren(table(['Opening / sample','Your score','What it means','Next step'],report.openings.map(o=>{
      const name=node('div');name.append(node('strong',o.opening),node('small',`${o.games} games · ${o.eco||'No ECO code'}`,'study-row-note'));
      return [name,pct(o.score_rate),o.review_candidate?'Lower score in a matched comparison. Review the examples.':'Descriptive sample; no proven weakness.',button('Review games',()=>{state.selected.opening=o.opening;state.offset=0;go(href('games'));},'text-link')];
    })));
  }
  async function loadGames(sequence=state.routeSequence) {
    const request=++state.gamesRequest,data=await api('games',{...selection(),offset:state.offset,limit:10,order:state.gameOrder});if(sequence!==state.routeSequence||request!==state.gamesRequest)return;
    $('#study-opening-context').textContent=state.selected.opening?`Showing ${state.selected.opening}.`:'Open a game to replay moves, try a correction and ask about its position.';
    $('#study-clear-opening').hidden=!state.selected.opening;
    $('#study-filter-context').textContent=`Collection v${state.versions.find(v=>v.id===state.snapshot)?.version||'—'} · ${num(data.total)} matching games${state.selected.last_games?' · latest '+state.selected.last_games+' after filters':''}`;
    $('#study-game-timing').textContent=data.missing_time_games?`${num(data.missing_time_games)} games have dates without start times; their same-day order is approximate.`:'Ordered by game start time. Times are shown in UTC.';
    $('#study-games').replaceChildren(gamesTable(data.rows));
    if(!data.rows.length)$('#study-games').append(node('p','No games match this selection. Widen the period or clear a filter.','study-empty'));
    $('#study-page-label').textContent=data.rows.length?`Games ${state.offset+1}–${state.offset+data.rows.length}`:'No matching games';
    $('#study-previous').disabled=state.offset===0;$('#study-next').disabled=!data.has_more;
  }
  function gamesTable(rows,filters=state.selected){
    return table(['Started (UTC) / clock','Opponent','My color','Result','Opening','Review progress','Study'],rows.map(g=>{
      const when=node('div',g.started_at?new Date(g.started_at.replace(' ','T')).toISOString().replace('T',' ').slice(0,19):g.game_date||'Date unavailable');when.append(node('small',g.speed+(g.started_at?'':' · time unknown'),'study-row-note'));
      const opponent=node('div',g.opponent);if(g.opponent_rating!=null)opponent.append(node('small',`Rating ${g.opponent_rating}`,'study-row-note'));
      const action=link('Open editor →',href('games',g.game_id,0,filters,filters===state.selected?state.offset:0));
      const practice={queued:'To review',reviewed:'Reviewed',practiced:'Practised'},engine={queued:'Review queued',running:'Analysing',complete:'Engine review ready',failed:'Retry needed'};
      const progress=practice[g.practice_status]||engine[g.engine_status]||(Object.hasOwn(g,'engine_status')?'Not analysed':'Open editor to review');
      return [when,opponent,g.player_color,g.result,g.opening,progress,action];
    }));
  }
  async function loadSwitcher(sequence){
    const data=await api('games',{...selection(),offset:state.offset,limit:10,order:state.gameOrder});if(sequence!==state.routeSequence)return;
    state.gameRows=data.rows;$('#study-game-switcher').replaceChildren(gamesTable(data.rows));
    const index=state.gameRows.findIndex(g=>g.game_id===state.replay.game_id);
    $('#study-previous-game').disabled=index<=0;$('#study-next-game').disabled=index<0||index>=state.gameRows.length-1;
  }
  async function loadSaved() {
    const data=await api('studies');const current=$('#study-current-exports');current.replaceChildren();
    for(const [format,label] of [['parquet','Download data (Parquet)'],['pgn','Download games (PGN)'],['manifest','Data dictionary & provenance'],['report','Download study report']])current.append(link(label,exportUrl(format)));
    const box=$('#study-saved-list');box.replaceChildren();
    for(const study of data.studies) {const card=node('div',null,'study-saved-row');card.append(node('strong',study.name),node('p',`${study.player} · collection v${study.version} · ${Object.entries(study.filters).map(([k,v])=>`${k}: ${v}`).join(', ')||'all games'}`,'muted'));const links=node('div',null,'study-actions');for(const format of ['parquet','pgn','manifest','report'])links.append(link(format.toUpperCase(),`/api/personal/export?study_id=${encodeURIComponent(study.id)}&format=${format}`));card.append(links);box.append(card);}
    if(!data.studies.length)box.append(node('p','Save a selection to return to exactly the same games later.','muted'));
  }
  function exportUrl(format) { const params=new URLSearchParams({library_id:state.library,snapshot_id:state.snapshot,format,...state.selected});return '/api/personal/export?'+params; }
  function renderPrompts() {
    const box=$('#study-prompts');box.replaceChildren();
    for(const question of ['Summarize my last 3 games','What should I practise from my last 5 games?','Summarize my games in the last 30 days','Compare White versus Black'])box.append(button(question,()=>{$('#study-question textarea').value=question;$('#study-question textarea').focus();},'study-prompt'));
  }
  function renderAnswer(answer,target) {
    const box=$(target);box.replaceChildren(node('p',answer.answer,'study-answer-text'),node('p',`${answer.scope_label||'Selected games'} · collection v${answer.version}`,'muted'));
    if(answer.games?.length)box.append(gamesTable(answer.games,answer.filters));
    const refs=node('div',null,'study-actions');for(const game of answer.references)refs.append(gameLink(game,`Open evidence game ${game.game_id}`));box.append(refs);
  }
  async function loadGame(gameId,ply,sequence=state.routeSequence,refresh=false) {
    const replay=await api(`games/${encodeURIComponent(gameId)}?library_id=${encodeURIComponent(state.library)}&snapshot_id=${encodeURIComponent(state.snapshot)}`);
    if(sequence!==state.routeSequence)return;
    if(refresh&&state.replay?.game_id===gameId){
      state.replay.analysis_jobs=replay.analysis_jobs;renderEngine();
      if(replay.analysis_jobs.some(j=>['queued','running'].includes(j.status)))state.timer=setTimeout(()=>loadGame(gameId,state.ply,sequence,true).catch(error),2500);
      return;
    }
    state.preferences=await api('board/preferences');if(sequence!==state.routeSequence)return;
    for(const [key,value] of Object.entries(state.preferences)){const field=$('#board-preferences').elements[key];if(field){if(field.type==='checkbox')field.checked=value;else field.value=value;}}
    state.replay=replay;state.ply=Math.max(0,Math.min(ply,replay.moves.length));state.preview=null;state.trial=null;state.boardKey=null;state.noteKey=null;state.flipped=state.preferences.orientation==='black'||state.preferences.orientation==='player'&&replay.player_color==='black';
    state.trialRequest=(state.trialRequest||0)+1;pendingTrial(false);
    $('#study-game-back').href=href('games');$('#study-replay-title').textContent=`${replay.white} — ${replay.black}`;
    $('#study-game-meta').textContent=`${replay.game_date||'Date unavailable'} · ${replay.speed} · ${replay.outcome} · ${replay.opening}`;
    $('#study-game-id').textContent=`Game ID: ${replay.game_id} · collection v${replay.version}`;
    $('#study-ply').max=replay.moves.length;
    $('#study-moves').replaceChildren(...replay.moves.map(move=>button(`${move.fullmove}${move.color==='white'?'.':'…'} ${move.san}`,()=>{state.trial=null;state.preview=null;state.ply=move.ply;renderBoard();},'study-move')));
    renderBoard();renderEngine();
    if(replay.analysis_jobs.some(j=>['queued','running'].includes(j.status)))state.timer=setTimeout(()=>loadGame(gameId,state.ply,sequence,true).catch(error),2500);
  }
  function renderBoard() {
    if(!state.replay)return;
    const key=state.replay.revision_id+':'+state.ply;
    if(key!==state.boardKey||state.preview){state.trial=null;state.boardKey=key;}
    if(state.preview||state.positionContext!==positionKey()){clearTimeout(state.positionTimer);$('#study-position-engine').replaceChildren();state.positionContext=null;}
    $('#study-positions').replaceChildren();
    const fen=state.trial?.fen||state.preview?.fen||state.replay.positions[state.ply];
    const board=$('#study-board');
    const last=state.ply&&!state.preview?state.replay.moves[state.ply-1]:null;
    const lastUci=state.trial?.moves.at(-1)||last?.uci;
    const draw=position=>window.ChessBoard.render(board,position,{flipped:state.flipped,preferences:state.preferences,
      last:lastUci?[lastUci.slice(0,2),lastUci.slice(2,4)]:[],onMove:state.preview?null:move=>tryMoves([...(state.trial?.moves||[]),move]).catch(error)});
    const known=state.trial?.fen===fen?state.trial:state.boardPositionKey===key&&state.boardPosition?.fen===fen?state.boardPosition:null;
    draw(known||{fen});
    const sequence=++state.boardSequence,route=state.routeSequence;
    if(!state.preview){if(known)renderVariations(state.replay.variations||known.variations);else api('board/position',boardSelection()).then(value=>{if(sequence!==state.boardSequence||route!==state.routeSequence)return;state.boardPosition=value;state.boardPositionKey=key;state.replay.variations=value.variations;draw(value);renderVariations(value.variations);}).catch(error);}
    $('#study-trial').hidden=!state.trial?.moves.length;$('#study-trial-line').textContent=state.trial?'Trial: '+state.trial.san.join(' '):'';
    $('#study-ply').value=state.ply;
    const clock=last?.clock_seconds!=null?` · clock ${last.clock_seconds.toFixed(1)}s`:'';
    const evaluation=last?.uploaded_eval_cp!=null?` · uploaded evaluation ${(last.uploaded_eval_cp/100).toFixed(2)} (unverified)`:'';
    $('#study-position-label').textContent=state.trial?.moves.length?`TRIAL LINE · from played position ${state.ply} · ${state.trial.turn} to move${state.trial.result?' · '+state.trial.result:''}`:state.preview?`ENGINE VARIATION · ${state.preview.label} · not the played game`:`Position ${state.ply}/${state.replay.moves.length}${clock}${evaluation}`;
    $('#study-question-context').textContent=state.trial?.moves.length?`Current trial: ${state.trial.san.join(' ')}. Evaluate this position to ask for an engine continuation.`:`Selected game vs ${state.replay.opponent} · played position ${state.ply}. Questions about “this game” use this game only.`;
    const questionContext=positionKey();if(state.questionContext!==questionContext){$('#study-game-answer').replaceChildren();state.questionContext=questionContext;}
    [...$('#study-moves').children].forEach((b,i)=>b.classList.toggle('active',i+1===state.ply));
    const noteKey=`${state.replay.revision_id}:${state.ply}`;
    if(state.noteKey!==noteKey){const note=state.replay.notes.find(n=>n.ply===state.ply);$('#study-note textarea').value=note?.note||'';$('#study-note select').value=note?.status||'queued';state.noteKey=noteKey;}
  }
  function boardSelection(){return {...selection(),game_id:state.replay.game_id,ply:state.ply,moves:state.trial?.moves||[]};}
  function positionKey(){return JSON.stringify({...boardSelection(),profile:$('#study-position-profile').value});}
  function pendingTrial(pending){state.trialPending=pending;$('#study-position-analyze').disabled=pending;$('#study-game-question button').disabled=pending;$('#study-variation-save button').disabled=pending;}
  async function tryMoves(moves){
    const key=state.boardKey,route=state.routeSequence,sequence=++state.boardSequence,request=(state.trialRequest||0)+1;state.trialRequest=request;pendingTrial(true);
    try{const value=await api('board/position',{...boardSelection(),moves});if(key!==state.boardKey||route!==state.routeSequence||sequence!==state.boardSequence)return;state.trial=value;state.replay.variations=value.variations;renderBoard();}
    finally{if(request===state.trialRequest)pendingTrial(false);}
  }
  async function refreshVariations(){const selected=boardSelection(),context=JSON.stringify(selected),route=state.routeSequence,value=await api('board/position',selected);if(route!==state.routeSequence||context!==JSON.stringify(boardSelection()))return;state.replay.variations=value.variations;if(state.trial)state.trial=value;else {state.boardPosition=value;state.boardPositionKey=state.boardKey;}renderBoard();}
  function renderVariations(variations){const box=$('#study-saved-variations');box.replaceChildren();if(variations.length)box.append(node('h4','Saved continuations'));for(const v of variations){const row=node('div',null,'saved-variation');row.append(button(`${v.label} · position ${v.ply}`,async()=>{state.preview=null;state.ply=v.ply;state.trial=null;state.boardKey=state.replay.revision_id+':'+v.ply;await tryMoves(v.moves);}),button('Remove',async()=>{await api('board/variations/remove',{id:v.id});await refreshVariations();},'text-link'));box.append(row);}}
  $('#board-preferences').addEventListener('submit',async e=>{e.preventDefault();try{const value=Object.fromEntries(new FormData(e.target));for(const key of ['coordinates','highlights'])value[key]=e.target.elements[key].checked;state.preferences=await api('board/preferences',value);state.flipped=value.orientation==='black'||value.orientation==='player'&&state.replay.player_color==='black';renderBoard();$('#board-settings-status').textContent='Saved to your account.';}catch(err){error(err);}});
  $('#study-trial-reset').addEventListener('click',()=>{state.trial=null;renderBoard();});
  $('#study-trial-undo').addEventListener('click',()=>tryMoves((state.trial?.moves||[]).slice(0,-1)).catch(error));
  $('#study-variation-save').addEventListener('submit',async e=>{e.preventDefault();try{await api('board/variations',{...boardSelection(),label:new FormData(e.target).get('label')});e.target.reset();await refreshVariations();alert('Continuation saved separately from the played game.',true);}catch(err){error(err);}});
  async function pollPosition(id,context,route){const value=await api('board/analysis/'+id);if(route!==state.routeSequence||context!==positionKey())return;state.positionContext=context;const box=$('#study-position-engine');box.replaceChildren(node('p','Position search: '+value.status));if(value.error)box.append(node('p',value.error,'callout'));if(value.status==='complete'){const e=value.evidence;box.append(node('p',e.mate!=null?'Mate score for White: '+e.mate:'White evaluation: '+(e.cp/100).toFixed(2)+' pawns'),node('p',e.pv_san.join(' ')),node('p',`${e.engine} · ${num(e.nodes)} nodes · depth ${e.depth} · ${e.explanation}`,'muted'));}else if(['queued','running'].includes(value.status))state.positionTimer=setTimeout(()=>pollPosition(id,context,route).catch(error),1200);}
  $('#study-position-analyze').addEventListener('click',async()=>{try{if(state.preview)throw new Error('Return to the played game or a trial line to evaluate a position.');const selected=boardSelection(),context=positionKey(),route=state.routeSequence,value=await api('board/analysis',{...selected,profile:$('#study-position-profile').value});clearTimeout(state.positionTimer);await pollPosition(value.id,context,route);}catch(err){error(err);}});
  $('#study-position-profile').addEventListener('change',()=>{clearTimeout(state.positionTimer);state.positionContext=null;state.questionContext=null;$('#study-position-engine').replaceChildren();$('#study-game-answer').replaceChildren();});
  function renderEngine() {
    const box=$('#study-engine');box.replaceChildren();
    const jobs=state.replay.analysis_jobs,job=jobs[0];
    const current=job&&(job.evidence.review_version===3||job.evidence.budget?.analysis_version===3);
    $('#study-analyze').disabled=Boolean(current&&['queued','running','complete'].includes(job.status));
    if(!job)return;
    box.append(node('p',`Engine review: ${job.status}`,'study-pill'));
    if(job.error)box.append(node('p',job.error,'callout'));
    if(job.status!=='complete'){
      const p=job.progress||{};
      box.append(node('p',p.stage?`${p.stage} · ${num(p.positions_checked)}/${num(p.total_plies)} played moves checked · ${num(p.player_moves_reviewed)}/${num(p.player_moves_total)} of your decisions compared.`:'Waiting for the engine worker.'));
      return;
    }
    const e=job.evidence,summary=node('div',null,'study-review-summary');
    summary.append(node('strong',current?(e.complete_coverage?'Complete game checked':'Review partially checked'):'Earlier review · request the updated full-game review'),node('p',`${e.engine} · ${num(e.analyzed_plies)} of ${num(e.total_plies)} played moves checked (White and Black).`));
    if(current)summary.append(node('p',`${num(e.player_moves_reviewed)} of ${num(e.player_moves_total)} of your decisions compared · ${e.candidates.length} ${e.candidates.length===1?'decision':'decisions'} worth revisiting.`),node('p',e.coverage_explanation));
    summary.append(node('p','Scores are from your side: +1.00 is roughly a pawn’s advantage; −1.00 favors your opponent. Mate scores count moves to checkmate. These are engine estimates.','muted'));box.append(summary);
    if(!e.candidates.length)box.append(node('p','No move crossed the 1.50-pawn review threshold in the checked portion. This does not establish error-free play.'));
    for(const [index,candidate] of e.candidates.entries()){
      const card=node('details',null,'study-engine-item');card.open=index===0;
      card.append(node('summary',`Move ${candidate.fullmove||Math.ceil(candidate.ply/2)} · ${candidate.played_san} · ${candidate.motif||'Decision to review'}`),node('p',candidate.explanation||candidate.summary),button(`Before ${candidate.played_san}`,()=>{state.trial=null;state.preview=null;state.ply=candidate.ply-1;renderBoard();},'text-link'));
      const alternatives=candidate.alternatives||[{rank:1,pv_san:candidate.pv_san,positions:candidate.preferred_positions||[],explanation:'Earlier search evidence. Request the updated review to compare three lines.'}];
      if(alternatives.length<3&&current)card.append(node('p',`This position returned ${alternatives.length} alternative ${alternatives.length===1?'line':'lines'}. Fewer than three other legal continuations may be available.`,'muted'));
      for(const line of alternatives){
        const item=node('section',null,'study-engine-line');item.append(node('h5',`Line ${line.rank}: ${line.pv_san[0]||'Game ended'}${line.score_label?' · '+line.score_label:''}`),node('p',line.explanation),node('p',line.pv_san.join(' '),'study-pv'));
        item.append(lineControls(line.positions,line.pv_san,`Line ${line.rank}`,candidate.ply-1));card.append(item);
      }
      if(candidate.reply_pv_san?.length){const reply=node('details');reply.append(node('summary','Why the played move loses ground'),node('p',candidate.reply_pv_san.join(' '),'study-pv'),lineControls(candidate.reply_positions,candidate.reply_pv_san,'Played continuation',candidate.ply));card.append(reply);}
      card.append(button('Return to played game',()=>{state.trial=null;state.preview=null;renderBoard();},'text-link'));box.append(card);
    }
    if(jobs.length>1){const history=node('details');history.append(node('summary','Earlier reviews'));
      for(const old of jobs.slice(1)){history.append(node('p',`${old.evidence.engine||'Engine'} · ${old.status} · ${old.evidence.analyzed_plies||0}/${old.evidence.total_plies||0} played moves checked`,'muted'));
        for(const finding of old.evidence.candidates||[])history.append(node('p',`${finding.played_san}: ${finding.explanation||finding.summary}`));
      }box.append(history);
    }
  }
  function lineControls(positions,moves,label,ply){
    let step=0;const controls=node('div',null,'study-line-controls'),status=node('span','Line ready','muted');
    const show=()=>{state.trial=null;state.ply=ply;state.preview={fen:positions[step],label:`${label} · ${step?moves[step-1]:'starting position'}`};renderBoard();status.textContent=`${step}/${moves.length} moves`;previous.disabled=step===0;next.disabled=step>=moves.length;};
    const previous=button('← Back',()=>{step=Math.max(0,step-1);show();}),next=button('Next →',()=>{step=Math.min(moves.length,step+1);show();});
    previous.disabled=true;next.disabled=!moves.length;
    controls.append(button('Replay line',()=>{step=0;show();}),previous,next,status);return controls;
  }
  function source(value) {state.source=value;$('#study-file-fields').hidden=value!=='file';$('#study-api-fields').hidden=value!=='api';$('#study-source-file').classList.toggle('active',value==='file');$('#study-source-api').classList.toggle('active',value==='api');}
  async function previewLink() {const value=$('#study-import-form input[name=url]').value;if(!value)return;try{const data=await api('imports/preview',{url:value});$('#study-api-preview').textContent=data.explanation;}catch(e){$('#study-api-preview').textContent=e.message;}}
  async function pollJob(key,redirect=true) {
    clearTimeout(state.timer);const sequence=state.routeSequence,value=await api('imports/'+encodeURIComponent(key));
    if(sequence!==state.routeSequence||$('#personal').hidden)return;
    state.job=value;$('#study-import-progress').hidden=false;
    $('#study-job-phase').textContent=value.phase;$('#study-job-detail').textContent=`${num(value.games_processed)} games read · ${num(value.new_games)} new${value.bytes_received?' · '+(value.bytes_received/1048576).toFixed(1)+' MiB received':''}`;
    if(value.sync_mode)$('#study-job-detail').textContent+=` · ${num(value.metadata_games)} game IDs checked · ${num(value.skipped_old_games)} stored games skipped without downloading moves`;
    const diagnostics=(value.diagnostics||[]).map(item=>`Game ${item.game_number}: ${item.reason}`).join('\n');
    $('#study-job-error').hidden=!(value.error||diagnostics);$('#study-job-error').textContent=[value.error,diagnostics].filter(Boolean).join('\n');
    const active=['queued','running'].includes(value.status);$('#study-job-progress').hidden=!active;$('#study-job-cancel').hidden=!active;
    $('#study-job-retry').hidden=!(value.source_kind==='lichess'&&['failed','cancelled'].includes(value.status));$('#study-job-open').hidden=value.status!=='complete'||!value.snapshot_id;
    if(value.status==='complete'&&!value.snapshot_id){alert('Sync complete. No completed standard games are available yet. You can sync again later.',true);return;}
    if(value.status==='complete'){
      const outcome=value.outcome||{message:'Your games are ready.',details:[]};
      $('#study-job-phase').textContent=outcome.message;$('#study-job-detail').textContent=outcome.details.join(' ');
      $('#study-job-open').href=`#personal/${value.library_id}/games?snapshot=${value.snapshot_id}`;
      if(redirect){alert(outcome.message,true);resetLibrary(value.library_id);state.snapshot=value.snapshot_id;go(href('games'));return;}
    }
    if(active)state.timer=setTimeout(()=>{if(!$('#personal').hidden)pollJob(key,true).catch(error);},2000);
  }
  function upload(form) {
    return new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();xhr.open('POST','/api/personal/imports/file');
      xhr.setRequestHeader('X-Study-Action','1');xhr.setRequestHeader('X-CSRF-Token',state.user.csrf);
      xhr.upload.onprogress=e=>{if(e.lengthComputable)alert(`Uploading ${(e.loaded/1048576).toFixed(1)} of ${(e.total/1048576).toFixed(1)} MiB…`,true);};
      xhr.onload=()=>{
        if($('#study-alert').textContent.startsWith('Uploading '))alert('');
        try{const value=JSON.parse(xhr.responseText);if(xhr.status>=400)reject(new Error(value.error));else resolve(value);}catch(e){reject(e);}
      };
      xhr.onerror=()=>reject(new Error('The upload connection was interrupted. Choose the file again to retry.'));xhr.send(form);
    });
  }

  $('#study-signin-tab').addEventListener('click',()=>{state.signup=false;$('#study-auth-title').textContent='Open your study library.';$('#study-auth-submit').textContent='Sign in';$('#study-signin-tab').classList.add('active');$('#study-signup-tab').classList.remove('active');});
  $('#study-signup-tab').addEventListener('click',()=>{state.signup=true;$('#study-auth-title').textContent='Create your private study space.';$('#study-auth-submit').textContent='Create account';$('#study-signup-tab').classList.add('active');$('#study-signin-tab').classList.remove('active');});
  $('#study-auth-form').addEventListener('submit',async e=>{e.preventDefault();const b=$('#study-auth-submit');b.disabled=true;try{const result=await api(state.signup?'signup':'login',Object.fromEntries(new FormData(e.target)));state.user=result.user;e.target.reset();alert('');go('#personal');}catch(err){error(err);}finally{b.disabled=false;}});
  $('#study-logout').addEventListener('click',async()=>{try{await api('logout',{});state.user=null;state.library=null;state.snapshot=null;state.selected={};showAuth();go('#personal');}catch(e){error(e);}});
  $('#study-password').addEventListener('submit',async e=>{e.preventDefault();try{const value=await api('password',Object.fromEntries(new FormData(e.target)));state.user=value.user;e.target.reset();alert('App password changed. Other sessions have been signed out.',true);}catch(err){error(err);}});
  $('#study-source-file').addEventListener('click',()=>source('file'));$('#study-source-api').addEventListener('click',()=>source('api'));
  $('#study-api-example').addEventListener('click',()=>{const player=$('#study-player').value.trim();if(!player){alert('Enter a Lichess player name below to create the link.');$('#study-player').focus();return;}$('#study-import-form input[name=url]').value='https://lichess.org/api/games/user/'+encodeURIComponent(player);previewLink();});
  $('#study-import-form input[name=url]').addEventListener('change',previewLink);
  $('#study-import-mode').addEventListener('change',()=>{$('#study-mode-help').textContent={append:'Add games you do not have yet. Stored games and notes stay unchanged. An unfiltered Lichess link syncs from the last successful check; the first sync checks game IDs without downloading old moves.',merge:'Refresh games in this import and add missing games. Changed information creates a new version; previous saved studies and notes remain available.',replace:'Make this import the active collection for this player. Games outside this import disappear from the active view; earlier saved versions and studies remain available. Shared game data is reused.'}[$('#study-import-mode').value];});
  $('#study-import-form').addEventListener('submit',async e=>{e.preventDefault();const b=$('#study-import-submit'),sequence=state.routeSequence;b.disabled=true;try{let result;if(state.source==='file'){const form=new FormData(e.target);if(!form.get('file')?.size)throw new Error('Choose a PGN file first.');form.delete('url');result=await upload(form);}else result=await api('imports/lichess',{url:e.target.elements.url.value,player:e.target.elements.player.value,mode:e.target.elements.mode.value});if(sequence!==state.routeSequence||$('#personal').hidden)return;alert('Import started. You can leave this page and return from Recent imports.',true);state.job=null;history.replaceState(null,'',`#personal/import?job=${result.job_id}`);await pollJob(result.job_id,true);}catch(err){error(err);}finally{b.disabled=false;}});
  $('#study-job-cancel').addEventListener('click',async()=>{if(!state.job)return;try{await api(`imports/${state.job.id}/cancel`,{});await pollJob(state.job.id,false);}catch(e){error(e);}});
  $('#study-job-retry').addEventListener('click',async()=>{const sequence=state.routeSequence;try{const result=await api(`imports/${state.job.id}/retry`,{});if(sequence!==state.routeSequence||$('#personal').hidden)return;history.replaceState(null,'',`#personal/import?job=${result.job_id}`);await pollJob(result.job_id,true);}catch(e){error(e);}});
  $('#study-period').addEventListener('change',updatePeriod);
  $('#study-filters').addEventListener('submit',async e=>{e.preventDefault();state.selected=filterValues();state.offset=0;rememberLocation();try{const r=route();if(r.parts[2]==='games')await loadGames();else await loadReport();}catch(err){error(err);}});
  $('#study-reset-filters').addEventListener('click',()=>{state.selected={};state.offset=0;fillFilters();go(href('games'));});
  $('#study-game-order').addEventListener('change',()=>{state.gameOrder=$('#study-game-order').value;state.offset=0;rememberLocation();loadGames().catch(error);});
  for(const count of [3,5,10])$('#study-recent-shortcuts').append(button('Latest '+count,()=>{state.selected={...state.selected,last_games:String(count)};state.offset=0;fillFilters();rememberLocation();loadGames().catch(error);}));
  $('#study-recent-shortcuts').append(button('All matching games',()=>{delete state.selected.last_games;state.offset=0;fillFilters();rememberLocation();loadGames().catch(error);}));
  $('#study-version').addEventListener('change',()=>{state.snapshot=$('#study-version').value;state.offset=0;go(href(route().parts[2]||'overview'));});
  $('#study-previous').addEventListener('click',()=>{state.offset=Math.max(0,state.offset-10);rememberLocation();loadGames().catch(error);});$('#study-next').addEventListener('click',()=>{state.offset+=10;rememberLocation();loadGames().catch(error);});
  $('#study-clear-opening').addEventListener('click',()=>{delete state.selected.opening;state.offset=0;rememberLocation();loadGames().catch(error);});
  for(const [id,direction] of [['study-previous-game',-1],['study-next-game',1]])$('#'+id).addEventListener('click',()=>{const index=state.gameRows.findIndex(g=>g.game_id===state.replay.game_id),next=state.gameRows[index+direction];if(index>=0&&next)go(href('games',next.game_id));});
  for(const question of ['Summarize this game','What is the best move here?','Why did I lose this game?'])$('#study-game-prompts').append(button(question,()=>{$('#study-game-question textarea').value=question;$('#study-game-question textarea').focus();},'study-prompt'));
  $('#study-question').addEventListener('submit',async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{const answer=await api('assistant',{...selection(),question:new FormData(e.target).get('question')});renderAnswer(answer,'#study-answer');}catch(err){error(err);}finally{b.disabled=false;}});
  $('#study-game-question').addEventListener('submit',async e=>{e.preventDefault();const b=e.target.querySelector('button'),context=positionKey(),sequence=state.routeSequence;b.disabled=true;try{if(state.preview)throw new Error('Return to the played game or try a move on the board to ask about that position.');const answer=await api('assistant',{...selection(),question:new FormData(e.target).get('question'),...boardSelection(),profile:$('#study-position-profile').value});if(context===positionKey()&&sequence===state.routeSequence)renderAnswer(answer,'#study-game-answer');}catch(err){error(err);}finally{b.disabled=Boolean(state.trialPending);}});
  $('#study-save').addEventListener('submit',async e=>{e.preventDefault();try{await api('studies',{...selection(),name:new FormData(e.target).get('name')});await loadSaved();alert('Saved with this exact collection version and filters.',true);}catch(err){error(err);}});
  $('#study-ply').addEventListener('input',e=>{state.trial=null;state.preview=null;state.ply=Number(e.target.value);renderBoard();});
  for(const [id,value] of [['study-start',()=>0],['study-prev',()=>state.ply-1],['study-next-move',()=>state.ply+1],['study-end',()=>state.replay.moves.length]])$(`#${id}`).addEventListener('click',()=>{if(!state.replay)return;state.trial=null;state.preview=null;state.ply=Math.max(0,Math.min(value(),state.replay.moves.length));renderBoard();});
  $('#study-flip').addEventListener('click',()=>{state.flipped=!state.flipped;renderBoard();});
  $('#study-copy-link').addEventListener('click',async()=>{const url=location.origin+location.pathname+href('games',state.replay.game_id,state.ply);try{await navigator.clipboard.writeText(url);alert('Game link copied.',true);}catch(e){alert(url,true);}});
  $('#study-analyze').addEventListener('click',async()=>{try{await api('analysis',{...selection(),game_id:state.replay.game_id});await loadGame(state.replay.game_id,state.ply,state.routeSequence,true);alert('Engine review queued. Its findings will appear here.',true);}catch(e){error(e);}});
  $('#study-note').addEventListener('submit',async e=>{e.preventDefault();const sequence=state.routeSequence,game=state.replay.game_id,ply=state.ply;try{if(state.preview||state.trial?.moves.length)throw new Error('Return to the played game before saving a note on its position.');await api('review',{...selection(),game_id:game,ply,...Object.fromEntries(new FormData(e.target))});if(sequence!==state.routeSequence)return;await loadGame(game,ply,sequence);await loadSwitcher(sequence);if(sequence===state.routeSequence)alert('Practice note saved.',true);}catch(err){error(err);}});
  $('#study-position').addEventListener('click',async()=>{try{if(state.preview||state.trial?.moves.length)throw new Error('Return to the played game before looking up its position.');const game=state.replay.game_id,ply=state.ply;const result=await api('positions',{...selection(),game_id:game,ply});if(!state.replay||state.replay.game_id!==game||state.ply!==ply||state.preview||state.trial?.moves.length)return;$('#study-positions').replaceChildren(node('h4',`${result.games} games reached this position`),table(['Played move','Games','Your score','Example'],result.alternatives.map(a=>[a.move,a.games,pct(a.score_rate),gameLink(a.example,'Open example')])),node('p',result.explanation,'muted'));}catch(e){error(e);}});
  return {open:()=>open().catch(error),close:()=>{clearTimeout(state.timer);clearTimeout(state.positionTimer);++state.boardSequence;++state.routeSequence;}};
})();
