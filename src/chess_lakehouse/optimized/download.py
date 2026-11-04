"""Bounded resumable HTTP download, trusted only after full-file SHA-256 verification."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import os

from ..pipeline import sha256_file
from .storage import atomic_json, check_disk


def download_source(settings):
    source = settings.source
    filename = f'lichess_db_standard_rated_{source.month}.pgn.zst'
    target = settings.data_root/'bronze'/source.sha256/filename
    target.parent.mkdir(parents=True,exist_ok=True)
    legacy = settings.data_root/'bronze'/filename
    if not target.exists() and legacy.exists() and sha256_file(legacy)==source.sha256:
        os.link(legacy,target)
    if target.exists():
        if target.stat().st_size>source.max_download_bytes or sha256_file(target)!=source.sha256:
            raise ValueError('cached archive size/checksum mismatch')
        return target
    partial = target.with_suffix(target.suffix+'.part')
    metadata_path = target.with_suffix(target.suffix+'.download.json')
    descriptor = dict(url=source.url,sha256=source.sha256)
    previous = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    if any(previous.get(k)!=v for k,v in descriptor.items()):
        partial.unlink(missing_ok=True)
        previous = descriptor
        atomic_json(metadata_path,previous)
    for attempt in range(3):
        check_disk(target.parent,settings.min_free_disk_bytes)
        offset = partial.stat().st_size if partial.exists() else 0
        if offset>source.max_download_bytes:
            raise ValueError('partial archive exceeds configured size limit')
        if offset and previous.get('total')==offset and sha256_file(partial)==source.sha256:
            os.replace(partial,target)
            return target
        headers = {'Accept-Encoding':'identity','User-Agent':'chess-lakehouse/2'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
            if previous.get('etag'):
                headers['If-Range'] = previous['etag']
        try:
            with urlopen(Request(source.url,headers=headers),timeout=60) as response:
                if response.status==206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
                    if not match or int(match[1])!=offset:
                        raise ValueError('server returned an invalid resume range')
                    total = int(match[3])
                    if previous.get('etag') and response.headers.get('ETag')!=previous['etag']:
                        partial.unlink(missing_ok=True)
                        previous = descriptor
                        atomic_json(metadata_path,previous)
                        raise ValueError('resume validator changed; download will restart')
                    mode='ab'
                elif response.status==200:
                    offset=0
                    total=int(response.headers['Content-Length']) if response.headers.get('Content-Length') else None
                    mode='wb'
                else:
                    raise ValueError('unexpected archive HTTP status')
                if total is not None and total>source.max_download_bytes:
                    raise ValueError('archive exceeds configured size limit')
                previous={**descriptor,'etag':response.headers.get('ETag'),'total':total}
                atomic_json(metadata_path,previous)
                received=offset
                with partial.open(mode) as stream:
                    while block:=response.read(1024*1024):
                        received+=len(block)
                        if received>source.max_download_bytes:
                            raise ValueError('archive exceeds configured size limit')
                        check_disk(target.parent,settings.min_free_disk_bytes,len(block))
                        stream.write(block)
                    stream.flush(); os.fsync(stream.fileno())
                if total is not None and received!=total:
                    raise OSError('incomplete archive transfer; partial retained for resume')
            if sha256_file(partial)!=source.sha256:
                partial.unlink(missing_ok=True)
                raise ValueError('download checksum mismatch; untrusted partial removed')
            os.replace(partial,target)
            return target
        except HTTPError as error:
            if error.code==416:
                if partial.exists() and sha256_file(partial)==source.sha256:
                    os.replace(partial,target)
                    return target
                partial.unlink(missing_ok=True)
            elif error.code in (400,401,403,404):
                raise
            if attempt==2:
                raise
        except (OSError,ValueError):
            if attempt==2:
                raise
        time.sleep(2**attempt)
    raise RuntimeError('unreachable')
