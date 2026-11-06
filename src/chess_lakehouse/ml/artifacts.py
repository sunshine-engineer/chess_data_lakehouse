"""Load only artifacts produced by the explicit trusted local training command."""
from functools import lru_cache
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re

import joblib
from .features import FEATURES


class ArtifactError(ValueError):
    """Missing, incompatible or corrupt server-owned model publication."""


class Artifacts:
    def __init__(self,root):
        self.root=Path(root)

    def report(self):
        try:
            return self._report()
        except (ValueError,KeyError,TypeError,OSError) as exc:
            raise ArtifactError('Model artifacts are missing, corrupt or incompatible. Retrain with the documented command.') from exc

    def _report(self):
        pointer=json.loads((self.root/'latest.json').read_text())
        run_id=pointer['run_id']
        if not isinstance(run_id,str) or not re.fullmatch(r'\d{8}T\d{6}Z-[a-f0-9]{8}',run_id):
            raise ValueError('invalid model run identifier')
        folder=self.root/run_id
        manifest=json.loads((folder/'manifest.json').read_text())
        expected={'report.json','outcome.joblib','length.joblib','clusters.joblib'}
        if manifest.get('run_id')!=run_id or set(manifest.get('files',{}))!=expected:
            raise ValueError('invalid model manifest')
        for name,checksum in manifest['files'].items():
            if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=checksum:
                raise ValueError('model artifact checksum mismatch')
        report=json.loads((folder/'report.json').read_text())
        if report.get('run_id')!=run_id or report.get('schema_version')!=1:
            raise ValueError('unsupported model report')
        if report.get('features')!=list(FEATURES):
            raise ValueError('model feature contract changed; retrain')
        for name,version in report['versions'].items():
            if importlib.metadata.version(name)!=version:
                raise ValueError('model dependency version mismatch; retrain in this image')
        return report

    @lru_cache(maxsize=4)
    def model(self,run_id,name):
        if name not in ('outcome','length'):
            raise ValueError('unsupported prediction model')
        # report verifies fixed filenames/checksums before any pickle-based load.
        report=self.report()
        if report['run_id']!=run_id:
            raise ArtifactError('Model publication changed; retry the prediction.')
        return joblib.load(self.root/run_id/(name+'.joblib'))
