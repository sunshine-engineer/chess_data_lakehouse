'use strict';
// Original SVG geometry, drawn for Chess Atlas. No external fonts, assets or CDN.
window.ChessBoard=(()=>{
 const ns='http://www.w3.org/2000/svg',names={p:'pawn',n:'knight',b:'bishop',r:'rook',q:'queen',k:'king'};
 const paths={p:'M24 48 L27 35 Q18 29 24 21 Q32 13 40 21 Q46 29 37 35 L40 48 Z',
  n:'M18 48 L20 38 L28 32 L21 32 L19 27 L29 14 L38 11 L38 18 Q49 24 45 39 L44 48 Z M28 22 L31 22',
  b:'M22 48 L27 35 Q17 27 32 12 Q47 27 37 35 L42 48 Z M31 20 L36 28',
  r:'M21 48 L23 25 L18 25 L18 13 L25 13 L25 19 L29 19 L29 13 L35 13 L35 19 L39 19 L39 13 L46 13 L46 25 L41 25 L43 48 Z',
  q:'M21 48 L16 23 L26 29 L32 15 L38 29 L48 23 L43 48 Z M22 39 L42 39',
  k:'M22 48 L25 34 Q17 26 25 21 L30 21 L30 16 L25 16 L25 11 L30 11 L30 7 L35 7 L35 11 L40 11 L40 16 L35 16 L35 21 L40 21 Q48 26 39 34 L42 48 Z'};
 function piece(char,style){const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 64 64');svg.setAttribute('aria-hidden','true');svg.classList.add('board-piece',char===char.toUpperCase()?'ivory':'ebony',style||'classic');
  for(const d of [paths[char.toLowerCase()],'M18 49 L46 49 L49 55 L15 55 Z']){const p=document.createElementNS(ns,'path');p.setAttribute('d',d);svg.append(p);}return svg;}
 function render(root,position,options={}){
  const prefs={theme:'forest',pieces:'classic',size:'comfortable',coordinates:true,highlights:true,...options.preferences};
  root.dataset.theme=prefs.theme;root.dataset.size=prefs.size;root.classList.add('atlas-board');root.setAttribute('role','group');root.setAttribute('aria-label',options.label||'Chess board. Select a piece, then its destination. Arrow keys navigate squares.');
  const map={};position.fen.split(' ')[0].split('/').forEach((rank,i)=>{let f=0;for(const c of rank){if(/[1-8]/.test(c))f+=Number(c);else map['abcdefgh'[f++]+(8-i)]=c;}});
  let selected=null,focusSquare=null;const legal=position.legal_moves||[];
  function draw(){const old=root.contains(document.activeElement)?document.activeElement.dataset.square:null;root.replaceChildren();const cells=[];
   for(let index=0;index<64;index++){const ri=Math.floor(index/8),fi=index%8,r=options.flipped?7-ri:ri,f=options.flipped?7-fi:fi,s='abcdefgh'[f]+(8-r),char=map[s],cell=document.createElement('button');
    cell.type='button';cell.dataset.square=s;cell.className='chess-square '+((r+f)%2?'dark':'light');cell.title=s;cell.tabIndex=index===0?0:-1;
    cell.setAttribute('aria-label',s+(char?' '+(char===char.toUpperCase()?'White ':'Black ')+names[char.toLowerCase()]:' empty'));
    if(char)cell.append(piece(char,prefs.pieces));const coord=document.createElement('small');coord.textContent=s;coord.className='square-coordinates';coord.hidden=!prefs.coordinates||(ri!==7&&fi!==0);cell.append(coord);
    if(prefs.highlights&&options.last?.includes(s))cell.classList.add('last-move');if(prefs.highlights&&position.check===s)cell.classList.add('in-check');
    if(selected===s)cell.classList.add('selected');if(prefs.highlights&&selected&&legal.some(m=>m.startsWith(selected+s)))cell.classList.add('legal-target');
    cell.addEventListener('keydown',event=>{const delta={ArrowLeft:-1,ArrowRight:1,ArrowUp:-8,ArrowDown:8}[event.key];if(delta){event.preventDefault();const next=Math.max(0,Math.min(63,index+delta));cells.forEach(c=>c.tabIndex=-1);cells[next].tabIndex=0;cells[next].focus();}if(event.key==='Escape'){selected=null;draw();}});
    cell.addEventListener('click',()=>{if(!options.onMove)return;
     const matches=selected?legal.filter(m=>m.startsWith(selected+s)):[];
     if(matches.length){if(matches.length>1){const chooser=document.createElement('div');chooser.className='promotion-picker';chooser.setAttribute('role','group');chooser.setAttribute('aria-label','Choose promotion');for(const move of matches){const b=document.createElement('button');b.type='button';b.textContent='Promote to '+names[move[4]];b.onclick=()=>options.onMove(move);chooser.append(b);}root.after(chooser);chooser.querySelector('button').focus();}else options.onMove(matches[0]);selected=null;}
     else {selected=legal.some(m=>m.startsWith(s))?s:null;focusSquare=s;draw();}
    });cells.push(cell);root.append(cell);
   }
   const focus=focusSquare||old;if(focus)root.querySelector('[data-square="'+focus+'"]')?.focus({preventScroll:true});
  }
  root.parentElement.querySelectorAll('.promotion-picker').forEach(n=>n.remove());draw();
 }
 return {render};
})();
