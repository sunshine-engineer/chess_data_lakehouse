"""Build a small reproducible public teaching sample from verified archive frames.

Run inside the loader container, after the personal/academy schema migration.
Reservoir here is deterministic lowest SHA-256 game-ID order (600/month by
default). PGNs >128 KiB and nonstandard/invalid games are excluded, counted and
never quietly replaced with invented moves. No player identities are retained.
"""
import argparse
import hashlib
import heapq
import io
import json
import os
import re

import chess.pgn
import pyarrow.parquet as pq
from psycopg.types.json import Jsonb

from .. import academy
from .settings import load
from .workflow import prepare_source
from .warehouse import connect


def build(settings,limit=600):
    if not 1<=limit<=1000:raise ValueError('Choose 1–1000 teaching games per month')
    frames=prepare_source(settings);heap=[];oversized=0
    for shard in frames['shards']:
        for batch in pq.ParquetFile(shard['path']).iter_batches(batch_size=16,columns=['pgn']):
            for text in batch.column(0).to_pylist():
                if len(text.encode())>128*1024:oversized+=1;continue
                match=re.search(r'^\[Site "https?://lichess.org/([A-Za-z0-9]{8})"\]',text,re.M)
                if not match:continue
                identity=match[1];rank=int.from_bytes(hashlib.sha256(identity.encode()).digest(),'big')
                entry=(-rank,identity,text)
                if len(heap)<limit:heapq.heappush(heap,entry)
                elif entry>heap[0]:heapq.heapreplace(heap,entry)
    examples=[];invalid=0
    for _,identity,text in sorted(heap,reverse=True):
        game=chess.pgn.read_game(io.StringIO(text))
        if game is None or game.errors or game.headers.get('Variant','Standard')!='Standard':invalid+=1;continue
        board=game.board()
        if not board.is_valid():invalid+=1;continue
        payload={'initial_fen':board.fen(),'moves':[m.uci() for m in game.mainline_moves()]}
        positions=academy.phase_positions(payload)
        if not positions:continue
        examples.append((identity,dict(positions=positions,eco=game.headers.get('ECO'),
            date=game.headers.get('UTCDate',game.headers.get('Date')),source='historical')))
    method=f'Lowest SHA256 game-ID sample; cap {limit}; PGN cap 128 KiB. Oversized excluded: {oversized}; invalid sampled games: {invalid}. Positions use a material/ply phase heuristic. Observed moves are not optimality labels.'
    with connect(settings) as c:
        c.execute("SELECT pg_advisory_xact_lock(hashtext('academy-publication'))")
        month=settings.source.month+'-01'
        c.execute('DELETE FROM academy.datasets WHERE source_month=%s',(month,))
        c.execute('INSERT INTO academy.datasets(source_month,source_sha256,input_games,sample_games,method) VALUES(%s,%s,%s,%s,%s)',
            (month,settings.source.sha256,settings.source.expected_games,len(examples),method))
        with c.cursor().copy('COPY academy.examples(source_month,game_id,payload) FROM STDIN') as copy:
            for identity,payload in examples:copy.write_row((month,identity,Jsonb(payload)))
    print(json.dumps({'month':settings.source.month,'sample_games':len(examples),'input_games':settings.source.expected_games,'method':method}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--month',required=True);parser.add_argument('--limit',type=int,default=600)
    parser.add_argument('--config',default='config/archive-demo.toml');args=parser.parse_args()
    if os.environ.get('CHESS_CONTAINER')!='1':parser.error('Run inside Docker')
    build(load(args.config,args.month),args.limit)
