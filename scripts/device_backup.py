"""Create and verify sanitized device-transfer archives. Run Python in Docker.

Never archives database backups or private study state to the public repository.
A coverage report records the resulting limits; it cannot authorize deletion.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile
import zlib

ROOT = Path('/workspace')
TEXT = {'.py','.md','.txt','.json','.toml','.yaml','.yml','.sql','.sh','.ps1','.ini','.cfg','.html','.js','.css','.ipynb','.log','.list','.example'}
SECRET_KEY = re.compile(r'PASSWORD|TOKEN|SECRET|API_KEY|PRIVATE_KEY', re.I)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def replacements():
    """Read local secrets without logging them; only use them for redaction/checks."""
    result = {}
    for name in ('.env', '.env.cloudsql'):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if '=' not in line or line.lstrip().startswith('#'):
                continue
            key, value = line.split('=', 1)
            value = value.strip().strip('\"\'')
            if len(value) >= 8 and (SECRET_KEY.search(key) or key == 'CLOUDSQL_CONNECTION_NAME'):
                result[value.encode()] = b'DUMMY_REPLACE_ON_NEW_DEVICE' if SECRET_KEY.search(key) else b'your-project:your-region:your-instance'
                if key == 'CLOUDSQL_CONNECTION_NAME':
                    result[value.split(':')[0].encode()] = b'your-project'
    directory = ROOT / 'data/secrets'
    if directory.exists():
        for path in directory.rglob('*'):
            if path.is_file():
                value = path.read_bytes().strip()
                if len(value) >= 8:
                    result[value] = b'DUMMY_REPLACE_ON_NEW_DEVICE'
    return result


def sanitize(path, data, secrets):
    """Redact known live credentials and connection identifiers, retaining files."""
    if path.startswith('data/secrets/'):
        return b'DUMMY_REPLACE_ON_NEW_DEVICE\n'
    for source, dummy in secrets.items():
        data = data.replace(source, dummy)
    if Path(path).suffix.lower() in TEXT or Path(path).name.startswith('.env'):
        data = re.sub(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', b'DUMMY_PRIVATE_KEY_REGENERATE_ON_NEW_DEVICE', data, flags=re.S)
        data = re.sub(rb'(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{30,})', b'DUMMY_TOKEN_REPLACE_ON_NEW_DEVICE', data)
        if Path(path).name.startswith('.env'):
            lines = []
            for line in data.decode('utf-8').splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key, value = line.split('=', 1)
                    if SECRET_KEY.search(key):
                        line = key + '=DUMMY_REPLACE_ON_NEW_DEVICE'
                    elif key == 'GCLOUD_CONFIG_DIR':
                        line = key + '=/absolute/path/to/new-device/gcloud'
                lines.append(line)
            data = ('\n'.join(lines) + '\n').encode()
        if path.endswith('.ipynb'):
            value = json.loads(data)
            for cell in value.get('cells', []):
                if cell.get('cell_type') == 'code':
                    cell['outputs'] = []
                    cell['execution_count'] = None
            data = (json.dumps(value, indent=1) + '\n').encode()
    for value in secrets:
        if value in data:
            raise ValueError('Credential remained after redaction: ' + path)
    return data


def policy(path, tracked):
    """Conservative public-publication boundary, with explicit recovery exclusions."""
    low = path.lower()
    if low.startswith('.git/') or low == '.git':
        return 'git-object-database-recovered-via-branches'
    if low.startswith(('data/device-backup/', 'reports/device-backup/')):
        return 'backup-output-or-generated-evidence'
    if low.startswith('data/secrets/'):
        return None
    if low.startswith(('data/transfers/', 'data/validation/')) or low == 'data/owner-access.json':
        return 'private-migration-or-row-level-validation'
    if low.startswith('data/src/') and 'lichess_db_standard_rated_2013-' not in low:
        return 'personal-game-export'
    if low.endswith(('.dump','.backup','.sqlite','.db','.sqlite3')):
        return 'database-snapshot-not-published'
    if low.startswith('reports/') and path not in tracked:
        return 'unreviewed-private-diagnostic-or-screenshot'
    if low.endswith(('.pem','.p12','.pfx','.key')) or 'id_ed25519' in low or 'id_rsa' in low:
        return 'private-or-device-key'
    return None


def scan(root):
    """Inspect reachable loose Git objects for known live secrets before pushing history."""
    secrets = replacements()
    hits = []
    count = 0
    stream_path = root / 'git-objects.private.bin'
    if stream_path.exists():
        with stream_path.open('rb') as stream:
            while header := stream.readline():
                oid, kind, length = header.decode().strip().split()
                payload = stream.read(int(length))
                if len(payload) != int(length) or stream.read(1) != b'\n':
                    raise ValueError('Truncated Git object export')
                count += 1
                if any(secret in payload for secret in secrets) or re.search(rb'-----BEGIN (?:OPENSSH|RSA|EC) PRIVATE KEY-----|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}', payload):
                    hits.append({'oid': oid, 'type': kind})
        write_json(root / 'history-scan.json', {'objects_scanned': count, 'credential_matches': len(hits), 'matches': hits})
        print(json.dumps({'objects_scanned': count, 'credential_matches': len(hits)}))
        if hits:
            raise ValueError('Do not publish unsanitized history; inspect local scan receipt')
        return
    for line in (root / 'objects.txt').read_text(encoding='utf-8-sig').splitlines():
        oid = line.split(' ', 1)[0]
        path = ROOT / '.git/objects' / oid[:2] / oid[2:]
        if not path.exists():
            raise ValueError('Packed object requires a separate history scan')
        data = zlib.decompress(path.read_bytes())
        header, payload = data.split(b'\0', 1)
        count += 1
        if any(secret in payload for secret in secrets) or re.search(rb'-----BEGIN (?:OPENSSH|RSA|EC) PRIVATE KEY-----|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}', payload):
            hits.append({'oid': oid, 'type': header.split()[0].decode()})
    result = {'objects_scanned': count, 'credential_matches': len(hits), 'matches': hits}
    write_json(root / 'history-scan.json', result)
    print(json.dumps({'objects_scanned': count, 'credential_matches': len(hits)}))
    if hits:
        raise ValueError('Do not publish unsanitized history; inspect local scan receipt')


def archive(label, source, output, secrets, tracked=None):
    entries = []
    excluded = []
    destination = output / (label + '.zip')
    tracked = tracked or set()
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as bundle:
        for path in sorted(source.rglob('*')):
            relative = path.relative_to(source).as_posix()
            if path.is_symlink():
                raise ValueError('Symlink requires explicit recovery handling: ' + relative)
            if path.is_dir():
                if not any(path.iterdir()):
                    reason = policy(relative + '/', tracked) if label == 'workspace' else None
                    if not reason:
                        bundle.writestr(relative + '/', b'')
                continue
            if not path.is_file():
                continue
            reason = policy(relative, tracked) if label == 'workspace' else ('device-key' if 'id_ed25519' in relative or 'id_rsa' in relative else None)
            if reason:
                excluded.append({'path': relative, 'reason': reason, 'bytes': path.stat().st_size})
                continue
            original = path.read_bytes()
            data = sanitize(relative, original, secrets)
            info = zipfile.ZipInfo.from_file(path, arcname=relative)
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data)
            entries.append({'path': relative, 'bytes': len(data), 'sha256': sha(data), 'sanitized': data != original})
    with zipfile.ZipFile(destination) as bundle:
        for item in entries:
            if sha(bundle.read(item['path'])) != item['sha256']:
                raise ValueError('Archive readback mismatch')
    manifest = {'archive': destination.name, 'files': entries, 'file_count': len(entries), 'sha256': sha(destination.read_bytes()), 'bytes': destination.stat().st_size}
    write_json(output / (label + '.manifest.json'), manifest)
    return manifest, excluded


def build(root):
    """Build public-safe archives without changing original configurations/data."""
    secrets = replacements()
    tracked = set((root / 'tracked.txt').read_text(encoding='utf-8-sig').splitlines())
    output = root / 'assets'
    output.mkdir(exist_ok=True)
    manifests = []
    exclusions = {}
    for label, path in [('workspace', ROOT)] + [(p.name, p) for p in sorted(Path('/volumes').iterdir()) if p.is_dir()]:
        print('Archiving ' + label, flush=True)
        manifest, omitted = archive(label, path, output, secrets, tracked)
        manifests.append(manifest)
        exclusions[label] = omitted
    write_json(root / 'excluded-files.private.json', exclusions)
    summary = {'status': 'prepared', 'repository_visibility': 'public', 'archives': [{k:v for k,v in m.items() if k != 'files'} for m in manifests], 'sanitized_files': sum(sum(f['sanitized'] for f in m['files']) for m in manifests), 'excluded_file_count': sum(len(v) for v in exclusions.values()), 'git_recovery': 'dated remote backup branches; no raw .git configuration or reflogs', 'private_database_and_study_volumes_included': False, 'safe_to_delete_project_or_docker_state': False}
    write_json(output / 'PUBLIC_BACKUP_COVERAGE.json', summary)
    (output / 'SHA256SUMS.txt').write_text(''.join(sha(p.read_bytes()) + '  ' + p.name + '\n' for p in sorted(output.iterdir()) if p.is_file() and p.name != 'SHA256SUMS.txt'))
    print(json.dumps(summary, indent=2))


def verify(directory):
    """Verify downloaded file bytes and every restored archive member."""
    checked = 0
    for line in (directory / 'SHA256SUMS.txt').read_text().splitlines():
        wanted, name = line.split('  ', 1)
        if PurePosixPath(name).name != name:
            raise ValueError('Unsafe checksum path')
        if sha((directory / name).read_bytes()) != wanted:
            raise ValueError('Asset checksum mismatch: ' + name)
        checked += 1
    for manifest in directory.glob('*.manifest.json'):
        item = json.loads(manifest.read_text())
        with zipfile.ZipFile(directory / item['archive']) as bundle:
            for entry in item['files']:
                if sha(bundle.read(entry['path'])) != entry['sha256']:
                    raise ValueError('Member checksum mismatch')
    print(json.dumps({'downloaded_assets_verified': checked, 'member_hashes': 'passed'}))


def restore(directory, name, target):
    """Restore a verified archive into an empty directory, rejecting unsafe paths."""
    verify(directory)
    if target.exists() and any(target.iterdir()):
        raise ValueError('Restore destination must be empty')
    target.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((directory / (name + '.manifest.json')).read_text())
    with zipfile.ZipFile(directory / manifest['archive']) as bundle:
        for entry in bundle.infolist():
            path = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename or stat.S_ISLNK(mode):
                raise ValueError('Unsafe archive member')
            destination = target.joinpath(*path.parts)
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('xb') as stream:
                stream.write(bundle.read(entry))
            if mode:
                destination.chmod(stat.S_IMODE(mode))
    for entry in manifest['files']:
        if sha(target.joinpath(*PurePosixPath(entry['path']).parts).read_bytes()) != entry['sha256']:
            raise ValueError('Restored file mismatch')
    print(json.dumps({'restored_archive': name, 'files_verified': manifest['file_count']}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['scan','build','verify','restore'])
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--archive')
    parser.add_argument('--target', type=Path)
    args = parser.parse_args()
    if args.action == 'scan': scan(args.directory)
    elif args.action == 'build': build(args.directory)
    elif args.action == 'verify': verify(args.directory)
    else:
        if not args.archive or args.target is None: parser.error('restore requires --archive and --target')
        restore(args.directory, args.archive, args.target)


if __name__ == '__main__':
    main()
