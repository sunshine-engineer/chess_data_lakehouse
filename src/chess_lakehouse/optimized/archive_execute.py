"""One archive subprocess. SIGTERM stops work; committed shards remain resumable."""
import json
import os
from pathlib import Path
import sys

from .settings import Settings,Source
from .download import download_source
from .storage import file_lock
from .workflow import run,event
from ..runtime import load as runtime


def execute(job):
    """Run one verified archive job in a subprocess with durable pipeline checkpoints."""
    source=job['source'];settings=Settings(Source(**{k:source[k] for k in Source.__dataclass_fields__}),data_root=runtime().paths.data)
    lock=settings.data_root/'bronze'/source['sha256']/'.archive-job.lock'
    with file_lock(lock):
        def progress(received,total):event('download_progress',bytes_received=received,total_bytes=total)
        archive=download_source(settings,progress=progress)
    event('archive_verified',month=source['month'],bytes=archive.stat().st_size)
    return run(job['engine'],settings,reports=settings.data_root/'archive-reports',group='archive-app',resume=job.get('run_id'),archive=archive)


if __name__=='__main__':execute(json.loads(Path(sys.argv[1]).read_text()))
