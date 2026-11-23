"""MV 製作工具 主畫面。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback

from PySide6.QtCore import (Qt, QTimer, QThread, Signal, QObject, QRunnable, QThreadPool, QUrl,
                            QSettings, QStandardPaths, QEvent, QSize, QPoint)
from PySide6.QtGui import QImage, QPixmap, QIcon, QFont, QColor, QBrush, QKeySequence, QGuiApplication
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QSplitter, QFrame, QSlider, QFileDialog, QMessageBox,
                               QProgressBar, QSizePolicy, QButtonGroup, QTabWidget, QAbstractItemView)

from . import core
from .core import (Project, Clip, LyricLine, Style, VERSION, parse_time, fmt_time, parse_seconds,
                   parse_lyrics_text, spread_untimed, kind_of, build_plan, unique_path, COLORS, POSITIONS, EFFECTS,
                   MOTIONS, TRANSITIONS, PORTRAIT_BGS, YESNO, SIZES, SEG_LENS, AUDIO_EXT)
from .ui_table import ExcelTable, Col
from . import media

GOLD = "#C9971C"
APP_NAME = "MV製作工具"
OTHER_FONT = "其他字型檔…"
PREVIEW_W = 640


def np_to_qimage(arr) -> QImage:
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def data_dir() -> str:
    d = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if not d:
        d = os.path.join(os.path.expanduser("~"), "." + APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


# ======================================================================== 背景工作
class Worker(QThread):
    progress = Signal(float)
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            res = self.fn(*self.args, progress=self.progress.emit, cancelled=lambda: self._cancel,
                          **({"log": self.log.emit} if self.fn is _do_autopick else {}))
            self.done.emit(res)
        except media.Cancelled:
            self.failed.emit("已取消")
        except Exception as ex:
            traceback.print_exc()
            self.failed.emit(str(ex))


def _do_autopick(clips, song_len, seg_len, progress, cancelled, log):
    from .autopick import auto_pick
    return auto_pick(clips, song_len, seg_len, progress=progress, cancelled=cancelled, log=log)


def _do_export(project, out, progress, cancelled):
    return media.export(project, out, progress, cancelled)


class ThumbBridge(QObject):
    ready = Signal(object, QImage)


class ThumbJob(QRunnable):
    def __init__(self, key, path, kind, t, bridge):
        super().__init__()
        self.key, self.path, self.kind, self.t, self.bridge = key, path, kind, t, bridge

    def run(self):
        arr = media.thumbnail(self.path, self.kind, (128, 72), self.t + 0.5 if self.kind == "video" else 0)
        if arr is not None:
            try:
                self.bridge.ready.emit(self.key, np_to_qimage(arr))
            except RuntimeError:
                pass        # 視窗已經關了


# ======================================================================== 表格
class LyricsTable(ExcelTable):
    def __init__(self, win):
        super().__init__([Col("chk", "", "check", 44), Col("time", "時間", "time", 120),
                          Col("text", "歌詞", "text", stretch=True)], blank_row=True)
        self.win = win

    @property
    def lines(self) -> list[LyricLine]:
        return self.win.project.lyrics

    def row_count(self):
        return len(self.lines)

    def cell_text(self, r, key):
        l = self.lines[r]
        return fmt_time(l.time) if key == "time" else l.text

    def cell_check(self, r, key):
        return r in self.win.lyric_checked

    def set_check(self, r, key, v):
        (self.win.lyric_checked.add if v else self.win.lyric_checked.discard)(r)

    def set_value(self, r, key, text):
        new = r >= len(self.lines)
        if key == "time":
            t = parse_time(text)
            if text and t is None:
                return False
            if t is not None and self.win.project.song_duration and t > self.win.project.song_duration + 1:
                return False
            if new:
                if t is None:
                    return True
                self.lines.append(LyricLine(t, ""))
            else:
                self.lines[r].time = t
                self.lines[r].est = False
        else:
            if new:
                if not text.strip():
                    return True
                self.lines.append(LyricLine(None, text.strip()))
            else:
                self.lines[r].text = text.strip()
        return True

    def decorate(self, r, items):
        if self.lines[r].est:
            it = items["time"]
            it.setForeground(QBrush(self.palette().placeholderText().color()))
            f = it.font()
            f.setItalic(True)
            it.setFont(f)
            it.setToolTip("自動估的時間，還沒對準。用「邊聽邊標記」或直接改。")

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Paste) and self.state() != QAbstractItemView.EditingState:
            text = QGuiApplication.clipboard().text()
            if "\n" in text.strip():
                self.win.paste_lyrics(max(0, self.currentRow()), text)
                return
        super().keyPressEvent(e)


class ClipsTable(ExcelTable):
    def __init__(self, win):
        super().__init__([Col("chk", "", "check", 44), Col("star", "星號", "star", 64),
                          Col("thumb", "縮圖", "thumb", 144), Col("name", "檔名", "label", stretch=True),
                          Col("start", "開始", "time", 112), Col("end", "結束", "time", 112),
                          Col("sec", "秒數", "seconds", 92), Col("audio", "原音", "bool", 72)], row_height=80)
        self.win = win
        self.setIconSize(QSize(128, 72))

    @property
    def clips(self) -> list[Clip]:
        return self.win.project.clips

    def row_count(self):
        return len(self.clips)

    def editable(self, r, col):
        if r >= len(self.clips):
            return False
        c = self.clips[r]
        if col.key in ("start", "end"):
            return c.kind == "video"
        return col.key == "sec"

    def cell_text(self, r, key):
        c = self.clips[r]
        if key == "name":
            return os.path.basename(c.path)
        if key == "star":
            return "★" if c.star else ""
        if key == "start":
            return fmt_time(c.start) if c.kind == "video" else ""
        if key == "end":
            return fmt_time(c.end) if c.kind == "video" else ""
        if key == "sec":
            L = c.length()
            return f"{L:,.1f}" if L else ("自動" if c.kind == "photo" else "")
        return ""

    def cell_check(self, r, key):
        c = self.clips[r]
        if key == "chk":
            return c.checked
        if key == "audio":
            return c.keep_audio if c.kind == "video" else None
        return None

    def set_check(self, r, key, v):
        c = self.clips[r]
        if key == "chk":
            c.checked = v
        elif key == "audio":
            c.keep_audio = v

    def set_value(self, r, key, text):
        c = self.clips[r]
        if key == "sec":
            if not text or text == "自動":
                if c.kind == "photo":
                    c.seconds = None
                    return True
                return False
            v = parse_seconds(text)
            if v is None or v > 600:
                return False
            if c.kind == "photo":
                c.seconds = v
            else:
                dur = media.probe(c.path)["duration"]
                s = c.start or 0.0
                if dur and s + v > dur + 0.05:
                    return False
                c.end = round(s + v, 2)
            return True
        t = parse_time(text)
        if t is None:
            return False
        dur = media.probe(c.path)["duration"]
        if dur and t > dur + 0.05:
            return False
        if key == "start":
            if c.end is not None and t >= c.end:
                return False
            c.start = t
        else:
            if c.start is not None and t <= c.start:
                return False
            c.end = t
        return True

    def decorate(self, r, items):
        c = self.clips[r]
        st = items["star"]
        st.setForeground(QBrush(QColor(GOLD)))
        f = st.font()
        f.setPointSize(18)
        st.setFont(f)
        th = items["thumb"]
        icon = self.win.thumb_icon(c)
        th.setIcon(icon if icon else QIcon())
        th.setText("" if icon else ("照片" if c.kind == "photo" else "影片"))
        items["name"].setToolTip(c.path)
        if c.kind == "photo" and not c.seconds:
            items["sec"].setForeground(QBrush(self.palette().placeholderText().color()))


STYLE_ROWS = [
    # (key, 項目名稱, 選項, 數字靠右)
    ("font", "字體", None, False),
    ("font_size", "字大小", [str(x) for x in SIZES], True),
    ("color", "字幕顏色", list(COLORS), False),
    ("position", "字幕位置", POSITIONS, False),
    ("effect", "字幕效果", EFFECTS, False),
    ("motion", "照片動態", MOTIONS, False),
    ("transition", "轉場", TRANSITIONS, False),
    ("portrait_bg", "方向不合時", PORTRAIT_BGS, False),
    ("star_chorus", "星號放副歌", YESNO, False),
    ("seg_len", "自動挑每段秒數", SEG_LENS, True),
]


class StyleTable(ExcelTable):
    """風格設定：一列一個項目，右邊那格點一下才出現下拉。"""

    def __init__(self, win):
        super().__init__([Col("name", "項目", "label", 190), Col("value", "設定", "combo", stretch=True)],
                         row_height=46)
        self.win = win

    @property
    def st(self) -> Style:
        return self.win.project.style

    def row_count(self):
        return len(STYLE_ROWS)

    def editable(self, r, col):
        return col.key == "value"

    def options_for(self, r, col):
        key, _, opts, _ = STYLE_ROWS[r]
        if key == "font":
            return [n for n, _ in media.available_fonts()] + [OTHER_FONT]
        return opts

    def cell_text(self, r, key):
        k, label, _, _ = STYLE_ROWS[r]
        if key == "name":
            return label
        s = self.st
        if k == "font":
            return media.font_name(s.font or media.default_font())
        if k == "font_size":
            return str(s.font_size)
        if k == "seg_len":
            return f"{s.seg_len:g}"
        return str(getattr(s, k))

    def decorate(self, r, items):
        if STYLE_ROWS[r][3]:
            items["value"].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def set_value(self, r, key, text):
        k = STYLE_ROWS[r][0]
        s = self.st
        if k == "font":
            if text == OTHER_FONT:
                QTimer.singleShot(0, self.win.pick_font_file)
                return True
            for n, p in media.available_fonts():
                if n == text:
                    s.font = p
            return True
        if k == "font_size":
            try:
                v = int(text)
            except ValueError:
                return False
            if not 12 <= v <= 300:
                return False
            s.font_size = v
            return True
        if k == "seg_len":
            v = parse_seconds(text)
            if v is None:
                return False
            s.seg_len = v
            return True
        setattr(s, k, text)
        return True


# ======================================================================== 介面零件
class NoWheelSlider(QSlider):
    def wheelEvent(self, e):
        e.ignore()


def section(title: str) -> tuple[QFrame, QHBoxLayout, QVBoxLayout]:
    box = QFrame()
    box.setObjectName("section")
    v = QVBoxLayout(box)
    v.setContentsMargins(12, 10, 12, 12)
    v.setSpacing(8)
    headw = QWidget()
    headw.setFixedHeight(46)          # 每個區塊標題列一樣高，標題才會對齊
    head = QHBoxLayout(headw)
    head.setContentsMargins(0, 0, 0, 0)
    head.setSpacing(8)
    lab = QLabel(title)
    lab.setObjectName("sectionTitle")
    head.addWidget(lab)
    head.addStretch(1)
    v.addWidget(headw)
    return box, head, v


def btn(text, slot=None, primary=False) -> QPushButton:
    b = QPushButton(text)
    b.setMinimumHeight(44)
    b.setCursor(Qt.PointingHandCursor)
    if primary:
        b.setObjectName("primary")
    if slot:
        b.clicked.connect(slot)
    return b


class PreviewLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setObjectName("preview")
        self.img: QImage | None = None

    def set_image(self, img: QImage):
        self.img = img
        self._show()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._show()

    def _show(self):
        if self.img is None:
            return
        pm = QPixmap.fromImage(self.img).scaled(self.width() - 8, self.height() - 8,
                                                Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(pm)


# ======================================================================== 主視窗
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"MV 製作工具 v{VERSION}")
        self.settings = QSettings("ArchieKUO", APP_NAME)
        self.project = Project()
        self.project_path = ""
        self.lyric_checked: set[int] = set()
        self.thumbs: dict = {}
        self.thumb_pending: set = set()
        self.bridge = ThumbBridge()
        self.bridge.ready.connect(self._thumb_ready)
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(2)
        self.composer = None
        self.comp_dirty = True
        self.worker: Worker | None = None
        self.marking = False
        self.mark_row = 0
        self.seek_t = 0.0
        self.last_out = ""
        self._build()
        self._init_player()

        self.save_timer = QTimer(self, singleShot=True, interval=800, timeout=self.save_project)
        self.seek_timer = QTimer(self, singleShot=True, interval=60, timeout=self.render_still)
        self.play_timer = QTimer(self, interval=50, timeout=self._tick)

        last = self.settings.value("last_project", "")
        if last and os.path.exists(last):
            self.load_project(last, quiet=True)
        self.refresh_all()

    # ------------------------------------------------------------ 版面
    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(10)

        # 標題列
        top = QHBoxLayout()
        title = QLabel("MV 製作工具")
        title.setObjectName("appTitle")
        author = QLabel(f"作者：ArchieKUO　v{VERSION}")
        author.setObjectName("author")
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(author)
        v.addLayout(top)

        # 工具列
        tb = QHBoxLayout()
        tb.setSpacing(8)
        tb.addWidget(btn("新專案", self.new_project))
        tb.addWidget(btn("選歌曲", self.pick_song))
        tb.addWidget(btn("匯入歌詞", self.import_lyrics))
        tb.addWidget(btn("選素材資料夾", self.pick_folder))
        tb.addWidget(btn("加入檔案", self.add_files))
        tb.addStretch(1)
        self.b169 = btn("橫式 16:9")
        self.b916 = btn("直式 9:16")
        for b in (self.b169, self.b916):
            b.setCheckable(True)
            b.setObjectName("toggle")
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        grp.addButton(self.b169)
        grp.addButton(self.b916)
        self.b169.clicked.connect(lambda: self.set_orientation("16:9"))
        self.b916.clicked.connect(lambda: self.set_orientation("9:16"))
        tb.addWidget(self.b169)
        tb.addWidget(self.b916)
        tb.addSpacing(8)
        self.b_export = btn("輸出 MP4", self.export_mp4, primary=True)
        tb.addWidget(self.b_export)
        v.addLayout(tb)

        # 中間：預覽 | 歌詞 ； 下面：素材
        vs = self.vsplit = QSplitter(Qt.Vertical)
        vs.setChildrenCollapsible(False)
        hs = QSplitter(Qt.Horizontal)
        hs.setChildrenCollapsible(False)

        pv, pvh, pvv = section("預覽")
        self.song_label = QLabel("還沒選歌曲")
        self.song_label.setObjectName("muted")
        pvh.addWidget(self.song_label)
        self.preview = PreviewLabel()
        pvv.addWidget(self.preview, 1)
        ctl = QHBoxLayout()
        ctl.setSpacing(10)
        self.b_play = btn("播放", self.toggle_play)
        self.b_play.setFixedWidth(110)
        self.slider = NoWheelSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setMinimumHeight(36)
        self.slider.sliderMoved.connect(self._slider_moved)
        self.slider.sliderPressed.connect(lambda: self._slider_moved(self.slider.value()))
        self.time_label = QLabel("00:00.0 / 00:00.0")
        self.time_label.setObjectName("timeLabel")
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_label.setMinimumWidth(210)
        ctl.addWidget(self.b_play)
        ctl.addWidget(self.slider, 1)
        ctl.addWidget(self.time_label)
        pvv.addLayout(ctl)
        hs.addWidget(pv)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("rightTabs")
        lyp = QWidget()
        lyv = QVBoxLayout(lyp)
        lyv.setContentsMargins(10, 10, 10, 10)
        lyv.setSpacing(8)
        lyh = QHBoxLayout()
        lyh.setSpacing(8)
        self.b_mark = btn("邊聽邊標記", self.toggle_mark)
        self.b_mark.setCheckable(True)
        lyh.addWidget(self.b_mark)
        lyh.addStretch(1)
        lyh.addWidget(btn("刪除所選", self.delete_lyrics))
        lyv.addLayout(lyh)
        self.mark_hint = QLabel("")
        self.mark_hint.setObjectName("markHint")
        self.mark_hint.setWordWrap(True)
        self.mark_hint.hide()
        lyv.addWidget(self.mark_hint)
        self.lyrics = LyricsTable(self)
        self.lyrics.changed.connect(self.project_changed)
        self.lyrics.cellClicked.connect(self._lyric_clicked)
        lyv.addWidget(self.lyrics, 1)
        self.tabs.addTab(lyp, "歌詞與時間")

        stp = QWidget()
        stv = QVBoxLayout(stp)
        stv.setContentsMargins(10, 10, 10, 10)
        stv.setSpacing(8)
        tip = QLabel("點右邊那一格，再點一下或按 ↓ 打開選項")
        tip.setObjectName("muted")
        tip.setMinimumHeight(44)
        stv.addWidget(tip)
        self.style_tbl = StyleTable(self)
        self.style_tbl.changed.connect(self.project_changed)
        stv.addWidget(self.style_tbl, 1)
        self.tabs.addTab(stp, "風格")
        ly = self.tabs
        hs.addWidget(ly)
        hs.setStretchFactor(0, 5)
        hs.setStretchFactor(1, 4)
        vs.addWidget(hs)

        cl, clh, clv = section("素材片段（先勾選，再按按鈕）")
        self.b_auto = btn("自動挑片段", self.auto_pick, primary=False)
        self.b_auto.setObjectName("accent")
        for w in (self.b_auto, btn("上移", lambda: self.move_clips(-1)), btn("下移", lambda: self.move_clips(1)),
                  self._star_btn(), btn("刪除所選", self.delete_clips)):
            clh.addWidget(w)
        self.clips = ClipsTable(self)
        self.clips.changed.connect(self.project_changed)
        self.clips.cellClicked.connect(self._clip_clicked)
        clv.addWidget(self.clips, 1)
        vs.addWidget(cl)
        vs.setStretchFactor(0, 1)
        vs.setStretchFactor(1, 1)
        v.addWidget(vs, 1)

        # 狀態列
        st = QHBoxLayout()
        st.setSpacing(10)
        self.status = QLabel("")
        self.status.setObjectName("status")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(True)
        self.progress.setFixedWidth(360)
        self.progress.setMinimumHeight(30)
        self.progress.hide()
        self.b_cancel = btn("取消", self.cancel_work)
        self.b_cancel.hide()
        self.b_open = btn("開啟資料夾", self.open_output_folder)
        self.b_open.hide()
        st.addWidget(self.status, 1)
        st.addWidget(self.progress)
        st.addWidget(self.b_cancel)
        st.addWidget(self.b_open)
        v.addLayout(st)

    def _star_btn(self):
        b = btn("標星號", self.star_clips)
        b.setToolTip("星號片段一定會用；「風格」裡「星號放副歌」選「是」時，會優先放在副歌")
        return b

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_sized", False):
            self._sized = True
            h = max(400, self.vsplit.height())
            self.vsplit.setSizes([int(h * 0.48), int(h * 0.52)])

    def _init_player(self):
        self.player = None
        if os.environ.get("MV_SMOKE") and not os.environ.get("MV_AUDIO_TEST"):
            return                          # 自動測試的機器沒有音效卡
        from .player import AudioPlayer
        self.player = AudioPlayer(self)
        self.player.stateChanged.connect(self._play_state)
        self.player.message.connect(lambda m: self.say(m, warn=True))

    # ------------------------------------------------------------ 專案
    def project_file_for(self, song: str) -> str:
        if song:
            return os.path.splitext(song)[0] + ".MV專案.json"
        return os.path.join(data_dir(), "未命名.MV專案.json")

    def save_project(self):
        path = self.project_file_for(self.project.song)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.project.to_json())
            self.project_path = path
            self.settings.setValue("last_project", path)
        except OSError:
            # 歌曲資料夾不能寫：改存到使用者資料夾
            alt = os.path.join(data_dir(), os.path.basename(path))
            try:
                with open(alt, "w", encoding="utf-8") as f:
                    f.write(self.project.to_json())
                self.settings.setValue("last_project", alt)
            except OSError:
                pass

    def load_project(self, path: str, quiet=False):
        try:
            with open(path, encoding="utf-8") as f:
                p = Project.from_json(f.read())
        except Exception as ex:
            if not quiet:
                self.say(f"讀不到專案：{ex}", error=True)
            return False
        p.clips = [c for c in p.clips if os.path.exists(c.path)]
        self.project = p
        self.project_path = path
        self.lyric_checked.clear()
        if p.song and os.path.exists(p.song):
            self._set_player_source(p.song)
        return True

    def project_changed(self):
        self.comp_dirty = True
        self.save_timer.start()
        self.update_song_label()
        if not self.playing():
            self.seek_timer.start()

    def refresh_all(self):
        self.lyrics.refresh()
        self.clips.refresh()
        self.style_tbl.refresh()
        self.b169.setChecked(self.project.style.orientation == "16:9")
        self.b916.setChecked(self.project.style.orientation != "16:9")
        self.update_song_label()
        self.request_thumbs()
        self.comp_dirty = True
        self.seek_timer.start()

    def update_song_label(self):
        p = self.project
        if p.song:
            self.song_label.setText(f"{os.path.basename(p.song)}（{fmt_time(p.song_duration)[:-2]}）")
        else:
            self.song_label.setText("還沒選歌曲")
        self.time_label.setText(f"{fmt_time(self.seek_t)} / {fmt_time(self.total_len())}")

    def total_len(self) -> float:
        p = self.project
        if p.song_duration:
            return p.song_duration
        plan = build_plan(p)
        return plan[-1].t0 + plan[-1].dur if plan else 0.0

    def new_project(self):
        if (self.project.clips or self.project.lyrics) and not self.confirm("開新專案？目前的專案已自動存檔。"):
            return
        self.stop()
        self.project = Project()
        self.project_path = ""
        self.settings.setValue("last_project", "")
        self.seek_t = 0.0
        self.refresh_all()
        self.say("已開新專案")

    # ------------------------------------------------------------ 歌曲 / 歌詞 / 素材
    def start_dir(self, key="dir"):
        return self.settings.value(key, "") or QStandardPaths.writableLocation(QStandardPaths.MusicLocation)

    def pick_song(self):
        exts = " ".join("*" + e for e in sorted(AUDIO_EXT))
        path, _ = QFileDialog.getOpenFileName(self, "選歌曲", self.start_dir("song_dir"), f"音樂檔 ({exts})")
        if not path:
            return
        self.settings.setValue("song_dir", os.path.dirname(path))
        self.stop()
        pf = self.project_file_for(path)
        if os.path.exists(pf) and self.load_project(pf):
            self.say("已讀取這首歌之前的設定")
        else:
            self.project.song = path
            self.project.song_duration = media.probe(path)["duration"]
            self._set_player_source(path)
            if any(l.time is None for l in self.project.lyrics):
                self._after_lyrics_loaded()
            # 同資料夾有同名歌詞檔就自動讀
            for ext in (".lrc", ".txt"):
                lf = os.path.splitext(path)[0] + ext
                if os.path.exists(lf) and not self.project.lyrics:
                    self._read_lyrics_file(lf)
                    break
        self.seek_t = 0.0
        self.save_project()
        self.refresh_all()

    def _set_player_source(self, path):
        if self.player:
            self.player.setSource(path)

    def import_lyrics(self):
        path, _ = QFileDialog.getOpenFileName(self, "匯入歌詞", self.start_dir("song_dir"),
                                              "歌詞檔 (*.lrc *.txt)")
        if not path:
            return
        if self.project.lyrics and not self.confirm("要用新的歌詞取代目前的歌詞嗎？"):
            return
        self._read_lyrics_file(path)
        self.lyric_checked.clear()
        self.lyrics.errors.clear()
        self.lyrics.refresh()
        self.project_changed()

    def _read_lyrics_file(self, path):
        raw = open(path, "rb").read()
        for enc in ("utf-8-sig", "utf-16", "cp950", "gb18030"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", "ignore")
        self.project.lyrics = parse_lyrics_text(text)
        self._after_lyrics_loaded()

    def _after_lyrics_loaded(self):
        lines = self.project.lyrics
        n = len(lines)
        untimed = sum(1 for l in lines if l.time is None)
        if not n:
            self.say("歌詞檔裡沒有讀到歌詞", error=True)
        elif untimed and not self.project.song_duration:
            self.say(f"讀到 {n} 句歌詞，還沒有時間。請按「選歌曲」，選好歌就會先幫你排上時間。", warn=True)
        elif untimed:
            spread_untimed(lines, self.project.song_duration)
            self.say(f"讀到 {n} 句歌詞。歌詞檔裡沒有時間，已先平均排好（灰色字）；"
                     f"請按「邊聽邊標記」對準。", warn=True)
        else:
            self.say(f"讀到 {n} 句歌詞")

    def paste_lyrics(self, row: int, text: str):
        new = parse_lyrics_text(text)
        if not new:
            return
        lines = self.project.lyrics
        if any(l.time is not None for l in new):
            lines[row:row] = new
        else:
            for i, l in enumerate(new):
                if row + i < len(lines):
                    lines[row + i].text = l.text
                else:
                    lines.append(LyricLine(None, l.text))
        self.lyrics.refresh()
        self.project_changed()

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "選素材資料夾", self.start_dir("media_dir"))
        if not d:
            return
        self.settings.setValue("media_dir", d)
        files = [os.path.join(d, f) for f in os.listdir(d) if kind_of(f)]
        if not files:
            self.say("這個資料夾裡沒有照片或影片", error=True)
            return
        files.sort(key=lambda p: (os.path.getmtime(p), p.lower()))
        self._add_media(files)

    def add_files(self):
        exts = " ".join("*" + e for e in sorted(core.IMAGE_EXT | core.VIDEO_EXT))
        files, _ = QFileDialog.getOpenFileNames(self, "加入照片或影片", self.start_dir("media_dir"),
                                                f"照片與影片 ({exts})")
        if files:
            self.settings.setValue("media_dir", os.path.dirname(files[0]))
            self._add_media(files)

    def _add_media(self, files):
        if self.project.clips:
            mb = QMessageBox(self)
            mb.setWindowTitle("加入素材")
            mb.setText(f"要加入 {len(files)} 個檔案。目前已經有 {len(self.project.clips)} 個素材。")
            b_add = mb.addButton("加在後面", QMessageBox.AcceptRole)
            b_rep = mb.addButton("取代全部", QMessageBox.DestructiveRole)
            mb.addButton("取消", QMessageBox.RejectRole)
            mb.exec()
            if mb.clickedButton() == b_rep:
                self.project.clips.clear()
            elif mb.clickedButton() != b_add:
                return
        long_vid = 0
        for f in files:
            k = kind_of(f)
            if k == "video":
                dur = media.probe(f)["duration"]
                if dur > 20:
                    long_vid += 1
                self.project.clips.append(Clip(f, "video", 0.0, round(min(dur, 6.0), 1) if dur else 6.0))
            elif k == "photo":
                self.project.clips.append(Clip(f, "photo"))
        self.clips.errors.clear()
        self.clips.refresh()
        self.request_thumbs()
        self.project_changed()
        msg = f"加入 {len(files)} 個檔案"
        if long_vid:
            msg += f"。有 {long_vid} 段長影片，可以按「自動挑片段」挑出好看的段落"
        self.say(msg)

    # ------------------------------------------------------------ 素材操作
    def checked_clips(self) -> list[int]:
        return [i for i, c in enumerate(self.project.clips) if c.checked]

    def move_clips(self, d: int):
        idx = self.checked_clips()
        if not idx:
            self.say("請先勾選要移動的素材", error=True)
            return
        clips = self.project.clips
        rng = idx if d < 0 else list(reversed(idx))
        for i in rng:
            j = i + d
            if 0 <= j < len(clips) and not clips[j].checked:
                clips[i], clips[j] = clips[j], clips[i]
        self.clips.errors.clear()
        self.clips.refresh()
        self.project_changed()

    def star_clips(self):
        idx = self.checked_clips()
        if not idx:
            self.say("請先勾選要標星號的素材", error=True)
            return
        clips = self.project.clips
        val = not all(clips[i].star for i in idx)
        for i in idx:
            clips[i].star = val
        self.clips.refresh()
        self.project_changed()

    def delete_clips(self):
        idx = self.checked_clips()
        if not idx:
            self.say("請先勾選要刪除的素材", error=True)
            return
        if not self.confirm(f"確定要從清單刪除 {len(idx)} 個素材嗎？（電腦裡的檔案不會被刪掉）"):
            return
        self.project.clips = [c for c in self.project.clips if not c.checked]
        self.clips.errors.clear()
        self.clips.refresh()
        self.project_changed()

    def delete_lyrics(self):
        idx = sorted(self.lyric_checked)
        if not idx:
            self.say("請先勾選要刪除的歌詞", error=True)
            return
        if not self.confirm(f"確定要刪除 {len(idx)} 句歌詞嗎？"):
            return
        self.project.lyrics = [l for i, l in enumerate(self.project.lyrics) if i not in self.lyric_checked]
        self.lyric_checked.clear()
        self.lyrics.errors.clear()
        self.lyrics.refresh()
        self.project_changed()

    def pick_font_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "選字型檔", r"C:\Windows\Fonts", "字型檔 (*.ttf *.ttc *.otf)")
        if path:
            self.project.style.font = path
        self.style_tbl.refresh()
        self.project_changed()

    def set_orientation(self, o):
        self.project.style.orientation = o
        self.project_changed()

    # ------------------------------------------------------------ 縮圖
    def thumb_key(self, c: Clip):
        return (c.path, round(c.start or 0, 1) if c.kind == "video" else 0)

    def thumb_icon(self, c: Clip):
        return self.thumbs.get(self.thumb_key(c))

    def request_thumbs(self):
        for c in self.project.clips:
            k = self.thumb_key(c)
            if k not in self.thumbs and k not in self.thumb_pending:
                self.thumb_pending.add(k)
                self.pool.start(ThumbJob(k, c.path, c.kind, c.start or 0, self.bridge))

    def _thumb_ready(self, key, img):
        self.thumb_pending.discard(key)
        self.thumbs[key] = QIcon(QPixmap.fromImage(img))
        for r, c in enumerate(self.project.clips):
            if self.thumb_key(c) == key:
                it = self.clips.item(r, 2)
                if it:
                    it.setIcon(self.thumbs[key])
                    it.setText("")

    # ------------------------------------------------------------ 預覽
    def ensure_composer(self):
        if self.composer is None or self.comp_dirty:
            if self.composer:
                self.composer.close()
            W, H = media.canvas_size(self.project.style, PREVIEW_W)
            self.composer = media.Composer(self.project, W, H, fps=15)
            self.comp_dirty = False
        return self.composer

    def show_frame(self, t: float):
        try:
            comp = self.ensure_composer()
            arr = comp.frame_at(max(0.0, t))
            self.preview.set_image(np_to_qimage(arr))
        except Exception:
            traceback.print_exc()

    def render_still(self):
        self.show_frame(self.seek_t)
        self._update_time(self.seek_t)

    def playing(self):
        return bool(self.player) and self.player.is_playing()

    def toggle_play(self):
        if not self.player or not self.project.song:
            self.say("請先選歌曲", error=True)
            return
        if self.playing():
            self.player.pause()
        else:
            self.player.setPosition(int(self.seek_t * 1000))
            self.player.play()

    def stop(self):
        if self.player:
            self.player.pause()
        if self.marking:
            self.toggle_mark()

    def _play_state(self, _s):
        on = self.playing()
        self.b_play.setText("暫停" if on else "播放")
        if on:
            self.play_timer.start()
        else:
            self.play_timer.stop()
            if self.player:
                self.seek_t = self.player.position() / 1000
            self.render_still()

    def _tick(self):
        t = self.player.position() / 1000
        self.seek_t = t
        self.show_frame(t)
        self._update_time(t)

    def _update_time(self, t):
        total = self.total_len()
        self.time_label.setText(f"{fmt_time(t)} / {fmt_time(total)}")
        if not self.slider.isSliderDown() and total:
            self.slider.blockSignals(True)
            self.slider.setValue(int(1000 * t / total))
            self.slider.blockSignals(False)
        # 目前這句歌詞、目前這段素材
        cur = -1
        for i, l in enumerate(self.project.lyrics):
            if l.time is not None and l.time <= t:
                if cur < 0 or l.time >= self.project.lyrics[cur].time:
                    cur = i
        if not self.marking:
            self.lyrics.set_active_row(cur)
        comp = self.composer
        if comp and comp.plan:
            seg = comp.plan[comp.seg_index(t)]
            row = next((i for i, c in enumerate(self.project.clips) if c is seg.clip), -1)
            self.clips.set_active_row(row)

    def _slider_moved(self, v):
        self.seek_t = self.total_len() * v / 1000
        if self.playing():
            self.player.setPosition(int(self.seek_t * 1000))
        else:
            self.seek_timer.start()
            self._update_time(self.seek_t)

    def seek(self, t):
        self.seek_t = max(0.0, t)
        if self.playing():
            self.player.setPosition(int(self.seek_t * 1000))
        else:
            self.render_still()

    def _lyric_clicked(self, r, c):
        if self.marking:
            self.mark_row = r
            self._update_mark_hint()
            return
        if r < len(self.project.lyrics) and self.project.lyrics[r].time is not None and c != 0:
            self.seek(self.project.lyrics[r].time)

    def _clip_clicked(self, r, c):
        if r >= len(self.project.clips) or c == 0:
            return
        comp = self.ensure_composer()
        clip = self.project.clips[r]
        seg = next((s for s in comp.plan if s.clip is clip), None)
        if seg:
            self.seek(seg.t0 + min(0.6, seg.dur / 2))

    # ------------------------------------------------------------ 邊聽邊標記
    def toggle_mark(self):
        if not self.marking:
            if not self.project.song or not self.player:
                self.b_mark.setChecked(False)
                self.say("請先選歌曲", error=True)
                return
            if not self.project.lyrics:
                self.b_mark.setChecked(False)
                self.say("請先匯入或輸入歌詞", error=True)
                return
            lines = self.project.lyrics
            # 從第一句還沒對準的開始；全部對過了就從目前選的那句開始
            r = next((i for i, l in enumerate(lines) if l.time is None or l.est), -1)
            if r < 0:
                r = self.lyrics.currentRow()
                if not 0 <= r < len(lines):
                    r = 0
            self.mark_row = r
            prev = lines[r - 1].time if r > 0 and lines[r - 1].time is not None else 0.0
            self.marking = True
            self.b_mark.setChecked(True)
            self.b_mark.setText("停止標記")
            QApplication.instance().installEventFilter(self)
            self.lyrics.clearFocus()
            self.mark_hint.show()
            self._update_mark_hint()
            self.seek_t = max(0.0, prev - 1.0)
            self.player.setPosition(int(self.seek_t * 1000))
            self.player.play()
        else:
            self.marking = False
            QApplication.instance().removeEventFilter(self)
            self.b_mark.setChecked(False)
            self.b_mark.setText("邊聽邊標記")
            self.mark_hint.hide()
            if self.player:
                self.player.pause()
            self.lyrics.refresh()
            self.project_changed()

    def _update_mark_hint(self):
        lines = self.project.lyrics
        if self.mark_row < len(lines):
            self.mark_hint.setText(f"唱到這句時按空白鍵：「{lines[self.mark_row].text}」　（Esc 停止）")
            self.lyrics.set_active_row(self.mark_row)
        else:
            self.mark_hint.setText("全部標記完成")

    def eventFilter(self, obj, ev):
        if self.marking and ev.type() == QEvent.KeyPress and not ev.isAutoRepeat():
            if ev.key() == Qt.Key_Space:
                lines = self.project.lyrics
                if self.mark_row < len(lines):
                    lines[self.mark_row].time = round(max(0.0, self.player.position() / 1000 - 0.1), 1)
                    lines[self.mark_row].est = False
                    self.lyrics.refresh()
                    self.mark_row += 1
                    self._update_mark_hint()
                    self.comp_dirty = True
                    if self.mark_row >= len(lines):
                        QTimer.singleShot(1500, lambda: self.marking and self.toggle_mark())
                return True
            if ev.key() == Qt.Key_Escape:
                self.toggle_mark()
                return True
        return super().eventFilter(obj, ev)

    # ------------------------------------------------------------ 自動挑片段
    def auto_pick(self):
        if self.worker:
            return
        if not self.project.clips:
            self.say("請先選素材資料夾或加入檔案", error=True)
            return
        if not self.confirm("自動挑片段會重新整理素材清單：\n"
                            "・長影片會切成幾段好看的片段\n・幾乎一樣的照片只留一張\n\n要繼續嗎？"):
            return
        self.stop()
        clips = [Clip(**{k: getattr(c, k) for k in ("path", "kind", "start", "end", "seconds", "star", "keep_audio")})
                 for c in self.project.clips]
        self._run(Worker(_do_autopick, clips, self.project.song_duration, self.project.style.seg_len),
                  "正在分析影片…", self._autopick_done)

    def _autopick_done(self, clips):
        before = len(self.project.clips)
        self.project.clips = clips
        self.clips.errors.clear()
        self.clips.refresh()
        self.request_thumbs()
        self.project_changed()
        self.say(f"自動挑片段完成：{before} 個素材整理成 {len(clips)} 段")

    # ------------------------------------------------------------ 輸出
    def export_mp4(self):
        if self.worker:
            return
        p = self.project
        if not p.clips:
            self.say("請先加入照片或影片", error=True)
            return
        if not p.song:
            if not self.confirm("還沒選歌曲，影片會沒有聲音。要繼續嗎？"):
                return
        self.stop()
        base = os.path.splitext(os.path.basename(p.song))[0] if p.song else "MV"
        tag = "橫式" if p.style.orientation == "16:9" else "直式"
        d = self.settings.value("out_dir", "") or (os.path.dirname(p.song) if p.song else
                                                    QStandardPaths.writableLocation(QStandardPaths.MoviesLocation))
        suggest = unique_path(os.path.join(d, f"{base}_{tag}_v1.mp4"))
        out, _ = QFileDialog.getSaveFileName(self, "輸出 MP4", suggest, "MP4 影片 (*.mp4)")
        if not out:
            return
        if not out.lower().endswith(".mp4"):
            out += ".mp4"
        self.settings.setValue("out_dir", os.path.dirname(out))
        snap = Project.from_json(p.to_json())
        self.b_open.hide()
        self._run(Worker(_do_export, snap, out), "正在輸出影片…", self._export_done)
        self._export_start = time.time()

    def _export_done(self, path):
        self.last_out = path
        mins = (time.time() - self._export_start) / 60
        self.say(f"完成：{os.path.basename(path)}（花了 {mins:.1f} 分鐘）")
        self.b_open.show()

    def open_output_folder(self):
        if not self.last_out:
            return
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.last_out)])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(self.last_out)])

    # ------------------------------------------------------------ 背景工作共用
    def _run(self, worker: Worker, msg, on_done):
        self.worker = worker
        self.say(msg)
        self.progress.setValue(0)
        self.progress.show()
        self.b_cancel.show()
        self.b_export.setEnabled(False)
        self.b_auto.setEnabled(False)
        worker.progress.connect(lambda f: self.progress.setValue(int(f * 1000)))
        worker.log.connect(self.say)
        worker.done.connect(on_done)
        worker.failed.connect(lambda m: self.say(m if m == "已取消" else f"失敗：{m}", error=m != "已取消"))
        worker.finished.connect(self._work_finished)
        worker.start()

    def _work_finished(self):
        self.worker = None
        self.progress.hide()
        self.b_cancel.hide()
        self.b_export.setEnabled(True)
        self.b_auto.setEnabled(True)

    def cancel_work(self):
        if self.worker and self.confirm("確定要取消嗎？"):
            self.worker.cancel()

    # ------------------------------------------------------------ 雜項
    def say(self, msg, error=False, warn=False):
        self.status.setText(msg)
        self.status.setProperty("kind", "error" if error else "warn" if warn else "")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def confirm(self, text) -> bool:
        mb = QMessageBox(self)
        mb.setWindowTitle("確認")
        mb.setText(text)
        yes = mb.addButton("確定", QMessageBox.AcceptRole)
        mb.addButton("取消", QMessageBox.RejectRole)
        mb.exec()
        return mb.clickedButton() == yes

    def closeEvent(self, e):
        if self.worker:
            if not self.confirm("還在處理中，確定要關閉嗎？"):
                e.ignore()
                return
            self.worker.cancel()
            self.worker.wait(5000)
        self.save_project()
        self.pool.clear()
        if self.player:
            self.player.close()
        if self.composer:
            self.composer.close()
        super().closeEvent(e)


# ======================================================================== 樣式
def checkbox_images(dark: bool) -> tuple[str, str]:
    """畫勾選框圖片（大、兩種模式都清楚）。"""
    from PySide6.QtGui import QPainter, QPen
    d = data_dir()
    out = []
    for checked in (False, True):
        path = os.path.join(d, f"chk_{'d' if dark else 'l'}_{int(checked)}.png").replace("\\", "/")
        pm = QPixmap(48, 48)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        if checked:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#3B82D6"))
            p.drawRoundedRect(3, 3, 42, 42, 9, 9)
            pen = QPen(QColor("white"), 6)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawPolyline([QPoint(13, 25), QPoint(21, 33), QPoint(36, 15)])
        else:
            p.setPen(QPen(QColor("#9A9A9A" if dark else "#8A8A8A"), 4))
            p.setBrush(QColor("#262626" if dark else "#FFFFFF"))
            p.drawRoundedRect(4, 4, 40, 40, 8, 8)
        p.end()
        pm.save(path)
        out.append(path.replace("\\", "/"))
    return out[0], out[1]


def stylesheet(dark: bool) -> str:
    unchk, chk = checkbox_images(dark)
    text2 = "#A8A8A8" if dark else "#666666"
    line = "#3A3A3A" if dark else "#D8D8D8"
    sec = "#242424" if dark else "#FFFFFF"
    head = "#2C2C2C" if dark else "#F2F2F2"
    acc = "#3B82D6"
    return f"""
    QWidget {{ font-size: 13pt; }}
    QLabel#appTitle {{ font-size: 18pt; font-weight: bold; }}
    QLabel#author {{ color: {GOLD}; font-style: italic; font-size: 13pt; }}
    QFrame#section {{ background: {sec}; border: 1px solid {line}; border-radius: 10px; }}
    QLabel#sectionTitle {{ font-size: 13pt; font-weight: bold; }}
    QLabel#muted {{ color: {text2}; }}
    QLabel#timeLabel {{ font-size: 13pt; }}
    QLabel#status {{ font-size: 15pt; padding: 4px 10px; border-radius: 8px; }}
    QLabel#status[kind="error"] {{ color: #FFFFFF; background: #C0392B; font-weight: bold; }}
    QLabel#status[kind="warn"] {{ color: #3A2A00; background: #F5C542; font-weight: bold; }}
    QLabel#markHint {{ background: rgba(201,151,28,0.18); border: 1px solid {GOLD}; border-radius: 8px;
                       padding: 8px 12px; font-size: 15pt; font-weight: bold; }}
    QLabel#preview {{ background: #000000; border-radius: 6px; }}
    QPushButton {{ padding: 6px 16px; border-radius: 8px; border: 1px solid {line}; background: {head}; }}
    QPushButton:hover {{ border-color: {acc}; }}
    QPushButton:disabled {{ color: {text2}; }}
    QPushButton#primary {{ background: {acc}; color: white; border: none; font-weight: bold; }}
    QPushButton#primary:disabled {{ background: #7A9CC4; }}
    QPushButton#accent {{ border: 2px solid {acc}; font-weight: bold; }}
    QPushButton#toggle:checked {{ border: 2px solid {acc}; background: rgba(59,130,214,0.18); font-weight: bold; }}
    QPushButton:checked {{ border: 2px solid {GOLD}; }}
    QTableWidget {{ gridline-color: {line}; border: 1px solid {line}; border-radius: 6px; }}
    QHeaderView::section {{ background: {head}; padding: 6px 8px; border: none;
                            border-bottom: 1px solid {line}; border-right: 1px solid {line}; font-weight: bold; }}
    QTableWidget::item {{ padding: 2px 8px; }}
    QTableWidget::indicator {{ width: 26px; height: 26px; }}
    QTableWidget::indicator:unchecked {{ image: url({unchk}); }}
    QTableWidget::indicator:checked {{ image: url({chk}); }}
    QComboBox {{ padding: 2px 8px; }}
    QTableWidget::item:selected {{ background: rgba(59,130,214,0.35); color: palette(text); }}
    QTableWidget {{ background: palette(base); }}
    QSplitter::handle {{ background: transparent; }}
    QSplitter::handle:vertical {{ height: 10px; }}
    QSplitter::handle:horizontal {{ width: 10px; }}
    QTabWidget::pane {{ border: 1px solid {line}; border-radius: 10px; background: {sec}; top: -1px; }}
    QTabBar::tab {{ padding: 10px 26px; margin-right: 4px; border: 1px solid {line}; border-bottom: none;
                    border-top-left-radius: 8px; border-top-right-radius: 8px; background: {head}; font-weight: bold; }}
    QTabBar::tab:selected {{ background: {sec}; border-top: 3px solid {acc}; }}
    QProgressBar {{ border: 1px solid {line}; border-radius: 6px; text-align: center; }}
    QProgressBar::chunk {{ background: {acc}; border-radius: 5px; }}
    """


def is_dark(app) -> bool:
    forced = os.environ.get("MV_THEME")
    if forced:
        return forced == "dark"
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def apply_theme(app):
    """跟著 Windows 的深色 / 淺色模式。"""
    from PySide6.QtGui import QPalette
    dark = is_dark(app)
    pal = QPalette()
    if dark:
        c = dict(window="#1C1C1C", base="#262626", alt="#2C2C2C", text="#F2F2F2", button="#2E2E2E",
                 mid="#555555", ph="#8A8A8A", tip="#333333")
    else:
        c = dict(window="#F0F0F0", base="#FFFFFF", alt="#F7F7F7", text="#1A1A1A", button="#F4F4F4",
                 mid="#BBBBBB", ph="#8C8C8C", tip="#FFFFE6")
    roles = {QPalette.Window: c["window"], QPalette.Base: c["base"], QPalette.AlternateBase: c["alt"],
             QPalette.WindowText: c["text"], QPalette.Text: c["text"], QPalette.ButtonText: c["text"],
             QPalette.Button: c["button"], QPalette.Mid: c["mid"], QPalette.PlaceholderText: c["ph"],
             QPalette.ToolTipBase: c["tip"], QPalette.ToolTipText: c["text"],
             QPalette.Highlight: "#3B82D6", QPalette.HighlightedText: "#FFFFFF", QPalette.BrightText: "#FFFFFF"}
    for role, col in roles.items():
        pal.setColor(role, QColor(col))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c["ph"]))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(c["ph"]))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(c["ph"]))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(dark))


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ArchieKUO.MVMaker")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("ArchieKUO")
    app.setStyle("Fusion")
    f = QFont("Microsoft JhengHei UI" if os.name == "nt" else "Noto Sans CJK TC")
    f.setPointSize(13)
    app.setFont(f)
    icon_path = os.path.join(media._app_dir(), "icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    apply_theme(app)
    if hasattr(app.styleHints(), "colorSchemeChanged"):
        app.styleHints().colorSchemeChanged.connect(lambda *_: apply_theme(app))
    w = MainWindow()
    if os.environ.get("MV_SMOKE"):
        from .smoke import run_smoke
        run_smoke(app, w)
    else:
        w.showMaximized()
    sys.exit(app.exec())
