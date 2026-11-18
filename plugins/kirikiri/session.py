"""Manage the registered KiriKiri Window fixture on a private desktop."""
from pathlib import Path
import shutil

from gameqakit.admission import file_digest, no_links
from gameqakit.duration_session import DurationSession, ManagedCapture
from . import host, prepare_authored as recipe


class ManagedSupervisor(ManagedCapture, host.Supervisor):
    pass


class KiriKiriSession(DurationSession):
    def __init__(self, plan):
        super().__init__(plan, recipe, recipe.RUNTIME)

    def _prepare_runtime(self):
        self.runtime.mkdir()
        for source, destination, digest in (
                (Path(self.plan['source']) / recipe.SCRIPT, 'startup.tjs', recipe.SCRIPT_SHA256),
                (Path(self.plan['artifacts']) / recipe.RUNTIME, recipe.RUNTIME, recipe.RUNTIME_SHA256),
                (recipe.BRIDGE, recipe.BRIDGE.name, self.plan['artifact_digests'][recipe.BRIDGE.name])):
            no_links(source)
            copy = self.runtime / destination
            shutil.copyfile(source, copy)
            if file_digest(copy) != digest:
                raise ValueError('runtime_changed: KiriKiri input changed during preparation')

    def _run_host(self):
        host.run(self.executable, self.directory, self.session, supervisor=ManagedSupervisor)
