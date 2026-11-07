'use strict';

window.Study = (() => {
  const state = {user:null, signup:false, source:'file', library:null, snapshot:null, versions:[], report:null,
    selected:{}, offset:0, replay:null, ply:0, flipped:false, preview:null, job:null, timer:null, routeSequence:0,
    preferences:{},trial:null,boardKey:null,boardSequence:0,positionTimer:null};
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
  function href(section='overview',gameId=null,ply=0) {
    const path=`#personal/${state.library}/${gameId?'game/'+encodeURIComponent(gameId):section}`;
    const q=new URLSearchParams();if(state.snapshot)q.set('snapshot',state.snapshot);if(ply)q.set('ply',ply);
    return path+(q.size?'?'+q:'');
  }
  function go(hash) { if(location.hash===hash)open().catch(error);else location.hash=hash; }
  function selection() { return {library_id:state.library,snapshot_id:state.snapshot,filters:state.selected}; }
  function gameLink(value,text) { return link(text||value.game_id,href('games',value.game_id,value.ply||0)); }
  function filterValues() {
    const entries=Object.fromEntries(new FormData($('#study-filters')));
    if(entries.period!=='custom') { delete entries.from_date;delete entries.to_date; }
    if(state.selected.opening)entries.opening=state.selected.opening;
    return Object.fromEntries(Object.entries(entries).filter(([,v])=>v));
  }
  function updatePeriod() { document.querySelectorAll('.study-custom-date').forEach(n=>n.hidden=$('#study-period').value!=='custom'); }
  function resetLibrary(library) {
    state.library=library;state.snapshot=null;state.selected={};state.offset=0;state.replay=null;state.report=null;
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
    if(r.query.get('snapshot'))state.snapshot=r.query.get('snapshot');
    const versions=await api(`libraries/${encodeURIComponent(sub)}/versions`);
    if(sequence!==state.routeSequence)return;
    state.versions=versions.versions;
    if(!state.snapshot)state.snapshot=versions.library.active_snapshot;
    if(!state.snapshot){go('#personal/import');return;}
    $('#study-version').replaceChildren(...versions.versions.map(v=>{const o=node('option',`v${v.version} · ${num(v.game_count)} games`);o.value=v.id;return o;}));$('#study-version').value=state.snapshot;
    if(r.parts[2]==='game') { page('study-game-page');await loadGame(decodeURIComponent(r.parts[3]||''),Number(r.query.get('ply')||0),sequence);return; }
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
    for(const library of data.libraries) {const card=node('article',null,'panel study-library-card');card.append(node('p','PLAYER LIBRARY','eyebrow'),node('h3',library.player),node('strong',`${num(library.games)} games`),node('p',library.version?`Collection v${library.version} · all imported dates`:'Waiting for a successful import','muted'),link('Open analysis →',`#personal/${library.id}/overview`));box.append(card);}
    const recent=$('#study-recent-imports');recent.replaceChildren();
    if(!jobs.jobs.length)recent.append(node('p','No imports yet.','muted'));
    for(const job of jobs.jobs.slice(0,5)){const row=node('div',null,'study-import-row');row.append(node('strong',job.player||'Detecting player'),node('span',`${job.status} · ${num(job.games_processed)} games read`, 'muted'),link('Import details',`#personal/import?job=${job.id}`));if(job.status==='complete')row.append(link('Open study',`#personal/${job.library_id}/overview?snapshot=${job.snapshot_id}`));recent.append(row);}
  }
  async function loadReport(sequence=state.routeSequence) {
    const report=await api('report',selection());if(sequence!==state.routeSequence)return;
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
    const data=await api('games',{...selection(),offset:state.offset,limit:10});if(sequence!==state.routeSequence)return;
    $('#study-opening-context').textContent=state.selected.opening?`Showing ${state.selected.opening}.`:'Ten games per page, with a stable link to each review.';
    $('#study-clear-opening').hidden=!state.selected.opening;
    $('#study-games').replaceChildren(table(['When / clock','Opponent','My color','Result','Opening','Review'],data.rows.map(g=>{
      const when=node('div',g.game_date||'Date unavailable');when.append(node('small',g.speed,'study-row-note'));
      const opponent=node('div',g.opponent);opponent.append(node('small',g.opponent_rating==null?'Rating unavailable':`Rating ${g.opponent_rating}`,'study-row-note'));
      return [when,opponent,g.player_color,g.result,g.opening,gameLink(g,'Open game →')];
    })));
    if(!data.rows.length)$('#study-games').append(node('p','No games match this selection. Widen the period or clear a filter.','study-empty'));
    $('#study-page-label').textContent=data.rows.length?`Games ${state.offset+1}–${state.offset+data.rows.length}`:'No matching games';
    $('#study-previous').disabled=state.offset===0;$('#study-next').disabled=!data.has_more;
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
    for(const question of ['What should I study next?','Where do I underperform in blitz games?','Summarize my games in the last 30 days','Compare White versus Black'])box.append(button(question,()=>{$('#study-question textarea').value=question;$('#study-question textarea').focus();},'study-prompt'));
  }
  function renderAnswer(answer,target) {
    const box=$(target);box.replaceChildren(node('p',answer.answer,'study-answer-text'),node('p',`Collection v${answer.version} · ${Object.entries(answer.filters).map(([k,v])=>`${k}: ${v}`).join(', ')||'all games in the selected version'}`,'muted'));
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
    if(state.preview||state.positionContext!==JSON.stringify(boardSelection())){clearTimeout(state.positionTimer);$('#study-position-engine').replaceChildren();state.positionContext=null;}
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
    [...$('#study-moves').children].forEach((b,i)=>b.classList.toggle('active',i+1===state.ply));
    const noteKey=`${state.replay.revision_id}:${state.ply}`;
    if(state.noteKey!==noteKey){const note=state.replay.notes.find(n=>n.ply===state.ply);$('#study-note textarea').value=note?.note||'';$('#study-note select').value=note?.status||'queued';state.noteKey=noteKey;}
  }
  function boardSelection(){return {...selection(),game_id:state.replay.game_id,ply:state.ply,moves:state.trial?.moves||[]};}
  async function tryMoves(moves){const key=state.boardKey,route=state.routeSequence,sequence=++state.boardSequence,value=await api('board/position',{...boardSelection(),moves});if(key!==state.boardKey||route!==state.routeSequence||sequence!==state.boardSequence)return;state.trial=value;state.replay.variations=value.variations;renderBoard();}
  async function refreshVariations(){const selected=boardSelection(),context=JSON.stringify(selected),route=state.routeSequence,value=await api('board/position',selected);if(route!==state.routeSequence||context!==JSON.stringify(boardSelection()))return;state.replay.variations=value.variations;if(state.trial)state.trial=value;else {state.boardPosition=value;state.boardPositionKey=state.boardKey;}renderBoard();}
  function renderVariations(variations){const box=$('#study-saved-variations');box.replaceChildren();if(variations.length)box.append(node('h4','Saved continuations'));for(const v of variations){const row=node('div',null,'saved-variation');row.append(button(`${v.label} · position ${v.ply}`,async()=>{state.preview=null;state.ply=v.ply;state.trial=null;state.boardKey=state.replay.revision_id+':'+v.ply;await tryMoves(v.moves);}),button('Remove',async()=>{await api('board/variations/remove',{id:v.id});await refreshVariations();},'text-link'));box.append(row);}}
  $('#board-preferences').addEventListener('submit',async e=>{e.preventDefault();try{const value=Object.fromEntries(new FormData(e.target));for(const key of ['coordinates','highlights'])value[key]=e.target.elements[key].checked;state.preferences=await api('board/preferences',value);state.flipped=value.orientation==='black'||value.orientation==='player'&&state.replay.player_color==='black';renderBoard();$('#board-settings-status').textContent='Saved to your account.';}catch(err){error(err);}});
  $('#study-trial-reset').addEventListener('click',()=>{state.trial=null;renderBoard();});
  $('#study-trial-undo').addEventListener('click',()=>tryMoves((state.trial?.moves||[]).slice(0,-1)).catch(error));
  $('#study-variation-save').addEventListener('submit',async e=>{e.preventDefault();try{await api('board/variations',{...boardSelection(),label:new FormData(e.target).get('label')});e.target.reset();await refreshVariations();alert('Continuation saved separately from the played game.',true);}catch(err){error(err);}});
  async function pollPosition(id,context,route){const value=await api('board/analysis/'+id);if(route!==state.routeSequence||context!==JSON.stringify(boardSelection()))return;state.positionContext=context;const box=$('#study-position-engine');box.replaceChildren(node('p','Position search: '+value.status));if(value.error)box.append(node('p',value.error,'callout'));if(value.status==='complete'){const e=value.evidence;box.append(node('p',e.mate!=null?'Mate score for White: '+e.mate:'White evaluation: '+(e.cp/100).toFixed(2)+' pawns'),node('p',e.pv_san.join(' ')),node('p',`${e.engine} · ${num(e.nodes)} nodes · depth ${e.depth} · ${e.explanation}`,'muted'));}else if(['queued','running'].includes(value.status))state.positionTimer=setTimeout(()=>pollPosition(id,context,route).catch(error),1200);}
  $('#study-position-analyze').addEventListener('click',async()=>{try{if(state.preview)throw new Error('Return to the played game or a trial line to evaluate a position.');const selected=boardSelection(),context=JSON.stringify(selected),route=state.routeSequence,value=await api('board/analysis',{...selected,profile:$('#study-position-profile').value});clearTimeout(state.positionTimer);await pollPosition(value.id,context,route);}catch(err){error(err);}});
  function renderEngine() {
    const box=$('#study-engine');box.replaceChildren();
    for(const job of state.replay.analysis_jobs){box.append(node('p',`Engine review: ${job.status}`,'study-pill'));if(job.error)box.append(node('p',job.error,'callout'));if(job.status!=='complete')continue;
      const e=job.evidence;box.append(node('p',`${e.engine} · ${e.analyzed_plies}/${e.total_plies} half-moves reviewed · ${e.complete_coverage?'full coverage':'partial coverage'}`,'muted'));
      if(!e.candidates.length)box.append(node('p','No move crossed the review threshold in the analyzed portion. This does not establish error-free play.'));
      for(const candidate of e.candidates.slice(0,3)){const card=node('div',null,'study-engine-item');card.append(node('h4',candidate.motif||'Move to review'),node('p',candidate.explanation||candidate.summary),button(`Before ${candidate.played_san}`,()=>{state.preview=null;state.ply=candidate.ply-1;renderBoard();},'text-link'),node('p',candidate.practice,'muted'));
        const lines=node('div',null,'study-variation-buttons');for(const [label,positions,moves] of [['Preferred',candidate.preferred_positions||[],candidate.pv_san],['Opponent reply',candidate.reply_positions||[],candidate.reply_pv_san||[]]])moves.forEach((move,i)=>{if(positions[i+1])lines.append(button(`${label} ${i+1}: ${move}`,()=>{state.preview={fen:positions[i+1],label:`${label} after ${move}`};renderBoard();},'secondary'));});lines.append(button('Return to played game',()=>{state.preview=null;renderBoard();},'text-link'));card.append(lines);box.append(card);}
    }
  }
  function source(value) {state.source=value;$('#study-file-fields').hidden=value!=='file';$('#study-api-fields').hidden=value!=='api';$('#study-source-file').classList.toggle('active',value==='file');$('#study-source-api').classList.toggle('active',value==='api');}
  async function previewLink() {const value=$('#study-import-form input[name=url]').value;if(!value)return;try{const data=await api('imports/preview',{url:value});$('#study-api-preview').textContent=data.explanation;}catch(e){$('#study-api-preview').textContent=e.message;}}
  async function pollJob(key,redirect=true) {
    clearTimeout(state.timer);const sequence=state.routeSequence,value=await api('imports/'+encodeURIComponent(key));
    if(sequence!==state.routeSequence||$('#personal').hidden)return;
    state.job=value;$('#study-import-progress').hidden=false;
    $('#study-job-phase').textContent=value.phase;$('#study-job-detail').textContent=`${num(value.games_processed)} games read · ${num(value.new_games)} new · ${num(value.changed_games)} updated · ${num(value.duplicate_games)} reused${value.rejected_games?' · '+num(value.rejected_games)+' not imported':''}${value.bytes_received?' · '+(value.bytes_received/1048576).toFixed(1)+' MiB received':''}`;
    const diagnostics=(value.diagnostics||[]).map(item=>`Game ${item.game_number}: ${item.reason}`).join('\n');
    $('#study-job-error').hidden=!(value.error||diagnostics);$('#study-job-error').textContent=[value.error,diagnostics].filter(Boolean).join('\n');
    const active=['queued','running'].includes(value.status);$('#study-job-progress').hidden=!active;$('#study-job-cancel').hidden=!active;
    $('#study-job-retry').hidden=!(value.source_kind==='lichess'&&['failed','cancelled'].includes(value.status));$('#study-job-open').hidden=value.status!=='complete';
    if(value.status==='complete'){$('#study-job-open').href=`#personal/${value.library_id}/overview?snapshot=${value.snapshot_id}`;if(redirect){const skipped=value.rejected_games?` ${num(value.rejected_games)} unsupported, unfinished or unrelated games were excluded; open Recent imports for details.`:'';alert(`Import complete: ${num(value.new_games)} new games, ${num(value.changed_games)} updated, ${num(value.duplicate_games)} reused. Your collection is ready.${skipped}`,true);resetLibrary(value.library_id);state.snapshot=value.snapshot_id;go(href('overview'));return;}}
    if(active)state.timer=setTimeout(()=>{if(!$('#personal').hidden)pollJob(key,true).catch(error);},2000);
  }
  function upload(form) {
    return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST','/api/personal/imports/file');xhr.setRequestHeader('X-Study-Action','1');xhr.setRequestHeader('X-CSRF-Token',state.user.csrf);xhr.upload.onprogress=e=>{if(e.lengthComputable)alert(`Uploading ${(e.loaded/1048576).toFixed(1)} of ${(e.total/1048576).toFixed(1)} MiB…`,true);};xhr.onload=()=>{try{const value=JSON.parse(xhr.responseText);if(xhr.status>=400)reject(new Error(value.error));else resolve(value);}catch(e){reject(e);}};xhr.onerror=()=>reject(new Error('The upload connection was interrupted. Choose the file again to retry.'));xhr.send(form);});
  }

  $('#study-signin-tab').addEventListener('click',()=>{state.signup=false;$('#study-auth-title').textContent='Open your study library.';$('#study-auth-submit').textContent='Sign in';$('#study-signin-tab').classList.add('active');$('#study-signup-tab').classList.remove('active');});
  $('#study-signup-tab').addEventListener('click',()=>{state.signup=true;$('#study-auth-title').textContent='Create your private study space.';$('#study-auth-submit').textContent='Create account';$('#study-signup-tab').classList.add('active');$('#study-signin-tab').classList.remove('active');});
  $('#study-auth-form').addEventListener('submit',async e=>{e.preventDefault();const b=$('#study-auth-submit');b.disabled=true;try{const result=await api(state.signup?'signup':'login',Object.fromEntries(new FormData(e.target)));state.user=result.user;e.target.reset();alert('');go('#personal');}catch(err){error(err);}finally{b.disabled=false;}});
  $('#study-logout').addEventListener('click',async()=>{try{await api('logout',{});state.user=null;state.library=null;state.snapshot=null;state.selected={};showAuth();go('#personal');}catch(e){error(e);}});
  $('#study-password').addEventListener('submit',async e=>{e.preventDefault();try{const value=await api('password',Object.fromEntries(new FormData(e.target)));state.user=value.user;e.target.reset();alert('App password changed. Other sessions have been signed out.',true);}catch(err){error(err);}});
  $('#study-source-file').addEventListener('click',()=>source('file'));$('#study-source-api').addEventListener('click',()=>source('api'));
  $('#study-api-example').addEventListener('click',()=>{const player=$('#study-player').value.trim();if(!player){alert('Enter a Lichess player name below to create the link.');$('#study-player').focus();return;}$('#study-import-form input[name=url]').value='https://lichess.org/api/games/user/'+encodeURIComponent(player);previewLink();});
  $('#study-import-form input[name=url]').addEventListener('change',previewLink);
  $('#study-import-mode').addEventListener('change',()=>{$('#study-mode-help').textContent=$('#study-import-mode').value==='merge'?'Keep the games already in this library. Add new games, reuse identical games, and update changed information. This is the usual choice.':'Make this import the active collection for this player. Games outside this import disappear from the active view; earlier saved versions and studies remain available. Shared game data is reused.';});
  $('#study-import-form').addEventListener('submit',async e=>{e.preventDefault();const b=$('#study-import-submit'),sequence=state.routeSequence;b.disabled=true;try{let result;if(state.source==='file'){const form=new FormData(e.target);if(!form.get('file')?.size)throw new Error('Choose a PGN file first.');form.delete('url');result=await upload(form);}else result=await api('imports/lichess',{url:e.target.elements.url.value,player:e.target.elements.player.value,mode:e.target.elements.mode.value});if(sequence!==state.routeSequence||$('#personal').hidden)return;alert('Import started. You can leave this page and return from Recent imports.',true);state.job=null;history.replaceState(null,'',`#personal/import?job=${result.job_id}`);await pollJob(result.job_id,true);}catch(err){error(err);}finally{b.disabled=false;}});
  $('#study-job-cancel').addEventListener('click',async()=>{if(!state.job)return;try{await api(`imports/${state.job.id}/cancel`,{});await pollJob(state.job.id,false);}catch(e){error(e);}});
  $('#study-job-retry').addEventListener('click',async()=>{const sequence=state.routeSequence;try{const result=await api(`imports/${state.job.id}/retry`,{});if(sequence!==state.routeSequence||$('#personal').hidden)return;history.replaceState(null,'',`#personal/import?job=${result.job_id}`);await pollJob(result.job_id,true);}catch(e){error(e);}});
  $('#study-period').addEventListener('change',updatePeriod);
  $('#study-filters').addEventListener('submit',async e=>{e.preventDefault();state.selected=filterValues();state.offset=0;const r=route();if(r.parts[2]==='games')await loadGames();else await loadReport();});
  $('#study-version').addEventListener('change',()=>{state.snapshot=$('#study-version').value;state.offset=0;go(href(route().parts[2]||'overview'));});
  $('#study-previous').addEventListener('click',()=>{state.offset=Math.max(0,state.offset-10);loadGames().catch(error);});$('#study-next').addEventListener('click',()=>{state.offset+=10;loadGames().catch(error);});
  $('#study-clear-opening').addEventListener('click',()=>{delete state.selected.opening;state.offset=0;loadGames().catch(error);});
  $('#study-question').addEventListener('submit',async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{const answer=await api('assistant',{...selection(),question:new FormData(e.target).get('question')});renderAnswer(answer,'#study-answer');}catch(err){error(err);}finally{b.disabled=false;}});
  $('#study-game-question').addEventListener('submit',async e=>{e.preventDefault();try{if(state.preview||state.trial?.moves.length)throw new Error('Return to the played game before asking about its position.');const answer=await api('assistant',{...selection(),question:new FormData(e.target).get('question'),game_id:state.replay.game_id,ply:state.ply});renderAnswer(answer,'#study-game-answer');}catch(err){error(err);}});
  $('#study-save').addEventListener('submit',async e=>{e.preventDefault();try{await api('studies',{...selection(),name:new FormData(e.target).get('name')});await loadSaved();alert('Saved with this exact collection version and filters.',true);}catch(err){error(err);}});
  $('#study-ply').addEventListener('input',e=>{state.trial=null;state.preview=null;state.ply=Number(e.target.value);renderBoard();});
  for(const [id,value] of [['study-start',()=>0],['study-prev',()=>state.ply-1],['study-next-move',()=>state.ply+1],['study-end',()=>state.replay.moves.length]])$(`#${id}`).addEventListener('click',()=>{if(!state.replay)return;state.trial=null;state.preview=null;state.ply=Math.max(0,Math.min(value(),state.replay.moves.length));renderBoard();});
  $('#study-flip').addEventListener('click',()=>{state.flipped=!state.flipped;renderBoard();});
  $('#study-copy-link').addEventListener('click',async()=>{const url=location.origin+location.pathname+href('games',state.replay.game_id,state.ply);try{await navigator.clipboard.writeText(url);alert('Game link copied.',true);}catch(e){alert(url,true);}});
  $('#study-analyze').addEventListener('click',async()=>{try{await api('analysis',{...selection(),game_id:state.replay.game_id});await loadGame(state.replay.game_id,state.ply,state.routeSequence,true);alert('Engine review queued. Its findings will appear here.',true);}catch(e){error(e);}});
  $('#study-note').addEventListener('submit',async e=>{e.preventDefault();try{if(state.preview||state.trial?.moves.length)throw new Error('Return to the played game before saving a note on its position.');await api('review',{...selection(),game_id:state.replay.game_id,ply:state.ply,...Object.fromEntries(new FormData(e.target))});await loadGame(state.replay.game_id,state.ply);alert('Practice note saved.',true);}catch(err){error(err);}});
  $('#study-position').addEventListener('click',async()=>{try{if(state.preview||state.trial?.moves.length)throw new Error('Return to the played game before looking up its position.');const game=state.replay.game_id,ply=state.ply;const result=await api('positions',{...selection(),game_id:game,ply});if(!state.replay||state.replay.game_id!==game||state.ply!==ply||state.preview||state.trial?.moves.length)return;$('#study-positions').replaceChildren(node('h4',`${result.games} games reached this position`),table(['Played move','Games','Your score','Example'],result.alternatives.map(a=>[a.move,a.games,pct(a.score_rate),gameLink(a.example,'Open example')])),node('p',result.explanation,'muted'));}catch(e){error(e);}});
  return {open:()=>open().catch(error),close:()=>{clearTimeout(state.timer);clearTimeout(state.positionTimer);++state.boardSequence;++state.routeSequence;}};
})();
