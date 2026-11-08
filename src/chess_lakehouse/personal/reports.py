"""Bounded personal analytics with explicit sample limits and game evidence."""
from collections import defaultdict
from datetime import date
import math

from . import repository as db

ALLOWED={'color','speed','opponent_band','opening','from_date','to_date'}
SPEEDS={'ultrabullet','bullet','blitz','rapid','classical','unknown'}


def validate_filters(value,settings):
    """Validate supported legacy study filters before selecting private games."""
    if not isinstance(value,dict) or set(value)-ALLOWED:
        raise ValueError('unsupported personal-study filter')
    result={}
    for name,v in value.items():
        if not isinstance(v,str):
            raise ValueError('personal filters must be text values')
        if not v:
            continue
        if name=='color' and v not in ('white','black'):
            raise ValueError('color must be white or black')
        if name=='speed' and v not in SPEEDS:
            raise ValueError('unsupported time class')
        if name=='opponent_band' and v not in ('weaker','similar','stronger','unknown'):
            raise ValueError('unsupported opponent-strength group')
        if name=='opening' and len(v)>200:
            raise ValueError('opening filter is too long')
        if name in ('from_date','to_date'):
            d=date.fromisoformat(v)
            if d.isoformat()[:7]!=settings.month:
                raise ValueError('date filters must stay within the approved personal month')
        result[name]=v
    if result.get('from_date','0000')>result.get('to_date','9999'):
        raise ValueError('start date exceeds end date')
    return result


def select_games(conn,import_id,filters):
    """Select legacy private payloads using the validated analytical filters."""
    columns={'color':'player_color','speed':'speed','opponent_band':'opponent_band','opening':'opening'}
    clauses=['g.import_id=%s']
    params=[import_id]
    for key,value in filters.items():
        if key in columns:
            clauses.append('g.'+columns[key]+'=%s')
        elif key=='from_date':
            clauses.append('g.game_date>=%s')
        else:
            clauses.append('g.game_date<=%s')
        params.append(value)
    return conn.execute('SELECT g.* FROM study.games g WHERE '+' AND '.join(clauses)+' ORDER BY g.game_date,g.game_id',params).fetchall()


def wilson(wins,n):
    """Compute a Wilson interval for an observed success count and sample size."""
    if not n:
        return None
    z=1.959963984540054
    p=wins/n
    center=(p+z*z/(2*n))/(1+z*z/n)
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return [max(0,center-half),min(1,center+half)]


def summary(rows):
    """Summarize the selected private games using results and available metadata."""
    n=len(rows)
    wins=sum(r['result']=='win' for r in rows)
    draws=sum(r['result']=='draw' for r in rows)
    ratings=[r['opponent_rating'] for r in rows if r['opponent_rating'] is not None]
    return dict(games=n,wins=wins,draws=draws,losses=n-wins-draws,
        score_rate=sum(r['points'] for r in rows)/n if n else None,
        win_rate=wins/n if n else None,win_rate_interval_95=wilson(wins,n),
        average_opponent_rating=sum(ratings)/len(ratings) if ratings else None,
        missing_rating_games=sum(r['player_rating'] is None or r['opponent_rating'] is None for r in rows))


def grouped(rows,key):
    """Compute result statistics for the requested grouping of private games."""
    groups=defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    return [dict(label=name,**summary(group)) for name,group in sorted(groups.items())]


def build(rows,reference_rows,settings,version):
    """Assemble a deterministic legacy study report and its evidence references."""
    openings=defaultdict(list)
    for row in rows:
        openings[row['opening']].append(row)
    opening_results=[]
    for name,group in openings.items():
        matched,reference_points=[],[]
        reference_ids=set()
        for row in group:
            if row['opponent_band']=='unknown':
                continue
            pool=[r for r in reference_rows if r['opening']!=name and
                (r['player_color'],r['speed'],r['opponent_band'])==
                (row['player_color'],row['speed'],row['opponent_band'])]
            if len(pool)>=3:
                matched.append(row['points'])
                reference_points.append(sum(r['points'] for r in pool)/len(pool))
                reference_ids.update(r['game_id'] for r in pool)
        coverage=len(matched)/len(group)
        reference=sum(reference_points)/len(reference_points) if reference_points else None
        observed=sum(matched)/len(matched) if matched else None
        adequate=len(group)>=settings.minimum_opening_games and coverage>=.5 and len(matched)>=3
        opening_results.append(dict(opening=name,eco=next((r['eco'] for r in group if r['eco']),None),
            **summary(group),matched_games=len(matched),matched_coverage=coverage,
            other_reference_games=len(reference_ids),matched_score_rate=observed,
            reference_score_rate=reference,score_difference=observed-reference if observed is not None else None,
            evidence_level='review candidate' if adequate and observed-reference<=-.15 else 'descriptive',
            sample_note='Comparison is descriptive, not causal.' if adequate else 'Too little comparable evidence for an underperformance claim.',
            example_games=[r['game_id'] for r in sorted(group,key=lambda r:(r['points'],str(r['game_date']),r['game_id']))[:5]]))
    opening_results.sort(key=lambda r:(r['evidence_level']!='review candidate',r['score_difference'] if r['score_difference'] is not None else 1,-r['games'],r['opening']))
    candidates=[]
    for row in sorted(rows,key=lambda r:(r['result']!='loss',r['opponent_band'] not in ('similar','weaker'),-r['ply_count'],r['game_id']))[:10]:
        candidates.append(dict(game_id=row['game_id'],opening=row['opening'],result=row['result'],
            reason='Review this loss in its move context; the result alone does not identify a mistake.' if row['result']=='loss' else
                'Review this game as a comparison example; no mistake is inferred from its result.',
            evidence_type='game outcome',ply=None))
    return dict(import_id=str(version['id']),version=version['version'],dataset_sha256=version['dataset_sha256'],
        player=settings.player,month=settings.month,summary=summary(rows),openings=opening_results,
        by_color=grouped(rows,'player_color'),by_speed=grouped(rows,'speed'),
        by_opponent=grouped(rows,'opponent_band'),review_candidates=candidates,
        games=[{k:r[k] for k in ('game_id','game_date','player_color','player_rating','opponent_rating','opponent_band','result','points','speed','eco','opening','ply_count')} for r in rows],
        interpretation=dict(score='Win=1, draw=0.5, loss=0; score is not win probability.',
            interval='95% Wilson interval for the win event; not an interval for score or expected improvement.',
            reference='Other openings in this personal version, matched by color, clock class and opponent rating difference (±200). At least three reference games per matched stratum.',
            scope='One player-month; opening associations do not prove that an opening caused losses.'))


def report(settings,identifier=None,filters=None):
    """Read a legacy publication and compute its filtered study report."""
    filters=validate_filters(filters or {},settings)
    with db.connect() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        v=db.version(c,settings,identifier)
        rows=select_games(c,v['id'],filters)
        # Opening filtering should not remove the other-opening comparison pool.
        reference=select_games(c,v['id'],{k:value for k,value in filters.items() if k!='opening'})
        result=build(rows,reference,settings,v)
        result['filters']=filters
        provenance=v['manifest'].get('metadata_defaults',{})
        result['metadata_defaults']={key:value for key,value in provenance.items() if key in {row['game_id'] for row in rows}}
        for row in result['games']:
            row['metadata_defaults']=provenance.get(row['game_id'],[])
        result['review_progress']=c.execute('SELECT game_id,ply,status,note FROM study.review_items WHERE import_id=%s ORDER BY updated_at DESC',(v['id'],)).fetchall()
        result['analysis_jobs']=c.execute('SELECT id,game_id,status,evidence,error FROM study.analysis_jobs WHERE import_id=%s ORDER BY created_at DESC',(v['id'],)).fetchall()
        selected={r['game_id'] for r in rows}
        engine_candidates=[]
        for job in result['analysis_jobs']:
            if job['status']=='complete' and job['game_id'] in selected:
                for candidate in job['evidence'].get('candidates',[])[:3]:
                    engine_candidates.append(dict(candidate,game_id=job['game_id'],analysis_id=str(job['id']),
                        evidence_type='bounded engine analysis',reason=candidate['summary']))
        result['review_candidates']=sorted(engine_candidates,key=lambda r:-r['loss_cp'])[:10]+result['review_candidates']
        return result
