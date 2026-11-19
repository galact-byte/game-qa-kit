"""GameMaker fixed authored YYC bridge tests."""
import os
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest import mock

from plugins.gamemaker.channel import BINDING, COMMAND, STATUS, Channel, KINDS, encode_binding


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('GAMEQAKIT_TEST_EVIDENCE'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.token = 'a' * 32
        self.channel = Channel(self.root, self.token, 123)

    def status(self, *, sample=1, clock=8000, command=0, ack=0, kind=0, paused=1, held=0, reason=0,
               token=None, pid=123):
        data = STATUS.pack(b'GQSGM02\0', (token or self.token).encode(), pid, sample, clock,
                           command, ack, kind, paused, held, reason)
        (self.root / f'consumed.{sample}.bin').write_bytes(data)
        return data

    def test_clock_from_target_controls_deadlines_and_renewal_preserves_hold(self):
        self.status()
        self.channel.observe()
        self.assertEqual(self.channel.send('down', 200), 1)
        command = COMMAND.unpack((self.root / 'input.0.bin').read_bytes())
        self.assertEqual(command[2:], (123, 1, 2, 0, 9500, 8200))
        self.status(sample=2, clock=8100, command=1, ack=1, kind=2, paused=0, held=1)
        self.channel.observe()
        self.channel.heartbeat(1500)
        renewed = COMMAND.unpack((self.root / 'input.1.bin').read_bytes())
        self.assertEqual(renewed[3], 1)
        self.assertEqual(renewed[-2:], (9600, 8200))
        self.channel.heartbeat(0)
        self.assertEqual(COMMAND.unpack((self.root / 'input.2.bin').read_bytes())[-2:], (8100, 8200))

    def test_clock_above_32_bits_is_preserved(self):
        self.status(clock=2**40)
        self.channel.observe()
        self.channel.send('down', 1)
        self.assertEqual(COMMAND.unpack((self.root / 'input.0.bin').read_bytes())[-1], 2**40 + 1)

    def test_missing_or_stale_clock_never_publishes_input(self):
        with self.assertRaises(ValueError): self.channel.send('resume')
        self.status()
        self.channel.observe()
        self.channel.observed_at = time.monotonic() - 1.1
        with self.assertRaises(ValueError): self.channel.send('resume')
        self.assertEqual(list(self.root.glob('input.*')), [])

    def test_identity_bad_lengths_and_inconsistent_consumption_are_rejected(self):
        for changes in ({'token': 'b'*32}, {'pid': 124}, {'ack': 2, 'command': 1},
                        {'kind': 6}, {'paused': 2}, {'held': 2}, {'sample': 0}, {'reason': 7},
                        {'paused': 1, 'held': 1}, {'held': 1, 'kind': 3}):
            with self.subTest(changes=changes):
                for path in self.root.glob('consumed.*'): path.unlink()
                self.status(**changes)
                with self.assertRaises(ValueError): self.channel.observe()
        for raw in (b'', b'bad', self.status() + b'x'):
            (self.root / 'consumed.1.bin').write_bytes(raw)
            with self.assertRaises(ValueError): self.channel.observe()

    def test_retired_scan_does_not_regress_or_refresh_the_last_observation(self):
        self.status(sample=2, command=1, ack=1, kind=1)
        previous = self.channel.observe()
        observed_at = self.channel.observed_at
        self.status(sample=1)
        self.status(sample=3, clock=8016, command=1, ack=1, kind=1)
        # The directory listing precedes publication and retirement by the target.
        with mock.patch.object(Path, 'glob', return_value=iter([self.root / 'consumed.1.bin'])):
            self.assertEqual(self.channel.observe(), previous)
        self.assertEqual(self.channel.observed_at, observed_at)
        self.assertEqual(self.channel.observe()['sample_seq'], 3)

    def test_record_names_regression_and_immutable_publication(self):
        self.status(sample=2, command=1, ack=1, kind=1)
        self.channel.observe()
        (self.root / 'consumed.2.bin').unlink()
        self.status(sample=3, clock=7999, command=1, ack=1, kind=1)
        with self.assertRaises(ValueError): self.channel.observe()
        (self.root / 'consumed.3.bin').unlink()
        self.status(sample=3, command=2, ack=2, kind=1)
        self.channel.observe()
        (self.root / 'input.0.bin').write_bytes(b'existing')
        with self.assertRaises(ValueError): self.channel.send('resume')
        self.assertEqual((self.root / 'input.0.bin').read_bytes(), b'existing')
        (self.root / 'consumed.3.bin').rename(self.root / 'consumed.9.bin')
        with self.assertRaises(ValueError): self.channel.observe()

    def test_invalid_commands_and_binding_fail_closed(self):
        self.status()
        self.channel.observe()
        for kind, duration in [('unknown',0), ('down',0), ('down',1001), ('down',True), ('up',1)]:
            with self.subTest(kind=kind, duration=duration):
                with self.assertRaises(ValueError): self.channel.send(kind, duration)
        self.assertEqual(BINDING.unpack(encode_binding(self.token,123)),
                         (b'GQBGM02\0', self.token.encode(), 123, 2))
        for token, pid in [('A'*32,123), ('a'*31,123), (self.token,True), (self.token,0)]:
            with self.assertRaises(ValueError): encode_binding(token,pid)


class AdmissionTests(unittest.TestCase):
    def test_authored_marker_has_a_fixed_recipe_without_enabling_commercial_games(self):
        from gameqakit.admission import inspect
        source = Path(__file__).resolve().parents[1] / 'plugins/gamemaker/samples'
        state = inspect(source)
        self.assertEqual(state['engine'], 'gamemaker_authored_candidate')
        self.assertEqual(state['launch_readiness'], 'blocked')
        self.assertEqual(state['blockers'][0]['field'], 'artifacts')

    def test_source_and_unknown_runtime_are_rejected(self):
        import shutil
        from plugins.gamemaker import prepare
        source = Path(__file__).resolve().parents[1] / 'plugins/gamemaker/samples'
        with tempfile.TemporaryDirectory(dir=os.environ.get('GAMEQAKIT_TEST_EVIDENCE')) as tmp:
            root = Path(tmp)
            fixture = root / 'sample'
            shutil.copytree(source, fixture)
            self.assertEqual(prepare.source_digests(fixture), prepare.SOURCE_DIGESTS)
            (fixture / 'objects/obj_probe/Step_0.gml').write_text('x += 1000;', encoding='utf-8')
            with self.assertRaises(ValueError): prepare.source_digests(fixture)
            shutil.copyfile(source / 'objects/obj_probe/Step_0.gml', fixture / 'objects/obj_probe/Step_0.gml')
            (fixture / 'save').mkdir()
            with self.assertRaises(ValueError): prepare.source_digests(fixture)
            (root / prepare.RUNTIME).write_bytes(b'MZunknown')
            with self.assertRaises((ValueError,OSError)): prepare.artifact_digests(root)


if __name__ == '__main__':
    unittest.main()
