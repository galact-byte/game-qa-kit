"""Unity 私有目录联接创建时的短暂占用与失败边界。"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from plugins.unity.session import UnitySession


@unittest.skipUnless(os.name == 'nt', '需要 Windows 目录联接')
class UnityJunctionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = UnitySession({'destination': str(self.root)})
        self.session.link = self.root / 'LocalLow' / 'PrivateCompany'
        self.addCleanup(self.remove_junction)

    def remove_junction(self):
        if self.session.link.is_junction():
            self.session.link.rmdir()

    def sharing_failure(self, target, link):
        Path(link).mkdir()
        error = PermissionError('junction sharing violation')
        error.winerror = 32
        raise error

    def test_transient_sharing_failure_reaches_real_private_junction(self):
        import _winapi
        create = _winapi.CreateJunction
        first = True
        def transient(target, link):
            nonlocal first
            if first:
                first = False
                self.sharing_failure(target, link)
            return create(target, link)
        with mock.patch('_winapi.CreateJunction', side_effect=transient):
            try:
                self.session.connect_persistent()
            except PermissionError as error:
                self.fail(f'Transient sharing violation prevented startup: {error}')
        self.assertTrue(self.session.link.is_junction())
        (self.session.link / 'progress.save').write_bytes(b'private progress')
        self.assertEqual((self.root / 'persistent/progress.save').read_bytes(), b'private progress')
        self.assertTrue(self.session.link_created)

    def test_persistent_sharing_failure_is_bounded_and_removes_empty_remnant(self):
        with mock.patch('_winapi.CreateJunction', side_effect=self.sharing_failure) as create:
            with self.assertRaises(PermissionError) as caught:
                self.session.connect_persistent()
        self.assertEqual(caught.exception.winerror, 32)
        self.assertLessEqual(create.call_count, 4)
        self.assertFalse(self.session.link.exists())
        self.assertFalse(self.session.link_created)

    def test_other_error_is_not_retried(self):
        error = PermissionError('access denied')
        error.winerror = 5
        with mock.patch('_winapi.CreateJunction', side_effect=error) as create:
            with self.assertRaises(PermissionError) as caught:
                self.session.connect_persistent()
        self.assertIs(caught.exception, error)
        self.assertEqual(create.call_count, 1)
        self.assertFalse(self.session.link_created)

    def test_nonempty_remnant_is_preserved_and_not_retried(self):
        def occupied(target, link):
            Path(link).mkdir()
            (Path(link) / 'other.save').write_bytes(b'keep this')
            error = PermissionError('junction sharing violation')
            error.winerror = 32
            raise error
        with mock.patch('_winapi.CreateJunction', side_effect=occupied) as create:
            with self.assertRaises(OSError):
                self.session.connect_persistent()
        self.assertEqual(create.call_count, 1)
        self.assertEqual((self.session.link / 'other.save').read_bytes(), b'keep this')
        self.assertFalse(self.session.link_created)

    def test_existing_directory_is_rejected_without_native_call(self):
        self.session.link.mkdir(parents=True)
        (self.session.link / 'existing.save').write_bytes(b'original')
        with mock.patch('_winapi.CreateJunction') as create:
            with self.assertRaisesRegex(RuntimeError, 'private_identity_exists'):
                self.session.connect_persistent()
        create.assert_not_called()
        self.assertEqual((self.session.link / 'existing.save').read_bytes(), b'original')

    def test_junction_remnant_is_not_removed_or_retried(self):
        import _winapi
        create_real = _winapi.CreateJunction
        def linked(target, link):
            create_real(target, link)
            error = PermissionError('junction sharing violation')
            error.winerror = 32
            raise error
        with mock.patch('_winapi.CreateJunction', side_effect=linked) as create:
            with self.assertRaises((ValueError, RuntimeError)):
                self.session.connect_persistent()
        self.assertEqual(create.call_count, 1)
        self.assertTrue(self.session.link.is_junction())
        self.assertFalse(self.session.link_created)
