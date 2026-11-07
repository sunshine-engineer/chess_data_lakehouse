'use strict';
window.Atlas=(()=>{
 const $=s=>document.querySelector(s),number=v=>v==null?'—':Number(v).toLocaleString(undefined,{maximumFractionDigits:1}),pct=v=>v==null?'—':(100*v).toFixed(1)+'%';
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
 const link=(text,href,cls='text-link')=>{const n=el('a',text,cls);n.href=href;return n;};
 const button=(text,action,cls='secondary')=>{const n=el('button',text,cls);n.type='button';n.addEventListener('click',()=>Promise.resolve(action()).catch(e=>notice(e.message)));return n;};
 const defaults={engine:'python',month:'2013-01',speed:'',result:'',min_rating:'0',max_rating:'4000',from_date:'',to_date:'',eco:'',game_id:''};
 const state={view:'personal',route:null,filters:{...defaults},sort:'newest',catalog:null,epoch:0,controller:null,loadToken:0};
 const pages={};
 function table(headers,rows,empty='No observations match this selection.'){
  const wrap=el('div',null,'table-wrap'),t=el('table'),head=el('thead'),tr=el('tr');
  headers.forEach(text=>{const n=el('th',text);n.scope='col';tr.append(n);});head.append(tr);t.append(head);
  const body=el('tbody');for(const row of rows){const r=el('tr');for(const value of row){const cell=el('td');if(value instanceof Node)cell.append(value);else cell.textContent=value??'—';r.append(cell);}body.append(r);}t.append(body);wrap.append(t);
  if(!rows.length)wrap.append(el('p',empty,'muted'));return wrap;
 }
 function metric(label,value,detail){const n=el('article',null,'metric');n.append(el('span',label,'label'),el('strong',value),el('small',detail));return n;}
 function empty(title,message,action){const n=el('div',null,'empty-state');n.append(el('span','◈','empty-symbol'),el('h2',title),el('p',message));if(action)n.append(action);return n;}
 function notice(message,retry=true){$('#notice').hidden=!message;$('#notice-message').textContent=message||'';$('#retry-page').hidden=!retry;}
 async function request(path,options={},signal){
  const controller=new AbortController(),cancel=()=>controller.abort();if(signal){if(signal.aborted)controller.abort();else signal.addEventListener('abort',cancel,{once:true});}
  let timedOut=false;const timer=setTimeout(()=>{timedOut=true;controller.abort();},20000);
  try{const response=await fetch(path,{...options,signal:controller.signal});let data;try{data=await response.json();}catch(e){throw new Error('The service returned an unreadable response. Please retry.');}
   if(!response.ok){const error=new Error(data.error||'The request could not be completed.');error.status=response.status;throw error;}return data;
  }catch(e){if(timedOut)throw new Error('The request took too long. Try again when the service is ready.');throw e;}
  finally{clearTimeout(timer);signal?.removeEventListener('abort',cancel);}
 }
 function query(extra={}){const p=new URLSearchParams();for(const [k,v] of Object.entries({...state.filters,...extra}))if(v!==''&&v!=null)p.set(k,v);return p;}
 function href(view,extra={},filters=state.filters){const p=new URLSearchParams();if(view.startsWith('overview')||view.startsWith('explorer')){for(const [k,v] of Object.entries(filters))if(v!==''&&v!=null&&String(v)!==defaults[k])p.set(k,v);if(state.sort!=='newest')p.set('sort',state.sort);}
  for(const [k,v] of Object.entries(extra))if(v!==''&&v!=null)p.set(k,v);return '#'+view+(p.size?'?'+p:'');}
 function go(view,extra={}){const hash=href(view,extra);if(location.hash===hash)navigate();else location.hash=hash;}
 function setFilters(changes,view='explorer'){state.filters={...state.filters,...changes};go(view);}
 function syncFilters(){for(const [key,value] of Object.entries(state.filters)){const field=$('#filters').elements[key];if(!field)continue;if(key==='month'&&![...field.options].some(o=>o.value===value)){const option=el('option',value);option.value=value;field.append(option);}field.value=value;}
  $('#game-sort').value=state.sort;$('#game-search input').value=state.filters.game_id;$('#clear-search').hidden=!state.filters.game_id;
  if(state.filters.eco||state.filters.from_date||state.filters.to_date||state.filters.min_rating!=='0'||state.filters.max_rating!=='4000')$('.filter-more').open=true;
  const publication=state.catalog?.publications.find(p=>p.engine===state.filters.engine&&p.month===state.filters.month);
  $('#archive-scope-title').textContent='Lichess · '+state.filters.month+' archive';
  $('#archive-scope-detail').textContent=publication?number(publication.games)+' published games · '+publication.first_date+' to '+publication.last_date+' game dates · separate from Personal Study':'No published games for this archive and processing path. Personal Study is separate.';
 }
 async function catalog(signal){if(state.catalog)return state.catalog;const d=await request('/api/catalog',{},signal);state.catalog=d;
  const selected=state.filters.month;const months=[...new Set([selected,...d.publications.map(p=>p.month)])].sort();$('#month').replaceChildren(...months.map(m=>{const n=el('option',m);n.value=m;return n;}));syncFilters();return d;}
 async function load(fetcher,render){const epoch=state.epoch,token=++state.loadToken,view=state.view,root=$('#'+view);state.controller?.abort();const controller=new AbortController();state.controller=controller;
  notice('');root.dataset.state='loading';root.setAttribute('aria-busy','true');$('#page-loading').hidden=false;
  try{const d=await fetcher(controller.signal);if(epoch!==state.epoch||token!==state.loadToken)return;render(d);root.dataset.state='ready';}
  catch(e){if(epoch!==state.epoch||token!==state.loadToken||e.name==='AbortError')return;root.dataset.state='error';notice(e.message);}
  finally{if(epoch===state.epoch&&token===state.loadToken){$('#page-loading').hidden=true;root.setAttribute('aria-busy','false');}}
 }
 const labels={personal:['Personal Study','Import your games. Find a focus. Practice the position.'],overview:['The archive, in perspective.','Follow a pattern into the games behind it.'],explorer:['Find the game behind the number.','Search, inspect and export the retained public archive.'],pipelines:['Understand the engineering trade-off.','Compare measured execution, resources and equivalent outputs.'],models:['Explore the estimate. Inspect the evidence.','Historical models, simple baselines and comparable games in one place.'],studies:['Practical questions. Inspectable answers.','Read the decisions, measured outcomes and limits behind this workspace.']};
 async function navigate(){
  const [path,search='']=location.hash.slice(1).split('?');const parts=(path||'personal').split('/'),view=labels[parts[0]]?parts[0]:'personal';
  state.controller?.abort();++state.epoch;state.view=view;state.route={path:view==='personal'?(path||'personal'):path,parts,params:new URLSearchParams(search)};
  if(view!=='personal')window.Study?.close?.();
  if(['overview','explorer'].includes(view)){state.filters={...defaults};for(const key of Object.keys(defaults))if(state.route.params.has(key))state.filters[key]=state.route.params.get(key);state.sort=state.route.params.get('sort')||'newest';syncFilters();}
  document.body.classList.toggle('personal-view',view==='personal');document.querySelectorAll('.view').forEach(n=>n.hidden=n.id!==view);
  document.querySelectorAll('.nav').forEach(n=>{const active=n.dataset.view===view;n.classList.toggle('active',active);if(active)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');});
  $('#filters').hidden=!['overview','explorer'].includes(view)||parts[1]==='game';$('#archive-context').hidden=!['overview','explorer'].includes(view);
  $('#refresh-page').hidden=view==='personal';$('#copy-view').hidden=view==='personal';
  $('#page-title').textContent=labels[view][0];$('#page-subtitle').textContent=labels[view][1];$('#breadcrumb').textContent=[...document.querySelector('[data-view="'+view+'"]').childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('').trim();
  $('#page-eyebrow').textContent=view==='personal'?'YOUR CHESS WORKSPACE':view==='overview'||view==='explorer'?'PUBLIC ARCHIVE EXPLORATION':'METHODS & EVIDENCE';
  $('#study-page-scope').textContent=view==='personal'?'Private game library':view==='models'?'Historical model publication':view==='pipelines'?'Recorded benchmark results':'Public archive · historical evidence';
  $('#workspace-footer-scope').textContent=view==='personal'?'Your games · Evidence-linked review · Deliberate practice':'Lichess public archive · January 2013 · Personal libraries remain private';
  document.title=labels[view][0]+' · Chess Atlas';notice('');$('#page-loading').hidden=true;window.scrollTo(0,0);
  if(view==='personal')await window.Study.open();else await pages[view]?.();
 }
 function start(){
  $('.skip').addEventListener('click',event=>{event.preventDefault();$('#main').tabIndex=-1;$('#main').focus({preventScroll:true});window.scrollTo(0,0);});
  window.addEventListener('hashchange',navigate);
  document.querySelectorAll('.nav').forEach(n=>n.addEventListener('click',event=>{if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault();go(n.dataset.view);}));
  $('#filters').addEventListener('submit',event=>{event.preventDefault();const values=Object.fromEntries(new FormData(event.target));state.filters={...state.filters,...values};go(state.view);});
  $('#clear-filters').addEventListener('click',()=>{state.filters={...defaults};go(state.view);});
  $('#refresh-page').addEventListener('click',()=>{state.catalog=null;pages.models?.invalidate?.();navigate();});
  $('#retry-page').addEventListener('click',()=>navigate());
  $('#copy-view').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(location.href);notice('View link copied. It includes this page and its selected filters.',false);}catch(e){notice('Copy this view from your browser address bar.',false);}});
  navigate();
 }
 window.addEventListener('DOMContentLoaded',start);
 return {$,el,link,button,table,number,pct,metric,empty,notice,request,query,href,go,setFilters,syncFilters,catalog,load,state,pages};
})();
