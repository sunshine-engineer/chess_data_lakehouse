"""SQL aggregates and small, actionable report sections over any-size libraries."""
from datetime import date,timedelta
import math

from ..reports import wilson
from . import storage as db

FILTER_COLUMNS={'color':'player_color','speed':'speed','opponent_band':'opponent_band','opening':'opening','result':'result'}
ORDER='sort_at DESC NULLS LAST,game_id'


def filters(value):
    if not isinstance(value,dict) or set(value)-set(FILTER_COLUMNS)-{'from_date','to_date','period','last_games','opponent'}:raise ValueError('Unsupported analysis filter')
    result={}
    for key,v in value.items():
        if not isinstance(v,str):raise ValueError('Analysis filters must be text')
        if not v:continue
        if key=='color' and v not in ('white','black'):raise ValueError('Choose White or Black')
        if key=='speed' and v not in ('ultrabullet','bullet','blitz','rapid','classical','unknown'):raise ValueError('Unknown clock class')
        if key=='opponent_band' and v not in ('weaker','similar','stronger','unknown'):raise ValueError('Unknown opponent group')
        if key=='opening' and len(v)>200:raise ValueError('Opening name is too long')
        if key=='result' and v not in ('win','draw','loss'):raise ValueError('Choose a win, draw or loss')
        if key=='opponent' and len(v)>50:raise ValueError('Opponent search is too long')
        if key=='last_games':
            if not v.isdigit() or not 1<=int(v)<=200:raise ValueError('Choose 1–200 recent games')
            v=str(int(v))
        if key in ('from_date','to_date'):v=date.fromisoformat(v).isoformat()
        if key=='period':
            if v not in ('7','30','90','all','custom'):raise ValueError('Unknown analysis period')
            if v in ('7','30','90'):
                result['from_date']=(date.today()-timedelta(days=int(v)-1)).isoformat();result['to_date']=date.today().isoformat()
            continue
        result[key]=v
    if result.get('from_date','0000')>result.get('to_date','9999'):raise ValueError('The start date must precede the end date')
    return result


def where(owner,snap,selected,alias='f'):
    conditions=[alias+'.owner_id=%s',alias+'.snapshot_id=%s'];params=[owner,snap['id']]
    for key,v in selected.items():
        if key in FILTER_COLUMNS:conditions.append(alias+'.'+FILTER_COLUMNS[key]+'=%s')
        elif key=='from_date':conditions.append(alias+'.game_date>=%s')
        elif key=='to_date':conditions.append(alias+'.game_date<=%s')
        elif key=='opponent':conditions.append('position(lower(%s) in lower('+alias+'.opponent))>0')
        elif key=='_game_ids':conditions.append(alias+'.game_id=ANY(%s)')
        else:continue
        params.append(v)
    if 'last_games' in selected:
        base,values=where(owner,snap,{k:v for k,v in selected.items() if k!='last_games'},'recent')
        conditions.append(alias+'.game_id IN (SELECT recent.game_id FROM study.workspace_game_details recent WHERE '+base+' ORDER BY recent.sort_at DESC NULLS LAST,recent.game_id LIMIT %s)')
        params+=values+[int(selected['last_games'])]
    return ' AND '.join(conditions),params


def cohort(c,owner,snap,selected,scope_ids=None):
    """Resolve once so summaries, references and comparisons share exact games."""
    if scope_ids is not None:return {k:v for k,v in selected.items() if k!='last_games'}|{'_game_ids':scope_ids}
    if 'last_games' not in selected:return dict(selected)
    clause,params=where(owner,snap,selected)
    ids=[r['game_id'] for r in c.execute('SELECT game_id FROM study.workspace_game_details f WHERE '+clause+' ORDER BY '+ORDER,params)]
    return {k:v for k,v in selected.items() if k!='last_games'}|{'_game_ids':ids}


SUMMARY="""count(*) AS games,count(*) FILTER(WHERE result='win') AS wins,
 count(*) FILTER(WHERE result='draw') AS draws,count(*) FILTER(WHERE result='loss') AS losses,
 avg(points)::float8 AS score_rate,avg((result='win')::int)::float8 AS win_rate,
 avg(opponent_rating)::float8 AS average_opponent_rating,
 count(*) FILTER(WHERE player_rating IS NULL OR opponent_rating IS NULL) AS missing_rating_games,
 count(*) FILTER(WHERE game_date IS NULL) AS missing_date_games,
 count(*) FILTER(WHERE started_at IS NULL) AS missing_time_games,min(game_date) AS first_date,max(game_date) AS last_date"""


def summarize(c,clause,params):
    row=dict(c.execute('SELECT '+SUMMARY+' FROM study.workspace_game_details f WHERE '+clause,params).fetchone())
    row['win_rate_interval_95']=wilson(row['wins'],row['games'])
    return row


def groups(c,clause,params,column):
    rows=c.execute('SELECT '+column+' AS label,'+SUMMARY+' FROM study.workspace_game_details f WHERE '+clause+' GROUP BY '+column+' ORDER BY count(*) DESC,'+column,params).fetchall()
    return [dict(r,win_rate_interval_95=wilson(r['wins'],r['games'])) for r in rows]


def examples(c,clause,params,extra=None,values=None,limit=3):
    return [dict(r) for r in c.execute('''SELECT game_id,game_date,opponent,result,opening,player_color,speed,ply_count
        FROM study.workspace_game_details f WHERE '''+clause+(' AND '+extra if extra else '')+
        ' ORDER BY (result=\'loss\') DESC,game_date DESC NULLS LAST,game_id LIMIT %s',params+(values or [])+[limit]).fetchall()]


def opening_report(c,owner,snap,selected,minimum):
    clause,params=where(owner,snap,selected)
    base,base_params=where(owner,snap,{k:v for k,v in selected.items() if k!='opening'})
    rows=c.execute('SELECT opening,max(eco) AS eco,'+SUMMARY+' FROM study.workspace_game_details f WHERE '+clause+
        ' GROUP BY opening ORDER BY count(*) DESC,opening LIMIT 5',params).fetchall()
    results=[]
    for row in rows:
        name=row['opening']
        matched=c.execute('''WITH a AS (
            SELECT player_color,speed,opponent_band,count(*) AS n,sum(points) AS points FROM study.workspace_game_details f
            WHERE '''+clause+''' AND opening=%s AND opponent_band<>'unknown' GROUP BY player_color,speed,opponent_band
        ), b AS (
            SELECT player_color,speed,opponent_band,count(*) AS n,sum(points) AS points FROM study.workspace_game_details f
            WHERE '''+base+''' AND opening<>%s GROUP BY player_color,speed,opponent_band
        ) SELECT coalesce(sum(a.n),0)::bigint AS n,sum(a.points)::float8 AS observed,
            sum(a.n*b.points/b.n)::float8 AS reference FROM a JOIN b USING(player_color,speed,opponent_band) WHERE b.n>=3''',
            params+[name]+base_params+[name]).fetchone()
        coverage=matched['n']/row['games']
        actual=matched['observed']/matched['n'] if matched['n'] else None
        ref=matched['reference']/matched['n'] if matched['n'] else None
        adequate=row['games']>=minimum and matched['n']>=3 and coverage>=.5
        candidate=adequate and actual-ref<=-.15
        results.append(dict(row,win_rate_interval_95=wilson(row['wins'],row['games']),
            matched_games=matched['n'],matched_coverage=coverage,matched_score_rate=actual,reference_score_rate=ref,
            score_difference=actual-ref if actual is not None else None,
            review_candidate=candidate,evidence_level='review candidate' if candidate else 'descriptive',
            explanation='Lower score than your other openings against similar-strength opponents in the same color and clock class.' if candidate else
                'A useful set of games to inspect; there is not enough matched evidence to call this an opening weakness.',
            games_to_review=examples(c,clause,params,'opening=%s',[name])))
    return sorted(results,key=lambda r:(not r['review_candidate'],-r['games']))


def report(owner,library_id,snapshot_id=None,selected=None,config=None,scope_ids=None):
    selected=filters(selected or {})
    with db.connect() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        snap=db.snapshot(c,owner,library_id,snapshot_id);public_filters=dict(selected);selected=cohort(c,owner,snap,selected,scope_ids);clause,params=where(owner,snap,selected)
        summary=summarize(c,clause,params)
        openings=opening_report(c,owner,snap,selected,config.minimum_opening_games if config else 5)
        by_speed=groups(c,clause,params,'speed');by_color=groups(c,clause,params,'player_color');by_opponent=groups(c,clause,params,'opponent_band')
        recent=examples(c,clause,params,limit=5)
        exact=[dict(r) for r in c.execute('SELECT game_id,game_date,started_at,opponent,player_color,result,speed,opening,ply_count FROM study.workspace_game_details f WHERE '+clause+' ORDER BY '+ORDER+' LIMIT 200',params)] if 'last_games' in public_filters or scope_ids is not None else []
        engine=c.execute('''SELECT a.id,a.evidence,f.game_id FROM study.workspace_analysis a
            JOIN study.workspace_game_details f ON f.revision_id=a.revision_id AND f.owner_id=a.owner_id
            WHERE '''+clause+" AND a.status='complete' AND a.player=%s ORDER BY a.finished_at DESC LIMIT 5",params+[snap['player']]).fetchall()
        progress=dict(c.execute('''SELECT count(*) FILTER(WHERE v.status='reviewed') AS reviewed,
            count(*) FILTER(WHERE v.status='practiced') AS practiced,count(*) FILTER(WHERE v.status='queued') AS queued
            FROM study.workspace_reviews v JOIN study.workspace_game_details f ON f.revision_id=v.revision_id AND f.owner_id=v.owner_id WHERE '''+clause,params).fetchone())
        findings=[]
        for item in engine:
            for candidate in item['evidence'].get('candidates',[])[:1]:
                findings.append({'kind':'engine','title':candidate.get('motif','Review this move'),
                    'detail':candidate.get('explanation',candidate['summary']),'action':candidate['practice'],
                    'evidence':'Stockfish search; '+str(item['evidence']['budget']['nodes'])+' nodes per position',
                    'games':[{'game_id':item['game_id'],'ply':candidate['ply']-1}]})
        if openings:
            item=openings[0]
            if item['opening']=='Unclassified opening':
                findings.append({'kind':'opening','title':'Start with the first unfamiliar position',
                    'detail':str(item['games'])+' games have no opening label. There is not enough metadata to name an opening weakness.',
                    'action':'Replay a linked game and note where you first felt uncertain. Lichess imports request opening labels automatically.',
                    'evidence':'Missing source opening labels; move replay remains available','games':item['games_to_review']})
            else:findings.append({'kind':'opening','title':('Review ' if item['review_candidate'] else 'Build a study around ')+item['opening'],
                'detail':str(item['games'])+' games, '+format(item['score_rate']*100,'.1f')+'% score. '+item['explanation'],
                'action':'Replay the linked losses. Write down the first unfamiliar position, then compare the engine alternative in one game.',
                'evidence':str(item['games'])+' games; matched coverage '+format(item['matched_coverage']*100,'.0f')+'%; descriptive comparison',
                'games':item['games_to_review']})
        eligible=[g for g in by_speed if g['games']>=5 and g['label']!='unknown']
        if eligible:
            group=min(eligible,key=lambda g:g['score_rate'])
            findings.append({'kind':'clock','title':'Review your '+group['label']+' decisions',
                'detail':str(group['games'])+' games with '+format(group['score_rate']*100,'.1f')+'% score. Clock differences alone do not explain a loss.',
                'action':'Choose one loss and inspect its critical move and remaining clock. Separate a missed tactic from a time-management problem.',
                'evidence':'Observed results in this period; ratings and opponent selection may differ',
                'games':examples(c,clause,params,'speed=%s',[group['label']])})
        return {'library_id':str(snap['library_id']),'snapshot_id':str(snap['id']),'version':snap['version'],'player':snap['player'],
            'dataset_sha256':snap['dataset_sha256'],'filters':public_filters,'summary':summary,'findings':findings[:3],
            'openings':openings,'by_speed':by_speed,'by_color':by_color,'by_opponent':by_opponent,
            'suggested_games':exact or recent,'cohort_games':exact,'progress':progress,
            'interpretation':'Score counts a win as 1 and a draw as ½. Findings describe this collection; they do not prove causes or future improvement.'}


def games(owner,library_id,snapshot_id,selected,offset=0,limit=10,order='newest'):
    if isinstance(offset,bool) or not isinstance(offset,int) or offset<0:raise ValueError('Invalid page offset')
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=50:raise ValueError('Page size must be 1–50')
    if order not in ('newest','oldest'):raise ValueError('Choose newest or oldest first')
    selected=filters(selected or {})
    with db.connect() as c:
        snap=db.snapshot(c,owner,library_id,snapshot_id);clause,params=where(owner,snap,selected)
        rows=c.execute('''SELECT game_id,revision_id,game_date,started_at,time_source,opponent,player_color,player_rating,opponent_rating,
            opponent_band,result,points,speed,opening,ply_count,
            (SELECT a.status FROM study.workspace_analysis a WHERE a.owner_id=f.owner_id AND a.revision_id=f.revision_id AND a.player=%s ORDER BY a.created_at DESC LIMIT 1) AS engine_status,
            (SELECT v.status FROM study.workspace_reviews v WHERE v.owner_id=f.owner_id AND v.revision_id=f.revision_id ORDER BY v.updated_at DESC LIMIT 1) AS practice_status
            FROM study.workspace_game_details f WHERE '''+clause+
            ' ORDER BY sort_at '+('DESC' if order=='newest' else 'ASC')+' NULLS LAST,game_id OFFSET %s LIMIT %s',[snap['player']]+params+[offset,limit+1]).fetchall()
        total=summarize(c,clause,params)
        return {'snapshot_id':str(snap['id']),'rows':[dict(row) for row in rows[:limit]],'has_more':len(rows)>limit,'offset':offset,'total':total['games'],'missing_time_games':total['missing_time_games'],'filters':selected}
