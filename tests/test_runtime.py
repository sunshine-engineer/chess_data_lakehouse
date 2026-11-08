"""Deployment configuration fails early, respects precedence and hides secrets."""
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from chess_lakehouse.runtime import load, secret, postgres_options


def test_environment_overrides_file_without_writing_paths(monkeypatch, tmp_path):
    config=tmp_path/'runtime.toml'
    config.write_text('[paths]\nstudy="/another-study"\n[server]\nweb_threads=6\n[workers]\nembedded=false\n')
    monkeypatch.setenv('CHESS_WEB_THREADS','2')
    monkeypatch.setenv('STUDY_WORKER','1')
    runtime=load(config)
    assert runtime.paths.study==Path('/another-study')
    assert runtime.server.web_threads==2 and runtime.workers.embedded


@pytest.mark.parametrize('text', ['[paths]\nstudy="relative"', '[server]\nweb_port=70000',
    '[server]\nweb_threads=1.5', '[workers]\nembedded="yes"', '[unknown]\na=1',
    '[server]\nstudy_url="http://user:secret@service"'])
def test_invalid_runtime_config_is_rejected(tmp_path,text,monkeypatch):
    monkeypatch.delenv('STUDY_WORKER',raising=False)
    config=tmp_path/'runtime.toml';config.write_text(text)
    with pytest.raises(ValueError):load(config)


def test_mounted_secrets_and_conflicting_sources(monkeypatch,tmp_path):
    file=tmp_path/'password';file.write_text('sample-secret\n')
    monkeypatch.delenv('PGPASSWORD',raising=False)
    monkeypatch.setenv('PGPASSWORD_FILE',str(file))
    assert secret('PGPASSWORD')=='sample-secret'
    assert postgres_options()=={'password':'sample-secret'}
    assert 'sample-secret' not in json.dumps(asdict(load()),default=str)
    monkeypatch.setenv('PGPASSWORD','different')
    with pytest.raises(ValueError,match='not both'):postgres_options()
