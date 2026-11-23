"""自動測試用：載入範例專案、截圖、結束。設 MV_SMOKE=1、MV_DEMO=範例資料夾。"""
import os

from PySide6.QtCore import QTimer

from .core import Clip, kind_of, parse_lyrics_text
from . import media


def run_smoke(app, w):
    demo = os.environ.get("MV_DEMO", "")
    out = os.environ.get("MV_SHOTS", "shots")
    os.makedirs(out, exist_ok=True)
    size = os.environ.get("MV_SIZE", "1600x1000").split("x")
    w.resize(int(size[0]), int(size[1]))
    w.show()

    if demo and os.path.isdir(demo):
        p = w.project
        song = os.path.join(demo, "song.mp3")
        if os.path.exists(song):
            p.song = song
            p.song_duration = media.probe(song)["duration"]
            w._set_player_source(song)
        lrc = os.path.join(demo, "lyr.lrc")
        if os.path.exists(lrc):
            p.lyrics = parse_lyrics_text(open(lrc, encoding="utf-8").read())
        files = sorted(f for f in os.listdir(demo) if kind_of(f))
        for f in files:
            k = kind_of(f)
            path = os.path.join(demo, f)
            if k == "video":
                p.clips.append(Clip(path, k, 2.0, 7.5))
            else:
                p.clips.append(Clip(path, k))
        if p.clips:
            p.clips[1].star = True
            p.clips[0].checked = True
        w.refresh_all()

    tag = os.environ.get("MV_THEME", "auto")
    steps = []

    def shot(name):
        def f():
            app.processEvents()
            w.grab().save(os.path.join(out, f"{tag}_{size[0]}x{size[1]}_{name}.png"))
        return f

    def seek():
        w.seek(8.3)

    def edit_combo():
        w.tabs.setCurrentIndex(1)
        t = w.style_tbl
        t.setFocus()
        t.setCurrentCell(4, 1)
        t.edit(t.currentIndex())

    def edit_time():
        w.tabs.setCurrentIndex(0)
        w.lyrics.setFocus()
        w.lyrics.setCurrentCell(1, 1)
        w.lyrics.edit(w.lyrics.currentIndex())

    def portrait():
        w.set_orientation("9:16")
        w.refresh_all()
        w.seek(8.3)

    def export_test():
        """在打包好的 exe 裡實際跑一次自動挑片段和輸出，確認 ffmpeg 等都有帶進去。"""
        import time, traceback
        rep = []
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            QMediaPlayer()
            rep.append("multimedia ok")
        except Exception as ex:
            rep.append(f"multimedia FAIL {ex}")
        try:
            from .autopick import auto_pick
            t = time.time()
            clips = auto_pick(list(w.project.clips), w.project.song_duration, 4.0)
            rep.append(f"autopick ok {len(clips)} clips {time.time() - t:.1f}s")
            w.project.clips = clips
            for o in ("16:9", "9:16"):
                w.project.style.orientation = o
                t = time.time()
                f = media.export(w.project, os.path.join(out, f"test_{o.replace(':', 'x')}.mp4"))
                info = media.probe(f)
                rep.append(f"export {o} ok {info['width']}x{info['height']} {info['duration']:.1f}s "
                           f"audio={info['has_audio']} took {time.time() - t:.1f}s")
        except Exception:
            rep.append("FAIL\n" + traceback.format_exc())
        open(os.path.join(out, "export_report.txt"), "w", encoding="utf-8").write("\n".join(rep))

    steps = [(1500, seek), (2500, shot("main")), (2700, edit_combo), (3000, shot("combo")),
             (3200, edit_time), (3500, shot("timeedit")), (3700, portrait), (5000, shot("portrait")),
             (5500, export_test if os.environ.get("MV_EXPORT_TEST") else (lambda: None)),
             (6000, app.quit)]
    for ms, fn in steps:
        QTimer.singleShot(ms, fn)
