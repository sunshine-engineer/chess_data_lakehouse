"""Small CLI probes for containers and Kubernetes init/readiness checks."""
import argparse
import time
from urllib.request import urlopen

from .runtime import load,postgres_options


def check(role):
    """Check a named database schema or the foreground worker's readiness endpoint."""
    if role=='worker':
        with urlopen(f'http://127.0.0.1:{load().server.worker_port}/readyz',timeout=4) as response:
            if response.status!=200:raise ValueError('Worker is not ready')
        return
    import psycopg
    with psycopg.connect('',connect_timeout=4,options='-c statement_timeout=4000',**postgres_options()) as conn:
        if role=='database':conn.execute('SELECT 1')
        elif role=='study':conn.execute('SELECT 1 FROM study.accounts LIMIT 1')
        elif role=='archive':conn.execute('SELECT 1 FROM study.archive_jobs LIMIT 1')
        else:raise ValueError('Unknown health role')


def main():
    """Optionally wait for a schema bootstrap without printing connection secrets."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role',choices=('database','study','archive','worker'))
    parser.add_argument('--wait-seconds',type=int,default=0)
    args=parser.parse_args();deadline=time.monotonic()+args.wait_seconds
    while True:
        try:check(args.role);return
        except Exception:
            if time.monotonic()>=deadline:parser.exit(1,'Dependency is not ready\n')
            time.sleep(min(2,max(0,deadline-time.monotonic())))


if __name__=='__main__':main()
