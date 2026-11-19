"""Strict, session-bound channel using the target clock and immutable consumption records."""
import re
import time
from pathlib import Path

from gameqakit.duration_host import open_shared

MOD = 2**31
KINDS = {'resume':1,'down':2,'up':3,'pause':4,'close':5}
STATUS = re.compile(rb'GQS1 ([a-f0-9]{32}) ((?:0|[1-9][0-9]*) )((?:0|[1-9][0-9]*) ){8}(none|invalid|lease_expired|action_expired|script_error)\n')


class Channel:
    def __init__(self, directory, session, pid):
        if not re.fullmatch(r'[a-f0-9]{32}', session) or type(pid) is not int or not 0<pid<MOD:
            raise ValueError('Invalid Artemis channel identity')
        self.root, self.token, self.pid = Path(directory), session, pid
        self.seq = self.kind = self.hold_until = 0
        self.snapshot = {}
        self.observed_at = 0
        self.generation = 0
        self.published = None

    def observe(self):
        records = []
        for path in self.root.glob('consumed.*.tsv'):
            try:
                with open_shared(path) as stream: raw=stream.read(513)
            except (FileNotFoundError, PermissionError):
                # A retired immutable name can be delete-pending on Windows. Only
                # successfully read new records refresh the one-second activity bound.
                continue
            # Only immutable, committed records are eligible; a partial record cannot replace one.
            if not STATUS.fullmatch(raw): continue
            fields=raw.decode('ascii').split()
            values=[int(value) for value in fields[2:11]]
            if any(not 0<=value<MOD for value in values): raise ValueError('Artemis status integer out of range')
            pid,sample,callbacks,clock,command,ack,kind,paused,held=values
            if (fields[1]!=self.token or pid!=self.pid or ack>command or kind>5
                    or paused not in (0,1) or held not in (0,1) or sample<1):
                raise ValueError('Artemis status identity or consumption is invalid')
            if path.name != f'consumed.{sample}.tsv': raise ValueError('Artemis consumption record identity mismatch')
            records.append(dict(session=fields[1],pid=pid,sample_seq=sample,callbacks=callbacks,
                                clock_ms=clock,command_seq=command,ack_seq=ack,ack_kind=kind,
                                paused=bool(paused),held=bool(held),reason=fields[-1]))
        if not records: return self.snapshot
        latest=max(records,key=lambda value:value['sample_seq'])
        if self.snapshot and (latest['sample_seq']<self.snapshot['sample_seq']
                              or latest['ack_seq']<self.snapshot['ack_seq']):
            raise ValueError('Artemis consumption sequence regressed')
        if not self.snapshot or latest['sample_seq']>self.snapshot['sample_seq']:
            self.snapshot=latest; self.observed_at=time.monotonic()
        return self.snapshot

    def send(self, kind, duration_ms=0):
        if (kind not in KINDS or not self.snapshot or time.monotonic()-self.observed_at>1
                or type(duration_ms) is not int or (kind=='down' and not 1<=duration_ms<=1000)
                or (kind!='down' and duration_ms!=0) or self.seq>=MOD-1):
            raise ValueError('Invalid Artemis command or stale target clock')
        self.seq+=1; self.kind=KINDS[kind]
        self.hold_until=(self.snapshot['clock_ms']+duration_ms)%MOD if kind=='down' else 0
        self.heartbeat(1500)
        return self.seq

    def heartbeat(self, lease_ms):
        if not self.seq: return
        if type(lease_ms) is not int or lease_ms not in (0,1500): raise ValueError('Invalid Artemis lease')
        due=(self.snapshot['clock_ms']+lease_ms)%MOD
        raw=f'GQA1 {self.token} {self.pid} {self.seq} {self.kind} {due} {self.hold_until}\n'
        if raw == self.published:
            return
        if self.generation >= MOD:
            raise ValueError('Artemis command publication generation exhausted')
        destination = self.root / f'input.{self.generation}.txt'
        if destination.exists():
            raise ValueError('Artemis command publication generation already exists')
        temporary=self.root/'input.part'
        try:
            temporary.write_text(raw,encoding='ascii',newline='\n')
            deadline=time.monotonic()+.1
            while True:
                try:
                    temporary.rename(destination); break
                except PermissionError:
                    if time.monotonic()>=deadline: raise
                    time.sleep(.005)
        finally: temporary.unlink(missing_ok=True)
        self.generation += 1
        self.published = raw
