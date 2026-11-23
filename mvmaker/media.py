"""影像處理：讀照片、讀影片、合成畫面、字幕、輸出 MP4。"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .core import COLORS, Project, Segment, Style, build_plan, lyric_spans

try:
    import pillow_heif  # noqa
    pillow_heif.register_heif_opener()
except Exception:
    pass

FPS = 30
XFADE = 0.5          # 轉場淡入淡出秒數
FADE_IN = 1.0
FADE_OUT = 2.0
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ---------------------------------------------------------------- ffmpeg 位置
def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    for d in (os.path.join(_app_dir(), "ffmpeg"), _app_dir(), os.path.dirname(sys.executable)):
        p = os.path.join(d, exe)
        if os.path.exists(p):
            return p
    p = shutil.which(name)
    if p:
        return p
    raise FileNotFoundError(f"找不到 {exe}")


def run(cmd, **kw):
    return subprocess.run(cmd, creationflags=NO_WINDOW, **kw)


def popen(cmd, **kw):
    return subprocess.Popen(cmd, creationflags=NO_WINDOW, **kw)


# ---------------------------------------------------------------- 檔案資訊
@lru_cache(maxsize=512)
def probe(path: str) -> dict:
    """回傳 {duration, width, height, has_audio}（寬高已含旋轉）。"""
    r = run([tool("ffprobe"), "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path], capture_output=True)
    info = {"duration": 0.0, "width": 0, "height": 0, "has_audio": False}
    try:
        d = json.loads(r.stdout.decode("utf-8", "ignore"))
    except Exception:
        return info
    try:
        info["duration"] = float(d.get("format", {}).get("duration") or 0)
    except ValueError:
        pass
    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and not info["width"] and s.get("disposition", {}).get("attached_pic", 0) == 0:
            w, h = int(s.get("width", 0)), int(s.get("height", 0))
            rot = 0
            for sd in s.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = int(float(sd["rotation"]))
            if "rotate" in s.get("tags", {}):
                rot = int(s["tags"]["rotate"])
            if abs(rot) % 180 == 90:
                w, h = h, w
            info["width"], info["height"] = w, h
        if s.get("codec_type") == "audio":
            info["has_audio"] = True
    return info


def load_image(path: str, max_side: int = 2600) -> np.ndarray:
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    im = im.convert("RGB")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    return np.asarray(im)


def video_frame(path: str, t: float, max_side: int = 1280) -> np.ndarray | None:
    """抓影片某一秒的一張畫面。"""
    vf = f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'"
    r = run([tool("ffmpeg"), "-v", "error", "-ss", f"{max(0, t):.3f}", "-i", path,
             "-frames:v", "1", "-vf", vf, "-f", "image2pipe", "-vcodec", "png", "-"],
            capture_output=True)
    if not r.stdout:
        return None
    arr = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
    return None if arr is None else cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)


def thumbnail(path: str, kind: str, size=(160, 90), t: float = 0.0) -> np.ndarray | None:
    try:
        img = load_image(path, 800) if kind == "photo" else video_frame(path, t, 480)
    except Exception:
        return None
    if img is None:
        return None
    return fit_cover(img, *size)


# ---------------------------------------------------------------- 版面工具
def fit_cover(img: np.ndarray, W: int, H: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = max(W / w, H / h)
    nw, nh = max(W, round(w * s)), max(H, round(h * s))
    r = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    x, y = (nw - W) // 2, (nh - H) // 2
    return np.ascontiguousarray(r[y:y + H, x:x + W])


def blur_bg(img: np.ndarray, W: int, H: int) -> np.ndarray:
    small = fit_cover(img, max(16, W // 12), max(16, H // 12))
    small = cv2.GaussianBlur(small, (0, 0), 3)
    big = cv2.resize(small, (W, H), interpolation=cv2.INTER_LINEAR)
    return (big.astype(np.float32) * 0.55).astype(np.uint8)


def mismatched(w: int, h: int, W: int, H: int) -> bool:
    a, A = w / max(1, h), W / max(1, H)
    return (a > 1.05 and A < 0.95) or (a < 0.95 and A > 1.05)


# ---------------------------------------------------------------- 畫面來源
class PhotoSource:
    def __init__(self, seg: Segment, W: int, H: int, style: Style):
        self.seg, self.W, self.H = seg, W, H
        try:
            self.img = load_image(seg.clip.path, max(W, H) * 2)
        except Exception:
            self.img = np.zeros((H, W, 3), np.uint8)
        h, w = self.img.shape[:2]
        self.fit = mismatched(w, h, W, H) and style.portrait_bg != "裁切填滿"
        if self.fit:
            self.bg = blur_bg(self.img, W, H) if style.portrait_bg == "模糊延伸" else np.zeros((H, W, 3), np.uint8)

    def frame(self, lt: float) -> np.ndarray:
        W, H, img, seg = self.W, self.H, self.img, self.seg
        h, w = img.shape[:2]
        span = seg.dur + XFADE
        p = min(1.0, max(0.0, lt / span))
        p = p * p * (3 - 2 * p) * 0.6 + p * 0.4   # 柔和起停
        m = seg.motion
        z, dx = 1.0, 0.0
        if m == "慢慢放大":
            z = 1.0 + 0.12 * p
        elif m == "慢慢縮小":
            z = 1.12 - 0.12 * p
        elif m == "左右平移":
            z, dx = 1.12, (p - 0.5)
        base = min(W / w, H / h) if self.fit else max(W / w, H / h)
        s = base * z
        # 平移範圍：放大後多出來的寬度
        extra = max(0.0, w * s - W)
        tx = W / 2 - (w * s) / 2 - dx * extra
        ty = H / 2 - (h * s) / 2
        M = np.float32([[s, 0, tx], [0, s, ty]])
        interp = cv2.INTER_AREA if s < 0.7 else cv2.INTER_LINEAR
        if self.fit:
            out = self.bg.copy()
            cv2.warpAffine(img, M, (W, H), dst=out, flags=interp, borderMode=cv2.BORDER_TRANSPARENT)
            return out
        return cv2.warpAffine(img, M, (W, H), flags=interp, borderMode=cv2.BORDER_REFLECT)

    def close(self):
        pass


class VideoSource:
    def __init__(self, seg: Segment, W: int, H: int, style: Style, fps: int = FPS, offset: float = 0.0):
        self.seg, self.W, self.H, self.fps = seg, W, H, fps
        info = probe(seg.clip.path)
        vw, vh = info["width"] or W, info["height"] or H
        self.fit = mismatched(vw, vh, W, H) and style.portrait_bg != "裁切填滿"
        self.blur = style.portrait_bg == "模糊延伸"
        if self.fit:
            s = min(W / vw, H / vh)
            self.fw, self.fh = max(2, int(vw * s) // 2 * 2), max(2, int(vh * s) // 2 * 2)
            vf = f"scale={self.fw}:{self.fh}"
        else:
            self.fw, self.fh = W, H
            vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
        self.offset = offset
        start = seg.src_start + offset
        length = seg.dur + XFADE - offset + 0.5
        self.proc = popen([tool("ffmpeg"), "-v", "error", "-ss", f"{start:.3f}", "-i", seg.clip.path,
                           "-t", f"{max(0.1, length):.3f}", "-an", "-vf", f"{vf},fps={fps}",
                           "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10 ** 7)
        self.idx = -1
        self.last = np.zeros((self.fh, self.fw, 3), np.uint8)
        self.nbytes = self.fw * self.fh * 3
        self.eof = False

    def _read(self):
        if self.eof:
            return
        buf = self.proc.stdout.read(self.nbytes)
        if not buf or len(buf) < self.nbytes:
            self.eof = True
            return
        self.last = np.frombuffer(buf, np.uint8).reshape(self.fh, self.fw, 3)

    def frame(self, lt: float) -> np.ndarray:
        want = int((lt - self.offset) * self.fps + 1e-6)
        while self.idx < want and not self.eof:
            self._read()
            self.idx += 1
        fg = self.last
        if not self.fit:
            return fg
        W, H = self.W, self.H
        out = blur_bg(fg, W, H) if self.blur else np.zeros((H, W, 3), np.uint8)
        x, y = (W - self.fw) // 2, (H - self.fh) // 2
        out[y:y + self.fh, x:x + self.fw] = fg
        return out

    def close(self):
        try:
            self.proc.stdout.close()
            self.proc.kill()
            self.proc.wait(timeout=2)
        except Exception:
            pass


def make_source(seg, W, H, style, fps=FPS, offset=0.0):
    if seg.clip.kind == "video":
        return VideoSource(seg, W, H, style, fps, offset)
    return PhotoSource(seg, W, H, style)


# ---------------------------------------------------------------- 字幕
def default_font() -> str:
    cands = [
        r"C:\Windows\Fonts\msjhbd.ttc", r"C:\Windows\Fonts\msjh.ttc", r"C:\Windows\Fonts\mingliu.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return ""


def available_fonts() -> list[tuple[str, str]]:
    """(顯示名稱, 檔案路徑)，只列出電腦上真的有的中文字型。"""
    known = [
        ("微軟正黑體 粗", "msjhbd.ttc"), ("微軟正黑體", "msjh.ttc"), ("標楷體", "kaiu.ttf"),
        ("新細明體", "mingliu.ttc"), ("微軟雅黑", "msyh.ttc"), ("華康毛筆", "DFKai-SB.ttf"),
        ("思源黑體", "NotoSansTC-VF.ttf"), ("思源宋體", "NotoSerifTC-VF.ttf"),
    ]
    dirs = [r"C:\Windows\Fonts", os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Windows\Fonts"),
            "/usr/share/fonts/opentype/noto"]
    out = []
    for name, fn in known:
        for d in dirs:
            p = os.path.join(d, fn)
            if os.path.exists(p):
                out.append((name, p))
                break
    if not out:
        for d in dirs:
            if os.path.isdir(d):
                for fn in sorted(os.listdir(d)):
                    if "CJK" in fn and fn.endswith((".ttc", ".otf")):
                        out.append((fn.split(".")[0], os.path.join(d, fn)))
    return out


def font_name(path: str) -> str:
    for n, p in available_fonts():
        if os.path.normcase(p) == os.path.normcase(path):
            return n
    return os.path.splitext(os.path.basename(path))[0] if path else "預設字型"


class SubtitleRenderer:
    def __init__(self, style: Style, W: int, H: int, scale: float = 1.0):
        self.style, self.W, self.H = style, W, H
        size = max(12, int(style.font_size * scale * (H / 1080 if W > H else W / 1080)))
        path = style.font or default_font()
        try:
            self.font = ImageFont.truetype(path, size) if path else ImageFont.load_default(size)
        except Exception:
            self.font = ImageFont.load_default(size)
        self.size = size
        self.stroke = max(2, size // 14)
        self.cache: dict = {}
        self.color = COLORS.get(style.color, (255, 255, 255))
        self.hi = COLORS["金色"] if style.color != "金色" else COLORS["白色"]

    def _wrap(self, text: str) -> list[str]:
        maxw = self.W * 0.88
        total = self.font.getlength(text)
        if total <= maxw:
            return [text]
        n = math.ceil(total / maxw)
        target = total / n                       # 每行差不多一樣長
        lines, cur, brk = [], "", -1
        for ch in text:
            cur += ch
            if ch in " ，,、。！？；;":
                brk = len(cur)
            w = self.font.getlength(cur)
            if w >= target * 0.98 and len(lines) < n - 1 or w > maxw:
                if brk > 0 and self.font.getlength(cur[:brk]) > target * 0.55:
                    lines.append(cur[:brk])
                    cur = cur[brk:]
                elif w > maxw:
                    lines.append(cur[:-1])
                    cur = ch
                else:
                    lines.append(cur)
                    cur = ""
                brk = -1
        if cur.strip():
            lines.append(cur)
        lines = [l.strip(" ，,、") for l in lines]
        return [l for l in lines if l] or [text]

    def layer(self, text: str, color) -> np.ndarray:
        key = (text, color)
        if key in self.cache:
            return self.cache[key]
        lines = self._wrap(text)
        pad = self.stroke * 3
        lh = int(self.size * 1.3)
        widths = [int(self.font.getlength(l)) for l in lines]
        w = max(widths) + pad * 2
        h = lh * len(lines) + pad * 2
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dr = ImageDraw.Draw(im)
        for i, l in enumerate(lines):
            x = pad + (w - pad * 2 - widths[i]) // 2
            y = pad + i * lh
            dr.text((x + self.stroke, y + self.stroke), l, font=self.font, fill=(0, 0, 0, 110),
                    stroke_width=self.stroke, stroke_fill=(0, 0, 0, 110))
            dr.text((x, y), l, font=self.font, fill=color + (255,),
                    stroke_width=self.stroke, stroke_fill=(0, 0, 0, 255))
        arr = np.asarray(im)
        if len(self.cache) > 200:
            self.cache.clear()
        self.cache[key] = arr
        return arr

    def draw(self, frame: np.ndarray, text: str, s: float, e: float, t: float):
        st = self.style
        base = self.layer(text, self.color)
        h, w = base.shape[:2]
        H, W = frame.shape[:2]
        x = (W - w) // 2
        portrait = H > W
        if st.position == "中間":
            y = (H - h) // 2
        elif st.position == "上方":
            y = int(H * (0.10 if portrait else 0.06))
        else:
            y = H - h - int(H * (0.16 if portrait else 0.06))
        op = 1.0
        if st.effect == "淡入淡出":
            op = min(1.0, (t - s) / 0.35, (e - t) / 0.35)
        op = max(0.0, min(1.0, op))
        if op <= 0:
            return
        layer = base
        if st.effect == "卡拉OK填色":
            hi = self.layer(text, self.hi)
            prog = (t - s) / max(0.5, (e - s) * 0.9)
            cut = int(w * max(0.0, min(1.0, prog)))
            layer = base.copy()
            layer[:, :cut] = hi[:, :cut]
        _blend(frame, layer, x, y, op)


def _blend(frame, rgba, x, y, op):
    H, W = frame.shape[:2]
    h, w = rgba.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1:
        return
    sub = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    a = sub[:, :, 3:4].astype(np.float32) * (op / 255.0)
    roi = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = (roi * (1 - a) + sub[:, :, :3].astype(np.float32) * a).astype(np.uint8)


# ---------------------------------------------------------------- 合成
def canvas_size(style: Style, preview_w: int | None = None) -> tuple[int, int]:
    W, H = (1920, 1080) if style.orientation == "16:9" else (1080, 1920)
    if preview_w:
        s = preview_w / max(W, H)
        W, H = int(W * s) // 2 * 2, int(H * s) // 2 * 2
    return W, H


class Composer:
    """依時間產生畫面。時間往後走時重複使用來源，跳回去時重新開。"""

    def __init__(self, project: Project, W: int, H: int, fps: int = FPS, sub_scale: float = 1.0):
        self.p, self.W, self.H, self.fps = project, W, H, fps
        self.total = project.song_duration or 0.0
        self.plan = build_plan(project, self.total or None)
        if not self.total and self.plan:
            self.total = self.plan[-1].t0 + self.plan[-1].dur
        self.spans = lyric_spans(project.lyrics, self.total)
        self.subs = SubtitleRenderer(project.style, W, H, sub_scale)
        self.sources: dict[int, object] = {}
        self.last_t = -1.0

    def seg_index(self, t: float) -> int:
        for i, s in enumerate(self.plan):
            if s.t0 <= t < s.t0 + s.dur:
                return i
        return len(self.plan) - 1

    def _src(self, i: int, lt: float):
        src = self.sources.get(i)
        if src is not None and isinstance(src, VideoSource):
            pos = src.offset + src.idx / src.fps
            if lt < pos - 0.05 or lt > pos + 3:     # 往回跳或往前跳太遠：重開
                src.close()
                src = None
        if src is None:
            seg = self.plan[i]
            src = make_source(seg, self.W, self.H, self.p.style, self.fps,
                              offset=max(0.0, lt) if seg.clip.kind == "video" and lt > 0.2 else 0.0)
            self.sources[i] = src
        return src

    def frame_at(self, t: float) -> np.ndarray:
        W, H = self.W, self.H
        if not self.plan:
            frame = np.full((H, W, 3), 24, np.uint8)
        else:
            i = self.seg_index(t)
            seg = self.plan[i]
            lt = t - seg.t0
            frame = self._src(i, lt).frame(lt).copy()
            xf = self.p.style.transition == "淡入淡出"
            if xf and i > 0 and lt < XFADE:
                prev = self.plan[i - 1]
                pf = self._src(i - 1, t - prev.t0).frame(t - prev.t0)
                a = lt / XFADE
                frame = cv2.addWeighted(pf, 1 - a, frame, a, 0)
            # 關掉用完的來源
            for k in list(self.sources):
                if k < i - 1 or k > i + 1 or (k == i - 1 and lt >= XFADE):
                    self.sources.pop(k).close()
        for s, e, text in self.spans:
            if s <= t < e:
                self.subs.draw(frame, text, s, e, t)
                break
        # 片頭片尾淡入淡出
        k = 1.0
        if t < FADE_IN:
            k = t / FADE_IN
        if self.total and t > self.total - FADE_OUT:
            k = min(k, max(0.0, (self.total - t) / FADE_OUT))
        if k < 1.0:
            frame = (frame.astype(np.float32) * k).astype(np.uint8)
        self.last_t = t
        return frame

    def close(self):
        for s in self.sources.values():
            s.close()
        self.sources.clear()


# ---------------------------------------------------------------- 輸出
class Cancelled(Exception):
    pass


def export(project: Project, out_path: str, progress=None, cancelled=lambda: False) -> str:
    W, H = canvas_size(project.style)
    comp = Composer(project, W, H, FPS)
    total = comp.total
    if total <= 0:
        raise ValueError("沒有歌曲也沒有素材，無法輸出")
    n = int(round(total * FPS))

    cmd = [tool("ffmpeg"), "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    filters, mix = [], []
    have_song = bool(project.song and os.path.exists(project.song))
    idx = 1
    duck = []
    extra = []
    if have_song:
        cmd += ["-i", project.song]
        song_idx = idx
        idx += 1
    for seg in comp.plan:
        c = seg.clip
        if c.kind == "video" and c.keep_audio and probe(c.path)["has_audio"]:
            cmd += ["-ss", f"{seg.src_start:.3f}", "-t", f"{seg.dur:.3f}", "-i", c.path]
            extra.append((idx, seg))
            duck.append((seg.t0, seg.t0 + seg.dur))
            idx += 1
    if have_song:
        vol = "1"
        if duck:
            cond = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in duck)
            vol = f"'if({cond},0.3,1)'"
        filters.append(f"[{song_idx}:a]atrim=0:{total:.3f},volume={vol}:eval=frame,"
                       f"afade=t=out:st={max(0, total - FADE_OUT):.3f}:d={FADE_OUT}[song]")
        mix.append("[song]")
    for k, seg in extra:
        ms = int(seg.t0 * 1000)
        filters.append(f"[{k}:a]afade=t=in:d=0.3,afade=t=out:st={max(0, seg.dur - 0.4):.3f}:d=0.4,"
                       f"adelay={ms}|{ms}[a{k}]")
        mix.append(f"[a{k}]")
    if mix:
        if len(mix) > 1:
            filters.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first[aout]")
        else:
            filters[-1] = filters[-1].rsplit("[", 1)[0] + "[aout]"
        cmd += ["-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[aout]",
                "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-t", f"{total:.3f}", "-movflags", "+faststart", out_path]

    errf = tempfile.TemporaryFile()
    proc = popen(cmd, stdin=subprocess.PIPE, stderr=errf)
    try:
        for f in range(n):
            if cancelled():
                raise Cancelled()
            frame = comp.frame_at(f / FPS)
            proc.stdin.write(frame.tobytes())
            if progress and f % 15 == 0:
                progress(f / n)
        proc.stdin.close()
        proc.wait()
        if proc.returncode != 0:
            errf.seek(0)
            raise RuntimeError(errf.read().decode("utf-8", "ignore")[-800:] or "ffmpeg 失敗")
    except (Cancelled, BrokenPipeError, OSError) as ex:
        try:
            proc.kill()
        except Exception:
            pass
        proc.wait()
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        if isinstance(ex, Cancelled):
            raise
        errf.seek(0)
        raise RuntimeError(errf.read().decode("utf-8", "ignore")[-800:] or str(ex))
    finally:
        comp.close()
        errf.close()
    if progress:
        progress(1.0)
    return out_path
