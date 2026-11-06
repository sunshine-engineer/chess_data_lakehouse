"""Strict bounded manual Lichess import and legal, reproducible replay payloads."""
from datetime import datetime
import hashlib
import io
import json
import math
import re

import chess
import chess.pgn
import pyarrow as pa

from ..models import rating,time_control

PAYLOAD_SCHEMA=pa.schema([
    ('game_id',pa.string()),('game_date',pa.string()),('white',pa.string()),('black',pa.string()),
    ('white_rating',pa.int32()),('black_rating',pa.int32()),('result',pa.string()),
    ('player_color',pa.string()),('player_rating',pa.int32()),('opponent_rating',pa.int32()),
    ('opponent_band',pa.string()),('outcome',pa.string()),('points',pa.float64()),
    ('speed',pa.string()),('initial_seconds',pa.int64()),('increment_seconds',pa.int64()),
    ('eco',pa.string()),('opening',pa.string()),('initial_fen',pa.string()),
    ('moves',pa.list_(pa.string())),('clocks',pa.list_(pa.float64())),
    ('uploaded_eval_cp',pa.list_(pa.int32())),('uploaded_mate',pa.list_(pa.int32())),
    ('pgn',pa.string())
])


class StrictBuilder(chess.pgn.GameBuilder):
    max_plies=600

    def begin_game(self):
        super().begin_game()
        self.plies=0
        self.final_result=None

    def begin_variation(self):
        return chess.pgn.SKIP

    def visit_move(self,board,move):
        self.plies+=1
        if self.plies>self.max_plies:
            raise ValueError('a game exceeds the mainline ply limit')
        super().visit_move(board,move)

    def visit_result(self,result):
        self.final_result=result
        super().visit_result(result)

    def handle_error(self,error):
        raise ValueError('illegal or malformed PGN mainline') from error

    def result(self):
        game=super().result()
        if self.final_result and game.headers.get('Result')!=self.final_result:
            raise ValueError('PGN header and movetext results differ')
        return game


def position_key(board):
    """Position equality includes turn, castling and legal en-passant rights."""
    return hashlib.sha256(' '.join(board.fen(en_passant='legal').split()[:4]).encode()).digest()


def parse(content,settings):
    if not content or len(content)>settings.max_upload_bytes:
        raise ValueError('PGN file is empty or exceeds the 10 MiB upload limit')
    try:
        text=content.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('PGN must be UTF-8 text') from None
    if '\x00' in text:
        raise ValueError('binary PGN input is unsupported')
    stream=io.StringIO(text)
    games={}
    total_plies=0
    builder=type('BoundedBuilder',(StrictBuilder,),{'max_plies':settings.max_game_plies})
    occurrences=0
    while True:
        game=chess.pgn.read_game(stream,Visitor=builder)
        if game is None:
            break
        occurrences+=1
        if occurrences>settings.max_games:
            raise ValueError('upload exceeds 500 game occurrences; split the file manually')
        h=game.headers
        if h.get('Variant','Standard') not in ('Standard','Chess'):
            raise ValueError('only standard chess games are supported')
        site=re.fullmatch(r'https?://lichess\.org/([A-Za-z0-9]{8})(?:[A-Za-z0-9]{4})?/?',h.get('Site',''))
        if not site:
            raise ValueError('each game requires a Lichess game URL in its Site header')
        names={c:h.get(c.title(),'').strip() for c in ('white','black')}
        if any(not name or len(name)>50 or any(ord(c)<32 for c in name) for name in names.values()):
            raise ValueError('valid White and Black names are required')
        colors=[c for c,name in names.items() if name.casefold()==settings.normalized_player]
        if len(colors)!=1:
            raise ValueError('every game must contain the approved player exactly once')
        color=colors[0]
        try:
            date=datetime.strptime(h.get('UTCDate',h.get('Date','')),'%Y.%m.%d').date()
        except ValueError:
            raise ValueError('every game needs an exact UTCDate or Date') from None
        if date.isoformat()[:7]!=settings.month:
            raise ValueError('all game-start dates must be in the approved personal month')
        result=h.get('Result')
        if result not in ('1-0','0-1','1/2-1/2'):
            raise ValueError('only completed games can be imported')
        ratings={c:rating(h.get(c.title()+'Elo')) for c in ('white','black')}
        control=time_control(h.get('TimeControl','-'))
        board=game.board()
        if not board.is_valid():
            raise ValueError('invalid starting position')
        initial_fen=board.fen()
        moves,clocks,evals,mates=[],[],[],[]
        for node in game.mainline():
            if node.move not in board.legal_moves:
                raise ValueError('illegal PGN move')
            moves.append(node.move.uci())
            board.push(node.move)
            clock=node.clock()
            clocks.append(clock if clock is not None and math.isfinite(clock) and 0<=clock<=604800 else None)
            evaluation=node.eval()
            cp=evaluation.white().score() if evaluation else None
            mate=evaluation.white().mate() if evaluation else None
            evals.append(cp if cp is not None and abs(cp)<=100000 else None)
            mates.append(mate if mate is not None and abs(mate)<=1000 else None)
            # Persist numeric annotations only; arbitrary comments never enter prompts.
            node.comment=''
        if not moves:
            raise ValueError('each imported game must contain at least one move')
        if board.is_checkmate():
            expected='0-1' if board.turn==chess.WHITE else '1-0'
            if result!=expected:
                raise ValueError('game result contradicts the final checkmate')
        elif board.is_stalemate() and result!='1/2-1/2':
            raise ValueError('game result contradicts final stalemate')
        total_plies+=len(moves)
        if total_plies>settings.max_total_plies:
            raise ValueError('upload exceeds the total personal-study ply limit')
        opponent='black' if color=='white' else 'white'
        own,other=ratings[color],ratings[opponent]
        band='unknown' if own is None or other is None else 'stronger' if other-own>200 else 'weaker' if other-own< -200 else 'similar'
        outcome='draw' if result=='1/2-1/2' else 'win' if result==('1-0' if color=='white' else '0-1') else 'loss'
        eco=h.get('ECO')
        if eco and not re.fullmatch(r'[A-E][0-9]{2}',eco):
            raise ValueError('invalid ECO opening code')
        opening=h.get('Opening','Unclassified opening').strip()[:200] or 'Unclassified opening'
        # Rebuild a safe mainline PGN: selected headers, no comments or variations.
        safe=chess.pgn.Game()
        safe.setup(chess.Board(initial_fen))
        safe.headers.update(Event='Personal study',Site='https://lichess.org/'+site[1],Date=date.strftime('%Y.%m.%d'),
            White=names['white'],Black=names['black'],Result=result,TimeControl=h.get('TimeControl','-'))
        if eco:
            safe.headers['ECO']=eco
        safe.headers['Opening']=opening
        for c,value in ratings.items():
            if value is not None:
                safe.headers[c.title()+'Elo']=str(value)
        tail=safe
        for uci in moves:
            tail=tail.add_variation(chess.Move.from_uci(uci))
        row=dict(game_id=site[1],game_date=date.isoformat(),white=names['white'],black=names['black'],
            white_rating=ratings['white'],black_rating=ratings['black'],result=result,player_color=color,
            player_rating=own,opponent_rating=other,opponent_band=band,outcome=outcome,
            points={'win':1.,'draw':.5,'loss':0.}[outcome],speed=control['speed'],
            initial_seconds=control['initial_seconds'],increment_seconds=control['increment_seconds'],
            eco=eco,opening=opening,initial_fen=initial_fen,moves=moves,clocks=clocks,
            uploaded_eval_cp=evals,uploaded_mate=mates,pgn=str(safe))
        if site[1] in games and games[site[1]]!=row:
            raise ValueError('conflicting duplicate game IDs in the same upload')
        games[site[1]]=row
    if not games:
        raise ValueError('no completed PGN games found')
    return sorted(games.values(),key=lambda row:row['game_id']),occurrences-len(games)


def dataset_hash(rows):
    return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def replay(row):
    board=chess.Board(row['initial_fen'])
    positions=[board.fen()]
    moves=[]
    for ply,uci in enumerate(row['moves'],1):
        move=chess.Move.from_uci(uci)
        moves.append(dict(ply=ply,uci=uci,san=board.san(move),color='white' if board.turn else 'black',
            fullmove=board.fullmove_number,clock_seconds=row['clocks'][ply-1],
            uploaded_eval_cp=row['uploaded_eval_cp'][ply-1],uploaded_mate=row['uploaded_mate'][ply-1]))
        board.push(move)
        positions.append(board.fen())
    return dict(game_id=row['game_id'],positions=positions,moves=moves,player_color=row['player_color'],
        white=row['white'],black=row['black'],result=row['result'],opening=row['opening'],
        annotation_origin='Uploaded PGN annotations; not independently verified engine analysis')
