"""Manage the registered Emuera fixture through the duration supervisor."""
from pathlib import Path
import shutil

from gameqakit.admission import file_digest, no_links
from gameqakit.duration_session import DurationSession, ManagedCapture
from . import host, prepare as recipe
from .build import build


class ManagedSupervisor(ManagedCapture, host.Supervisor):
    pass


class EmueraSession(DurationSession):
    def __init__(self, plan):
        super().__init__(plan, recipe, recipe.LAUNCHER)

    def _prepare_runtime(self):
        for name in ('csv', 'erb', 'sav'):
            (self.runtime / name).mkdir(parents=True)
        for source, destination, digest in (
                (Path(self.plan['artifacts']) / recipe.RUNTIME, self.runtime / recipe.RUNTIME, recipe.RUNTIME_SHA256),
                (Path(self.plan['source']) / recipe.SCRIPT, self.runtime / 'erb/bridge.erb', recipe.SCRIPT_SHA256),
                (recipe.LAUNCHER_SOURCE, self.runtime / 'Launcher.cs', self.plan['artifact_digests']['Launcher.cs'])):
            no_links(source)
            shutil.copyfile(source, destination)
            if file_digest(destination) != digest:
                raise ValueError('runtime_changed: Emuera input changed during preparation')
        (self.runtime / 'csv/GAMEBASE.csv').write_bytes(recipe.GAMEBASE)
        build(self.executable, source=self.runtime / 'Launcher.cs')

    def _run_host(self):
        host.run(self.runtime / recipe.RUNTIME, self.executable, self.directory, self.session,
                 supervisor=ManagedSupervisor)
