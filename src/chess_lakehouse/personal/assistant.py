"""Evidence-first analytics: bounded intent routing, deterministic facts and refusals."""
import json
import os
import re
import urllib.request

from . import reports

INTENTS={'overview','openings','speed','color','opponents','review'}
ROUTE_SCHEMA={'type':'object','properties':{
    'intent':{'type':'string','enum':sorted(INTENTS)},
    'filters':{'type':'object','properties':{
        'color':{'type':'string','enum':['white','black']},
        'speed':{'type':'string','enum':['ultrabullet','bullet','blitz','rapid','classical']},
        'opponent_band':{'type':'string','enum':['weaker','similar','stronger']}},
        'additionalProperties':False}},'required':['intent','filters'],'additionalProperties':False}
SUSPICIOUS=re.compile(r'ignore.{0,30}instructions|system\s+prompt|\b(drop|delete|insert|execute|password|token|javascript)\b|guarantee|cheat',re.I)


def template_route(question):
    text=question.casefold()
    if SUSPICIOUS.search(text):
        return 'refuse',{},'This assistant cannot execute commands, reveal secrets, prove cheating or guarantee improvement.'
    if re.search(r'predict.*rating|rating.*(next|future)|will.*(rating|win)|how much.*improv',text):
        return 'refuse',{},'This study cannot predict future ratings, wins or the size of an improvement.'
    if re.search(r'\b(since|until|between)\b|\b(after|before|over|under|above|below)\s+\d|\d+[- ]rated|rating\s*[<>=]',text):
        return 'refuse',{},'This assistant does not infer arbitrary date or numeric thresholds. Use the supported study filters or ask about a clock class, color, opening or opponent-strength group.'
    if not re.search(r'game|opening|play|rapid|blitz|bullet|classical|rating|opponent|win|loss|lose|draw|score|mistake|review|black|white|performance|best move|this position|this move',text):
        return 'refuse',{},'Ask about the imported games, openings, time classes, colors, opponents or completed engine reviews.'
    filters={}
    speeds=[s for s in ('ultrabullet','bullet','blitz','rapid','classical') if re.search(r'\b'+s+r'\b',text)]
    colors=[c for c in ('white','black') if re.search(r'\b'+c+r'\b',text)]
    compare=bool(re.search(r'compar|versus|\bvs\b|which.*(clock|color|time class)',text))
    if len(speeds)>1:
        if not compare:
            return 'refuse',{},'Choose one time class, or explicitly ask to compare time classes.'
        intent='speed'
    elif re.search(r'mistake|blunder|tactic|best move|this position|this move',text):
        intent='review'
    elif re.search(r'opening|underperform|struggl|weakness',text):
        intent='openings'
    elif re.search(r'review|which games|study next',text):
        intent='review'
    elif re.search(r'opponent|stronger|weaker',text):
        intent='opponents'
    elif compare and colors:
        intent='color'
    elif re.search(r'time class|clock|speed|blitz|rapid|bullet|classical',text):
        intent='speed'
    else:
        intent='overview'
    if len(speeds)==1:
        filters['speed']=speeds[0]
    if len(colors)==1:
        filters['color']=colors[0]
    if len(colors)>1 and not compare:
        return 'refuse',{},'Choose one color, or explicitly ask to compare both colors.'
    bands=[band for band in ('stronger','weaker','similar') if re.search(r'\b'+band+r'\b',text)]
    if len(bands)>1:
        if not compare:
            return 'refuse',{},'Choose one opponent-strength group, or explicitly ask to compare groups.'
        intent='opponents'
    elif bands:
        filters['opponent_band']=bands[0]
    return intent,filters,None


def model_route(question,fallback):
    """Optional local LLM proposes an intent only; it cannot write facts or SQL."""
    if os.environ.get('STUDY_ROUTER','template')!='ollama':
        return fallback,'template',{}
    host=os.environ.get('STUDY_LLM_URL','http://study-llm:11434').rstrip('/')
    model=os.environ.get('STUDY_LLM_MODEL','qwen2.5:0.5b')
    body=dict(model=model,stream=False,format=ROUTE_SCHEMA,
        prompt='Classify the chess question into ONE intent: overview (summary), openings (opening results), speed (clock comparison), color (White/Black comparison), opponents (opponent strengths), review (game review or best move). Return JSON with intent and filters. Use empty filters unless a single color, clock class or opponent band is explicit. Question: '+question,
        options={'temperature':0,'num_predict':64,'num_ctx':1024,'num_thread':2},keep_alive='2m')
    try:
        req=urllib.request.Request(host+'/api/generate',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=8) as response:
            value=json.loads(response.read(16384))
        prediction=json.loads(value['response'])
        intent=prediction.get('intent')
        inferred=prediction.get('filters',{})
        if intent not in INTENTS or set(prediction)-{'intent','filters'} or not isinstance(inferred,dict) or set(inferred)-{'color','speed','opponent_band'}:
            raise ValueError('invalid routing schema')
        # Recognized template semantics are an independent guard, not overwritten by a model.
        if intent!=fallback and fallback!='overview':
            return fallback,'ollama-disagreement-fallback',{}
        return intent,'ollama-validated',inferred
    except Exception:
        return fallback,'ollama-unavailable-fallback',{}


def answer(settings,identifier,question,filters=None,game_id=None,ply=None):
    if not isinstance(question,str) or not question.strip() or len(question)>500:
        raise ValueError('question must contain 1–500 characters')
    selected=reports.validate_filters(filters or {},settings)
    intent,explicit,refusal=template_route(question)
    if intent!='refuse':
        if intent=='speed' and len([s for s in reports.SPEEDS if re.search(r'\b'+s+r'\b',question,re.I)])>1:
            selected.pop('speed',None)
        if intent=='color' and re.search(r'white',question,re.I) and re.search(r'black',question,re.I):
            selected.pop('color',None)
        if intent=='opponents' and len([band for band in ('stronger','weaker','similar') if re.search(r'\b'+band+r'\b',question,re.I)])>1:
            selected.pop('opponent_band',None)
        selected.update(explicit)
        original_intent=intent
        intent,routing,inferred=model_route(question,intent)
        try:
            inferred=reports.validate_filters(inferred,settings)
        except ValueError:
            intent,routing,inferred=original_intent,'ollama-schema-fallback',{}
        if any(explicit.get(key)!=value for key,value in inferred.items()):
            intent,routing,inferred=original_intent,'ollama-filter-disagreement-fallback',{}
        # Model interpretation cannot silently override an existing UI filter.
        selected.update({key:value for key,value in inferred.items() if key not in selected})
    else:
        routing='guarded-refusal'
    r=reports.report(settings,identifier,selected)
    common=dict(version=r['version'],import_id=r['import_id'],dataset_sha256=r['dataset_sha256'],
        intent=intent,routing=routing,filters=selected,scope='Only this approved player-month and immutable personal version.')
    if refusal:
        return dict(common,answer=refusal,references=[],metrics={},supported=False)
    s=r['summary']
    if not s['games']:
        return dict(common,answer='No imported games match these filters. There is no evidence for a performance or tactical conclusion.',
            references=[],metrics=s,supported=False)
    references=[dict(game_id=g['game_id'],ply=0) for g in r['games'][:5]]
    if intent=='overview':
        text=f"Across {s['games']} matching games, your win rate was {100*s['win_rate']:.1f}% and your score was {100*s['score_rate']:.1f}% of available points: {s['wins']} wins, {s['draws']} draws and {s['losses']} losses. These are observed results, not probabilities for your next game."
        metrics=s
    elif intent=='openings':
        candidates=[o for o in r['openings'] if o['evidence_level']=='review candidate']
        frequency=bool(re.search(r'most common|most played|most frequent|popular',question,re.I))
        strongest=bool(re.search(r'best|strongest|highest|most wins',question,re.I))
        eligible=[o for o in r['openings'] if o['games']>=settings.minimum_opening_games]
        if frequency:
            o=max(r['openings'],key=lambda o:o['games'])
            text=f"Your most represented opening is {o['opening']}: {o['games']} matching games and {100*o['score_rate']:.1f}% score. Frequency does not establish move or opening quality."
        elif strongest and eligible:
            o=max(eligible,key=lambda o:(o['score_rate'],o['games']))
            text=f"Among openings meeting the {settings.minimum_opening_games}-game display threshold, {o['opening']} has the highest observed score: {100*o['score_rate']:.1f}% across {o['games']} games. Opponent differences and uncertainty prevent treating it as a guaranteed best choice."
        elif candidates and not strongest:
            o=candidates[0]
            text=f"Review {o['opening']}: {o['games']} matching games, {100*o['score_rate']:.1f}% score. In the matched subset, the other-opening reference scored {100*o['reference_score_rate']:.1f}%. Matched coverage is {100*o['matched_coverage']:.1f}%. This association does not prove that the opening caused losses."
        else:
            o=max(r['openings'],key=lambda o:o['games'])
            text=f"There is insufficient comparable evidence to label an opening as a weakness. Your most represented opening is {o['opening']} ({o['games']} games; {100*o['score_rate']:.1f}% score). Start by reviewing its example games."
        references=[dict(game_id=g,ply=0) for g in o['example_games']]
        metrics=o
    elif intent in ('speed','color','opponents'):
        groups=r[{'speed':'by_speed','color':'by_color','opponents':'by_opponent'}[intent]]
        text='; '.join(f"{g['label']}: {g['games']} games, {100*g['score_rate']:.1f}% score" for g in groups)+'. These are observed results; small samples and opponent differences limit comparisons.'
        metrics={'groups':groups}
    else:
        exact_position=bool(re.search(r'best move|this position|this move|\bhere\b',question,re.I))
        if exact_position and not game_id:
            return dict(common,answer='Select an imported game and replay position first. Tactical answers require engine evidence for that exact position.',
                references=[],metrics={},supported=False)
        engine_items=[item for item in r['review_candidates'] if item['evidence_type']=='bounded engine analysis']
        if game_id:
            if game_id not in {g['game_id'] for g in r['games']}:
                raise ValueError('selected replay game is outside the assistant filters')
            engine_items=[item for item in engine_items if item['game_id']==game_id]
            references=[dict(game_id=game_id,ply=0)]
        if game_id and exact_position:
            job=next((j for j in r['analysis_jobs'] if j['game_id']==game_id and j['status']=='complete'),None)
            evaluations=job['evidence'].get('positions',[]) if job else []
            if isinstance(ply,bool) or not isinstance(ply,int) or not 0<=ply<len(evaluations):
                return dict(common,answer='No completed engine evidence is stored for this exact replay position. Request an engine review and check its coverage.',
                    references=references,metrics={},supported=False)
            evaluation=evaluations[ply]
            line=evaluation['pv_san']
            text=(f"At replay ply {ply}, {job['evidence']['engine']} preferred {line[0]}; its line was {' '.join(line)}. This fixed-budget search is not a guarantee of an optimal move."
                if line else 'The engine supplied no continuation for this position; check whether the game has ended.')
            return dict(common,answer=text,references=[dict(game_id=game_id,ply=ply)],metrics=evaluation,supported=True)
        if engine_items:
            item=engine_items[0]
            text=f"In game {item['game_id']}, review the position before ply {item['ply']}. {item['summary']} This is a search-limited engine finding; compare the replay and principal variation before deciding what to practice."
            references=[dict(game_id=item['game_id'],ply=item['ply']-1)]
            metrics=item
        else:
            text='No completed engine evidence identifies a tactical review candidate in this selection. Request a bounded engine review for a linked game. A loss or uploaded comment alone cannot establish a mistake.'
            metrics={'engine_candidates':0,'games':s['games']}
    return dict(common,answer=text,references=references,metrics=metrics,supported=True)
