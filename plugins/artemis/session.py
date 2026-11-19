"""Prepare a private authored scene with the registered local Artemis runtime."""
from pathlib import Path
import shutil

from gameqakit.admission import file_digest, no_links
from gameqakit.duration_session import DurationSession
from . import host, prepare as recipe


class ArtemisSession(DurationSession):
    def __init__(self, plan):
        super().__init__(plan, recipe, recipe.RUNTIME)

    def _prepare_runtime(self):
        self.runtime.mkdir()
        for field, digests in (('source', self.plan['source_digests']), ('artifacts', self.plan['artifact_digests'])):
            for name, digest in digests.items():
                source = Path(self.plan[field]) / name
                no_links(source)
                destination = self.runtime / name
                shutil.copyfile(source, destination)
                if file_digest(destination) != digest:
                    raise ValueError('runtime_changed: Artemis source changed during preparation')
        shutil.copyfile(Path(__file__).with_name('GameQaBridge.lua'), self.runtime / 'GameQaBridge.lua')

    def _run_host(self):
        host.run(self.executable, self.directory, self.session)
