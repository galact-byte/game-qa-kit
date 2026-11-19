"""Session-bound immutable records in the target's GetTickCount64 time domain."""
import re
import struct
import time
from pathlib import Path

from gameqakit.duration_host import open_shared

BINDING = struct.Struct('<8s32sII')
COMMAND = struct.Struct('<8s32sIIIIQQ')
STATUS = struct.Struct('<8s32sIIQIIIIII')
KINDS = {'resume': 1, 'down': 2, 'up': 3, 'pause': 4, 'close': 5}
REASONS = ('none', 'invalid', 'lease_expired', 'action_expired', 'io_error', 'clock_error')
LIMIT = 2**32 - 1


def encode_binding(session, pid):
    if (not isinstance(session, str) or not re.fullmatch(r'[a-f0-9]{32}', session)
            or type(pid) is not int or not 0 < pid <= LIMIT):
        raise ValueError('Invalid GameMaker binding')
    return BINDING.pack(b'GQBGM02\0', session.encode('ascii'), pid, 2)


class Channel:
    def __init__(self, directory, session, pid):
        encode_binding(session, pid)
        self.root, self.token, self.pid = Path(directory), session, pid
        self.seq = self.kind = self.hold_until = self.generation = 0
        self.snapshot = {}
        self.observed_at = 0
        self.published = None

    def observe(self):
        records = []
        for path in self.root.glob('consumed.*.bin'):
            try:
                with open_shared(path) as stream:
                    raw = stream.read(STATUS.size + 1)
            except (FileNotFoundError, PermissionError):
                # A retired immutable record can be delete-pending on Windows.
                continue
            if len(raw) != STATUS.size:
                raise ValueError('Invalid GameMaker consumption length')
            magic, token, pid, sample, clock, command, ack, kind, paused, held, reason = STATUS.unpack(raw)
            if (magic != b'GQSGM02\0' or token != self.token.encode('ascii') or pid != self.pid
                    or not sample or ack > command or kind > 5 or paused > 1 or held > 1
                    or reason >= len(REASONS) or (held and (paused or kind != 2))
                    or path.name != f'consumed.{sample}.bin'):
                raise ValueError('Invalid GameMaker consumption identity or state')
            records.append(dict(sample_seq=sample, clock_ms=clock, command_seq=command,
                                ack_seq=ack, ack_kind=kind, paused=bool(paused), held=bool(held),
                                reason=REASONS[reason]))
        if not records:
            return self.snapshot
        latest = max(records, key=lambda value: value['sample_seq'])
        # A listing may outlive the target's publication/retirement pass. An older
        # readable record is not new evidence and must not refresh the deadline.
        if self.snapshot and latest['sample_seq'] < self.snapshot['sample_seq']:
            return self.snapshot
        if self.snapshot and (latest['ack_seq'] < self.snapshot['ack_seq']
                              or latest['clock_ms'] < self.snapshot['clock_ms']):
            raise ValueError('GameMaker consumption regressed')
        if not self.snapshot or latest['sample_seq'] > self.snapshot['sample_seq']:
            self.snapshot = latest
            self.observed_at = time.monotonic()
        return self.snapshot

    def send(self, kind, duration_ms=0):
        if (kind not in KINDS or not self.snapshot or time.monotonic() - self.observed_at > 1
                or type(duration_ms) is not int or (kind == 'down' and not 1 <= duration_ms <= 1000)
                or (kind != 'down' and duration_ms != 0) or self.seq >= LIMIT):
            raise ValueError('Invalid GameMaker command or stale target clock')
        self.seq += 1
        self.kind = KINDS[kind]
        self.hold_until = self.snapshot['clock_ms'] + duration_ms if kind == 'down' else 0
        self.heartbeat(1500)
        return self.seq

    def heartbeat(self, lease_ms):
        if not self.seq:
            return
        if type(lease_ms) is not int or lease_ms not in (0, 1500):
            raise ValueError('Invalid GameMaker lease')
        raw = COMMAND.pack(b'GQKGM02\0', self.token.encode('ascii'), self.pid, self.seq,
                           self.kind, 0, self.snapshot['clock_ms'] + lease_ms, self.hold_until)
        if raw == self.published:
            return
        if self.generation >= LIMIT:
            raise ValueError('GameMaker publication generation exhausted')
        destination = self.root / f'input.{self.generation}.bin'
        if destination.exists():
            raise ValueError('GameMaker immutable command already exists')
        temporary = self.root / 'input.part'
        try:
            temporary.write_bytes(raw)
            deadline = time.monotonic() + .1
            while True:
                try:
                    temporary.rename(destination)
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.005)
        finally:
            temporary.unlink(missing_ok=True)
        self.generation += 1
        self.published = raw
