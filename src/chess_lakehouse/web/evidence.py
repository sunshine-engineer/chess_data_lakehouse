"""Reject incomplete/non-finite benchmark publications before rendering results."""
import json
import math
from pathlib import Path
from .data import ENGINES

class EvidenceError(ValueError):pass


def benchmark(root):
    """Resolve measured benchmark evidence for the explicitly selected workload."""
    try:
        report=json.loads((Path(root)/'comparison-v2.json').read_text())
        if set(report['summary'])!=set(ENGINES) or not report['equality']:raise ValueError()
        if not all(isinstance(value,bool) for value in report['equality'].values()):raise ValueError()
        if not isinstance(report['benchmark_group'],str) or not isinstance(report['measurement'],str):raise ValueError()
        if not report['histories'] or any(len(row)!=2 for row in report['histories']):raise ValueError()
        for values in report['summary'].values():
            for key in ('samples','median_elapsed_seconds','median_input_games_per_second','median_container_peak_memory_mib',
                    'median_container_cpu_seconds','median_parquet_bytes'):
                value=values[key]
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ValueError()
            if values['samples']<1:raise ValueError()
        return report
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise EvidenceError('The recorded benchmark could not be verified. Refresh or read the comparison method.') from exc
