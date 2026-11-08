"""Read-only checksums for portable artifact volumes, excluding transient locks.

Run inventory after application writers stop, copy files at the same container
mount paths, then verify before starting destination workers. This does not
include PostgreSQL files: transfer the database with chess_lakehouse.transfer.
"""
import argparse
import hashlib
import json
from pathlib import Path
import stat

ROOTS = {"study": Path("/study"), "pipeline": Path("/data"), "models": Path("/models"), "reports": Path("/reports")}


def inventory(roots=None):
    """Hash regular files with bounded memory and reject links or device files."""
    result={}
    for label,root in (roots or ROOTS).items():
        if not root.is_dir() or root.is_symlink(): raise ValueError("Missing or unsafe volume: "+label)
        for path in sorted(root.rglob('*')):
            mode=path.lstat().st_mode
            if stat.S_ISLNK(mode): raise ValueError("Artifact volume contains a symlink")
            if stat.S_ISDIR(mode): continue
            if not stat.S_ISREG(mode): raise ValueError("Artifact volume contains a non-regular file")
            # flock files carry no durable state; the destination worker recreates them.
            if path.name.endswith('.lock'): continue
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
            result[label+'/'+str(path.relative_to(root))]={'bytes':path.stat().st_size,'sha256':digest.hexdigest()}
    return result


def main():
    """Print a file manifest or compare all mounted files against an existing one."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('inventory','verify'))
    parser.add_argument('--manifest',type=Path)
    args=parser.parse_args();actual=inventory()
    if args.action=='inventory':print(json.dumps(actual,sort_keys=True,indent=2));return
    if not args.manifest:parser.error('verify requires --manifest')
    if actual!=json.loads(args.manifest.read_text()):parser.exit(1,'Volume checksum verification failed\n')
    print(json.dumps({'status':'passed','files_verified':len(actual)}))


if __name__=='__main__':main()
