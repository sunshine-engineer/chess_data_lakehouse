"""Explicitly selected offline HTTP/browser fixture server; never the production entrypoint."""
from pathlib import Path
import io
import json
from datetime import datetime,timezone
from urllib.parse import urlsplit,parse_qs
import chess.pgn

from chess_lakehouse.personal.workspace import imports,sync
from chess_lakehouse.personal.workspace.application import create_app as real_app


class FixtureTransport:
    def open(self,request,timeout):
        content=Path('/app/tests/fixtures/personal-demo.pgn').read_text()
        if urlsplit(request.full_url).path=='/api/games/export/_ids':
            ids=set(request.data.decode().split(','));stream=io.StringIO(content);selected=[]
            while game:=chess.pgn.read_game(stream):
                if game.headers['Site'].rsplit('/',1)[-1] in ids:selected.append(str(game))
            return io.BytesIO(('\n\n'.join(selected)+'\n').encode())
        url,player,_=imports.lichess_url(request.full_url)
        if player.casefold()!='demostudent':raise ValueError('Synthetic transport only serves DemoStudent')
        if request.get_header('Accept')=='application/x-ndjson':
            q=parse_qs(urlsplit(request.full_url).query);stream=io.StringIO(content);selected=[]
            while game:=chess.pgn.read_game(stream):
                created=int(datetime.strptime(game.headers['Date'],'%Y.%m.%d').replace(tzinfo=timezone.utc).timestamp()*1000)
                if int(q.get('since',['0'])[0])<=created<=int(q['until'][0]):selected.append(json.dumps(dict(id=game.headers['Site'].rsplit('/',1)[-1],createdAt=created,status='mate',variant='standard')))
            return io.BytesIO(('\n'.join(selected)+'\n').encode())
        return io.BytesIO(content.encode())


original=imports.download
def fixture_download(record,config,opener=None):return original(record,config,FixtureTransport())
original_sync=sync.download
def fixture_sync(record,config,opener=None):return original_sync(record,config,FixtureTransport())


def create_app():
    imports.download=fixture_download
    sync.download=fixture_sync
    return real_app()
