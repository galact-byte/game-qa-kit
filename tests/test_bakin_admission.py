"""Read-only admission and registered worker dispatch for Bakin."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gameqakit import admission
from gameqakit.engines import inspect_game
from gameqakit.game_worker import session_for


class BakinAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='gqk-bakin-admission-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'game'
        (self.source/'data').mkdir(parents=True)
        for name,body in [('bakinplayer.exe',b'MZ'),('bakinengine.dll',b'MZ'),('data.rbpack',b'BKNPAK'+bytes([0,1]))]:
            (self.source/'data'/name).write_bytes(body)
        self.artifacts=self.root/'artifacts';self.artifacts.mkdir()

    def test_parent_of_native_data_is_detected_without_launching(self):
        value=inspect_game(self.source)
        self.assertEqual('bakin_candidate',value['engine'])
        self.assertEqual(str(self.source.resolve()),value['root'])
        self.assertFalse(value['control_verified'])

    def test_unknown_bakin_build_remains_blocked(self):
        value=admission.inspect(self.source,artifacts=self.artifacts)
        self.assertEqual('blocked',value['launch_readiness'])
        self.assertTrue(value['blockers'])
        self.assertEqual([],list(self.artifacts.iterdir()))

    def test_plan_revalidation_binds_registered_source_and_artifact_digests(self):
        with patch('plugins.bakin.prepare.source_digests',return_value={'registered':'a'*64}),patch('plugins.bakin.prepare.artifact_digests',return_value={'bridge':'b'*64}):
            value=admission.make_plan(self.source,self.root/'session',artifacts=self.artifacts)
            self.assertEqual('bakin_fixed',value['backend'])
            self.assertEqual(value,admission.revalidate_plan(value))
            changed=dict(value,source_digests={'registered':'c'*64})
            with self.assertRaisesRegex(ValueError,'plan_changed'):admission.revalidate_plan(changed)
        self.assertFalse((self.root/'session').exists())

    def test_worker_dispatches_to_bakin_without_starting_game(self):
        from plugins.bakin.session import BakinSession
        value=session_for(dict(backend='bakin_fixed',destination=str(self.root/'session')))
        self.assertIsInstance(value,BakinSession)
        self.assertEqual(60,value.startup_seconds)
        self.assertIsNone(value.host)
