"""Parameterized, timeout-bounded reader queries. No arbitrary SQL interface."""
from contextlib import contextmanager
from datetime import date,datetime,timezone
import base64
import hashlib
import json
import re

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

ENGINES = ('python', 'python_mp', 'spark')
SPEEDS = ('ultrabullet', 'bullet', 'blitz', 'rapid', 'classical', 'unknown')

class PublicationChanged(ValueError):pass


@contextmanager
def connect():
    with psycopg.connect('', row_factory=dict_row, connect_timeout=5,
            options='-c default_transaction_read_only=on -c statement_timeout=8000 -c idle_in_transaction_session_timeout=10000') as conn:
        yield conn


def integer(value, name, low, high):
    try:
        if not re.fullmatch(r'\d+', str(value)):
            raise ValueError()
        number = int(value)
    except (ValueError, TypeError):
        raise ValueError(f'{name} must be an integer') from None
    if not low <= number <= high:
        raise ValueError(f'{name} must be between {low} and {high}')
    return number


def filters(args):
    engine = args.get('engine', 'python')
    if engine not in ENGINES:
        raise ValueError('unsupported engine')
    month = args.get('month', '2013-01')
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
        raise ValueError('month must be YYYY-MM')
    source_month = date.fromisoformat(month+'-01')
    speed = args.get('speed', '')
    if speed and speed not in SPEEDS:
        raise ValueError('unsupported speed')
    low = integer(args.get('min_rating', 0), 'minimum rating', 0, 4000)
    high = integer(args.get('max_rating', 4000), 'maximum rating', 0, 4000)
    if low > high:
        raise ValueError('minimum rating exceeds maximum')
    clauses = ['f.source_month=%s']
    params = [source_month]
    if speed:
        clauses.append('f.speed=%s'); params.append(speed)
    # Default range includes games without ratings; explicit limits exclude them.
    if low or high != 4000:
        clauses.append('f.average_rating BETWEEN %s AND %s'); params.extend((low,high))
    result=args.get('result','')
    if result:
        if result not in ('1-0','0-1','1/2-1/2'):raise ValueError('Choose White win, Black win or Draw')
        clauses.append('f.result=%s');params.append(result)
    eco=args.get('eco','')
    if eco:
        if not re.fullmatch(r'[A-E][0-9]{2}',eco):raise ValueError('Opening code must be A00–E99')
        clauses.append('f.opening_sk IN (SELECT opening_sk FROM '+ 'warehouse_'+engine+'.dim_opening WHERE eco=%s)');params.append(eco)
    start,end=args.get('from_date',''),args.get('to_date','')
    for key,value,operator in (('from_date',start,'>='),('to_date',end,'<=')):
        if value:
            try:parsed=date.fromisoformat(value)
            except (TypeError,ValueError):raise ValueError('Game dates must use YYYY-MM-DD') from None
            clauses.append('f.date_key'+operator+'%s');params.append(parsed)
    if start and end and start>end:raise ValueError('The start date must precede the end date')
    identity=args.get('game_id','')
    if identity:
        game_id(identity);clauses.append('f.game_id=%s');params.append(identity)
    return engine, sql.SQL(' AND '.join(clauses)), params


def query(conn, engine, select, where, params, suffix=''):
    return conn.execute(sql.SQL(select+' FROM {}.fact_game f '+suffix+' WHERE {}').format(
        sql.Identifier('warehouse_'+engine), where), params).fetchall()


def overview(args):
    engine,where,params = filters(args)
    schema=sql.Identifier('warehouse_'+engine)
    with connect() as conn:
        # One snapshot keeps all panels consistent with atomic source publication.
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        total=query(conn,engine,'''SELECT count(*) AS games, min(date_key)::text AS first_date,
            max(date_key)::text AS last_date,count(DISTINCT date_key) AS days,
            round(avg(average_rating)::numeric,1)::float AS average_rating,
            round(avg(move_count)::numeric,1)::float AS average_moves,
            count(*) FILTER(WHERE white_rating IS NULL OR black_rating IS NULL) AS missing_ratings''',where,params)[0]
        def grouped(column):
            return conn.execute(sql.SQL('SELECT '+column+' AS label,count(*) AS count FROM {}.fact_game f WHERE {} GROUP BY 1 ORDER BY 1').format(schema,where),params).fetchall()
        unknown=conn.execute(sql.SQL('''SELECT count(*) AS games FROM {}.fact_game f
            JOIN {}.dim_player w ON w.player_sk=f.white_player_sk
            JOIN {}.dim_player b ON b.player_sk=f.black_player_sk
            WHERE {} AND (w.is_unknown OR b.is_unknown)''').format(schema,schema,schema,where),params).fetchone()['games']
        openings=conn.execute(sql.SQL('''SELECT coalesce(o.eco,'Unknown') AS eco,
            coalesce(o.family,'Unknown opening') AS label,count(*) AS count
            FROM {}.fact_game f JOIN {}.dim_opening o ON o.opening_sk=f.opening_sk
            WHERE {} GROUP BY 1,2 ORDER BY count(*) DESC,1,2 LIMIT 10''').format(schema,schema,where),params).fetchall()
        bands=grouped("coalesce((floor(average_rating/200)*200)::int::text,'Unknown')")
        rejects=conn.execute(sql.SQL('SELECT count(*) AS n FROM {}.rejected_game WHERE source_month=%s').format(schema),(params[0],)).fetchone()['n']
        months=conn.execute(sql.SQL("SELECT DISTINCT to_char(source_month,'YYYY-MM') AS month FROM {}.fact_game ORDER BY 1").format(schema)).fetchall()
        return dict(summary=total,unknown_player_games=unknown,archive_rejects=rejects,speeds=grouped('speed'),
            results=grouped('result'),daily=grouped('date_key::text'),rating_bands=bands,openings=openings,
            months=[r['month'] for r in months],engine=engine)


def game_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9]{8}',value):raise ValueError('Enter the eight-character Lichess game ID')
    return value


def catalog():
    """Discover actual publications without granting access to private study tables."""
    publications=[]
    with connect() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        for engine in ENGINES:
            rows=c.execute(sql.SQL('''SELECT to_char(source_month,'YYYY-MM') AS month,count(*) AS games,
                min(date_key)::text AS first_date,max(date_key)::text AS last_date,max(ingested_at) AS published_at,
                count(DISTINCT run_id) AS publication_runs FROM {}.fact_game GROUP BY source_month ORDER BY source_month''').format(sql.Identifier('warehouse_'+engine))).fetchall()
            publications.extend(dict(row,engine=engine) for row in rows)
    return {'publications':publications,'checked_at':datetime.now(timezone.utc).isoformat(),
        'source':'Lichess public archive','private_study_included':False}


def games(args):
    engine,where,params=filters(args)
    limit=integer(args.get('limit',25),'limit',1,100)
    offset=integer(args.get('offset',0),'offset',0,10000)
    sort=args.get('sort','id')
    if sort not in ('id','newest','oldest'):raise ValueError('Choose a supported game order')
    after=args.get('cursor','');fingerprint=hashlib.sha256(json.dumps([engine,where.as_string(),params,sort],default=str).encode()).hexdigest()
    suffix='';extra=[]
    if after:
        try:
            if len(after)>1024:raise ValueError()
            cursor=json.loads(base64.b64decode(after.encode(),altchars=b'-_',validate=True))
            if set(cursor)!={'id','date','filters'} or cursor['filters']!=fingerprint:raise ValueError()
            game_id(cursor['id']);date.fromisoformat(cursor['date'])
        except (ValueError,TypeError,KeyError,UnicodeError):raise ValueError('This page link does not match the selected filters. Start from the first page.') from None
        if offset:raise ValueError('Cursor and offset cannot be combined')
        if sort=='id':suffix=' AND f.game_id>%s';extra=[cursor['id']]
        else:
            suffix=" AND (coalesce(f.date_key,DATE '0001-01-01'),f.game_id)"+('<' if sort=='newest' else '>')+'(%s,%s)'
            extra=[cursor['date'],cursor['id']]
    order='f.game_id' if sort=='id' else "coalesce(f.date_key,DATE '0001-01-01') "+('DESC' if sort=='newest' else 'ASC')+',f.game_id '+('DESC' if sort=='newest' else 'ASC')
    with connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        total=query(conn,engine,'SELECT count(*) AS n',where,params)[0]['n']
        rows=conn.execute(sql.SQL('''SELECT game_id,date_key::text AS date,white_rating,black_rating,
            result,move_count,speed,white_title_as_played,black_title_as_played
            FROM {}.fact_game f WHERE {}'''+suffix+' ORDER BY '+order+' LIMIT %s OFFSET %s').format(
            sql.Identifier('warehouse_'+engine),where),params+extra+[limit+1,offset]).fetchall()
        shown=rows[:limit];next_cursor=None
        if len(rows)>limit and shown:
            last=shown[-1];next_cursor=base64.urlsafe_b64encode(json.dumps({'id':last['game_id'],'date':last['date'] or '0001-01-01','filters':fingerprint}).encode()).decode()
        return {'rows':shown,'has_more':len(rows)>limit,'next_cursor':next_cursor,'total':total,'offset':offset,'limit':limit}


DETAIL_SELECT='''SELECT f.game_id,f.date_key::text AS date,f.source_month::text AS source_month,
 f.white_rating,f.black_rating,f.average_rating,f.rating_difference,f.result,f.move_count,f.ply_count,f.speed,
 f.termination,f.white_title_as_played,f.black_title_as_played,f.run_id,f.ingested_at,
 t.initial_seconds,t.increment_seconds,t.control_code,o.eco,o.family AS opening
 FROM {}.fact_game f JOIN {}.dim_time_control t ON t.time_control_sk=f.time_control_sk
 JOIN {}.dim_opening o ON o.opening_sk=f.opening_sk WHERE {}'''


def detail(identity,args):
    engine,where,params=filters({'engine':args.get('engine','python'),'month':args.get('month','2013-01'),'game_id':game_id(identity)})
    schema=sql.Identifier('warehouse_'+engine)
    with connect() as c:
        row=c.execute(sql.SQL(DETAIL_SELECT).format(schema,schema,schema,where),params).fetchone()
        return dict(row,engine=engine,source_url='https://lichess.org/'+identity) if row else None


def comparable(row,engine,month,expected=None):
    """Descriptive archive observations; never confused with the training sample."""
    if engine not in ENGINES:raise ValueError('unsupported engine')
    with connect() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        if expected is not None and source_signature(c,engine,month)!=expected:
            raise PublicationChanged('The archive changed while preparing this estimate. Refresh before trying again.')
        result=c.execute(sql.SQL('''SELECT count(*) AS games,
            avg((result='1-0')::int)::float8 AS white_win_rate,avg((result='0-1')::int)::float8 AS black_win_rate,
            avg((result='1/2-1/2')::int)::float8 AS draw_rate,avg(move_count)::float8 AS average_moves
            FROM {}.fact_game WHERE source_month=%s AND speed=%s AND white_rating BETWEEN %s AND %s
            AND black_rating BETWEEN %s AND %s''').format(sql.Identifier('warehouse_'+engine)),
            (month+'-01',row['speed'],row['white_rating']-200,row['white_rating']+200,row['black_rating']-200,row['black_rating']+200)).fetchone()
    return dict(result,rule='Same time class and both ratings within 200 points. Full archive observations, not a model confidence interval.')


def source_signature(conn, engine, month):
    if engine not in ENGINES:
        raise ValueError('unsupported engine')
    # Reader does not require broad control-schema privileges.
    return conn.execute(sql.SQL('''SELECT count(*) AS games,min(date_key)::text AS first_date,
        max(date_key)::text AS last_date,md5(coalesce(string_agg(DISTINCT run_id,',' ORDER BY run_id),'')) AS publication
        FROM {}.fact_game WHERE source_month=%s''').format(sql.Identifier('warehouse_'+engine)),(month+'-01',)).fetchone()
