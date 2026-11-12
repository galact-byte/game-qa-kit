"""后台接入对话框；耗时检查与生命周期操作在工作线程执行。"""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import threading
import uuid

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout)

from . import admission
from .game_session import Manager


class _Signals(QObject):
    result = pyqtSignal(str, object, str)


class AdmissionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = Manager()
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
        self.source = QLineEdit()
        self.qa_root = QLineEdit()
        self.artifacts = QLineEdit(os.environ.get('GAMEQAKIT_NATIVE_ARTIFACTS', ''))
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
        buttons = QHBoxLayout()
        self.check = QPushButton('只读检查')
        self.start = QPushButton('确认并启动')
        self.stop = QPushButton('结束会话')
        finish = QPushButton('关闭')
        for button in (self.check, self.start, self.stop, finish):
            button.setMinimumHeight(44)
            buttons.addWidget(button)
        self.check.clicked.connect(self._check)
        self.start.clicked.connect(self._start)
        self.stop.clicked.connect(self._stop)
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
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(500)
        self._controls()

    def _choose(self, field, title):
        directory = QFileDialog.getExistingDirectory(self, '选择' + title, field.text())
        if directory:
            field.setText(directory)

    def _invalidate(self):
        self.plan = None
        self._controls()

    def _controls(self):
        editable = not self.busy and not self.closing and self.directory is None
        for field in (self.source, self.qa_root, self.artifacts, *self.path_buttons):
            field.setEnabled(editable)
        self.check.setEnabled(editable)
        self.start.setEnabled(editable and self.plan is not None)
        self.stop.setEnabled(not self.busy and not self.closing and self.directory is not None
                             and self.last_state not in ('stopped', 'failed', 'cleanup_failed', 'cancelled'))

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
            self._run('close', self.manager.close)

    def _poll(self):
        if self.busy or self.closing or self.directory is None:
            return
        try:
            result = self.manager.status(self.directory)
            self._display(result)
            if result['state'] == 'prepared' and not self.started:
                self.started = True
                self._run('start', lambda: self.manager.start(self.directory, request_id=self.request + '-start',
                          fingerprint=self.plan['fingerprint'], confirm=True))
        except Exception as exc:
            self.state.setText('状态查询失败：' + str(exc))

    def _display(self, result):
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
        if image and image != self.last_image:
            pixmap = QPixmap(image)
            if not pixmap.isNull():
                self.preview.setPixmap(pixmap.scaled(self.preview.width(), self.preview.height(),
                    Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                self.last_image = image
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
                self._run('close', self.manager.close)
