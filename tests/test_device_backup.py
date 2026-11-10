"""Recovery acceptance: hashes, traversal rejection, overwrite protection and redaction."""
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location('device_backup', Path(__file__).parents[1] / 'scripts/device_backup.py')
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


def assets(root, name='file.txt'):
    root.mkdir()
    with zipfile.ZipFile(root / 'workspace.zip', 'w') as stream:
        stream.writestr(name, b'roundtrip\x00bytes')
    metadata = {'archive': 'workspace.zip', 'file_count': 1,
                'files': [{'path': name, 'sha256': backup.sha(b'roundtrip\x00bytes')}]}
    (root / 'workspace.manifest.json').write_text(json.dumps(metadata))
    (root / 'SHA256SUMS.txt').write_text(''.join(backup.sha(path.read_bytes()) + '  ' + path.name + '\n'
        for path in sorted(root.iterdir())))
    return root


def test_real_file_roundtrip_and_overwrite_refusal(tmp_path):
    directory = assets(tmp_path / 'assets')
    target = tmp_path / 'restored'
    backup.restore(directory, 'workspace', target)
    assert (target / 'file.txt').read_bytes() == b'roundtrip\x00bytes'
    with pytest.raises(ValueError, match='empty'):
        backup.restore(directory, 'workspace', target)


def test_corrupted_download_is_rejected(tmp_path):
    directory = assets(tmp_path / 'assets')
    (directory / 'workspace.zip').write_bytes(b'corrupted download')
    with pytest.raises(ValueError, match='checksum'):
        backup.verify(directory)


def test_parent_traversal_is_rejected(tmp_path):
    directory = assets(tmp_path / 'assets', '../escape.txt')
    with pytest.raises(ValueError, match='Unsafe'):
        backup.restore(directory, 'workspace', tmp_path / 'restored')
    assert not (tmp_path / 'escape.txt').exists()


def test_live_values_only_change_in_backup_copy():
    original = b'POSTGRES_PASSWORD=synthetic-test-secret\nGCLOUD_CONFIG_DIR=/old/device\n'
    result = backup.sanitize('.env', original, {b'synthetic-test-secret': b'DUMMY_REPLACE_ON_NEW_DEVICE'})
    assert b'synthetic-test-secret' not in result
    assert b'/old/device' not in result
    assert b'synthetic-test-secret' in original


def test_notebook_outputs_removed():
    original = json.dumps({'cells': [{'cell_type':'code','outputs':[{'text':'private result'}], 'execution_count':9}]}).encode()
    result = json.loads(backup.sanitize('notebook.ipynb', original, {}))
    assert result['cells'][0]['outputs'] == []
    assert result['cells'][0]['execution_count'] is None
