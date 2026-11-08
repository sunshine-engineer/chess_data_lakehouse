"""Explicit retention and abandonment; published months are always protected."""
from contextlib import suppress
from pathlib import Path
import shutil

from .storage import file_lock
from .warehouse import connect, stage_for
from .workflow import event


def cleanup(settings, retain_days=7, apply=False):
    """Plan or apply bounded retention of eligible historical run artifacts."""
    if retain_days<1:
        raise ValueError('retain_days must be positive')
    with connect(settings) as conn:
        candidates = conn.execute("""SELECT r.run_id FROM control.pipeline_runs r
            WHERE r.status='complete' AND r.finished_at < now() - (%s * interval '1 day')
            AND NOT EXISTS(SELECT 1 FROM control.month_publications p WHERE p.run_id=r.run_id)
            ORDER BY r.finished_at LIMIT 500""", (retain_days,)).fetchall()
    removed=[]
    base=(settings.data_root/'runs-v2').resolve()
    for (run_id,) in candidates:
        stage_for(run_id)
        root=base/run_id
        if root.is_symlink() or (root.exists() and root.resolve().parent!=base):
            raise ValueError('unsafe artifact retention path')
        if apply and root.exists():
            with file_lock(root/'.run.lock',blocking=False):
                # Recheck the database pointer immediately before deleting files.
                with connect(settings) as conn:
                    if conn.execute('SELECT 1 FROM control.month_publications WHERE run_id=%s',(run_id,)).fetchone():
                        continue
                shutil.rmtree(root)
        removed.append(run_id)
    event('retention_applied' if apply else 'retention_dry_run',runs=removed,retain_days=retain_days)
    return removed


def abandon(run_id,settings):
    """Mark a selected unfinished run abandoned under the maintenance policy."""
    stage_for(run_id)
    root=settings.data_root/'runs-v2'/run_id
    with file_lock(root/'.run.lock',blocking=False),connect(settings) as conn:
        row=conn.execute('SELECT engine,status FROM control.pipeline_runs WHERE run_id=%s FOR UPDATE',(run_id,)).fetchone()
        if not row or row[1]=='complete':
            raise ValueError('only an unpublished run can be abandoned')
        if not conn.execute('SELECT pg_try_advisory_xact_lock(hashtext(%s))',('chess-v2-'+row[0],)).fetchone()[0]:
            raise ValueError('cannot abandon an active engine')
        conn.execute('SELECT control.drop_stage(%s)',(run_id,))
        conn.execute('DELETE FROM control.run_shards WHERE run_id=%s',(run_id,))
        conn.execute("UPDATE control.pipeline_runs SET status='abandoned',finished_at=now() WHERE run_id=%s",(run_id,))
    shutil.rmtree(root,ignore_errors=True)
    event('run_abandoned',run_id=run_id)
