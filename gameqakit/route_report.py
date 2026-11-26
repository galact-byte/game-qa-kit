"""绑定会话内的具名步骤与可携带证据；不替代引擎控制或生命周期所有者。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from html import escape
from pathlib import Path
import re
import time
import uuid

from .admission import no_links
from .sessions import read_json, write_json, write_text


def _text(value, name, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{name}必须为 1..{maximum} 个字符的非空文本')
    return value.strip()


def latest_report(directory):
    """只检索当前测试副本的报告，不遍历游戏资源或其他实例。"""
    if not directory:
        return None
    try:
        root = Path(directory).resolve()
        no_links(root)
        reports = []
        for path in (root / 'route-reports').glob('*/report.html'):
            no_links(path)
            if path.resolve().is_relative_to(root):
                reports.append(path)
        return max(reports, key=lambda path: (path.stat().st_mtime_ns, str(path)), default=None)
    except (OSError, ValueError, RuntimeError):
        return None


class RouteRun:
    def __init__(self, binding, goal):
        goal = _text(goal, '测试目标', 500)
        self.binding = binding
        self.client = binding.client_for({}, write=True)
        state = self.client.status()
        self.target = {key: state.get(key) for key in ('session_id', 'pid', 'exe_path', 'started_at')}
        self.root = self.client.directory.parent.resolve()
        no_links(self.root)
        route_id = uuid.uuid4().hex
        self.directory = self.root / 'route-reports' / route_id
        no_links(self.directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        checkpoint = self.root / 'checkpoint-before.json'
        no_links(checkpoint)
        self.data = dict(schema_version=1, route_id=route_id, goal=goal, state='recording',
                         started_at=time.time(), target=self.target, steps=[], outcome='unverified',
                         checkpoint=read_json(checkpoint) if checkpoint.exists() else None,
                         cleanup={'state': 'unverified', 'cleanup_verified': False},
                         report_directory=str(self.directory), report_html=str(self.directory / 'report.html'))
        self._flush()

    def status(self):
        return deepcopy(self.data)

    def _flush(self):
        try:
            no_links(self.directory)
            write_json(self.directory / 'report.json', self.data)
            write_text(self.directory / 'report.html', render_report(self.data))
        except Exception as exc:
            self.data.update(state='blocked', stop_reason='报告写入失败，停止后续输入：' + str(exc))
            raise

    def _assert_target(self):
        if self.binding.client is not self.client:
            raise RuntimeError('路线目标已解绑或切换，请结束原记录后新建路线')
        state = self.binding.client_for({}, write=True).status()
        if any(state.get(key) != value for key, value in self.target.items()):
            raise RuntimeError('路线目标身份或桥会话发生变化，停止本轮输入')
        return state

    def _pause(self):
        self._assert_target()
        self.client.pause()
        deadline = time.monotonic() + 3
        while True:
            state = self._assert_target()
            if state.get('paused') is True and state.get('held') == []:
                return True
            if time.monotonic() >= deadline:
                raise RuntimeError('尚未确认暂停松键，停止后续步骤')
            time.sleep(.01)

    def step(self, step_id, label, action, *, timeout=5.0, **options):
        label = _text(label, '步骤名称', 200)
        if not isinstance(step_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', step_id):
            raise ValueError('步骤编号仅允许 1..64 个字母、数字、下划线或连字符')
        request = dict(label=label, action=action, options=options, timeout=timeout)
        for previous in self.data['steps']:
            if previous['step_id'] == step_id:
                if previous['request'] != request:
                    raise ValueError('同一步骤编号不能重复用于不同内容')
                return deepcopy(previous)
        if self.data['state'] != 'recording':
            raise RuntimeError('路线已结束或因未知/失败结果停止，后续步骤已阻断')
        try:
            self._assert_target()
        except Exception as exc:
            self.data.update(state='blocked', stop_reason=str(exc))
            self._flush()
            raise
        row = dict(step_id=step_id, label=label, request=request, command_id=uuid.uuid4().hex,
                   state='running', started_at=time.time(), pause_confirmed=False)
        self.data['steps'].append(row)
        # 首次写入失败时不恢复控制，避免出现完全没有记录的动作。
        self._flush()
        try:
            resumed = self.binding.execute('resume', {}, command_id=uuid.uuid4().hex)
            self._check_result(resumed)
            result = self.binding.execute('action', {}, action=action, command_id=row['command_id'],
                                          timeout=timeout, **options)
            self._check_result(result)
            shot = Path(result['screenshot'])
            no_links(shot)
            if shot.parent.resolve() != (self.client.directory / 'screenshots').resolve():
                raise RuntimeError('动作截图不属于当前桥证据目录')
            body = shot.read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            if digest != result.get('screenshot_sha256'):
                raise RuntimeError('动作截图摘要已变化，不能作为完成证据')
            relative = 'images/' + row['command_id'] + '.png'
            destination = self.directory / relative
            no_links(destination)
            destination.parent.mkdir(exist_ok=True)
            with destination.open('xb') as stream:
                stream.write(body)
            row.update(state='done', image=relative, screenshot_sha256=digest,
                       scene=result.get('scene', ''), frame=result.get('frame'),
                       observation_seq=result.get('observation_seq'))
        except Exception as exc:
            row.update(state='unknown' if isinstance(exc, TimeoutError) else 'failed', error=str(exc))
        finally:
            try:
                row['pause_confirmed'] = self._pause()
            except Exception as exc:
                row['pause_error'] = str(exc)
                if row['state'] == 'done':
                    row.update(state='unknown', error=str(exc))
            if row['state'] != 'done':
                self.data.update(state='blocked', stop_reason=row.get('error', '动作未完成'))
            row['finished_at'] = time.time()
            self._flush()
        return deepcopy(row)

    @staticmethod
    def _check_result(result):
        if (result.get('status') != 'done' or result.get('ok') is not True
                or result.get('consumed') is not True or result.get('historical')):
            raise TimeoutError('动作结果未知或不是本轮新证据，停止后续步骤并查询原命令')

    def finish(self, outcome='unverified', note='', *, cleanup=None):
        if outcome not in ('passed', 'failed', 'unverified'):
            raise ValueError('复核结论仅允许 passed / failed / unverified')
        if not isinstance(note, str) or len(note) > 4000:
            raise ValueError('复核说明必须是至多 4000 字符的文本')
        if outcome != 'unverified':
            note = _text(note, '复核说明', 4000)
        if outcome == 'passed' and (not self.data['steps'] or self.data['state'] == 'blocked'
                                   or any(row['state'] != 'done' for row in self.data['steps'])):
            raise ValueError('步骤未完成或结果未知，不能标记目标通过')
        if self.data['state'] == 'finished':
            if self.data['outcome'] != outcome or self.data.get('note', '') != note:
                raise ValueError('已结束记录不能改写原复核结论')
            return self.status()
        self.data.update(state='finished', outcome=outcome, note=note, finished_at=time.time())
        if cleanup is not None:
            self.data['cleanup'] = deepcopy(cleanup)
            self.data['cleanup']['cleanup_verified'] = (
                cleanup.get('state') == 'stopped' and cleanup.get('worker_alive') is False
                and cleanup.get('target_exited') is True and not any(cleanup.get(key) is False
                    for key in ('release_confirmed', 'source_unchanged', 'original_unchanged',
                                'runtime_source_unchanged', 'checkpoint_unchanged', 'cleanup_verified')))
        self._flush()
        return self.status()


def render_report(data):
    def text(value):
        return escape(str(value or ''), quote=True)
    states = {'recording': '记录中', 'blocked': '已停止后续步骤', 'finished': '记录已结束',
              'running': '执行中', 'done': '动作已完成', 'failed': '动作失败', 'unknown': '结果未知'}
    outcomes = {'passed': '目标达成', 'failed': '目标未达成', 'unverified': '尚未复核目标'}
    steps = data['steps']
    completed = sum(row['state'] == 'done' for row in steps)
    last = steps[-1]['label'] if steps else '尚未执行步骤'
    cleanup = data['cleanup']
    reclaimed = (cleanup.get('state') == 'stopped' and cleanup.get('cleanup_verified') is True
                 and cleanup.get('worker_alive') is False)
    checkpoint = data.get('checkpoint')
    checkpoint_text = ('已导入测试档：' + '、'.join(checkpoint.get('files', {}))
                       + '。导入不等于已在游戏中读档，请以读档步骤画面为准。' if checkpoint else '本轮未登记检查点导入；不能据此认定从某个存档开始。')
    articles = []
    for number, row in enumerate(steps, 1):
        image = row.get('image')
        evidence = (f'<a href="{text(image)}"><img src="{text(image)}" alt="步骤 {number}：{text(row["label"])}的操作后画面" loading="lazy"></a>'
                    if image else '<p class="muted">本步没有已确认的操作后截图。</p>')
        error = '<p class="problem">' + text(row.get('error')) + '</p>' if row.get('error') else ''
        paused = '已确认暂停松键' if row.get('pause_confirmed') else '未确认暂停松键'
        articles.append(f'''<article><div><p class="step-meta">步骤 {number} · {text(states[row['state']])}</p>
<h3>{text(row['label'])}</h3><p>{text(row.get('scene'))}</p>{error}<p class="muted">{paused}</p>
<details><summary>动作与证据详情</summary><dl><dt>动作</dt><dd>{text(row['request']['action'])} {text(row['request']['options'])}</dd>
<dt>命令编号</dt><dd>{text(row['command_id'])}</dd><dt>截图 SHA-256</dt><dd>{text(row.get('screenshot_sha256'))}</dd></dl></details></div>
<div class="evidence">{evidence}</div></article>''')
    stop = f'<p class="problem">停止原因：{text(data["stop_reason"])}</p>' if data.get('stop_reason') else ''
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'">
<title>{text(data['goal'])} · GameQA 测试结果</title><style>
:root {{ color-scheme: light dark; --bg: #f5f6f8; --surface: #fff; --fg: #20242d; --muted: #596270; --line: #d6dbe2; --accent: #245ca1; --problem: #9f3030; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #171a20; --surface: #20252d; --fg: #edf0f5; --muted: #b7c0cc; --line: #48515e; --accent: #93bfff; --problem: #ffb1b1; }} }}
* {{ box-sizing: border-box; }} body {{ margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.65 "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; }}
main {{ max-width: 1120px; margin: auto; padding: 40px 28px 64px; }} h1 {{ font-size: clamp(24px, 3.4vw, 36px); line-height: 1.35; margin: 8px 0 24px; }}
h2 {{ margin: 32px 0 12px; font-size: 23px; }} h3 {{ font-size: 20px; margin: 4px 0 12px; }} p {{ margin: 8px 0; }}
.step-meta, .muted {{ color: var(--muted); }} .step-meta {{ font-size: 14px; }} .problem {{ color: var(--problem); }}
.summary, article {{ background: var(--surface); border: 1px solid var(--line); padding: 24px; }}
.summary dl {{ display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 10px 16px; }} dt {{ color: var(--muted); }} dd {{ margin: 0; }}
article {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 480px); gap: 24px; margin: 16px 0; }}
.evidence img {{ display: block; width: 100%; max-height: 360px; object-fit: contain; }} .evidence a {{ display: block; }}
a {{ color: var(--accent); }} summary {{ cursor: pointer; color: var(--accent); min-height: 44px; padding: 10px 0; }} a:focus-visible, summary:focus-visible {{ outline: 3px solid var(--accent); outline-offset: 4px; }}
dd, h1, h3, p {{ overflow-wrap: anywhere; }} details {{ font-size: 14px; }} details dl {{ margin: 8px 0; }} details dd {{ margin-bottom: 10px; }}
@media (max-width: 720px) {{ main {{ padding: 24px 16px 40px; }} .summary, article {{ padding: 18px; }} article {{ grid-template-columns: minmax(0, 1fr); }} .summary dl {{ grid-template-columns: minmax(0, 1fr); gap: 2px; }} .summary dd {{ margin-bottom: 10px; }} }}
</style></head><body><main><header><h1>{text(data['goal'])}</h1></header>
<section class="summary" aria-label="测试摘要"><dl><dt>记录状态</dt><dd>{text(states[data['state']])}</dd>
<dt>动作执行</dt><dd>{completed} / {len(steps)} 步完成；动作完成不等于玩法目标达成</dd>
<dt>最后步骤</dt><dd>{text(last)}</dd><dt>AI 复核</dt><dd>{text(outcomes[data['outcome']])}</dd>
<dt>实例回收</dt><dd>{'已确认回收' if reclaimed else '未确认回收；结束记录不等于关闭游戏'}</dd></dl>{stop}
<p>{text(data.get('note') or '尚无玩法结论，请核对步骤画面后再结束记录。')}</p></section>
<h2>起点与范围</h2><p>{text(checkpoint_text)}</p><p class="muted">本页记录显式绑定会话内的动作与画面，不代表其他游戏或完整流程已验证。</p>
<h2>执行过程</h2>{''.join(articles) or '<p>尚未执行步骤；本页不是通过证明。</p>'}
<details><summary>目标身份与原始记录</summary><p>{text(data['target'])}</p><a href="report.json">查看原始 JSON 记录</a></details>
</main></body></html>'''
