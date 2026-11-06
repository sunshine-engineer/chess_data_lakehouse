"""Parameterized, timeout-bounded reader queries. No arbitrary SQL interface."""
from contextlib import contextmanager
from datetime import date
import re

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

ENGINES = ('python', 'python_mp', 'spark')
SPEEDS = ('ultrabullet', 'bullet', 'blitz', 'rapid', 'classical', 'unknown')


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


def games(args):
    engine,where,params=filters(args)
    limit=integer(args.get('limit',25),'limit',1,100)
    offset=integer(args.get('offset',0),'offset',0,10000)
    with connect() as conn:
        rows=conn.execute(sql.SQL('''SELECT game_id,date_key::text AS date,white_rating,black_rating,
            result,move_count,speed,white_title_as_played,black_title_as_played
            FROM {}.fact_game f WHERE {} ORDER BY game_id LIMIT %s OFFSET %s''').format(
            sql.Identifier('warehouse_'+engine),where),params+[limit+1,offset]).fetchall()
        return {'rows':rows[:limit],'has_more':len(rows)>limit and offset+limit<=10000,'offset':offset,'limit':limit}


def source_signature(conn, engine, month):
    if engine not in ENGINES:
        raise ValueError('unsupported engine')
    # Reader does not require broad control-schema privileges.
    return conn.execute(sql.SQL('''SELECT count(*) AS games,min(date_key)::text AS first_date,
        max(date_key)::text AS last_date,md5(coalesce(string_agg(DISTINCT run_id,',' ORDER BY run_id),'')) AS publication
        FROM {}.fact_game WHERE source_month=%s''').format(sql.Identifier('warehouse_'+engine)),(month+'-01',)).fetchone()
