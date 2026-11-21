"""Supervise the registered Bakin player using ordinary engine input queries."""
import hashlib
import shutil
import time

from gameqakit.desktop import DesktopProcess
from gameqakit.duration_host import Supervisor
from gameqakit.duration_session import ManagedCapture
from gameqakit.control_time import LOADING_MODE
from .loading import LoadingLifecycle
from .prepare import BUILD, creation_identity
from .target import BakinMailbox, TargetChannel

BUTTONS={'up':1,'down':2,'left':4,'right':8,'ok':16,'cancel':32}


def game_window(target):
    import ctypes
    from gameqakit.win32 import user32
    from ctypes import wintypes
    for window in target.windows():
        if window['minimized'] or min(window['client']) <= 0:
            continue
        hwnd=wintypes.HWND(window['hwnd'])
        name=ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd,name,len(name))
        if name.value=='kmyAppWindow' and user32.IsWindowVisible(hwnd):
            return window
    return None


class BakinSupervisor(LoadingLifecycle,ManagedCapture,Supervisor):
    engine='bakin'
    actions=('move','confirm','cancel','wait')
    startup_seconds=60
    runtime_sha256=BUILD['player']['sha256']

    def __init__(self,target,directory,session,channel):
        super().__init__(target,directory,session)
        self.channel=channel
        self.target_state={}
        self.pause_pending=False
        self.deferred=None
        self.loading=None

    def _uses_shm_status(self):
        return isinstance(getattr(self.channel, 'mailbox', None), BakinMailbox)

    def connection_meta(self):
        meta = super().connection_meta()
        if self._uses_shm_status():
            meta['control_transport'] = 'bakin-shm-v1'
        return meta

    def publish_status(self):
        if self._uses_shm_status():
            self.channel.mailbox.write_json('supervisor', self.state())
        else:
            super().publish_status()

    def state(self):
        state=super().state()
        target=self.target_state
        state.update(buttons=list(BUTTONS),held=[name for name,key in BUTTONS.items() if target.get('held',0)&key],
            paused=self.paused and target.get('paused',False) and not self.pause_pending,
            target_paused=target.get('paused',False),target_sample_seq=target.get('sample_seq',0),
            target_command_seq=target.get('command_seq',0),target_scene=target.get('scene','NONE'),
            target_title_state=target.get('title_state','NONE'),
            loading_modes=[LOADING_MODE] if target.get('loading_protocol')==1 else [])
        if self.loading:
            state['loading']={key:self.loading[key] for key in ('command_id','phase','pause_id')}
            if self.loading['phase']!='completed':state['capabilities']=[]
        return state

    def send(self,ident,kind):
        if self.channel.sequence!=self.channel.status.get('command_seq',0):
            if self.deferred is not None:raise RuntimeError('Bakin deferred command backlog')
            self.deferred=(ident,kind)
            return
        if kind=='down':
            payload=self.active['request']['payload']
            button=payload.get('direction') if payload['action']=='move' else 'ok' if payload['action']=='confirm' else 'cancel'
            self.active['key']=BUTTONS[button]
            result=self.channel.command(kind,action_id=ident,key=BUTTONS[button],duration=payload['duration_ms'])
            self.active['start_deadline_ms']=self.channel.action_timing['start_deadline_ms']
            return result
        return self.channel.command(kind,action_id=ident)

    def accept(self,request):
        if self.active:
            if request==self.active['request']:return
            raise ValueError('Bakin action is already pending')
        if self.loading:
            if self.loading['phase']=='completed' and request.get('id')==self.loading['command_id']:
                return
            if self.loading['phase']!='completed' or request.get('payload',{}).get('action')!='resume':
                raise ValueError('Bakin loading requires completion review and explicit resume')
            shot=self.loading['reviewed']
            if (not self.map_complete(self.target_state) or self.target_state.get('ready') is not True
                    or self.target_state.get('paused') is not True or self.target_state.get('held')!=0
                    or not 0<=time.monotonic()*1000-shot['captured_monotonic_ms']<3000):
                raise ValueError('Bakin loading resume evidence is no longer fresh or ready')
        super().accept(request)
        if self.active and request['payload']['action']=='move':
            self.active['held_screenshots']=[]
        if self.active and request['payload'].get('loading_mode')==LOADING_MODE:
            self.begin_loading(request)
        if self.active and request['payload']['action']=='resume':
            self.loading=None
            self.send(request['id'],'resume')

    def request_pause(self):
        self.deferred=None
        self.pause_pending=True
        self.flush_commands()

    def flush_commands(self):
        if self.channel.sequence!=self.channel.status.get('command_seq',0):return
        if self.deferred:
            ident,kind=self.deferred
            self.deferred=None
            self.send(ident,kind)
        elif self.pause_pending:
            self.channel.command('pause')
            self.pause_pending=False

    def interrupt(self,error):
        if self.loading:self.loading['cancelled']=True
        self.deferred=None
        super().interrupt(error)
        self.request_pause()

    def observe(self):
        state=self.channel.observe()
        if not state:return
        self.target_state=state
        if state['sample_seq']>self.ui_seq:
            self.ui_seq=state['sample_seq']
            self.changed=time.monotonic()
        if self.paused or self.lease_valid():self.channel.lease()
        self.observer={'ready':state.get('ready') is True and state['scene'] in ('TITLE','MAP') and state['sample_seq']>0
                       and game_window(self.target) is not None}
        active=self.active
        if active and active['keyed']:
            press=state.get('press')
            if (not active['press'] and press and press.get('action_id')==active['down_id']
                    and self.channel.action_pressed(active['down_id'],active.get('key',0))):
                active['press']=True
                active['press_sample']=press['sample_seq']
                active['until']=time.monotonic()+(press['hold_until_ms']-self.channel.clock())/1000
            if self.channel.action_released(active['down_id'],active.get('key',0)):
                active['release']=True
                active['release_sample']=state.get('release',{}).get('sample_seq',state['sample_seq'])
                active['after_ui']=state['sample_seq']-1
        self.flush_commands()
        self.update_loading()
        if state.get('error'):self.fault('target_'+state['error'])

    def start_capture(self,ident,purpose):
        self.channel.request_capture(ident)
        self.capture=dict(started=time.monotonic(),purpose=purpose,ident=ident)

    def cancel_capture(self):
        self.capture=None

    def poll_capture(self):
        if not self.capture:return
        try:
            captured=self.channel.capture_result(allow_blank=self.loading_confirmed())
            if not captured:return
            image,receipt=captured
            capture=self.capture
            destination=self.directory/'screenshots'/(capture['ident']+'.png')
            destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(self.channel.root/receipt['screenshot'],destination)
            self.observation=destination.relative_to(self.directory).as_posix()
            self.observation_seq+=1
            self.last_capture=time.monotonic()
            self.capture=None
            self.loading_capture(receipt)
            if capture['purpose']=='held' and self.active:
                snapshot=receipt.get('input_snapshot') or {}
                if snapshot.get('action_id')==self.active['down_id'] and snapshot.get('held')==self.active['key']:
                    self.active['held_screenshots'].append(dict(screenshot=self.observation,
                        screenshot_sha256=receipt['sha256'],draw_seq=receipt['draw_seq'],
                        captured_at_ms=receipt['captured_at_ms'],input_snapshot=snapshot))
            if capture['purpose']=='action' and self.active and not self.loading_confirmed():
                if not self.lease_valid():self.interrupt('capture_lease_expired')
                self.finish(dict(screenshot=self.observation,screenshot_sha256=receipt['sha256']))
        except (OSError,ValueError,RuntimeError) as exc:
            self.observation_error=str(exc)
            self.cancel_capture()
            raise

    def progress(self):
        active=self.active
        if active and self.progress_loading():return
        if active:
            if active['request']['payload']['action']=='resume' and (
                    not self.channel.acknowledged('resume') or self.target_state.get('paused',True)):return
            complete=active['release'] if active['keyed'] else time.monotonic()>=active['until']
            if (not complete and not active['error'] and active.get('press')
                    and active['request']['payload']['action']=='move'
                    and self.target_state.get('held')==active.get('key')
                    and active['release_started'] is None and time.monotonic()<active['until']
                    and not self.capture and len(active.get('held_screenshots',[]))<2):
                self.start_capture(active['request']['id']+'_held_'+str(len(active['held_screenshots'])+1),'held')
            if complete:
                if active['error']:self.finish()
                elif not self.capture and self.ui_seq>active['after_ui']:
                    self.start_capture(active['request']['id'],'action')
        if self.stopping is not None and not self.active and self.target_state.get('paused') and not self.pause_pending:
            self.cancel_capture()
            self.reason='stop_requested'
            self.target.close()

    def deadlines(self):
        now=time.monotonic()
        if (self.active and self.active['keyed'] and not self.active['press']
                and self.channel.clock()>=self.active.get('start_deadline_ms',2**63-1)):
            return 'press_timeout'
        loading_deadline=self.loading_deadline(now)
        if loading_deadline is not None:return loading_deadline or None
        if self.active and now-self.active['started']>5:return 'action_timeout'
        if not self.observation_seq and self.active is None and self.stopping is None:
            # Initialization can sample NONE once before loading. It has no input permission or ready image yet.
            if now-self.started>self.startup_seconds:return 'startup_timeout'
            if self.capture and now-self.capture['started']>3:return 'capture_timeout'
            return None
        return super().deadlines()

    def finish(self,screenshot=None):
        evidence=dict(target_press_sample=self.active.get('press_sample'),
            target_release_sample=self.active.get('release_sample'),target_command_seq=self.target_state.get('command_seq',0),
            target_scene=self.target_state.get('scene','NONE'))
        if 'held_screenshots' in self.active:
            evidence['held_screenshots']=self.active['held_screenshots']
        super().finish({**evidence,**(screenshot or {})})


def run(native,directory,session):
    from pathlib import Path
    executable=Path(native['executable'])
    runtime=executable.parent.parent
    target_root=directory/'target'
    target_root.mkdir()
    env=dict(TEMP=str(runtime/'temp'),TMP=str(runtime/'temp'),GAMEQAKIT_BAKIN_BRIDGE=str(target_root))
    with DesktopProcess([str(executable),*native['arguments']],cwd=executable.parent,env=env,detached_stdio=True) as target:
        with TargetChannel(target_root,session,target.pid,creation_identity(target)) as channel:
            channel.lease()
            return BakinSupervisor(target,directory,session,channel).run()
