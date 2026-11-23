"""在打包機器上試播放：看內建播放器會不會動、備用播放器能不能接手。只報告，不判定失敗。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication

from mvmaker.player import AudioPlayer

app = QApplication(sys.argv)
p = AudioPlayer()
p.message.connect(lambda m: print("訊息:", m))
p.setSource(os.path.abspath(sys.argv[1]))
QTimer.singleShot(500, p.play)
for ms in (1500, 3000, 4500, 6000):
    QTimer.singleShot(ms, lambda ms=ms: print(f"{ms}ms 模式={p.mode} 播放中={p.is_playing()} 位置={p.position()}ms"))
QTimer.singleShot(6500, lambda: (p.close(), app.quit()))
app.exec()
