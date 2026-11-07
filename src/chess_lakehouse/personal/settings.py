"""One explicit personal player-month approval and bounded resource contract."""
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import tomllib


@dataclass(frozen=True)
class Settings:
    player: str = 'sunny2g'
    month: str = '2026-10'
    max_games: int = 500
    max_upload_bytes: int = 10*1024*1024
    max_game_plies: int = 600
    max_total_plies: int = 100000
    max_versions: int = 20
    max_cohorts: int = 50
    max_analyzed_games: int = 3
    engine_nodes: int = 20000
    engine_hash_mb: int = 32
    engine_game_seconds: int = 300
    minimum_opening_games: int = 5
    synthetic_fixture: bool = False

    def __post_init__(self):
        if not isinstance(self.synthetic_fixture,bool):
            raise ValueError('synthetic_fixture must be a boolean')
        if not re.fullmatch(r'[A-Za-z0-9_-]{2,30}',self.player):
            raise ValueError('invalid approved player name')
        if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',self.month):
            raise ValueError('approved month must be YYYY-MM')
        for name,maximum in (('max_games',500),('max_upload_bytes',10*1024*1024),('max_game_plies',600),
                ('max_total_plies',100000),('max_versions',20),('max_cohorts',50),
                ('max_analyzed_games',3),('engine_nodes',100000),('engine_hash_mb',64),
                ('engine_game_seconds',300),('minimum_opening_games',20)):
            if not 1<=getattr(self,name)<=maximum:
                raise ValueError(f'{name} exceeds supported personal-study bounds')

    @property
    def normalized_player(self):
        return self.player.casefold()

    @property
    def month_date(self):
        return date.fromisoformat(self.month+'-01')


def load(path='config/study.toml'):
    return Settings(**tomllib.loads(Path(path).read_text()))
