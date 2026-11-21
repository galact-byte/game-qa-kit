"""Frozen-copy and preparation-receipt trust boundary tests; no commercial files."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from plugins.bakin import prepare


class BakinPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gqk-bakin-prepare-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.target = self.root / 'source', self.root / 'target'
        self.source.mkdir()
        (self.source / 'data').mkdir()
        (self.source / 'launcher.exe').write_bytes(b'fixed launcher')
        (self.source / 'data/payload.pak').write_bytes(b'fixed payload')
        (self.source / 'data/savedata').mkdir()
        (self.source / 'data/savedata/Save_09.sgs').write_bytes(b'untouched progress')
        (self.source / 'data/diagnostic.exe').write_bytes(b'never execute or copy')
        self.build = dict(launcher='launcher.exe', files={
            name: dict(bytes=len((self.source / name).read_bytes()),
                       sha256=hashlib.sha256((self.source / name).read_bytes()).hexdigest())
            for name in ('launcher.exe', 'data/payload.pak')})
        self.registration = patch.object(prepare, 'BUILD', self.build)
        self.registration.start()
        self.addCleanup(self.registration.stop)

    def copy(self, **kwargs):
        return prepare.copy_registered(self.source, self.target, deadline=time.monotonic() + 5, **kwargs)

    def test_copy_uses_only_frozen_components_and_preserves_progress(self):
        self.copy()
        self.assertEqual(b'fixed payload', (self.target / 'data/payload.pak').read_bytes())
        self.assertFalse((self.target / 'data/savedata').exists())
        self.assertFalse((self.target / 'data/diagnostic.exe').exists())
        self.assertEqual(b'untouched progress', (self.source / 'data/savedata/Save_09.sgs').read_bytes())

    def test_source_component_change_is_rejected(self):
        (self.source / 'data/payload.pak').write_bytes(b'changed payload')
        with self.assertRaises(ValueError):
            self.copy()

    def test_existing_destination_is_never_overwritten(self):
        self.target.mkdir()
        (self.target / 'important').write_bytes(b'keep')
        with self.assertRaises((ValueError, FileExistsError)):
            self.copy()
        self.assertEqual(b'keep', (self.target / 'important').read_bytes())

    def test_source_destination_overlap_is_rejected(self):
        self.target = self.source / 'child'
        with self.assertRaises(ValueError):
            self.copy()

    def test_cancel_is_checked_before_copy(self):
        with self.assertRaises(InterruptedError):
            self.copy(cancelled=lambda: True)
        self.assertFalse(self.target.exists())

    def test_whole_preparation_deadline_is_not_reset(self):
        with self.assertRaises(TimeoutError):
            prepare.copy_registered(self.source, self.target, deadline=time.monotonic() - 1)
        self.assertFalse(self.target.exists())

    def test_manifest_paths_cannot_escape(self):
        original = self.build['files']['launcher.exe']
        for name in ('../escape', 'C:/escape', '/escape', 'data:alternate', 'data/../escape', 'data\\escape'):
            with self.subTest(path=name):
                self.build['files'] = {name: original}
                with self.assertRaises(ValueError):
                    self.copy()

    def receipt(self):
        return dict(protocol=2, phase='prepared', session_id='session-1', pid=12,
                    creation_id='123456', executable=str(self.source / 'data/bakinplayer.exe'),
                    arguments='/TMP="' + str(self.source / 'data') + '"|"' +
                    str(self.source / 'launcher.exe') + '"|"' + str(self.source / 'temp/unpacked') + '"|5 ' +
                    str(self.source / 'launcher.exe'))

    def validate(self, receipt):
        return prepare.validate_receipt(receipt, self.source, 'session-1', 12, '123456', 5)

    def test_valid_receipt_preserves_native_tmp_arguments(self):
        value = self.validate(self.receipt())
        self.assertEqual(str(self.source / 'data/bakinplayer.exe'), value['executable'])
        self.assertTrue(value['arguments'][0].startswith('/TMP='))
        self.assertIn(str(self.source / 'temp/unpacked'), value['arguments'][0])

    def test_receipt_identity_types_and_unknown_fields_are_rejected(self):
        for key, value in (('protocol', True), ('pid', True), ('session_id', 'other'),
                           ('creation_id', 'other'), ('surprise', 1)):
            with self.subTest(field=key):
                item = self.receipt(); item[key] = value
                with self.assertRaises(ValueError):
                    self.validate(item)

    def test_receipt_cannot_name_original_or_external_paths(self):
        item = self.receipt(); item['executable'] = str(self.root / 'outside.exe')
        with self.assertRaises(ValueError):
            self.validate(item)
        item = self.receipt(); item['arguments'] = item['arguments'].replace(str(self.source / 'temp/unpacked'), str(self.root))
        with self.assertRaises(ValueError):
            self.validate(item)
