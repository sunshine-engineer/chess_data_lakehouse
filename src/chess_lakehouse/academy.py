"""Original beginner lessons and rule-checked drills, shared by both services.

Worked game examples are observational. Only the explicit drill goal is graded;
no historical move or strategic principle is treated as a proven best move.
"""
import chess

LESSONS = [
 dict(id='opening-center',phase='opening',title='Give your pieces room',goal='center',
      fen=chess.STARTING_FEN,prompt='Advance a central pawn (d- or e-pawn) one or two squares.',
      ideas=['Central pawns can give your bishops and queen room and contest useful squares.',
             'Develop pieces, check threats and make your king safe. A pawn move is useful when it supports that plan.',
             'In a game example, name the squares each central pawn controls. Ask which piece can become active next.'],
      reflection='Compare the recorded move with another sensible developing move. What changes for the opponent?'),
 dict(id='opening-development',phase='opening',title='Bring a piece into play',goal='develop',
      fen='rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2',
      prompt='Move a knight or bishop from its starting square to an active square.',
      ideas=['Knights and bishops usually need to join the game before the rooks can cooperate.',
             'Avoid moving the same piece repeatedly without a concrete reason. A threat or forced reply can justify doing so.',
             'Castling needs a clear path, a king that is not in check, and safe transit squares. Clear a path without ignoring threats.'],
      reflection='In your example, count undeveloped minor pieces. Find a legal developing move and check the reply.'),
 dict(id='middlegame-checks',phase='middlegame',title='Scan forcing moves first',goal='check',
      fen='4k3/8/8/8/8/8/R7/4K3 w - - 0 1',prompt='Give check with the rook in this simplified drill.',
      ideas=['Before choosing a plan, look for checks, captures and direct threats for both sides.',
             'A check forces a response, but it can still lose material. Work through the opponent’s legal replies.',
             'Choose a plan based on king safety, loose pieces, pawn breaks and useful squares; verify a short line before moving.'],
      reflection='List checks and captures in the example. Which reply would most seriously challenge your intended move?'),
 dict(id='middlegame-plan',phase='middlegame',title='Choose a plan, then check the reply',goal='rook_support',
      fen='r1bq1rk1/ppp2ppp/2np1n2/4p3/2B1P3/2PP1N2/PP3PPP/RNBQ1RK1 w - - 0 7',
      prompt='For this practice plan, place a rook on the e-file to support the e-pawn. This is a target-square drill, not a best-move claim.',
      ideas=['First compare king safety, pawn structure and piece activity. Name a specific improvement rather than choosing an attack automatically.',
             'Identify your least active piece and a useful square, or a pawn break that could open a line. Check whether your pieces can support that change.',
             'In this diagram, rook support for the e-pawn is one plan to examine. Before playing it in a game, calculate the opponent’s checks, captures and direct threats.',
             'After each reply, reassess the position. A plan is a direction, not a sequence you must follow regardless of what changes.'],
      reflection='Write one sentence: I want to improve [piece] on [square] because [reason]. Then name the opponent’s strongest forcing reply.'),
 dict(id='middlegame-mate',phase='middlegame',title='Verify the whole threat',goal='mate',
      fen='r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4',
      prompt='Find a move that checkmates immediately.',
      ideas=['An attack works when pieces cooperate and the defender has no adequate reply.',
             'For a claimed mate, check captures of the checking piece, blocks and king escapes. A familiar pattern is only a starting point.',
             'In ordinary middlegames, improve your least active piece or prepare a pawn break when no forcing continuation works.'],
      reflection='Find an attacking piece and a defender in the example. Describe the opponent’s threat before making your own plan.'),
 dict(id='endgame-promotion',phase='endgame',title='Convert a passed pawn',goal='promotion',
      fen='7k/P7/8/8/8/8/8/4K3 w - - 0 1',prompt='Promote the pawn without leaving a stalemate.',
      ideas=['A passed pawn has no opposing pawn ahead on its file or neighboring files. A king or piece may still stop it.',
             'Use your king actively when it is safe, and calculate the pawn race rather than assuming the pawn will queen.',
             'Choose promotion deliberately: a queen is common, but a knight, bishop or rook can be necessary. Always check stalemate.'],
      reflection='In the example, identify passed pawns and the kings’ routes. Count the moves needed to promote and stop the opponent.'),
 dict(id='endgame-mate',phase='endgame',title='Finish without stalemate',goal='mate',
      fen='7k/5K2/6Q1/8/8/8/8/8 w - - 0 1',prompt='Deliver checkmate in one move with king and queen.',
      ideas=['With king and queen, restrict the enemy king while keeping the queen safe. Bring your king close enough to help.',
             'Checkmate requires check and no legal reply. Stalemate is a draw when the player is not in check and has no legal move.',
             'In pawn endings, opposing kings with one square between them can create a useful waiting-move advantage. Pawn moves can change who must give way.'],
      reflection='In the example, compare king activity and remaining pawn moves. Check whether a forcing move leaves the opponent any legal move.')
]


def lesson(identity):
    """Return a published authored lesson or reject an unknown lesson identifier."""
    value=next((x for x in LESSONS if x['id']==identity),None)
    if value is None:raise ValueError('Choose a published lesson')
    return value


def grade(identity,text):
    """Validate a legal drill move and report whether it meets the authored goal."""
    item=lesson(identity);board=chess.Board(item['fen'])
    try:move=chess.Move.from_uci(text)
    except (ValueError,TypeError):raise ValueError('Choose a legal move on the board') from None
    if move not in board.legal_moves:raise ValueError('That move is not legal in this drill')
    piece=board.piece_at(move.from_square);san=board.san(move);check=board.gives_check(move)
    board.push(move)
    correct={'center':piece.piece_type==chess.PAWN and chess.square_file(move.from_square) in (3,4),
             'develop':piece.piece_type in (chess.KNIGHT,chess.BISHOP) and chess.square_rank(move.from_square)==0,
             'check':check,'mate':board.is_checkmate(),
             'rook_support':piece.piece_type==chess.ROOK and chess.square_file(move.to_square)==4,
             'promotion':bool(move.promotion) and not board.is_stalemate()}[item['goal']]
    return dict(correct=correct,san=san,fen=board.fen(),
        feedback=('Goal met: '+san+'.' if correct else
                  san+' is legal but does not meet this drill’s goal. '+item['prompt']))


def phase_positions(payload):
    """At most one opening/middle/end example per game; phase is a teaching heuristic."""
    board=chess.Board(payload['initial_fen']);found={};opening_target=min(6,max(0,len(payload['moves'])-1))
    for ply,uci in enumerate(payload['moves']):
        material=sum(len(board.pieces(p,c))*v for p,v in ((chess.KNIGHT,3),(chess.BISHOP,3),(chess.ROOK,5),(chess.QUEEN,9)) for c in (True,False))
        phase='opening' if ply<20 else 'endgame' if material<=20 else 'middlegame'
        if phase not in found and (ply>=opening_target or phase!='opening'):
            following=board.copy();following.push_uci(uci)
            found[phase]=dict(phase=phase,ply=ply,fen=board.fen(),next_fen=following.fen(),played_uci=uci,played_san=board.san(chess.Move.from_uci(uci)))
        board.push_uci(uci)
    return list(found.values())
