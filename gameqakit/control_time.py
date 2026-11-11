"""时间协议的边界校验；观察序号不表示游戏帧。"""
import re

BUTTONS = {'up', 'down', 'left', 'right', 'ok', 'cancel'}
FIELDS = {'move': {'direction', 'duration_ms'}, 'confirm': {'duration_ms'},
          'cancel': {'duration_ms'}, 'wait': {'duration_ms'}, 'resume': set()}


def _buttons(value):
    return isinstance(value, list) and all(isinstance(item, str) and item in BUTTONS for item in value)


def validate_state(state):
    if (state.get('timing_mode') != 'duration_ms' or state.get('pause_scope') != 'input'
            or state.get('simulation_pause') is not False or 'frame' in state
            or type(state.get('observation_seq')) is not int or state['observation_seq'] < 0
            or type(state.get('max_duration_ms')) is not int or not 1 <= state['max_duration_ms'] <= 1000
            or not _buttons(state.get('buttons')) or not _buttons(state.get('held'))
            or any(action not in FIELDS or action == 'resume' for action in state['capabilities'])):
        raise RuntimeError('时间协议的观察序号、能力或暂停语义无效')


def validate_payload(payload, state):
    action = payload['action']
    if action not in FIELDS or set(payload) != FIELDS[action] | {'action'}:
        raise ValueError('时间后端仅接受显式毫秒动作，不接受 frames、press/release、click 或额外字段')
    if action == 'resume':
        return
    duration = payload['duration_ms']
    if type(duration) is not int or not 1 <= duration <= state['max_duration_ms']:
        raise ValueError(f"duration_ms 必须为 1..{state['max_duration_ms']} 的整数")
    button = (payload.get('direction') if action == 'move' else 'ok' if action == 'confirm'
              else 'cancel' if action == 'cancel' else None)
    if action == 'move' and button not in {'up', 'down', 'left', 'right'}:
        raise ValueError('移动方向必须为 up/down/left/right')
    if button is not None and button not in state['buttons']:
        raise ValueError('时间后端未声明此按钮能力')


def validate_result(result, request):
    keyed = request['payload']['action'] not in ('resume', 'wait')
    start, end = result.get('started_monotonic_ms'), result.get('completed_monotonic_ms')
    if (result.get('consumed') is not keyed or result.get('press_consumed') is not keyed
            or result.get('release_consumed') is not keyed
            or type(result.get('observation_seq')) is not int
            or result['observation_seq'] <= request['after_observation_seq']
            or result.get('pause_id') != request.get('pause_id', '')
            or result.get('timing_mode') != 'duration_ms' or result.get('pause_scope') != 'input'
            or result.get('simulation_pause') is not False or result.get('held') != []
            or type(start) is not int or type(end) is not int or not 0 <= end - start <= 5000
            or not isinstance(result.get('screenshot_sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', result['screenshot_sha256'])
            or 'frame' in result or 'capture_frame' in result):
        raise RuntimeError('缺少时间动作后的新观察、输入消费或松键证据，停止自动操作')
