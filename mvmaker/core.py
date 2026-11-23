"""資料模型、時間格式、歌詞檔、時間軸規劃。不依賴介面。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict

VERSION = "0.1.0"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic", ".heif", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".wmv", ".3gp", ".webm"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma"}


def kind_of(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    return None


# ---------------------------------------------------------------- 時間格式
_TIME_RE = re.compile(r"^\s*(?:(\d+)\s*[:：]\s*)?(\d+(?:[.,]\d*)?)\s*$")


def parse_time(text: str) -> float | None:
    """'01:12.4'、'72.4'、'1:2' 都接受；格式錯回傳 None。"""
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    m = _TIME_RE.match(text)
    if not m:
        return None
    mins = int(m.group(1)) if m.group(1) else 0
    secs = float(m.group(2).replace(",", "."))
    if m.group(1) and secs >= 60:
        return None
    return mins * 60 + secs


def fmt_time(sec: float | None) -> str:
    if sec is None:
        return ""
    sec = max(0.0, float(sec))
    m = int(sec // 60)
    s = sec - m * 60
    if round(s, 1) >= 60:
        m += 1
        s = 0.0
    return f"{m:02d}:{s:04.1f}"


def parse_seconds(text: str) -> float | None:
    if text is None:
        return None
    text = str(text).strip().replace(",", "")
    if not text:
        return None
    try:
        v = float(text)
    except ValueError:
        return None
    return v if v > 0 else None


# ---------------------------------------------------------------- 資料模型
@dataclass
class LyricLine:
    time: float | None = None
    text: str = ""


@dataclass
class Clip:
    path: str
    kind: str                     # photo / video
    start: float | None = None    # 影片才有
    end: float | None = None
    seconds: float | None = None  # 照片停留秒數；None = 自動分配
    star: bool = False
    keep_audio: bool = False
    checked: bool = False         # 介面勾選，不存檔

    def length(self) -> float | None:
        if self.kind == "video":
            if self.start is not None and self.end is not None and self.end > self.start:
                return self.end - self.start
            return None
        return self.seconds


@dataclass
class Style:
    orientation: str = "16:9"
    font: str = ""                # 字型檔路徑；空白 = 預設
    font_size: int = 64
    color: str = "白色"
    position: str = "下方"
    effect: str = "淡入淡出"
    motion: str = "慢慢放大"
    transition: str = "淡入淡出"
    portrait_bg: str = "模糊延伸"   # 畫面方向和影片不同時的背景
    star_chorus: str = "是"
    seg_len: float = 5.0          # 自動挑片段時每段幾秒


SEG_LENS = ["3", "4", "5", "6", "8", "10"]

COLORS = {
    "白色": (255, 255, 255), "淡黃": (255, 240, 170), "金色": (232, 190, 80),
    "淡藍": (190, 225, 255), "粉紅": (255, 200, 215), "黑色": (20, 20, 20),
}
POSITIONS = ["下方", "中間", "上方"]
EFFECTS = ["淡入淡出", "卡拉OK填色", "直接出現"]
MOTIONS = ["慢慢放大", "慢慢縮小", "左右平移", "隨機變化", "不動"]
TRANSITIONS = ["淡入淡出", "直接切換"]
PORTRAIT_BGS = ["模糊延伸", "黑邊", "裁切填滿"]
YESNO = ["是", "否"]
SIZES = [40, 48, 56, 64, 72, 84, 96, 110]


@dataclass
class Project:
    song: str = ""
    song_duration: float = 0.0
    lyrics: list[LyricLine] = field(default_factory=list)
    clips: list[Clip] = field(default_factory=list)
    style: Style = field(default_factory=Style)

    def to_json(self) -> str:
        d = {
            "app": "MV製作工具", "version": VERSION,
            "song": self.song, "song_duration": self.song_duration,
            "lyrics": [asdict(l) for l in self.lyrics],
            "clips": [{k: v for k, v in asdict(c).items() if k != "checked"} for c in self.clips],
            "style": asdict(self.style),
        }
        return json.dumps(d, ensure_ascii=False, indent=1)

    @staticmethod
    def from_json(text: str) -> "Project":
        d = json.loads(text)
        p = Project(song=d.get("song", ""), song_duration=d.get("song_duration", 0.0))
        p.lyrics = [LyricLine(**l) for l in d.get("lyrics", [])]
        p.clips = [Clip(**c) for c in d.get("clips", [])]
        st = Style()
        for k, v in d.get("style", {}).items():
            if hasattr(st, k):
                setattr(st, k, v)
        p.style = st
        return p


# ---------------------------------------------------------------- 歌詞檔
_LRC_TAG = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")


def parse_lyrics_text(text: str) -> list[LyricLine]:
    """讀 LRC（有時間）或純文字（沒有時間）。"""
    out: list[LyricLine] = []
    has_tags = bool(_LRC_TAG.search(text))
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if has_tags:
            tags = _LRC_TAG.findall(line)
            if not tags:
                continue
            body = _LRC_TAG.sub("", line).strip()
            for mm, ss in tags:
                t = int(mm) * 60 + float(ss.replace(":", "."))
                out.append(LyricLine(t, body))
        else:
            if not line or (line.startswith("[") and line.endswith("]")):
                continue  # 跳過空行與 [Verse] 這類段落標記
            out.append(LyricLine(None, line))
    if has_tags:
        out.sort(key=lambda l: l.time)
    return out


def lyrics_to_lrc(lines: list[LyricLine]) -> str:
    rows = []
    for l in lines:
        if l.time is None:
            continue
        m = int(l.time // 60)
        s = l.time - m * 60
        rows.append(f"[{m:02d}:{s:05.2f}]{l.text}")
    return "\n".join(rows) + "\n"


MAX_LINE_SHOW = 12.0


def lyric_spans(lines: list[LyricLine], song_len: float) -> list[tuple[float, float, str]]:
    """每句歌詞顯示 (開始, 結束, 文字)。空白文字的列 = 結束標記。"""
    timed = sorted([l for l in lines if l.time is not None], key=lambda l: l.time)
    spans = []
    for i, l in enumerate(timed):
        nxt = timed[i + 1].time if i + 1 < len(timed) else (song_len or l.time + 6)
        if not l.text.strip():
            continue
        end = min(nxt, l.time + MAX_LINE_SHOW)
        if end > l.time:
            spans.append((l.time, end, l.text.strip()))
    return spans


def chorus_ranges(lines: list[LyricLine], song_len: float) -> list[tuple[float, float]]:
    """歌詞重複出現的句子當作副歌。"""
    spans = lyric_spans(lines, song_len)
    count: dict[str, int] = {}
    for _, _, t in spans:
        key = re.sub(r"\W", "", t)
        count[key] = count.get(key, 0) + 1
    rng = []
    for s, e, t in spans:
        if count.get(re.sub(r"\W", "", t), 0) >= 2:
            if rng and s - rng[-1][1] < 3:
                rng[-1] = (rng[-1][0], e)
            else:
                rng.append((s, e))
    return rng


# ---------------------------------------------------------------- 時間軸
@dataclass
class Segment:
    clip: Clip
    t0: float       # 在 MV 裡的開始秒數
    dur: float
    src_start: float = 0.0   # 影片從第幾秒開始
    motion: str = "慢慢放大"


DEFAULT_PHOTO_SEC = 5.0


def build_plan(project: Project, total: float | None = None) -> list[Segment]:
    clips = [c for c in project.clips if c.kind in ("photo", "video")]
    if not clips:
        return []
    total = total or project.song_duration or sum((c.length() or DEFAULT_PHOTO_SEC) for c in clips)

    # 影片沒填時間 → 從頭取 6 秒
    def vlen(c: Clip) -> float:
        L = c.length()
        return L if L else 6.0

    # 星號放副歌：重排順序
    order = list(clips)
    if project.style.star_chorus == "是" and any(c.star for c in order):
        order = _star_to_chorus(order, project, total)

    fixed = sum(vlen(c) for c in order if c.kind == "video") + \
        sum(c.seconds for c in order if c.kind == "photo" and c.seconds)
    auto = [c for c in order if c.kind == "photo" and not c.seconds]
    durs: dict[int, float] = {}
    for c in order:
        if c.kind == "video":
            durs[id(c)] = vlen(c)
        elif c.seconds:
            durs[id(c)] = c.seconds
    if auto:
        each = (total - fixed) / len(auto) if total > fixed else DEFAULT_PHOTO_SEC
        each = max(2.0, min(each, 15.0))
        for c in auto:
            durs[id(c)] = each

    seq = list(order)
    used = sum(durs[id(c)] for c in seq)
    # 太長：先拿掉後面沒有星號的
    while used > total + 0.01 and len(seq) > 1:
        idx = next((i for i in range(len(seq) - 1, -1, -1) if not seq[i].star), None)
        if idx is None:
            break
        if used - durs[id(seq[idx])] < total:
            break
        used -= durs[id(seq[idx])]
        seq.pop(idx)
    # 太短：從頭再輪播
    base = list(seq)
    k = 0
    while used < total - 0.01 and base:
        c = base[k % len(base)]
        seq.append(c)
        used += durs[id(c)]
        k += 1
        if k > 2000:
            break

    motions = ["慢慢放大", "左右平移", "慢慢縮小"]
    segs: list[Segment] = []
    t = 0.0
    for i, c in enumerate(seq):
        d = durs[id(c)]
        if t + d > total:
            d = total - t
        if d <= 0.05:
            break
        m = project.style.motion
        if m == "隨機變化":
            m = motions[i % len(motions)]
        segs.append(Segment(c, t, d, c.start or 0.0, m))
        t += d
    return segs


def _star_to_chorus(order: list[Clip], project: Project, total: float) -> list[Clip]:
    rng = chorus_ranges(project.lyrics, total)
    stars = [c for c in order if c.star]
    rest = [c for c in order if not c.star]
    if not rng:
        return order
    # 依秒數排：先放一般素材，累計時間進入副歌時插入星號素材
    out: list[Clip] = []
    t = 0.0
    est = lambda c: c.length() or DEFAULT_PHOTO_SEC
    ri = 0
    while rest or stars:
        in_chorus = any(a <= t < b for a, b in rng)
        if in_chorus and stars:
            c = stars.pop(0)
        elif rest:
            c = rest.pop(0)
        else:
            c = stars.pop(0)
        out.append(c)
        t += est(c)
        ri += 1
    return out


# ---------------------------------------------------------------- 檔名
def unique_path(path: str) -> str:
    """檔名已存在就加 _v2、_v3…"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    base = re.sub(r"_v\d+$", "", base)
    n = 2
    while os.path.exists(f"{base}_v{n}{ext}"):
        n += 1
    return f"{base}_v{n}{ext}"
