"""Prepare an isolated copy of the registered authored YYC build."""
from pathlib import Path
import shutil

from gameqakit.admission import file_digest, no_links
from gameqakit.duration_session import DurationSession
from . import host, prepare as recipe


class GameMakerSession(DurationSession):
    def __init__(self, plan):
        super().__init__(plan, recipe, recipe.RUNTIME)

    def _prepare_runtime(self):
        self.runtime.mkdir()
        for name, digest in self.plan['artifact_digests'].items():
            source = Path(self.plan['artifacts']) / name
            no_links(source)
            destination = self.runtime / name
            shutil.copyfile(source, destination)
            if file_digest(destination) != digest:
                raise ValueError('runtime_changed: GameMaker component changed during preparation')
        configuration = self.runtime / 'options.ini'
        shutil.copyfile(Path(self.plan['source']) / 'options.ini', configuration)
        if file_digest(configuration) != self.plan['source_digests']['options.ini']:
            raise ValueError('runtime_changed: GameMaker configuration changed during preparation')

    def _run_host(self):
        host.run(self.executable, self.directory, self.session)
