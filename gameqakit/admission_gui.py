"""后台接入对话框；耗时检查与生命周期操作在工作线程执行。"""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import threading
import uuid

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout)

from . import admission
from .game_session import Binding, Manager


class _Signals(QObject):
    result = pyqtSignal(str, object, str)


class AdmissionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = Manager()
        self.binding = Binding()
        self.connected_external = False
        self.can_stop = False
        self.preference_error = ''
        try:
            preferences = admission.load_preferences()
        except (OSError, ValueError) as exc:
            preferences = {}
            self.preference_error = str(exc)
        self.busy = self.closing = self.closed = False
        self.plan = self.directory = None
        self.started = False
        self.request = uuid.uuid4().hex
        self.last_state = self.last_image = None
        self.sig = _Signals(self)
        self.sig.result.connect(self._result)
        self.setWindowTitle('AI 后台接入')
        self.resize(760, 680)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        hint = QLabel('选择游戏与 QA 数据目录，先检查本次操作范围，再确认启动。游戏初始只暂停就绪。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.source = QLineEdit(preferences.get('source', ''))
        self.qa_root = QLineEdit(preferences.get('qa_root', ''))
        self.artifacts = QLineEdit(preferences.get('artifacts', os.environ.get('GAMEQAKIT_NATIVE_ARTIFACTS', '')))
        self.path_buttons = []
        for text, field in [('游戏目录', self.source), ('QA 数据目录', self.qa_root), ('后台组件目录', self.artifacts)]:
            field.setAccessibleName(text)
            row = QHBoxLayout()
            row.addWidget(field)
            button = QPushButton('选择…')
            button.setAccessibleName('选择' + text)
            button.setMinimumHeight(44)
            button.clicked.connect(lambda checked=False, f=field, title=text: self._choose(f, title))
            self.path_buttons.append(button)
            row.addWidget(button)
            form.addRow(text, row)
            field.textChanged.connect(self._invalidate)
        self.qa_root.setPlaceholderText('选择专门保存测试副本与证据的位置')
        self.artifacts.setPlaceholderText('已有 native-host.exe 与 native-input.dll 所在目录')
        layout.addLayout(form)
        existing = QHBoxLayout()
        self.existing = QComboBox()
        self.existing.setAccessibleName('已有受管会话')
        self.existing.setMinimumWidth(100)
        self.existing.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        existing.addWidget(self.existing, 1)
        self.refresh = QPushButton('查找会话')
        self.connect_existing = QPushButton('连接所选')
        self.open_bridge = QPushButton('选择外部桥…')
        for button, action in ((self.refresh, self._refresh), (self.connect_existing, self._connect_selected),
                               (self.open_bridge, self._choose_bridge)):
            button.setMinimumHeight(44)
            button.clicked.connect(action)
            existing.addWidget(button)
        layout.addLayout(existing)
        self.existing.currentIndexChanged.connect(self._controls)
        self.qa_root.textChanged.connect(self.existing.clear)
        buttons = QGridLayout()
        self.check = QPushButton('只读检查')
        self.start = QPushButton('确认并启动')
        self.stop = QPushButton('结束会话')
        self.pause = QPushButton('暂停输入')
        self.disconnect = QPushButton('断开连接')
        finish = QPushButton('关闭')
        for index, button in enumerate((self.check, self.start, self.stop, self.pause, self.disconnect, finish)):
            button.setMinimumHeight(44)
            buttons.addWidget(button, index // 3, index % 3)
        self.check.clicked.connect(self._check)
        self.start.clicked.connect(self._start)
        self.stop.clicked.connect(self._stop)
        self.pause.clicked.connect(lambda: self._run('pause', lambda: self.binding.client_for({}).pause()))
        self.disconnect.clicked.connect(self._disconnect)
        finish.clicked.connect(self.close)
        layout.addLayout(buttons)
        self.state = QLabel('尚未检查；选择目录不会启动游戏。')
        self.state.setWordWrap(True)
        self.state.setAccessibleName('后台会话状态')
        layout.addWidget(self.state)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setAccessibleName('接入检查与操作记录')
        layout.addWidget(self.detail, 1)
        self.preview = QLabel('就绪后显示游戏首图')
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(180)
        self.preview.setAccessibleName('游戏首图预览')
        layout.addWidget(self.preview, 1)
        if self.preference_error:
            self.detail.setPlainText('读取目录偏好失败，请重新选择目录：' + self.preference_error)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(500)
        self._controls()

    def _choose(self, field, title):
        directory = QFileDialog.getExistingDirectory(self, '选择' + title, field.text())
        if directory:
            field.setText(directory)
            if field is self.qa_root:
                try:
                    value = admission.load_preferences(directory)
                    self.source.setText(value.get('source', self.source.text()))
                    self.artifacts.setText(value.get('artifacts', self.artifacts.text()))
                except (OSError, ValueError) as exc:
                    self.state.setText('读取目录偏好失败：' + str(exc))

    def _invalidate(self):
        self.plan = None
        self._controls()

    def _controls(self):
        available = not self.busy and not self.closing
        editable = available and self.directory is None and not self.connected_external
        for field in (self.source, self.qa_root, self.artifacts, *self.path_buttons):
            field.setEnabled(editable)
        self.check.setEnabled(editable)
        self.start.setEnabled(editable and self.plan is not None)
        self.stop.setEnabled(available and self.directory is not None and self.can_stop
                             and self.last_state not in ('stopped', 'failed', 'cleanup_failed', 'cancelled'))
        self.existing.setEnabled(editable)
        self.refresh.setEnabled(editable)
        self.connect_existing.setEnabled(editable and self.existing.currentData() is not None)
        self.open_bridge.setEnabled(editable)
        self.pause.setEnabled(available and self.connected_external)
        self.disconnect.setEnabled(available and self.connected_external)

    def _run(self, operation, action):
        self.busy = True
        self._controls()
        def worker():
            try:
                self.sig.result.emit(operation, action(), '')
            except Exception as exc:
                self.sig.result.emit(operation, None, str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def _check(self):
        if not self.source.text().strip() or not self.qa_root.text().strip():
            self.state.setText('请选择游戏目录和 QA 数据目录。')
            return
        source, qa_root, artifacts = (field.text().strip() for field in (self.source, self.qa_root, self.artifacts))
        destination = Path(qa_root).absolute() / ('session-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        self.plan = None
        self.state.setText('正在只读检查…')
        def inspect():
            result = admission.inspect(source, artifacts=artifacts or None)
            if result['launch_readiness'] == 'ready':
                result['plan'] = admission.make_plan(source, destination, artifacts=artifacts)
            admission.save_preferences(qa_root, source=source, artifacts=artifacts)
            return result
        self._run('check', inspect)

    def _start(self):
        if self.plan is None or self.busy:
            return
        summary = '\n'.join(self.plan['changes']) + '\n\n本次会话：' + self.plan['destination']
        answer = QMessageBox.question(self, '确认本次后台接入', summary,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.directory = self.plan['destination']
        self._run('prepare', lambda: self.manager.prepare(self.plan, request_id=self.request + '-prepare', confirm=True))

    def _stop(self):
        self._run('stop', lambda: self.manager.stop(self.directory, request_id=self.request + '-stop'))

    def _refresh(self):
        root = self.qa_root.text().strip()
        if not root:
            self.state.setText('请先选择保存 QA 会话的父目录。')
            return
        self._run('list', lambda: admission.list_sessions(root))

    def _connect_selected(self):
        row = self.existing.currentData()
        if row:
            self._connect(row['bridge_directory'], row.get('session_id'))

    def _choose_bridge(self):
        directory = QFileDialog.getExistingDirectory(self, '选择已有受控会话的桥目录', self.qa_root.text())
        if directory:
            self._connect(directory)

    def _connect(self, directory, session_id=None):
        def connect():
            if not session_id:
                from .sessions import read_json
                admission.no_links(Path(directory) / 'connection.json')
                identity = read_json(Path(directory) / 'connection.json')['session_id']
            else:
                identity = session_id
            return self.binding.bind(directory, identity, observe_only=True)
        self._run('connect', connect)

    def _disconnect(self):
        self._run('disconnect', self.binding.unbind)

    def _close_sessions(self):
        if self.connected_external:
            self.binding.unbind()
        self.manager.close()

    def _result(self, operation, result, error):
        self.busy = False
        if error:
            self.state.setText('操作失败：' + error)
            self.detail.appendPlainText(error)
            if operation == 'close':
                self.closing = False
        elif operation == 'close':
            self.closed = True
            self.timer.stop()
            self.close()
            return
        elif operation == 'list':
            self.existing.clear()
            for row in result['sessions']:
                self.existing.addItem(Path(row['directory']).name + ' · ' + row.get('state', '未知状态'), row)
            rows = [item['directory'] + '：' + item['error'] for item in result['errors']]
            if result['truncated']:
                rows.append('已达到本次查找上限，请选择更具体的会话父目录。')
            self.detail.setPlainText('\n'.join(rows))
            self.state.setText(f"找到 {len(result['sessions'])} 个会话记录；连接时会重新核对目标身份。")
        elif operation == 'connect':
            self.connected_external = True
            self.plan = None
            self._display_external(result)
        elif operation == 'disconnect':
            self.connected_external = False
            self.last_image = None
            self.preview.setText('已断开；原会话仍由创建者管理')
            self.state.setText('已确认松键或目标退出，连接已断开。')
        elif operation == 'pause':
            self.state.setText('已请求暂停输入；等待目标确认松键。')
        elif operation == 'check':
            self.plan = result.get('plan')
            rows = [row['message'] + '\n下一步：' + row['recovery'] for row in result.get('blockers', [])]
            if self.plan:
                rows.extend(self.plan['changes'])
                rows.append('会话目录：' + self.plan['destination'])
            self.detail.setPlainText('\n\n'.join(rows))
            self.state.setText('检查通过，请审阅操作范围。' if self.plan else '后台接入条件尚不满足；仍可使用人工盯屏。')
        else:
            self._display(result)
        self._controls()
        if self.closing:
            self._run('close', self._close_sessions)

    def _poll(self):
        if self.busy or self.closing:
            return
        if self.connected_external:
            try:
                self._display_external(self.binding.status())
            except Exception as exc:
                self._clear_preview('连接查询失败，预览已停止')
                self.state.setText('外部会话查询失败：' + str(exc))
            return
        if self.directory is None:
            return
        try:
            result = self.manager.status(self.directory)
            self._display(result)
            if result['state'] == 'prepared' and not self.started:
                self.started = True
                self._run('start', lambda: self.manager.start(self.directory, request_id=self.request + '-start',
                          fingerprint=self.plan['fingerprint'], confirm=True))
        except Exception as exc:
            self._clear_preview('状态查询失败，预览已停止')
            self.state.setText('状态查询失败：' + str(exc))

    def _display_external(self, result):
        if not result.get('connected'):
            self._clear_preview('连接失效，预览已停止')
            self.state.setText('外部管理：连接失效，请核查原会话。' + str(result.get('error', '')))
        else:
            label = '暂停且已松键' if result.get('paused') and result.get('held') == [] else '输入可能仍在执行'
            self.state.setText('外部管理：' + label + '；此连接仅查询与暂停，结束请回到创建者。')
            self._display_observation(result.get('directory'), result)
        self._controls()

    def _clear_preview(self, message):
        self.last_image = None
        self.preview.setText(message)

    def _display_observation(self, directory, state):
        if directory:
            folder = Path(directory) / 'screenshots'
            name = 'observation.png' if state.get('protocol') == 2 else 'startup.png'
            image = folder / name
            admission.no_links(image)
            token = (str(image), state.get('observation_seq', 0))
            if image.is_file() and token != self.last_image:
                self._show_image(str(image), token=token)

    def _display(self, result):
        self.can_stop = result.get('can_stop', False)
        code = result['state']
        labels = {'preparing': '正在准备副本', 'prepared': '副本准备完成', 'starting': '正在启动',
                  'ready_paused': '暂停就绪，可由 MCP 显式连接', 'running': '后台输入已恢复',
                  'stopping': '正在结束并核对数据', 'stopped': '会话已结束', 'failed': '会话失败',
                  'cleanup_failed': '收尾核对失败，副本已保留', 'interrupted_unverified': '会话中断，尚未核实收尾',
                  'connection_lost': '连接失效，停止输入并等待回收'}
        self.state.setText(labels.get(code, code))
        if code != self.last_state:
            self.detail.appendPlainText(labels.get(code, code))
            self.last_state = code
            for key in ('error', 'cleanup_error', 'bridge_directory', 'session_id'):
                if result.get(key):
                    self.detail.appendPlainText(key + ': ' + str(result[key]))
            if result.get('runtime_retained'):
                self.detail.appendPlainText('运行副本已保留：' + str(self.directory))
        image = result.get('screenshot')
        control = result.get('control', {})
        if control.get('connected'):
            self._display_observation(result.get('bridge_directory'), control)
        elif code in ('stopped', 'failed', 'cleanup_failed', 'interrupted_unverified', 'connection_lost'):
            self._clear_preview('会话已结束或连接失效，预览已停止')
        elif image and image != self.last_image:
            self._show_image(image)

    def _show_image(self, image, *, token=None):
        pixmap = QPixmap(image)
        if not pixmap.isNull():
            self.preview.setPixmap(pixmap.scaled(self.preview.width(), self.preview.height(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.last_image = image if token is None else token
        self._controls()

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if self.closed:
            event.accept()
            return
        event.ignore()
        if not self.closing:
            self.closing = True
            self.state.setText('正在结束自有会话，请等待数据核对与进程回收。')
            self._controls()
            if not self.busy:
                self._run('close', self._close_sessions)
