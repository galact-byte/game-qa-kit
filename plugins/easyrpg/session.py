"""Managed lifecycle for the fixed source-built EasyRPG RM2K scene."""
from pathlib import Path
import shutil

from gameqakit.admission import file_digest, no_links
from gameqakit.duration_session import DurationSession
from . import host, prepare as recipe


class EasyRpgSession(DurationSession):
    def __init__(self, plan):
        super().__init__(plan, recipe, recipe.RUNTIME)
        self.project = self.runtime / 'project'

    def _prepare_runtime(self):
        self.runtime.mkdir()
        original = Path(self.plan['artifacts']) / recipe.RUNTIME
        no_links(original)
        shutil.copyfile(original, self.executable)
        if file_digest(self.executable) != self.plan['artifact_digests'][recipe.RUNTIME]:
            raise ValueError('runtime_changed: EasyRPG runtime changed during preparation')
        for name, digest in self.plan['source_digests'].items():
            source = Path(self.plan['source']) / name
            destination = self.project / name
            no_links(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if file_digest(destination) != digest:
                raise ValueError('runtime_changed: EasyRPG scene changed during preparation')

    def _run_host(self):
        host.run(self.executable, self.project, self.directory, self.session, recipe.RUNTIME_SHA256)
