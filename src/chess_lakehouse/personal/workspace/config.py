"""Resource bounds limit concurrent work, not the size or date span of a library."""
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Config:
    max_library_games: int = 0
    max_storage_bytes: int = 0
    batch_games: int = 100
    max_record_bytes: int = 8*1024*1024
    max_game_plies: int = 2000
    min_free_disk_bytes: int = 256*1024*1024
    engine_nodes: int = 20000
    engine_hash_mb: int = 32
    engine_game_seconds: int = 300
    minimum_opening_games: int = 5
    engine_queue_limit: int = 10

    def __post_init__(self):
        for name,maximum in (('batch_games',1000),('max_record_bytes',32*1024*1024),('max_game_plies',5000),
                ('engine_nodes',100000),('engine_hash_mb',64),('engine_game_seconds',300),('engine_queue_limit',100)):
            if not 1<=getattr(self,name)<=maximum:raise ValueError(name+' exceeds supported per-task resource bounds')
        if self.max_library_games<0 or self.max_storage_bytes<0 or self.min_free_disk_bytes<0:raise ValueError('Storage quotas cannot be negative')


def load():
    values={}
    for name,field in Config.__dataclass_fields__.items():
        value=int(os.environ.get('STUDY_'+name.upper(),field.default))
        if value<0 or (name not in ('max_library_games','max_storage_bytes','min_free_disk_bytes') and value==0):
            raise ValueError('invalid workspace resource setting: '+name)
        values[name]=value
    return Config(**values)
