"""Strict bounded manual Lichess import and legal, reproducible replay payloads."""
from datetime import datetime
import hashlib
import io
import json
import math
import re
from urllib.parse import urlsplit

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
    ('pgn',pa.string()),('identity_source',pa.string()),('metadata_defaults',pa.list_(pa.string()))
])

DEFAULT_FIELDS={'game_date','player_name','opponent_name','white_rating','black_rating',
    'time_control','eco','opening','result','site','game_id','variant','clock_annotations','evaluation_annotations'}


def valid_game_id(value):
    return isinstance(value,str) and re.fullmatch(r'(?:[A-Za-z0-9]{8}|local_[a-f0-9]{64})',value) is not None


def missing(value):
    return value is None or value.strip() in ('','?','-')


def game_identity(headers,names,game_date,initial_fen,moves,defaults):
    """Prefer source IDs; generic PGNs receive a reproducible namespaced identity."""
    site=headers.get('Site','').strip()
    candidate=site if '://' in site else 'https://'+site
    try:
        url=urlsplit(candidate)
        match=re.fullmatch(r'/(?:game/)?([A-Za-z0-9]{8})(?:[A-Za-z0-9]{4})?(?:/(?:white|black))?/?',url.path)
        source_id=match[1] if url.scheme in ('http','https') and url.netloc.casefold() in ('lichess.org','www.lichess.org') and match else None
    except ValueError:
        source_id=None
    tag=headers.get('GameId','').strip()
    tagged_id=tag if re.fullmatch(r'[A-Za-z0-9]{8}',tag) else None
    if source_id and tagged_id and source_id!=tagged_id:
        raise ValueError('Site and GameId identify different games; correct the conflicting headers')
    if missing(site):defaults.add('site')
    if source_id or tagged_id:
        identifier=source_id or tagged_id
        return identifier,'source',('https://lichess.org/'+source_id if source_id else site or '?')
    saved=headers.get('StudyId','')
    if re.fullmatch(r'local_[a-f0-9]{64}',saved):
        return saved,'local_content',site or '?'
    identity=dict(names={k:v.casefold() for k,v in names.items()},date=game_date,
        time=headers.get('UTCTime',''),site=site if not missing(site) else '',
        source_tag=tag,initial_fen=initial_fen,moves=moves)
    identifier='local_'+hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    defaults.add('game_id')
    return identifier,'local_content',site or '?'


def game_date(headers,settings,defaults):
    flexible=getattr(settings,'flexible_dates',False)
    for key in ('UTCDate','Date'):
        raw=headers.get(key,'').strip()
        prefix=re.match(r'^(\d{4})[.-](\d{2})',raw)
        if not flexible and prefix and '-'.join(prefix.groups())!=settings.month:
            raise ValueError('all game-start dates must be in the approved personal month')
        try:
            parsed=datetime.strptime(raw.replace('-','.'),'%Y.%m.%d').date()
        except ValueError:
            continue
        if not flexible and parsed.isoformat()[:7]!=settings.month:
            raise ValueError('all game-start dates must be in the approved personal month')
        return parsed
    defaults.add('game_date')
    return settings.month_date


class StrictBuilder(chess.pgn.GameBuilder):
    max_plies=600

    def begin_game(self):
        super().begin_game()
        self.plies=0
        self.final_result=None

    def begin_variation(self):
        return chess.pgn.SKIP

    def end_headers(self):
        if missing(self.game.headers.get('Variant')):
            self.game.headers.pop('Variant',None)
        return super().end_headers()

    def end_variation(self):
        # The parser calls this even for SKIP. GameBuilder would pop a stack
        # that begin_variation intentionally did not push.
        pass

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
        completed=('1-0','0-1','1/2-1/2')
        if self.final_result in completed and game.headers.get('Result') in completed and game.headers['Result']!=self.final_result:
            raise ValueError('PGN header and movetext results differ')
        if self.final_result in completed and game.headers.get('Result') not in completed:
            game.headers['Result']=self.final_result
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
        defaults=set(h.get('StudyDefaults','').split(',')) & DEFAULT_FIELDS
        variant=h.get('Variant','Standard')
        if missing(variant):
            defaults.add('variant');variant='Standard'
        if variant not in ('Standard','Chess'):
            raise ValueError('only standard chess games are supported')
        names={c:h.get(c.title(),'').strip() for c in ('white','black')}
        absent=[c for c,name in names.items() if missing(name)]
        present=[c for c,name in names.items() if not missing(name) and name.casefold()==settings.normalized_player]
        if len(absent)==1:
            names[absent[0]]='Unknown opponent' if present else settings.player
            defaults.add('opponent_name' if present else 'player_name')
        if len(absent)==2:
            raise ValueError('White and Black are both missing; provide the approved player in one color before importing')
        if any(len(name)>50 or any(ord(c)<32 for c in name) for name in names.values()):
            raise ValueError('valid White and Black names are required')
        colors=[c for c,name in names.items() if name.casefold()==settings.normalized_player]
        if len(colors)!=1:
            raise ValueError('every game must contain the approved player exactly once')
        color=colors[0]
        date=game_date(h,settings,defaults)
        result=h.get('Result')
        ratings={}
        for c in ('white','black'):
            try: ratings[c]=rating(h.get(c.title()+'Elo'))
            except (ValueError,TypeError): ratings[c]=None
            if ratings[c] is None: defaults.add(c+'_rating')
        try:
            control=time_control(h.get('TimeControl','-'))
            if any(control[k] is not None and control[k]>604800 for k in ('initial_seconds','increment_seconds')):
                raise ValueError('unsupported clock range')
        except (ValueError,TypeError): control=time_control('-')
        if control['speed']=='unknown': defaults.add('time_control')
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
            try: clock=node.clock()
            except (ValueError,OverflowError): clock=None;defaults.add('clock_annotations')
            clocks.append(clock if clock is not None and math.isfinite(clock) and 0<=clock<=604800 else None)
            try: evaluation=node.eval()
            except (ValueError,OverflowError): evaluation=None;defaults.add('evaluation_annotations')
            cp=evaluation.white().score() if evaluation else None
            mate=evaluation.white().mate() if evaluation else None
            evals.append(cp if cp is not None and abs(cp)<=100000 else None)
            mates.append(mate if mate is not None and abs(mate)<=1000 else None)
            # Persist numeric annotations only; arbitrary comments never enter prompts.
            node.comment=''
        if not moves:
            raise ValueError('each imported game must contain at least one move')
        if result not in ('1-0','0-1','1/2-1/2'):
            terminal=board.outcome(claim_draw=False)
            if terminal is None:
                raise ValueError('game result is missing; provide Result or a completed movetext result (it cannot be guessed)')
            result=terminal.result();defaults.add('result')
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
        if not eco or not re.fullmatch(r'[A-E][0-9]{2}',eco):
            eco=None;defaults.add('eco')
        opening=h.get('Opening','').strip()[:200]
        if missing(opening): opening='Unclassified opening';defaults.add('opening')
        date_text=date.isoformat() if date else None
        identifier,identity_source,site=game_identity(h,names,date_text,initial_fen,moves,defaults)
        # Rebuild a safe mainline PGN: selected headers, no comments or variations.
        safe=chess.pgn.Game()
        safe.setup(chess.Board(initial_fen))
        safe.headers.update(Event='Personal study',Site=site,Date=date.strftime('%Y.%m.%d') if date else '????.??.??',
            White=names['white'],Black=names['black'],Result=result,
            TimeControl=control['time_control_key'] if control['speed']!='unknown' else '-')
        if identity_source=='local_content':safe.headers['StudyId']=identifier
        elif not site.startswith('https://lichess.org/'):safe.headers['GameId']=identifier
        if defaults:safe.headers['StudyDefaults']=','.join(sorted(defaults))
        if eco:
            safe.headers['ECO']=eco
        safe.headers['Opening']=opening
        for c,value in ratings.items():
            if value is not None:
                safe.headers[c.title()+'Elo']=str(value)
        tail=safe
        for uci in moves:
            tail=tail.add_variation(chess.Move.from_uci(uci))
        row=dict(game_id=identifier,game_date=date_text,white=names['white'],black=names['black'],
            white_rating=ratings['white'],black_rating=ratings['black'],result=result,player_color=color,
            player_rating=own,opponent_rating=other,opponent_band=band,outcome=outcome,
            points={'win':1.,'draw':.5,'loss':0.}[outcome],speed=control['speed'],
            initial_seconds=control['initial_seconds'],increment_seconds=control['increment_seconds'],
            eco=eco,opening=opening,initial_fen=initial_fen,moves=moves,clocks=clocks,
            uploaded_eval_cp=evals,uploaded_mate=mates,pgn=str(safe),identity_source=identity_source,metadata_defaults=sorted(defaults))
        if identifier in games and games[identifier]!=row:
            raise ValueError('conflicting duplicate game IDs in the same upload')
        games[identifier]=row
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
        identity_source=row.get('identity_source') or 'source',metadata_defaults=row.get('metadata_defaults') or [],
        annotation_origin='Uploaded PGN annotations; not independently verified engine analysis')
