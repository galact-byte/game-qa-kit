"""Managed lifecycle for the registered Flash fixture and private Edge profile."""
from pathlib import Path
import shutil
import time

from gameqakit.admission import file_digest, validate_destination
from gameqakit.duration_session import DurationSession
from . import prepare as recipe
from .cdp import Browser
from .host import FlashSupervisor
from .page import local_page, unpack


class FlashSession(DurationSession):
    def __init__(self, plan):
        super().__init__(plan, recipe, recipe.RUNTIME)
        self.executable = Path(plan['artifacts']) / recipe.RUNTIME

    def _prepare_runtime(self):
        validate_destination(recipe.ARCHIVE.parent, self.root / 'runtime')
        self.runtime.mkdir()
        shutil.copyfile(Path(self.plan['source']) / recipe.SCRIPT, self.runtime / recipe.SCRIPT)
        shutil.copyfile(recipe.ARCHIVE, self.runtime / recipe.ARCHIVE.name)
        if file_digest(self.runtime / recipe.SCRIPT) != recipe.SCRIPT_SHA:
            raise ValueError('Flash 场景副本摘要不符')
        unpack(self.runtime)

    def _run_host(self):
        deadline = time.monotonic() + 15
        work = self.root / 'browser'
        work.mkdir(exist_ok=False)
        with local_page(self.runtime) as url, Browser(self.executable, work) as browser:
            def startup_call(method, params=None, *, session=None):
                return browser.call(method, params, session=session, timeout=max(0, deadline - time.monotonic()))

            # Reuse the initial tab and keep all CDP setup within the managed startup deadline.
            targets = startup_call('Target.getTargets')['targetInfos']
            target = next(item['targetId'] for item in targets if item['type'] == 'page')
            tab = startup_call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
            startup_call('Emulation.setDeviceMetricsOverride', dict(width=400, height=300,
                         deviceScaleFactor=1, mobile=False), session=tab)
            startup_call('Emulation.setFocusEmulationEnabled', {'enabled': True}, session=tab)
            startup_call('Page.enable', session=tab)
            startup_call('Target.activateTarget', {'targetId': target})
            navigation = startup_call('Page.navigate', {'url': url}, session=tab)
            if navigation.get('errorText'):
                raise RuntimeError(navigation['errorText'])
            FlashSupervisor(browser, self.directory, self.session, tab).run(launch_target=False)
