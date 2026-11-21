"""Managed lifecycle for the registered Bakin commercial build."""
from gameqakit.duration_session import DurationSession
from . import host, prepare as recipe


class BakinSession(DurationSession):
    startup_seconds = recipe.STARTUP_SECONDS
    allowed_root_names = ('preparation',)

    def __init__(self, plan):
        super().__init__(plan, recipe, 'data/bakinplayer.exe')
        self.native = None
        self.cancelled = lambda: False

    def prepare(self, cancelled=lambda: False):
        self.cancelled = cancelled
        return super().prepare()

    def _prepare_runtime(self):
        copied, self.native = recipe.prepare_runtime(self.plan['source'], self.runtime,
            self.plan['artifacts'], self.root/'preparation', self.session, cancelled=self.cancelled)
        if copied != self.plan['source_digests']:
            raise ValueError('runtime_changed: Bakin source changed during preparation')

    def _run_host(self):
        host.run(self.native, self.directory, self.session)
