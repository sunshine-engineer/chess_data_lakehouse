"""Real HTTP range requests served entirely inside the test container."""
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
from pathlib import Path
from threading import Thread

import pytest

from chess_lakehouse.optimized.download import download_source
from chess_lakehouse.optimized.settings import Settings, Source
from chess_lakehouse.optimized.storage import atomic_json

BODY = b'known archive content ' * 2000


@contextmanager
def server(ignore_range=False, invalid_range=False):
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_GET(self):
            requested=self.headers.get('Range')
            requests.append(requested)
            offset=int(requested.split('=')[1].split('-')[0]) if requested else 0
            partial=bool(requested and not ignore_range)
            self.send_response(206 if partial else 200)
            if partial:
                start=offset+1 if invalid_range else offset
                self.send_header('Content-Range',f'bytes {start}-{len(BODY)-1}/{len(BODY)}')
            else:
                offset=0
            self.send_header('ETag','"fixed"')
            self.send_header('Content-Length',str(len(BODY)-offset))
            self.end_headers()
            try:
                self.wfile.write(BODY[offset:])
            except (BrokenPipeError,ConnectionResetError):
                pass
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=http.serve_forever,daemon=True); thread.start()
    try:
        yield f'http://127.0.0.1:{http.server_port}/archive',requests
    finally:
        http.shutdown(); thread.join(timeout=3); http.server_close()


def settings_for(tmp_path,url):
    return Settings(Source('2013-01',hashlib.sha256(BODY).hexdigest(),1,1000000,url),
                    data_root=tmp_path,min_free_disk_bytes=0)


@pytest.mark.parametrize('ignore_range',[False,True])
def test_resume_or_safe_restart_when_server_ignores_range(tmp_path,ignore_range):
    with server(ignore_range=ignore_range) as (url,requests):
        settings=settings_for(tmp_path,url)
        root=tmp_path/'bronze'/settings.source.sha256; root.mkdir(parents=True)
        target=root/'lichess_db_standard_rated_2013-01.pgn.zst'
        target.with_suffix('.zst.part').write_bytes(BODY[:1234])
        atomic_json(target.with_suffix('.zst.download.json'),dict(url=url,sha256=settings.source.sha256,etag='"fixed"',total=len(BODY)))
        result=download_source(settings)
        assert result.read_bytes()==BODY and requests==['bytes=1234-']
        download_source(settings)
        assert len(requests)==1


def test_invalid_range_is_never_published(tmp_path,monkeypatch):
    monkeypatch.setattr('chess_lakehouse.optimized.download.time.sleep',lambda value:None)
    with server(invalid_range=True) as (url,requests):
        settings=settings_for(tmp_path,url)
        root=tmp_path/'bronze'/settings.source.sha256; root.mkdir(parents=True)
        target=root/'lichess_db_standard_rated_2013-01.pgn.zst'
        target.with_suffix('.zst.part').write_bytes(BODY[:1234])
        atomic_json(target.with_suffix('.zst.download.json'),dict(url=url,sha256=settings.source.sha256,etag='"fixed"'))
        with pytest.raises(ValueError,match='invalid resume range'):
            download_source(settings)
        assert not target.exists() and len(requests)==3


def test_download_size_limit_and_bad_checksum(tmp_path,monkeypatch):
    monkeypatch.setattr('chess_lakehouse.optimized.download.time.sleep',lambda value:None)
    with server() as (url,requests):
        settings=settings_for(tmp_path,url)
        with pytest.raises(ValueError,match='size limit'):
            download_source(replace(settings,source=replace(settings.source,max_download_bytes=10)))
        bad=replace(settings,source=replace(settings.source,sha256='a'*64))
        with pytest.raises(ValueError,match='checksum mismatch'):
            download_source(bad)
        assert not (tmp_path/'bronze'/('a'*64)/'lichess_db_standard_rated_2013-01.pgn.zst').exists()
