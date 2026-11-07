"""Explicitly selected offline HTTP/browser fixture server; never the production entrypoint."""
from pathlib import Path
import io

from chess_lakehouse.personal.workspace import imports
from chess_lakehouse.personal.workspace.application import create_app as real_app


class FixtureTransport:
    def open(self,request,timeout):
        url,player,_=imports.lichess_url(request.full_url)
        if player.casefold()!='demostudent':raise ValueError('Synthetic transport only serves DemoStudent')
        return io.BytesIO(Path('/app/tests/fixtures/personal-demo.pgn').read_bytes())


original=imports.download
def fixture_download(record,config,opener=None):return original(record,config,FixtureTransport())


def create_app():
    imports.download=fixture_download
    return real_app()
