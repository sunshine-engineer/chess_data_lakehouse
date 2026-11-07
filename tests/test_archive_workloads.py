"""Explicit source bounds and honest sequential-workload measurements."""
from chess_lakehouse.optimized.settings import load
from chess_lakehouse.optimized.comparison import workload_rounds
import pytest


def test_opt_in_sources_and_default_pause():
    assert load().source.month=='2013-01'
    with pytest.raises(ValueError):load(month='2013-02')
    selected=[load('config/archive-demo.toml',m).source for m in ('2013-01','2013-02','2013-03')]
    assert sum(s.expected_games for s in selected)==403928
    assert len({s.sha256 for s in selected})==3
    with pytest.raises(ValueError):load('config/archive-demo.toml','2013-04')


def test_sequential_totals_use_summed_times_maximum_peak_and_missing_metrics():
    def row(month,time,memory):return dict(source_month=month,elapsed_seconds=time,input_games=100,
        container_cpu_seconds=time-1,container_peak_memory_mib=memory,parquet_bytes=1024,model_bytes=1000)
    runs=[row('2013-01',10,100),row('2013-02',20,400),row('2013-01',30,300),row('2013-02',40,None)]
    a,b=workload_rounds(runs,['2013-01','2013-02'],2)
    assert (a['elapsed_seconds'],a['container_cpu_seconds'],a['parquet_bytes'],a['container_peak_memory_mib'])==(30,28,2048,400)
    assert a['input_games_per_second']==200/30
    assert b['elapsed_seconds']==70 and b['container_peak_memory_mib'] is None
