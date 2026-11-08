"""Volume manifests detect missing, extra, edited and unsafe artifacts."""
import pytest
from chess_lakehouse.volumes import inventory


def test_volume_manifest_detects_changes_and_ignores_locks(tmp_path):
    folder=tmp_path/'nested';folder.mkdir()
    (folder/'game.pgn').write_text('unicode 雪\n')
    (folder/'.engine-worker.lock').touch()
    before=inventory({'study':tmp_path})
    assert list(before)==['study/nested/game.pgn']
    (folder/'game.pgn').write_text('changed')
    assert before!=inventory({'study':tmp_path})
    (folder/'link').symlink_to(folder/'game.pgn')
    with pytest.raises(ValueError,match='symlink'):inventory({'study':tmp_path})
