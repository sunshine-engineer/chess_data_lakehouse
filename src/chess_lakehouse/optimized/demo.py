"""Explicit synthetic CI restore fixture; never downloads any monthly archive."""
from pathlib import Path
import os
import tempfile

import zstandard as zstd

from ..pipeline import sha256_file
from .settings import Settings,Source
from .workflow import run,event


def main():
    # This module is run by the administrative tests container, then drops privileges
    # at the database connection boundary. No production source URL is requested.
    os.environ['PGUSER']='chess_loader'
    os.environ['PGPASSWORD']=os.environ['LOADER_PASSWORD']
    root=Path('/data/ci-synthetic')
    root.mkdir(parents=True,exist_ok=True)
    for month,game_id in [('2020-01','cifixt01'),('2020-02','cifixt02')]:
        content=f'''[Event "Synthetic CI restore fixture"]
[Site "https://lichess.org/{game_id}"]
[UTCDate "{month.replace('-','.').replace('2020','2020')}.15"]
[White "SyntheticAlice"]
[Black "SyntheticBob"]
[Result "1-0"]
[TimeControl "300+0"]

1. e4 e5 1-0
'''
        archive=root/(month+'.pgn.zst')
        archive.write_bytes(zstd.ZstdCompressor().compress(content.encode()))
        settings=Settings(Source(month,sha256_file(archive),1,1000000,'synthetic-fixture-no-network'),
                          data_root=Path('/data'),min_free_disk_bytes=0)
        run('python',settings,Path('/reports/ci-synthetic'),group='ci-synthetic',archive=archive)
    event('synthetic_restore_fixture_ready',months=['2020-01','2020-02'])


if __name__=='__main__':
    main()
