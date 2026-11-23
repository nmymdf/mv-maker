"""播放歌曲。先用 Qt 內建播放器；如果在這台電腦上不會動，自動改用 ffplay。"""
from __future__ import annotations

import os
import subprocess
import time

from PySide6.QtCore import QObject, QTimer, Signal, QUrl

from . import media


class AudioPlayer(QObject):
    stateChanged = Signal(bool)     # 是否正在播放
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = ""
        self.mode = "qt"
        self.qt = None
        self._playing = False
        self._pos = 0.0             # 秒（ffplay 模式用）
        self._t0 = 0.0
        self.proc = None
        try:
            if os.environ.get("MV_FFPLAY"):
                raise RuntimeError("指定使用備用播放器")
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
            self.qt = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.audio.setVolume(0.9)
            self.qt.setAudioOutput(self.audio)
            self.qt.playbackStateChanged.connect(self._qt_state)
            self.qt.errorOccurred.connect(self._qt_error)
        except Exception as ex:
            self.qt = None
            self.mode = "ffplay"
            self.message.emit(f"內建播放器無法使用，改用備用播放器（{ex}）")
        self.watch = QTimer(self, singleShot=True, interval=2500, timeout=self._check_moving)
        self._watch_from = 0

    # ---------------------------------------------------------- 對外
    def setSource(self, path: str):
        self.pause()
        self.path = path
        self._pos = 0.0
        if self.qt and self.mode == "qt":
            self.qt.setSource(QUrl.fromLocalFile(path))

    def play(self):
        if not self.path:
            return
        if self.mode == "qt":
            self._watch_from = self.qt.position()
            self.qt.play()
            self.watch.start()
        else:
            self._start_ffplay()

    def pause(self):
        if self.mode == "qt":
            if self.qt:
                self.qt.pause()
        else:
            if self._playing:
                self._pos = self.position() / 1000
            self._stop_ffplay()
            self._set_playing(False)

    def setPosition(self, ms: int):
        if self.mode == "qt":
            self.qt.setPosition(int(ms))
        else:
            was = self._playing
            self._stop_ffplay()
            self._pos = ms / 1000
            if was:
                self._start_ffplay()

    def position(self) -> int:
        if self.mode == "qt":
            return self.qt.position()
        if self._playing:
            return int((self._pos + time.monotonic() - self._t0) * 1000)
        return int(self._pos * 1000)

    def is_playing(self) -> bool:
        if self.mode == "qt":
            from PySide6.QtMultimedia import QMediaPlayer
            return self.qt.playbackState() == QMediaPlayer.PlayingState
        return self._playing

    def close(self):
        self._stop_ffplay()

    # ---------------------------------------------------------- Qt
    def _qt_state(self, _s):
        if self.mode == "qt":
            self.stateChanged.emit(self.is_playing())

    def _qt_error(self, _err, text=""):
        if self.mode == "qt":
            self._switch_to_ffplay(f"內建播放器出錯（{text or _err}），改用備用播放器")

    def _check_moving(self):
        """按了播放 2.5 秒，時間還沒動：內建播放器在這台電腦不能用。"""
        if self.mode == "qt" and self.is_playing() is not None:
            if self.qt.position() <= self._watch_from + 200:
                self._switch_to_ffplay("內建播放器沒有聲音，已改用備用播放器")

    def _switch_to_ffplay(self, why: str):
        pos = self.qt.position() / 1000 if self.qt else 0.0
        playing = True
        try:
            if self.qt:
                self.qt.blockSignals(True)
                self.qt.stop()
        except Exception:
            pass
        self.mode = "ffplay"
        self._pos = pos
        self.message.emit(why)
        if playing:
            self._start_ffplay()

    # ---------------------------------------------------------- ffplay
    def _start_ffplay(self):
        self._stop_ffplay()
        try:
            exe = media.tool("ffplay")
        except FileNotFoundError:
            self.message.emit("找不到播放器，無法播放聲音")
            self._set_playing(False)
            return
        self.proc = media.popen([exe, "-nodisp", "-autoexit", "-loglevel", "quiet",
                                 "-ss", f"{self._pos:.2f}", self.path],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._t0 = time.monotonic()
        self._set_playing(True)

    def _stop_ffplay(self):
        if self.proc:
            try:
                self.proc.kill()
                self.proc.wait(timeout=2)
            except Exception:
                pass
            self.proc = None

    def _set_playing(self, v: bool):
        if v != self._playing:
            self._playing = v
            self.stateChanged.emit(v)
        if v:
            QTimer.singleShot(500, self._ffplay_alive)

    def _ffplay_alive(self):
        if self.mode == "ffplay" and self._playing and self.proc and self.proc.poll() is not None:
            # 播完或結束
            if time.monotonic() - self._t0 < 1.5:
                self.message.emit("播放器無法發出聲音，請檢查喇叭或耳機有沒有接好、音量有沒有打開")
            self._pos = self.position() / 1000
            self.proc = None
            self._set_playing(False)
        elif self.mode == "ffplay" and self._playing:
            QTimer.singleShot(500, self._ffplay_alive)
