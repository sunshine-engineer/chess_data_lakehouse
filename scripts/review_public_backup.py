"""Review conservative exclusions and archive confirmed public-only artifacts.
Run in Docker with the repository at /workspace and backup workdir at /receipt.
"""
import importlib.util,json,shutil,sqlite3
from pathlib import Path
spec=importlib.util.spec_from_file_location('backup','/workspace/scripts/device_backup.py')
backup=importlib.util.module_from_spec(spec);spec.loader.exec_module(backup)
root=Path('/workspace');out=Path('/receipt');stage=out/'reviewed-public';stage.mkdir(exist_ok=True)
excluded=json.loads((out/'excluded-files.private.json').read_text())['workspace']
secrets=backup.replacements();counts={};selected=[]
for entry in excluded:
    name=entry['path'];path=root/name
    if entry['reason']=='unreviewed-private-diagnostic-or-screenshot' and path.suffix=='.json':
        data=backup.sanitize(name,path.read_bytes(),secrets)
        value=json.loads(data)
        # These reports describe public archive runs or clearly synthetic CI runs.
        if not (name.startswith(('reports/python_','reports/python_mp_','reports/spark_','reports/scale/','reports/ci-synthetic/','reports/expanded-import/'))):
            continue
        text=data.decode().lower()
        if any(x in text for x in ('sunny2g','game_revisions','password','api_token','private_key','study.accounts')):
            raise ValueError('Public benchmark report needs additional review: '+name)
        dest=stage/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data);selected.append(name)
    elif name.endswith('/certifi/cacert.pem'):
        data=path.read_bytes()
        if b'PRIVATE KEY' in data or b'BEGIN CERTIFICATE' not in data:raise ValueError('Unexpected CA bundle')
        dest=stage/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data);selected.append(name)
    elif name.endswith('/tornado/test/test.key'):
        dest=stage/name;dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text('DUMMY_TEST_PRIVATE_KEY_REGENERATE_IF_NEEDED\n');selected.append(name)
    elif name.endswith('warehouse.sqlite'):
        conn=sqlite3.connect('file:'+str(path)+'?mode=ro&immutable=1',uri=True)
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != {'dim_date','dim_opening','dim_player','dim_time_control','fact_game','rejected_game'}:raise ValueError('Unexpected database objects')
        months={r[0] for r in conn.execute('SELECT DISTINCT source_month FROM fact_game')}
        if months != {'2013-01'}:raise ValueError('Non-public sample month')
        counts[name]=conn.execute('SELECT count(*) FROM fact_game').fetchone()[0]
        dest=stage/name;dest.parent.mkdir(parents=True,exist_ok=True)
        conn.execute('VACUUM INTO ?', (str(dest),));conn.close();selected.append(name)
manifest,omitted=backup.archive('workspace-supplement',stage,out/'assets',secrets)
if omitted:raise ValueError('Unexpected supplement exclusions')
coverage=json.loads((out/'assets/PUBLIC_BACKUP_COVERAGE.json').read_text())
coverage['archives'].append({k:v for k,v in manifest.items() if k!='files'})
coverage['reviewed_supplement_files']=len(selected)
coverage['public_sqlite_fact_counts']=counts
coverage['supplement_note']='Reviewed public benchmark JSON, CA bundle, dummy vendor test key, and vacuumed January-2013 public-only SQLite databases. Restore separately then merge absent paths.'
backup.write_json(out/'assets/PUBLIC_BACKUP_COVERAGE.json',coverage)
backup.write_json(out/'supplement-review.json',{'files':selected,'public_sqlite_fact_counts':counts,'status':'passed'})
assets=out/'assets'
(assets/'SHA256SUMS.txt').write_text(''.join(backup.sha(p.read_bytes())+'  '+p.name+'\n' for p in sorted(assets.iterdir()) if p.is_file() and p.name!='SHA256SUMS.txt'))
print(json.dumps({'supplement_files':len(selected),'archive_bytes':manifest['bytes'],'public_sqlite_fact_counts':counts}))
