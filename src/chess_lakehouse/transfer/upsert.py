"""Resumable, source-wins data merges into pre-provisioned compatible tables.

Logged staging tables retain the complete source snapshot for verification.
Primary keys identify rows; no table is dropped, truncated or schema-rewritten.
Nullable foreign keys can be patched after insertion to break dependency cycles.
"""
import hashlib
import json
import shutil
from datetime import datetime, timezone

from psycopg import sql

from . import archive, inventory, state


def catalog(conn):
    """Describe physical tables, ordered keys, ancestry and foreign-key dependencies."""
    tables = inventory.relations(conn)
    tables = {oid: value for oid, value in tables.items() if value['kind']=='r'}
    ancestry = conn.execute('''WITH RECURSIVE parents(child,parent) AS (
        SELECT inhrelid,inhparent FROM pg_inherits UNION
        SELECT p.child,i.inhparent FROM parents p JOIN pg_inherits i ON i.inhrelid=p.parent)
        SELECT child::text,parent::text FROM parents''').fetchall()
    for oid, table in tables.items():
        table['ancestors'] = [oid]+sorted(parent for child,parent in ancestry if child==oid)
        table['column_contract'] = [dict(zip(('name','type','not_null','identity','generated','expression','collation','enum'),row))
            for row in conn.execute('''SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
                a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid),
                CASE WHEN a.attcollation=0 THEN NULL ELSE a.attcollation::regcollation::text END,
                ARRAY(SELECT enumlabel FROM pg_enum WHERE enumtypid=a.atttypid ORDER BY enumsortorder)
                FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum''',(int(oid),))]
        table['constraints'] = sorted(row[0] for row in conn.execute(
            'SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s',(int(oid),)))
        keys = conn.execute('''SELECT a.attname,i.indimmediate FROM pg_index i
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,position)
            JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
            WHERE i.indrelid=%s AND i.indisprimary AND k.position<=i.indnkeyatts ORDER BY k.position''',(int(oid),)).fetchall()
        table['keys'] = [row[0] for row in keys]
        table['immediate_key'] = all(row[1] for row in keys)
        table['partition'] = conn.execute('SELECT pg_get_expr(relpartbound,oid) FROM pg_class WHERE oid=%s',(int(oid),)).fetchone()[0]
        table['parents'] = sorted(row[0] for row in conn.execute('''SELECT format('%%I.%%I',n.nspname,c.relname)
            FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhparent JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE i.inhrelid=%s''',(int(oid),)))
        hooks=conn.execute('''SELECT n.nspname,p.proname,p.prosrc,t.tgtype::int,p.prosecdef,l.lanname
            FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace n ON n.oid=p.pronamespace
            JOIN pg_language l ON l.oid=p.prolang
            WHERE t.tgrelid=ANY(%s::oid[]) AND NOT t.tgisinternal AND t.tgenabled<>'D' ''',
            (list(map(int,table['ancestors'])),)).fetchall()
        guards=[namespace=='study' and name=='protect_published' and
                ' '.join(body.split())=="BEGIN RAISE EXCEPTION 'published study data is immutable'; END" and
                kind==27 and not security and language=='plpgsql'
                for namespace,name,body,kind,security,language in hooks]
        table['immutable_guard']=any(guards)
        table['unsafe_hooks']=any(not guard for guard in guards) or conn.execute(
            'SELECT EXISTS(SELECT 1 FROM pg_rewrite WHERE ev_class=ANY(%s::oid[]))',
            (list(map(int,table['ancestors'])),)).fetchone()[0]
        # User-defined composite/domain behavior needs its own schema contract.
        table['unsupported_types'] = conn.execute('''SELECT EXISTS(SELECT 1 FROM pg_attribute a
            JOIN pg_type t ON t.oid=a.atttypid WHERE a.attrelid=%s AND a.attnum>0
            AND NOT a.attisdropped AND t.typtype NOT IN ('b','e'))''',(int(oid),)).fetchone()[0]
        table['unique_keys'] = sorted([dict(columns=row[0],nulls_equal=row[1]) for row in conn.execute('''
            SELECT ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,position)
              JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
              WHERE k.position<=i.indnkeyatts ORDER BY k.position),
              coalesce((to_jsonb(i)->>'indnullsnotdistinct')::boolean,false)
            FROM pg_index i WHERE i.indrelid=%s AND i.indisunique AND NOT i.indisprimary
              AND i.indexprs IS NULL AND i.indpred IS NULL''',(int(oid),))],key=inventory.stable_json)
        table['unsafe_unique'] = conn.execute('''SELECT EXISTS(SELECT 1 FROM pg_index WHERE indrelid=%s
            AND indisunique AND (indexprs IS NOT NULL OR indpred IS NOT NULL))''',(int(oid),)).fetchone()[0]
    foreign = [dict(zip(('child','parent','columns','match'),row)) for row in conn.execute('''SELECT
        c.conrelid::text,c.confrelid::text,
        ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(attnum,position)
              JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum ORDER BY k.position),c.confmatchtype
        FROM pg_constraint c WHERE c.contype='f' ORDER BY c.oid''')]
    for oid,table in tables.items():
        table['immutable_self_reference']=False
        if not table['immutable_guard']: continue
        for fk in foreign:
            if fk['child'] not in table['ancestors'] or fk['parent'] not in table['ancestors']: continue
            populated=conn.execute(sql.SQL('SELECT EXISTS(SELECT 1 FROM ONLY {} WHERE {})').format(
                _table(table),sql.SQL(' AND ').join(sql.SQL('{} IS NOT NULL').format(sql.Identifier(key)) for key in fk['columns']))).fetchone()[0]
            table['immutable_self_reference'] |= populated
    return dict(tables=tables, foreign=foreign)


def preflight(source, conn):
    """Require compatible keyed tables and derive an enforceable FK insertion order."""
    target = catalog(conn)
    by_name = {(t['schema'],t['name']):t for t in target['tables'].values()}
    dependencies = {oid:set() for oid in source['tables']}
    deferred = {oid:set() for oid in source['tables']}
    contract = ('column_contract','constraints','keys','partition','parents','unique_keys','immutable_guard')
    for oid, table in source['tables'].items():
        name = table['schema']+'.'+table['name']
        other = by_name.get((table['schema'],table['name']))
        if other is None:
            raise ValueError('Missing destination table '+name+'; apply versioned schema migrations/partitions before upsert')
        if not table['keys'] or not table['immediate_key'] or not other['immediate_key']:
            raise ValueError('Upsert requires a non-deferrable primary key: '+name)
        if any(table[key]!=other[key] for key in contract):
            raise ValueError('Incompatible destination table definition: '+name)
        if table['persistence']!='p' or other['persistence']!='p':
            raise ValueError('Upsert requires logged tables for durable checkpoints: '+name)
        if table['unsafe_hooks'] or other['unsafe_hooks'] or table['unsupported_types'] or table['unsafe_unique'] or other['unsafe_unique']:
            raise ValueError('Upsert requires tables without user triggers/rules, domain/composite types or expression/partial unique indexes: '+name)
        if table['immutable_self_reference']:
            raise ValueError('Immutable self-referencing history requires a fresh restore: '+name)
        generated_keys = set(table['keys'])-set(table['columns'])
        if generated_keys: raise ValueError('Generated-expression primary keys are unsupported: '+name)
        for fk in source['foreign']:
            if fk['child'] not in table['ancestors']: continue
            parents = {key for key,value in source['tables'].items() if fk['parent'] in value['ancestors']}
            if not parents: continue
            nullable = {c['name'] for c in table['column_contract'] if not c['not_null'] and c['name'] not in table['keys'] and not c['generated']}
            candidates = set(fk['columns']) & nullable
            # MATCH SIMPLE skips a composite FK when any component is null.
            if table['immutable_guard'] and candidates:
                # Empty/null-parent legacy history needs no self dependency; guards stay enabled.
                dependencies[oid].update(parents-{oid})
            elif candidates and (fk['match']=='s' or set(fk['columns'])<=nullable):
                deferred[oid].update(candidates if fk['match']=='s' else fk['columns'])
            else:
                dependencies[oid].update(parents)
    order=[]
    while remaining:=set(dependencies)-set(order):
        ready=sorted(key for key in remaining if not (dependencies[key]-set(order)))
        if not ready: raise ValueError('Non-nullable foreign-key cycle prevents chunked upsert; use a fresh restore or an application-specific merge')
        order.extend(ready)
    sequences = owned_sequences(conn, [by_name[(t['schema'],t['name'])] for t in source['tables'].values()])
    if any(s['increment']<=0 or s['cycle'] for s in sequences):
        raise ValueError('Upsert requires ascending, non-cycling owned sequences')
    return dict(order=order, deferred={key:sorted(value) for key,value in deferred.items()},
                tables=len(order), conflict_policy='source-wins', destination_only_rows='preserve')


def owned_sequences(conn, tables):
    """Find serial/identity generators owned by selected tables or partition ancestors."""
    owners=sorted({int(oid) for table in tables for oid in table['ancestors']})
    return [dict(zip(('schema','name','table_schema','table','column','increment','cycle'),row)) for row in conn.execute('''
        SELECT sn.nspname,s.relname,tn.nspname,t.relname,a.attname,q.seqincrement,q.seqcycle
        FROM pg_class s JOIN pg_sequence q ON q.seqrelid=s.oid
        JOIN pg_namespace sn ON sn.oid=s.relnamespace JOIN pg_depend d ON d.objid=s.oid
          AND d.classid='pg_class'::regclass AND d.refclassid='pg_class'::regclass AND d.deptype IN ('a','i')
        JOIN pg_class t ON t.oid=d.refobjid JOIN pg_namespace tn ON tn.oid=t.relnamespace
        JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid
        WHERE d.refobjid=ANY(%s::oid[]) ORDER BY 1,2''',(owners,))]


def _stage(job, oid):
    return sql.Identifier(state.SCHEMA, 'stage_'+job.replace('-','')+'_'+oid)


def _table(table):
    return sql.Identifier(table['schema'],table['name'])


def _join(keys, left='t', right='s'):
    return sql.SQL(' AND ').join(sql.SQL('{}.{}={}.{}').format(sql.Identifier(left),sql.Identifier(key),
        sql.Identifier(right),sql.Identifier(key)) for key in keys)


def _digest(conn, query):
    total=rows=0
    with conn.cursor(name='upsert_digest') as cursor:
        cursor.itersize=500;cursor.execute(query)
        for row in cursor:
            total=(total+int.from_bytes(bytes(row[0]),'big')) % (1<<256);rows+=1
    return dict(rows=rows,sha256_sum=f'{total:064x}')


def _hash_rows(from_clause):
    return sql.SQL("SELECT sha256(convert_to(row_to_json(t)::text,'UTF8')) FROM {}").format(from_clause)


def _unit(conn, job, unit, data, rows, action, inject=None):
    checksum=hashlib.sha256(data).hexdigest()
    previous=state.lookup(conn,job,unit)
    if previous:
        if previous!={'checksum':checksum,'rows':rows}: raise ValueError('Upsert checkpoint payload changed')
        return False
    with conn.transaction():
        action()
        if inject: inject('before_commit',unit)
        state.mark(conn,job,unit,checksum,rows)
    if inject: inject('after_commit',unit)
    return True


def stage_chunk(conn, job, table, number, data, rows, inject=None):
    """Commit source rows to durable staging together with their resume marker."""
    columns=sql.SQL(',').join(map(sql.Identifier,table['columns']))
    def action():
        with conn.cursor().copy(sql.SQL('COPY {} ({}) FROM STDIN').format(_stage(job,str(table['oid'])),columns)) as copy:
            copy.write(data)
    return _unit(conn,job,f"stage:{table['oid']}:{number}",data,rows,action,inject)


def merge_chunk(conn, job, table, deferred, number, data, rows, patch=False, inject=None):
    """Upsert one chunk or complete its postponed nullable foreign-key values."""
    columns=table['columns'];keys=table['keys'];identifier=_table(table)
    def action():
        conn.execute(sql.SQL('CREATE TEMP TABLE upsert_chunk (LIKE {} INCLUDING GENERATED) ON COMMIT DROP').format(identifier))
        with conn.cursor().copy(sql.SQL('COPY upsert_chunk ({}) FROM STDIN').format(sql.SQL(',').join(map(sql.Identifier,columns)))) as copy:
            copy.write(data)
        if patch:
            updates=sql.SQL(',').join(sql.SQL('{}=s.{}').format(sql.Identifier(key),sql.Identifier(key)) for key in deferred)
            conn.execute(sql.SQL('UPDATE {} AS t SET {} FROM upsert_chunk AS s WHERE {}').format(identifier,updates,_join(keys)))
            return
        select=sql.SQL(',').join(sql.SQL('NULL') if key in deferred else sql.Identifier(key) for key in columns)
        updated=[key for key in columns if key not in keys and key not in deferred]
        conflict=sql.SQL('DO NOTHING')
        if updated:
            assignments=sql.SQL(',').join(sql.SQL('{}=EXCLUDED.{}').format(sql.Identifier(key),sql.Identifier(key)) for key in updated)
            distinct=sql.SQL(' OR ').join(sql.SQL('t.{} IS DISTINCT FROM EXCLUDED.{}').format(sql.Identifier(key),sql.Identifier(key)) for key in updated)
            conflict=sql.SQL('DO UPDATE SET {} WHERE {}').format(assignments,distinct)
        conn.execute(sql.SQL('INSERT INTO {} AS t ({}) OVERRIDING SYSTEM VALUE SELECT {} FROM upsert_chunk WHERE true '
            'ON CONFLICT ({}) {}').format(identifier,sql.SQL(',').join(map(sql.Identifier,columns)),select,
            sql.SQL(',').join(map(sql.Identifier,keys)),conflict))
    phase='patch' if patch else 'merge'
    return _unit(conn,job,f"{phase}:{table['oid']}:{number}",data,rows,action,inject)


def baseline(conn, target, options, root, job, tables, metadata):
    """Bind expected source-plus-destination-only contents before any target row changes."""
    path=root/'upsert-baseline.json';previous=state.lookup(conn,job,'upsert-baseline')
    if previous:
        if not path.is_file() or state.digest(path)!=previous['checksum']:
            raise ValueError('Original upsert baseline is missing or changed; restore its file before resuming')
        return json.loads(path.read_text())
    expected={}
    with conn.transaction():
        for oid,table in tables.items():
            if table['immutable_guard']:
                different=conn.execute(sql.SQL("SELECT EXISTS(SELECT 1 FROM ONLY {} AS t JOIN {} AS s ON {} "
                    "WHERE row_to_json(t)::text IS DISTINCT FROM row_to_json(s)::text)").format(
                    _table(table),_stage(job,oid),_join(table['keys']))).fetchone()[0]
                if different: raise ValueError('Source would update immutable published history: '+table['schema']+'.'+table['name'])
            for unique in table['unique_keys']:
                operator=sql.SQL('IS NOT DISTINCT FROM') if unique['nulls_equal'] else sql.SQL('=')
                match=sql.SQL(' AND ').join(sql.SQL('t.{} {} s.{}').format(sql.Identifier(key),operator,sql.Identifier(key)) for key in unique['columns'])
                conflict=conn.execute(sql.SQL('SELECT EXISTS(SELECT 1 FROM ONLY {} AS t JOIN {} AS s ON {} WHERE NOT ({}))').format(
                    _table(table),_stage(job,oid),match,_join(table['keys']))).fetchone()[0]
                if conflict: raise ValueError('Different primary keys share a unique identity in '+table['schema']+'.'+table['name']+'; reconcile identity mapping before upsert')
            staged=_digest(conn,_hash_rows(sql.SQL('{} AS t').format(_stage(job,oid))))
            if staged!=metadata['fingerprints'][oid]: raise ValueError('Staged source contents do not match the archive')
            extra=_digest(conn,_hash_rows(sql.SQL('ONLY {} AS t WHERE NOT EXISTS (SELECT 1 FROM {} AS s WHERE {})').format(
                _table(table),_stage(job,oid),_join(table['keys']))))
            expected[oid]=dict(rows=staged['rows']+extra['rows'],
                sha256_sum=f"{(int(staged['sha256_sum'],16)+int(extra['sha256_sum'],16)) % (1<<256):064x}",
                destination_only_rows=extra['rows'])
        report=dict(tables=expected,schema_sha256=archive.schema_digest(archive.schema_sql(target,options)),
                    identity=inventory.identity(conn),database_options=inventory.database_options(conn))
        state.atomic_json(path,report)
        state.mark(conn,job,'upsert-baseline',state.digest(path))
    return report


def advance_sequences(conn, job, tables, metadata):
    """Move owned identity/serial sequences forward past both libraries' retained IDs."""
    unit='upsert-sequences'
    if state.lookup(conn,job,unit): return
    source={(s['schema'],s['name']):s['last_value'] for s in metadata['sequences']}
    with conn.transaction():
        target_by_name={(t['schema'],t['name']):t for t in catalog(conn)['tables'].values()}
        selected=[target_by_name[(t['schema'],t['name'])] for t in tables.values()]
        for sequence in owned_sequences(conn,selected):
            identifier=sql.Identifier(sequence['schema'],sequence['name'])
            current=conn.execute(sql.SQL('SELECT last_value FROM {}').format(identifier)).fetchone()[0]
            maximum=conn.execute(sql.SQL('SELECT max({}) FROM {}').format(sql.Identifier(sequence['column']),
                sql.Identifier(sequence['table_schema'],sequence['table']))).fetchone()[0]
            value=max(current,maximum if maximum is not None else current,source.get((sequence['schema'],sequence['name']),current))
            conn.execute('SELECT setval(%s::regclass,%s,true)',(identifier.as_string(conn),value))
        state.mark(conn,job,unit,'forward-only-v1')


def verify(conn, target, options, job, tables, expected):
    """Check the entire merged table, including destination-only rows and source stages."""
    with conn.transaction():
        for oid,table in tables.items():
            actual=inventory.rows_digest(conn,table)
            wanted={k:v for k,v in expected['tables'][oid].items() if k!='destination_only_rows'}
            if actual!=wanted: raise ValueError('Upsert verification failed: '+table['schema']+'.'+table['name'])
        if inventory.identity(conn)!=expected['identity'] or inventory.database_options(conn)!=expected['database_options']:
            raise ValueError('Destination database metadata changed during upsert')
    if archive.schema_digest(archive.schema_sql(target,options))!=expected['schema_sha256']:
        raise ValueError('Destination schema changed during upsert')


def run(source, target, options, root, metadata, manifest_hash, tracker, inject=None):
    """Stage, merge, patch and verify a resumable data-only source-wins migration."""
    from .service import source_unchanged
    source_catalog=metadata['upsert_catalog'];tables=source_catalog['tables'];job=tracker['job_id']
    with target.connect(autocommit=True) as conn:
        checked=preflight(source_catalog,conn)
        state.bootstrap(conn,job,options.fingerprint(),manifest_hash,allow_existing=True)
        conn.execute("SELECT set_config('lock_timeout',%s,false)",(str(options.lock_timeout_seconds*1000),))
        def progress(phase, changed=False):
            tracker['phase']=phase
            if changed: tracker['units_committed']=tracker.get('units_committed',0)+1
            state.atomic_json(root/'tracker.json',tracker)
        tracker.pop('completed',None)
        tracker['units_committed']=conn.execute(f'SELECT count(*) FROM {state.SCHEMA}.units WHERE job_id=%s',(job,)).fetchone()[0]
        entries={entry['oid']:entry for entry in metadata['data_order'] if entry['table_data']}
        if set(entries)!=set(tables): raise ValueError('Archive table data does not match the upsert catalog')
        for oid,table in tables.items():
            unit='stage-table:'+oid
            if not state.lookup(conn,job,unit):
                with conn.transaction():
                    conn.execute(sql.SQL('CREATE TABLE {} (LIKE {} INCLUDING GENERATED)').format(_stage(job,oid),_table(table)))
                    conn.execute(sql.SQL('ALTER TABLE {} ADD PRIMARY KEY ({})').format(_stage(job,oid),sql.SQL(',').join(map(sql.Identifier,table['keys']))))
                    state.mark(conn,job,unit,'v1')
            for number,data,rows in archive.chunks(root,entries[oid],options):
                if shutil.disk_usage(root).free<options.min_free_bytes: raise OSError('Migration disk reserve reached')
                progress('staging',stage_chunk(conn,job,table,number,data,rows,inject))
        # Always recheck durable stages: a commit marker alone cannot prove retained contents.
        with conn.transaction():
            for oid,table in tables.items():
                if _digest(conn,_hash_rows(sql.SQL('{} AS t').format(_stage(job,oid))))!=metadata['fingerprints'][oid]:
                    raise ValueError('Staged source contents changed; restore the original staging data')
        expected=baseline(conn,target,options,root,job,tables,metadata)
        for oid in checked['order']:
            for number,data,rows in archive.chunks(root,entries[oid],options):
                progress('upserting',merge_chunk(conn,job,tables[oid],checked['deferred'][oid],number,data,rows,inject=inject))
        for oid in checked['order']:
            if not checked['deferred'][oid]: continue
            for number,data,rows in archive.chunks(root,entries[oid],options):
                progress('patching-foreign-keys',merge_chunk(conn,job,tables[oid],checked['deferred'][oid],number,data,rows,patch=True,inject=inject))
        advance_sequences(conn,job,tables,metadata)
        progress('validating')
        verify(conn,target,options,job,tables,expected)
        source_unchanged(source,metadata,options)
        report=dict(status='complete',mode='upsert',conflict_policy='source-wins',tables_verified=len(tables),
            source_rows=sum(metadata['fingerprints'][oid]['rows'] for oid in tables),
            destination_only_rows_preserved=sum(t['destination_only_rows'] for t in expected['tables'].values()),
            rows_verified=sum(t['rows'] for t in expected['tables'].values()),
            units_committed=conn.execute(f'SELECT count(*) FROM {state.SCHEMA}.units WHERE job_id=%s',(job,)).fetchone()[0],
            finished_at=datetime.now(timezone.utc).isoformat())
        with conn.transaction(): state.finish(conn,job,report)
        tracker.update(phase='complete',report=report,units_committed=report['units_committed'])
        state.atomic_json(root/'tracker.json',tracker)
        return report
