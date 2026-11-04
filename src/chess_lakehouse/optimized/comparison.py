"""Group-scoped benchmark evidence and natural-key equality across historical models."""
from __future__ import annotations

import json
from pathlib import Path
from statistics import median

from psycopg import sql

from .settings import ENGINES
from .storage import atomic_json
from .warehouse import connect, schema_for
from .workflow import event

QUERIES = {
    "fact_game": "SELECT * FROM {s}.semantic_fact",
    "dim_date": "SELECT d.* FROM {s}.dim_date d WHERE EXISTS(SELECT 1 FROM {s}.fact_game f WHERE f.date_key=d.date_key)",
    "dim_player": """SELECT encode(d.player_hash,'hex'),d.is_unknown,d.latest_title,d.last_observed_date
        FROM {s}.dim_player d WHERE EXISTS(SELECT 1 FROM {s}.fact_game f WHERE f.white_player_sk=d.player_sk OR f.black_player_sk=d.player_sk)""",
    "dim_opening": """SELECT encode(d.opening_hash,'hex'),d.eco,d.family,d.variation
        FROM {s}.dim_opening d WHERE EXISTS(SELECT 1 FROM {s}.fact_game f WHERE f.opening_sk=d.opening_sk)""",
    "dim_time_control": """SELECT d.control_code,d.initial_seconds,d.increment_seconds,d.speed
        FROM {s}.dim_time_control d WHERE EXISTS(SELECT 1 FROM {s}.fact_game f WHERE f.time_control_sk=d.time_control_sk)""",
    "rejected_game": "SELECT source_month,record_number,game_id,reason FROM {s}.rejected_game",
}


def compare(group, reports=Path('/reports'), required_engines=ENGINES):
    with connect() as conn:
        for engine in sorted(required_engines):
            if not conn.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", ('chess-v2-'+engine,)).fetchone()[0]:
                raise ValueError('comparison cannot race an active publication')
        rows = conn.execute("""SELECT engine,report FROM control.pipeline_runs
            WHERE benchmark_group=%s AND status='complete' AND report->>'resumed'='false'
            ORDER BY finished_at""", (group,)).fetchall()
        groups = {engine:[report for name,report in rows if name==engine] for engine in required_engines}
        if any(not values for values in groups.values()):
            raise ValueError('every required engine needs a fresh successful sample in this benchmark group')
        signatures = {(r['source_sha256'],r['config_hash'],r['code_hash'],r['image_id'],
                       r['cpu_limit'],r['memory_limit_mib'],r['runtime']['architecture'],r['runtime']['kernel'])
                      for values in groups.values() for r in values}
        if len(signatures) != 1:
            raise ValueError('group mixes source, code, image, configuration or resource limits')
        histories = []
        for engine in required_engines:
            histories.append(conn.execute('SELECT source_month,source_sha256 FROM control.month_publications WHERE engine=%s ORDER BY source_month', (engine,)).fetchall())
        if any(history != histories[0] for history in histories):
            raise ValueError('engines have different published archive histories')
        equality = {}
        reference = required_engines[0]
        for engine in required_engines[1:]:
            for table, template in QUERIES.items():
                left = sql.SQL(template).format(s=sql.Identifier(schema_for(reference)))
                right = sql.SQL(template).format(s=sql.Identifier(schema_for(engine)))
                for a,b in ((left,right),(right,left)):
                    if conn.execute(sql.SQL('SELECT EXISTS(({}) EXCEPT ALL ({}))').format(a,b)).fetchone()[0]:
                        raise ValueError(f'semantic model mismatch: {reference}/{engine}/{table}')
                equality[engine+'/'+table] = True
        summary = {}
        for engine, samples in groups.items():
            summary[engine] = dict(samples=len(samples))
            for metric in ('elapsed_seconds','input_games_per_second','container_cpu_seconds',
                           'container_peak_memory_mib','model_bytes','parquet_bytes'):
                values = [s[metric] for s in samples if s[metric] is not None]
                summary[engine]['median_'+metric] = median(values) if values else None
        plans = {}
        for engine in required_engines:
            month = groups[engine][-1]['source_month']+'-01'
            plans[engine] = conn.execute(sql.SQL('EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) SELECT speed,count(*) FROM {}.fact_game WHERE source_month=%s::date GROUP BY speed').format(sql.Identifier(schema_for(engine))), (month,)).fetchone()[0]
        result = dict(benchmark_group=group, summary=summary, equality=equality,
                      samples=groups, histories=histories[0], query_plans=plans,
                      measurement='parse typed Parquet, COPY, model publication; shared source preparation excluded')
        # DATE values occur only in the small history manifest, not game data.
        result['histories'] = [(str(month),checksum) for month,checksum in result['histories']]
        reports = Path(reports)
        atomic_json(reports/'comparison-v2.json', result)
        lines = ['# Optimized historical pipeline comparison','',f'Benchmark group: `{group}`.','',
                 '| Median metric | '+' | '.join(required_engines)+' |', '|---|'+ '---:|'*len(required_engines)]
        metrics = [('samples','Fresh samples'),('median_elapsed_seconds','Processing seconds'),
                   ('median_input_games_per_second','Input games/second'),('median_container_cpu_seconds','Client CPU seconds'),
                   ('median_container_peak_memory_mib','Container peak memory MiB'),
                   ('median_model_bytes','Historical PostgreSQL bytes'),('median_parquet_bytes','Typed Parquet bytes')]
        for name,label in metrics:
            values = [summary[e][name] for e in required_engines]
            lines.append('| '+label+' | '+' | '.join('n/a' if v is None else str(round(v,3)) for v in values)+' |')
        lines += ['', 'All active fact, dimension and reject rows match semantically across engines.',
                  'Warehouse surrogate numbers and run timestamps are intentionally excluded; natural keys and game-time attributes are compared.',
                  'Sources and complete archive histories match. Samples with changed code, images, settings or limits are rejected.',
                  'Download and shared framing are separately reported preparation work, excluded from these engine timings.',
                  'Spark startup/shutdown is included. PostgreSQL CPU/memory is not included in client cgroup metrics.',
                  'These are local measurements; no claim of validated 30 GB/month or multi-year production throughput is made.','']
        (reports/'comparison-v2.md').write_text('\n'.join(lines))
        event('comparison_complete', group=group, summary=summary, equality=equality)
        return result
