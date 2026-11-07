"""Full-game inspection and three-line coaching grounded in legal Stockfish PVs.

One engine, one thread. A quick pass covers every position; a deeper MultiPV pass
checks every decision by the studied player. Comparisons of played/alternative
moves start from the same position. All detected findings are retained.
"""
import time
import chess
import chess.engine

VALUES={chess.PAWN:1,chess.KNIGHT:3,chess.BISHOP:3,chess.ROOK:5,chess.QUEEN:9}


def material(board,color):
    return sum(v*(len(board.pieces(p,color))-len(board.pieces(p,not color))) for p,v in VALUES.items())


def score_text(cp,mate,winning=None):
    if mate is not None:
        return ('A mating attack for you' if winning else 'A mating attack against you')+(' in '+str(abs(mate))+' moves' if mate else '')
    if cp is None:return 'No numerical score available'
    return f'{cp/100:+.2f} for you'


def line_data(board,info,color):
    position=board.copy();fens=[position.fen()];sans=[];ucis=[]
    for move in info.get('pv',[])[:12]:
        if move not in position.legal_moves:break
        sans.append(position.san(move));ucis.append(move.uci());position.push(move);fens.append(position.fen())
    own=info['score'].pov(color);white=info['score'].white()
    return dict(cp=white.score(mate_score=100000),mate=white.mate(),depth=info.get('depth'),nodes=info.get('nodes'),
        score_cp=own.score(),score_mate=own.mate(),winning_mate=own.score(mate_score=100000)>0,
        score_value=own.score(mate_score=100000),pv_uci=ucis,pv_san=sans,positions=fens,
        score_label=score_text(own.score(),own.mate(),own.score(mate_score=100000)>0))


def explain(board,line,played,color):
    """Describe only facts observed in the returned legal continuation."""
    first=chess.Move.from_uci(line['pv_uci'][0]);piece=board.piece_at(first.from_square)
    action=line['pv_san'][0]+' '
    if board.is_castling(first):action+='castles and brings the rook into play.'
    elif board.is_capture(first):
        captured=chess.PAWN if board.is_en_passant(first) else board.piece_at(first.to_square).piece_type
        action+='captures a '+chess.piece_name(captured)+'.'
    elif piece.piece_type in (chess.KNIGHT,chess.BISHOP) and chess.square_rank(first.from_square)==(0 if color else 7):
        action+='develops your '+chess.piece_name(piece.piece_type)+' to '+chess.square_name(first.to_square)+'.'
    else:action+='places your '+chess.piece_name(piece.piece_type)+' on '+chess.square_name(first.to_square)+'.'
    if len(line['pv_san'])>1:action+=' The engine accounts for '+line['pv_san'][1]+' as the opponent’s reply.'
    end=chess.Board(line['positions'][-1]);delta=material(end,color)-material(board,color)
    if end.is_checkmate():action+=' The shown continuation ends in checkmate.'
    elif delta>0:action+=f' In this shown sequence your net material balance improves by {delta} pawn-equivalent points.'
    elif delta<0:action+=f' This line gives up {abs(delta)} pawn-equivalent points of material on balance; inspect the compensation in the continuation.'
    if line['score_mate'] is not None:
        action+=' '+line['score_label']+' is the engine’s search result.'
    elif line['score_cp']>50:action+=f' Its estimated assessment is {line["score_cp"]/100:+.2f} from your side, favoring your position.'
    elif line['score_cp']< -50:action+=f' Its estimated assessment is {line["score_cp"]/100:+.2f} from your side; the opponent still has the advantage.'
    else:action+=' The engine assesses the position as approximately balanced.'
    if played['score_mate'] is None and line['score_mate'] is None:
        gain=(line['score_cp']-played['score_cp'])/100
        action+=f' Compared with the game move, the score changes by {gain:+.2f} pawn units from your side.'
    elif played['score_mate'] is not None and not played['winning_mate'] and line['score_mate'] is None:
        action+=' The played move allowed a mating attack; this search finds no forced mate against you in this alternative.'
    return action


def analyze(row,config,binary,options,progress=None):
    started=time.monotonic();deadline=started+config.engine_game_seconds
    color=row['player_color']=='white';initial=chess.Board(row['initial_fen'])
    count=len(row['moves']);own_total=sum((initial.turn if i%2==0 else not initial.turn)==color for i in range(count))
    scores=[];candidates=[];reviewed=0
    def notify(stage,force=False):
        if progress and (force or (len(scores)+reviewed)%5==0):
            progress(dict(stage=stage,positions_checked=min(len(row['moves']),max(0,len(scores)-1)),
                total_plies=count,player_moves_reviewed=reviewed,player_moves_total=own_total,
                findings=len(candidates),elapsed_seconds=round(time.monotonic()-started,1)))
    with chess.engine.SimpleEngine.popen_uci(str(binary),timeout=5) as search:
        search.configure({'Threads':1,'Hash':config.engine_hash_mb});name=search.id.get('name','Stockfish')
        scan_deadline=started+config.engine_game_seconds*.35
        notify('Checking every played position',True)
        b=initial.copy()
        for i in range(count+1):
            remaining=scan_deadline-time.monotonic()
            if remaining<=0:break
            allot=max(.001,min(.6,remaining/(count+1-i)))
            info=search.analyse(b,chess.engine.Limit(nodes=config.engine_nodes,time=allot))
            scores.append(line_data(b,info,color));notify('Checking every played position')
            if i<count:b.push_uci(row['moves'][i])
        notify('Comparing your decisions with candidate lines',True)
        before=initial.copy()
        for i,played_uci in enumerate(row['moves']):
            if before.turn!=color:
                before.push_uci(played_uci);continue
            remaining=deadline-time.monotonic()-1
            if remaining<=0:break
            legal=list(before.legal_moves)
            allocation=remaining/(own_total-reviewed)
            think=min(6.,max(.002,allocation*.65))
            infos=search.analyse(before,chess.engine.Limit(nodes=config.engine_nodes*25,time=think),multipv=min(4,len(legal)))
            lines=[line_data(before,info,color) for info in infos]
            lines=[line for line in lines if line['pv_uci']]
            played=next((line for line in lines if line['pv_uci'][0]==played_uci),None)
            if played is None:
                remaining=deadline-time.monotonic()-.1
                if remaining<=0:break
                info=search.analyse(before,chess.engine.Limit(nodes=config.engine_nodes*25,time=min(remaining,max(.002,min(2.,allocation*.3)))),root_moves=[chess.Move.from_uci(played_uci)])
                played=line_data(before,info,color)
            if not played['pv_uci']:break
            reviewed+=1
            alternatives=sorted((l for l in lines if l['pv_uci'][0]!=played_uci),key=lambda l:-l['score_value'])[:3]
            if alternatives:
                best=alternatives[0];loss=max(0,best['score_value']-played['score_value'])
                if loss>=options['review_loss_cp']:
                    played_san=before.san(chess.Move.from_uci(played_uci));after=before.copy();after.push_uci(played_uci)
                    reply=played['pv_uci'][1:] if len(played['pv_uci'])>1 else []
                    # An immediate mating reply is a verified board fact.
                    motif='Blunder' if loss>=300 else 'Mistake to review'
                    reason=f'After {played_san}, the engine assesses your position as {played["score_label"]}; {best["pv_san"][0]} receives {best["score_label"]}.'
                    reply_board=after.copy();reply_fens=[reply_board.fen()];reply_san=[]
                    for uci in reply:
                        move=chess.Move.from_uci(uci)
                        if move not in reply_board.legal_moves:break
                        reply_san.append(reply_board.san(move));reply_board.push(move);reply_fens.append(reply_board.fen())
                    if len(reply_fens)>1 and chess.Board(reply_fens[1]).is_checkmate():
                        motif='Allows checkmate in one';reason=f'After {played_san}, the legal reply {reply_san[0]} checkmates. Check the opponent’s forcing replies before committing.'
                    for rank,line in enumerate(alternatives,1):
                        line['rank']=rank;line['explanation']=explain(before,line,played,color)
                    candidates.append(dict(ply=i+1,fullmove=before.fullmove_number,played_uci=played_uci,played_san=played_san,
                        suggested_uci=best['pv_uci'][0],suggested_san=best['pv_san'][0],loss_cp=loss,
                        before_cp=best['cp'],after_cp=played['cp'],before_mate=best['mate'],after_mate=played['mate'],
                        played_score=played['score_label'],best_score=best['score_label'],motif=motif,explanation=reason,summary=reason,
                        alternatives=alternatives,alternative_count=len(alternatives),pv_uci=best['pv_uci'],pv_san=best['pv_san'],
                        preferred_positions=best['positions'],reply_positions=reply_fens,reply_pv_san=reply_san,
                        practice='Compare the three plans, replay the opponent’s replies, then return to the game and choose your move again.'))
            notify('Comparing your decisions with candidate lines')
            before.push_uci(played_uci)
        notify('Review finished',True)
    complete=len(scores)==count+1 and reviewed==own_total
    candidates.sort(key=lambda c:(-c['loss_cp'],c['ply']))
    return dict(engine=name,review_version=3,budget={**options,'deep_nodes':config.engine_nodes*25,'candidate_lines':3},
        elapsed_seconds=round(time.monotonic()-started,2),analyzed_plies=min(len(row['moves']),max(0,len(scores)-1)),total_plies=len(row['moves']),
        player_moves_reviewed=reviewed,player_moves_total=own_total,complete_coverage=complete,
        candidates=candidates,positions=scores,interpretation='Every listed finding is search-based. Scores are from your side; +1 roughly means a pawn-equivalent advantage. A negative score favors the opponent. A mate score counts moves to mate, not pawns.',
        coverage_explanation='Every played move and each of your decisions was checked.' if complete else 'The time budget ended before all decisions could be checked. Coverage below shows exactly what finished.',
        source='Local Stockfish analysis of the immutable game; up to three distinct legal alternative first moves per finding')
