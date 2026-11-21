"""在原生 macOS arm64 上验收源码／冻结 MCP 的实际单窗口观察链。"""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.verify_macos_capture_probe import decode_counter, require_growth


def wait_value(reader, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = reader()
            if predicate(value):
                return value
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(.05)
    raise RuntimeError('等待原生验收条件超时')


class Client:
    def __init__(self, command, env, root):
        self.log = (root/'mcp.log').open('w')
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.log, text=True, encoding='utf-8', env=env, cwd=ROOT)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.serial = 0
        self.calls = []

    def rpc(self, method, params):
        self.serial += 1
        self.process.stdin.write(json.dumps({'jsonrpc':'2.0','id':self.serial,'method':method,'params':params})+'\n')
        self.process.stdin.flush()
        if not self.selector.select(12):
            raise RuntimeError('MCP 请求超时：'+method)
        row = json.loads(self.process.stdout.readline())
        assert row.get('id') == self.serial and 'error' not in row, row
        return row['result']

    def call(self, name, **arguments):
        result = self.rpc('tools/call', {'name':name,'arguments':arguments})
        self.calls.append({'tool':name,'arguments':arguments,'result':result})
        if result.get('isError'):
            raise RuntimeError(result)
        return json.loads(result['content'][0]['text'])

    def close(self):
        self.selector.close()
        self.process.stdin.close()
        try:
            self.process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait(timeout=3)
        self.process.stdout.close()
        self.log.close()


def run(root, fixture, executable):
    root.mkdir(parents=True, exist_ok=False)
    result = {'ok':False, 'mode':'frozen' if executable else 'source'}
    children = []
    client = None
    phases = {}
    def state():
        return json.loads((root/'target-state.json').read_text())
    def proof(phase):
        subprocess.run([str(fixture), 'evidence', str(root)], check=True, timeout=10)
        row = json.loads((root/'proof.json').read_text())
        assert row['coverage']['mismatched_pixels'] == 0 and row['coverage']['checked_pixels'] > 0, row
        (root/f'{phase}-proof.json').write_text(json.dumps(row,indent=2))
        (root/'desktop-proof.png').rename(root/f'{phase}-desktop.png')
        return row
    try:
        children.append(subprocess.Popen([str(fixture),'target',str(root)]))
        ready = wait_value(lambda: json.loads((root/'target-ready.json').read_text()), lambda x: 'window' in x)
        profile = {'name':'native-fixture','proc':fixture.stem,'title_substr':'GameQA target',
                   'exe_path':str(fixture),'data_dir':str(root/'qa')}
        (root/'profile.json').write_text(json.dumps(profile))
        env = {**os.environ, 'GAMEQAKIT_PROFILE':str(root/'profile.json'), 'GAMEQAKIT_PROFILES':str(root/'profiles')}
        command = [str(executable),'--mcp'] if executable else [sys.executable,'-X','utf8','-B','-m','gameqakit.mcp_server']
        client = Client(command, env, root)
        tools = {t['name'] for t in client.rpc('tools/list',{})['tools']}
        assert {'capture','watch_start','watch_stop','mark','win'} <= tools
        assert not tools.intersection({'click','key','game_start','background_start'})
        windows = wait_value(lambda: client.call('processes'),
                             lambda x: any(row.get('window_id') == ready['window'] for row in x.get('processes', [])))
        result['windows'] = windows
        result['window'] = client.call('win')
        assert result['window']['window_id'] == ready['window']
        initial = root/'visible-independent.png'
        client.call('capture', out=str(initial))
        with Image.open(initial) as image:
            result['initial_counter'] = decode_counter(image)
        status = client.call('watch_start', interval=.3, threshold=0, analysis_mode='rules')
        session = Path(status['out'])
        wait_value(lambda: client.call('watch_status'), lambda x: x.get('captured',0) >= 3)
        concurrent = root/'visible-watched.png'
        client.call('capture', out=str(concurrent))
        with Image.open(concurrent) as image:
            result['concurrent_visible_counter'] = decode_counter(image)
        children.append(subprocess.Popen([str(fixture),'cover',str(root)]))
        wait_value(lambda: json.loads((root/'cover-ready.json').read_text()), lambda x: 'window' in x)
        for phase in ('occluded','frozen','resized'):
            if phase == 'frozen':
                (root/'freeze').touch(); time.sleep(.3)
            elif phase == 'resized':
                (root/'freeze').unlink(); (root/'resize').touch()
                wait_value(state, lambda x: x.get('width') == 400)
            rows = []
            for index in range(4):
                path = root/f'{phase}-{index}.png'
                begin = time.monotonic()
                response = client.call('capture', out=str(path))
                with Image.open(path) as image:
                    counter = decode_counter(image.convert('RGB'))
                    size = list(image.size)
                rows.append({'counter':counter,'size':size,'file':path.name,
                             'elapsed_ms':(time.monotonic()-begin)*1000,'target':state(),'response':response})
                time.sleep(.35)
            counters = [x['counter'] for x in rows]
            if phase == 'frozen':
                assert len(set(counters)) == 1, counters
                try: require_growth(counters)
                except ValueError: pass
                else: raise AssertionError('冻结帧被错误接受')
            else:
                require_growth(counters)
                coverage = proof(phase)
                if 'frontmost' not in result: result['frontmost'] = coverage['frontmost']
                assert coverage['frontmost'] == result['frontmost'], coverage
            phases[phase] = rows
        result['mark'] = client.call('mark',note='原生遮挡采集人工标记')
        assert Path(result['mark']['marked']).is_file()
        status = client.call('watch_status')
        assert status['capture_source']['platform']=='darwin' and status['saved'] > 0, status
        index_rows = [json.loads(x) for x in (session/'index.jsonl').read_text().splitlines()]
        assert all(x['capture_source']==status['capture_source'] and x['hwnd'] is None for x in index_rows)
        timestamps = [x['t'] for x in index_rows]
        assert timestamps == sorted(set(timestamps)), timestamps
        client.call('watch_stop')
        result['stopped'] = wait_value(lambda:client.call('watch_status'),lambda x:not x.get('running'))
        assert result['stopped'].get('ok'), result['stopped']
        assert children[0].poll() is None, '停止采集关闭了用户窗口'
        client.call('watch_start',interval=.3,threshold=0,analysis_mode='off')
        wait_value(lambda:client.call('watch_status'),lambda x:x.get('captured',0)>0)
        (root/'stop').touch()
        result['closed_target'] = wait_value(lambda:client.call('watch_status'),lambda x:not x.get('running'))
        assert not result['closed_target']['ok'], result['closed_target']
        for child in children: child.wait(timeout=5)
        result.update(ok=True,phases=phases,archive_frames=len(index_rows))
    except Exception as exc:
        result.update(error=repr(exc),phases=phases)
        if client:
            try:
                result['last_watch_status'] = client.call('watch_status')
            except Exception as status_error:
                result['status_error'] = repr(status_error)
        raise
    finally:
        (root/'stop').touch()
        if client:
            try: client.call('watch_stop')
            except Exception: pass
            (root/'mcp-calls.json').write_text(json.dumps(client.calls,ensure_ascii=False,indent=2))
            client.close()
        cleanup=[]
        for child in children:
            forced=False
            try: child.wait(timeout=4)
            except subprocess.TimeoutExpired:
                forced=True; child.kill(); child.wait(timeout=3)
            cleanup.append({'pid':child.pid,'code':child.returncode,'forced':forced})
        result['cleanup']=cleanup
        (root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--fixture',required=True,type=Path)
    parser.add_argument('--executable',type=Path)
    args=parser.parse_args()
    print(json.dumps(run(args.root.resolve(),args.fixture.resolve(),args.executable.resolve() if args.executable else None),ensure_ascii=False))
