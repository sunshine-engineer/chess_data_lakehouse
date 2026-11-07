"""Bounded CSV creation on temporary disk, with spreadsheet-safe text fields."""
import csv
import hashlib
import io
import tempfile
from psycopg import sql
from . import data


def safe(value):
    if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r','\n')):return "'"+value
    return value


def archive(args):
    limit=data.integer(args.get('export_limit',5000),'export limit',1,10000)
    engine,where,params=data.filters(args);schema=sql.Identifier('warehouse_'+engine)
    stream=tempfile.TemporaryFile();digest=hashlib.sha256();rows=0
    def write(values):
        buffer=io.StringIO(newline='');csv.writer(buffer).writerow([safe(v) for v in values])
        block=buffer.getvalue().encode('utf-8');stream.write(block);digest.update(block)
    try:
        with data.connect() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            total=data.query(c,engine,'SELECT count(*) AS n',where,params)[0]['n']
            with c.cursor(name='archive_csv') as cursor:
                cursor.execute(sql.SQL(data.DETAIL_SELECT+' ORDER BY f.game_id LIMIT %s').format(schema,schema,schema,where),params+[limit])
                columns=[column.name for column in cursor.description];write(columns)
                while batch:=cursor.fetchmany(256):
                    for row in batch:write([row[name] for name in columns]);rows+=1
        size=stream.tell();stream.seek(0)
        return stream,{'rows':rows,'total':total,'truncated':rows<total,'limit':limit,'sha256':digest.hexdigest(),'bytes':size}
    except Exception:
        stream.close();raise
