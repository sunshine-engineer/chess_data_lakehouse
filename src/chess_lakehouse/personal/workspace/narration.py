"""Move-grounded game descriptions; no generated tactical claims without evidence."""
import chess

from ..review import material


def describe(payload, evidence=None, ply=None, loss=False):
    color=payload['player_color']=='white'
    board=chess.Board(payload['initial_fen']);initial_turn=board.turn;events=[];trajectory=[]
    previous=material(board,color)
    for i,uci in enumerate(payload['moves']):
        move=chess.Move.from_uci(uci);san=board.san(move);number=board.fullmove_number
        own=board.turn==color;white_to_move=board.turn==chess.WHITE
        if board.is_castling(move):events.append(f"{'You' if own else 'Your opponent'} castled on move {number} ({san}).")
        board.push(move);balance=material(board,color)
        if abs(balance-previous)>=3:
            trajectory.append(f"After {number}{'.' if white_to_move else '…'} {san}, your material balance was {balance:+d} pawn-equivalent points.")
        previous=balance
    opponent=payload['black'] if color else payload['white']
    text=f"You played {payload['player_color']} against {opponent} and recorded a {payload['outcome']} after {len(payload['moves'])} half-moves. Opening: {payload.get('opening') or 'not recorded'}."
    if events:text+=' '+' '.join(events[:2])
    if trajectory:text+=' '+' '.join(trajectory[-3:])
    ending='The final recorded position is checkmate.' if board.is_checkmate() else 'The final recorded position is stalemate.' if board.is_stalemate() else f"The final board has a material balance of {material(board,color):+d} for you."
    text+=' '+ending
    termination=payload.get('termination')
    if termination:text+=f' Recorded termination: {termination}.'
    clocks=payload.get('clocks',[])
    own_clocks=[value for index,value in enumerate(clocks) if value is not None and ((initial_turn if index%2==0 else not initial_turn)==color)]
    if own_clocks:text+=f' Your last recorded clock was {own_clocks[-1]:g} seconds.'
    if loss and not board.is_game_over() and not termination:
        text+=' The game ended without a recorded checkmate or drawn board position. The stored PGN does not identify whether the loss was resignation, time, or another off-board ending; the last clock alone cannot establish that.'
    if evidence:
        candidates=sorted(evidence.get('candidates',[]),key=lambda x:-x.get('loss_cp',0))
        if candidates:
            text+=' '+('The largest detected deterioration was: ' if loss else 'A decision worth revisiting: ')+candidates[0].get('explanation',candidates[0].get('summary',''))
            if len(candidates)>1:text+=' Another decision: '+candidates[1].get('explanation',candidates[1].get('summary',''))
        else:
            text+=' This review detected no deterioration above its reporting threshold.'
            if loss:text+=' It therefore does not identify a tactical cause of this loss. Revisit clock use and the final position rather than inventing a blunder.'
        text+=f" Reviewed {evidence.get('player_moves_reviewed',0)} of {evidence.get('player_moves_total',0)} of your decisions. These are bounded engine estimates."
    elif loss:
        text+=' Material and the result alone do not establish why you lost. I have requested a review of this game to compare your decisions with stronger continuations.'
    else:text+=' Material describes the moves played; it does not measure king safety or prove who was better.'
    if isinstance(ply,int) and 0<=ply<=len(payload['moves']):
        current=chess.Board(payload['initial_fen'])
        for uci in payload['moves'][:ply]:current.push_uci(uci)
        text+=f" At selected position {ply}, {'White' if current.turn else 'Black'} is to move; your material balance is {material(current,color):+d}."
    return text
