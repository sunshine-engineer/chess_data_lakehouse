'use strict';
(()=>{
 const A=window.Atlas,{$,el,link,button,table,number,pct,metric,state}=A;
 let paging={key:null,cursors:[null],index:0,next:null,total:0};
 const engineName={python:'Python',python_mp:'Parallel Python',spark:'Spark'},resultName={'1-0':'White win','0-1':'Black win','1/2-1/2':'Draw'};
 function bars(target,rows,labels={},action){
  const box=$(target);box.replaceChildren();const total=rows.reduce((n,r)=>n+r.count,0);
  if(!total){box.append(A.empty('No matching observations','Widen the filters to see this distribution.'));return;}
  for(const row of rows){const n=el('div',null,'bar-row'),track=el('div',null,'bar-track'),fill=el('div',null,'bar-fill');fill.style.width=(row.count/total*100)+'%';track.append(fill);
   n.append(action?button(labels[row.label]||row.label,()=>action(row),'text-link'):el('span',labels[row.label]||row.label),track,el('span',pct(row.count/total),'bar-count'));n.title=number(row.count)+' games';box.append(n);}
  const details=el('details');details.append(el('summary','View exact counts'),table(['Category','Games'],rows.map(r=>[labels[r.label]||r.label,number(r.count)])));box.append(details);
 }
 function chart(target,rows,line=false){
  const box=$(target);box.replaceChildren();if(!rows.length){box.append(A.empty('No observations','Try a wider selection.'));return;}
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 560 190');svg.setAttribute('role','img');svg.setAttribute('aria-label',line?'Games by game-start date':'Games by average-rating band');
  const draw=(tag,attrs,text)=>{const n=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));if(text!=null)n.textContent=text;svg.append(n);return n;},max=Math.max(1,...rows.map(r=>r.count)),x=i=>45+i*500/Math.max(1,rows.length-1),y=v=>145-v/max*120;
  for(const f of [0,.5,1]){draw('line',{x1:45,y1:y(f*max),x2:550,y2:y(f*max),stroke:'#dfe7de','stroke-dasharray':'3 4'});draw('text',{x:0,y:y(f*max)+3},number(f*max));}
  if(line){const d=rows.map((r,i)=>(i?'L':'M')+x(i)+' '+y(r.count)).join(' ');draw('path',{d:d+' L'+x(rows.length-1)+' 145 L45 145 Z',fill:'#e1eee6'});draw('path',{d,fill:'none',stroke:'#24816d','stroke-width':2});}
  else rows.forEach((r,i)=>{const n=draw('rect',{x:45+i*500/rows.length,y:y(r.count),width:Math.max(3,500/rows.length-6),height:145-y(r.count),fill:'#78a48a',rx:2});const title=document.createElementNS(ns,'title');title.textContent=r.label+': '+number(r.count)+' games';n.append(title);});
  [...new Set([0,Math.floor(rows.length/2),rows.length-1])].forEach(i=>draw('text',{x:line?x(i):45+i*500/rows.length,y:171,'text-anchor':i===rows.length-1?'end':'start'},line?rows[i].label.slice(5):rows[i].label));
  const details=el('details');details.append(el('summary','View chart data'),table([line?'Game date':'Rating band','Games'],rows.map(r=>[r.label,number(r.count)])));box.append(svg,details);
 }
 async function overview(){return A.load(async signal=>{const [catalog,data]=await Promise.all([A.catalog(signal),A.request('/api/overview?'+A.query(),{},signal)]);return {catalog,data};},({catalog,data:d})=>{
  const s=d.summary;$('#metrics').replaceChildren(metric('Games in this view',number(s.games),'Filtered, retained archive facts'),metric('Average rating',number(s.average_rating),'Mean of available game averages'),metric('Average game length',number(s.average_moves),'Full moves, rounded up from half moves'),metric('Game dates',number(s.days),(s.first_date||'No start')+' → '+(s.last_date||'No end')));
  chart('#daily-chart',d.daily,true);bars('#speed-chart',d.speeds,{},row=>A.setFilters({speed:row.label},'explorer'));bars('#result-chart',d.results,resultName,row=>A.setFilters({result:row.label},'explorer'));
  chart('#rating-chart',[...d.rating_bands].sort((a,b)=>(a.label==='Unknown'?Infinity:Number(a.label))-(b.label==='Unknown'?Infinity:Number(b.label))));
  const insights=$('#overview-insights');insights.replaceChildren();
  if(!s.games)insights.append(A.empty('No games match this view','Reset the filters or choose a published archive.',button('Reset filters',()=>{$('#clear-filters').click();})));
  else{
   const top=[...d.speeds].sort((a,b)=>b.count-a.count)[0],peak=[...d.daily].sort((a,b)=>b.count-a.count)[0];
   for(const [title,text,href] of [['Follow the main time class',top?number(top.count)+' '+top.label+' games make up '+pct(top.count/s.games)+' of this view.':'Clock information is unavailable.',A.href('explorer',{}, {...state.filters,speed:top?.label||''})],['Inspect the busiest day',peak?peak.label+' contains '+number(peak.count)+' matching games.':'No game dates are available.',A.href('explorer',{}, {...state.filters,from_date:peak?.label||'',to_date:peak?.label||''})],['Keep the selection',number(s.games)+' matches. Export up to 5,000 IDs with their facts and publication lineage.','/api/games.csv?'+A.query()]]){
    const card=el('article',null,'insight-tile');card.append(el('h3',title),el('p',text),link(title==='Keep the selection'?'Download CSV →':'Explore these games →',href));insights.append(card);}
  }
  $('#quality').replaceChildren();for(const [label,value] of [['Games with unknown identities',number(d.unknown_player_games)],['Games with a missing rating',number(s.missing_ratings)],['Rejected records in whole archive',number(d.archive_rejects)],['Archive month',state.filters.month]]){const row=el('div',null,'quality-stat');row.append(el('span',label),el('strong',value));$('#quality').append(row);}
  $('#openings').replaceChildren(table(['Opening','Code','Games','Share','Explore'],d.openings.map(o=>[o.label,o.eco,number(o.count),pct(o.count/(s.games||1)),o.eco!=='Unknown'?link('Open games →',A.href('explorer',{}, {...state.filters,eco:o.eco})):el('span','No opening code','muted')])));
  const publications=$('#publication-list');publications.replaceChildren();
  for(const p of catalog.publications){const row=el('div',null,'publication-list-item');row.append(link(engineName[p.engine],A.href('overview',{}, {...state.filters,engine:p.engine,month:p.month})),el('span',p.month+' · '+number(p.games)+' games'));publications.append(row);}
  if(!catalog.publications.length)publications.append(A.empty('No archive has been published','Your private Personal Study library is separate and remains available.',link('Open Personal Study →','#personal')));
 });}
 async function explorer(){
  const identity=state.route.parts[1]==='game'?state.route.parts[2]:null;
  $('#archive-game-list').hidden=!!identity;$('#archive-game-detail').hidden=!identity;
  if(identity)return detail(identity);
  const key=A.query({sort:state.sort}).toString();if(paging.key!==key)paging={key,cursors:[null],index:0,next:null,total:0};
  return A.load(async signal=>{const [,data]=await Promise.all([A.catalog(signal),A.request('/api/games?'+A.query({sort:state.sort,limit:25,cursor:paging.cursors[paging.index]}),{},signal)]);return data;},d=>{
   paging.next=d.next_cursor;paging.total=d.total;$('#game-count').textContent=number(d.total)+' matching games';
   $('#games').replaceChildren(table(['Game / date','White Elo','Black Elo','Result','Full moves','Clock','Inspect'],d.rows.map(r=>{const when=el('div',r.date||'Date unknown');when.append(el('small',r.game_id,'study-row-note'));return [when,number(r.white_rating),number(r.black_rating),resultName[r.result]||r.result,number(r.move_count),r.speed,link('Open game →',A.href('explorer/game/'+r.game_id))];}),'No games match. Clear the search or widen the filters.'));
   $('#page-label').textContent=d.rows.length?'Games '+number(paging.index*25+1)+'–'+number(paging.index*25+d.rows.length)+' of '+number(d.total):'No matching games';
   $('#previous').disabled=paging.index===0;$('#next').disabled=!d.has_more;$('#archive-csv').href='/api/games.csv?'+A.query();
  });
 }
 async function detail(identity){return A.load(async signal=>{const [,d]=await Promise.all([A.catalog(signal),A.request('/api/games/'+encodeURIComponent(identity)+'?'+A.query(),{},signal)]);return d;},d=>{
  const box=$('#archive-game-detail');box.replaceChildren(link('← Back to matching games',A.href('explorer')));
  const hero=el('article',null,'panel archive-game-hero');hero.append(el('p','ARCHIVE GAME','eyebrow'),el('h2',resultName[d.result]||d.result),el('p','Game '+d.game_id+' · '+(d.date||'Date unknown'),'game-id'));
  const facts=el('div',null,'fact-grid');for(const [label,value] of [['White rating',number(d.white_rating)],['Black rating',number(d.black_rating)],['Time class',d.speed],['Full moves',number(d.move_count)],['Time control',d.control_code],['Opening code',d.eco||'Unknown']]){const n=el('div');n.append(el('small',label),el('strong',value));facts.append(n);}hero.append(facts);box.append(hero);
  const grid=el('div',null,'archive-detail-grid'),context=el('article',null,'panel'),actions=el('article',null,'panel');
  context.append(el('p','RECORDED CONTEXT','eyebrow'),el('h2',d.opening||'Opening unavailable'));
  const dl=el('dl');for(const [label,value] of [['Termination',d.termination||'Unknown'],['Archive month',d.source_month.slice(0,7)],['White title when played',d.white_title_as_played||'None recorded'],['Black title when played',d.black_title_as_played||'None recorded'],['Processing path',engineName[d.engine]],['Publication run',d.run_id]])dl.append(el('dt',label),el('dd',value));context.append(dl);
  actions.append(el('p','CONTINUE EXPLORING','eyebrow'),el('h2','From a fact to a question.'),el('p','This archive stores structured game facts. For move-by-move review, open the public source or use a PGN in your private Personal Study library.'));
  const source=link('Open the public Lichess game ↗',d.source_url);source.target='_blank';source.rel='noopener noreferrer';actions.append(source);
  if(d.white_rating!=null&&d.black_rating!=null&&d.initial_seconds!=null&&d.increment_seconds!=null)actions.append(el('br'),link('Explore a pre-game estimate →',A.href('models',{white_rating:d.white_rating,black_rating:d.black_rating,initial_seconds:d.initial_seconds,increment_seconds:d.increment_seconds})));
  actions.append(el('br'),link('Review my own games →','#personal'));grid.append(context,actions);box.append(grid);
 });}
 A.pages.overview=overview;A.pages.explorer=explorer;
 window.addEventListener('DOMContentLoaded',()=>{
  $('#previous').addEventListener('click',()=>{if(paging.index){--paging.index;explorer();}});
  $('#next').addEventListener('click',()=>{if(paging.next){paging.cursors[++paging.index]=paging.next;explorer();}});
  $('#game-sort').addEventListener('change',()=>{state.sort=$('#game-sort').value;A.go('explorer');});
  $('#game-search').addEventListener('submit',event=>{event.preventDefault();A.setFilters({game_id:event.target.elements.game_id.value.trim()},'explorer');});
  $('#clear-search').addEventListener('click',()=>A.setFilters({game_id:''},'explorer'));
 });
})();
