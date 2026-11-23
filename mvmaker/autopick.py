"""自動挑片段：看畫面清楚、明亮、穩定、有沒有人臉，避開換場景的地方。"""
from __future__ import annotations

import os
import subprocess

import cv2
import numpy as np

from .core import Clip, DEFAULT_PHOTO_SEC
from .media import load_image, popen, probe, tool

AW, AH = 320, 180      # 分析用小畫面
SAMPLE_FPS = 2


def _face_detector():
    if not hasattr(cv2, "CascadeClassifier"):   # 有些 OpenCV 版本沒有人臉辨識：就不算人臉分數
        return None
    names = ["haarcascade_frontalface_default.xml"]
    dirs = []
    try:
        dirs.append(cv2.data.haarcascades)
    except Exception:
        pass
    from .media import _app_dir
    dirs += [os.path.join(_app_dir(), "data"), _app_dir()]
    for d in dirs:
        p = os.path.join(d, names[0])
        if os.path.exists(p):
            try:
                det = cv2.CascadeClassifier(p)
            except Exception:
                return None
            if not det.empty():
                return det
    return None


_FACE = None


def faces(gray) -> int:
    global _FACE
    if _FACE is None:
        _FACE = _face_detector() or False
    if not _FACE:
        return 0
    try:
        found = _FACE.detectMultiScale(gray, 1.15, 5, minSize=(18, 18))
    except Exception:
        return 0
    return len(found)


def _quality(gray) -> tuple[float, float]:
    sharp = cv2.Laplacian(gray, cv2.CV_64F).var()
    bright = float(gray.mean())
    return sharp, bright


def analyze_video(path: str, cancelled=lambda: False, progress=None):
    """回傳每個取樣點 [(秒, 分數)] 與換場景的秒數。"""
    info = probe(path)
    dur = info["duration"]
    w, h = info["width"] or 16, info["height"] or 9
    if w >= h:
        aw, ah = AW, max(2, int(AW * h / w) // 2 * 2)
    else:
        ah, aw = AW, max(2, int(AW * w / h) // 2 * 2)
    proc = popen([tool("ffmpeg"), "-v", "error", "-i", path, "-an",
                  "-vf", f"fps={SAMPLE_FPS},scale={aw}:{ah}", "-pix_fmt", "gray",
                  "-f", "rawvideo", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = aw * ah
    samples, cuts = [], []
    prev, prev_hist = None, None
    k = 0
    try:
        while True:
            if cancelled():
                break
            buf = proc.stdout.read(n)
            if len(buf) < n:
                break
            g = np.frombuffer(buf, np.uint8).reshape(ah, aw)
            t = k / SAMPLE_FPS
            sharp, bright = _quality(g)
            shake = 0.0
            hist = cv2.calcHist([g], [0], None, [32], [0, 256])
            cv2.normalize(hist, hist)
            if prev is not None:
                shake = float(np.mean(cv2.absdiff(g, prev)))
                if cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA) > 0.45:
                    cuts.append(t)
            nf = faces(g) if k % 2 == 0 else (samples[-1][4] if samples else 0)
            samples.append((t, sharp, bright, shake, nf))
            prev, prev_hist = g, hist
            k += 1
            if progress and dur and k % 10 == 0:
                progress(min(1.0, t / dur))
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()
    scored = []
    for t, sharp, bright, shake, nf in samples:
        s = 0.0
        s += min(1.0, sharp / 150.0) * 2.0                 # 清楚
        s += 1.0 - min(1.0, abs(bright - 125) / 100.0)     # 亮度適中
        if bright < 35 or bright > 235:
            s -= 2.0                                       # 太暗 / 過曝
        s -= min(2.0, max(0.0, shake - 6) / 8.0)           # 晃動
        s += min(2, nf) * 0.9                              # 有人臉
        scored.append((t, s))
    return scored, cuts, dur


def pick_segments(scored, cuts, dur, seg_len: float, count: int):
    """挑 count 段分數最高、互不重疊、不跨換場景的片段。"""
    if not scored or dur <= 0:
        return []
    seg_len = min(seg_len, dur)
    step = 1.0 / SAMPLE_FPS
    win = max(1, int(seg_len * SAMPLE_FPS))
    vals = np.array([s for _, s in scored])
    cands = []
    for i in range(0, max(1, len(vals) - win + 1)):
        t0 = i * step
        t1 = t0 + seg_len
        if t1 > dur + 0.01:
            break
        if any(t0 + 0.3 < c < t1 - 0.3 for c in cuts):
            continue
        cands.append((float(vals[i:i + win].mean()), t0))
    cands.sort(reverse=True)
    chosen = []
    for score, t0 in cands:
        if score < 0.6 and chosen:
            break
        if all(abs(t0 - c) >= seg_len + 1.0 for _, c in chosen):
            chosen.append((score, t0))
        if len(chosen) >= count:
            break
    chosen.sort(key=lambda x: x[1])
    return [(t0, min(dur, t0 + seg_len), sc) for sc, t0 in chosen]


def _dhash(img) -> int:
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    g = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def photo_score(path: str):
    img = load_image(path, 640)
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    sharp, bright = _quality(g)
    s = min(1.0, sharp / 150.0) * 2 + 1.0 - min(1.0, abs(bright - 125) / 100.0)
    s += min(2, faces(cv2.resize(g, (AW, int(AW * g.shape[0] / g.shape[1]))))) * 0.9
    return s, _dhash(img)


def auto_pick(clips: list[Clip], song_len: float, seg_len: float = 5.0,
              progress=None, cancelled=lambda: False, log=None) -> list[Clip]:
    """把素材表重新整理成建議的片段清單（照片去重複、影片挑好片段）。"""
    photos = [c for c in clips if c.kind == "photo"]
    # 每個原始影片只分析一次（同一個檔案可能已被切成多段）
    vids, seen = [], set()
    for c in clips:
        if c.kind == "video" and c.path not in seen:
            seen.add(c.path)
            vids.append(c)
    starred_paths = {c.path for c in clips if c.star}

    total_steps = len(photos) + len(vids) * 10 or 1
    done = 0

    def step(n=1):
        nonlocal done
        done += n
        if progress:
            progress(min(1.0, done / total_steps))

    # 照片：去掉幾乎一樣的，只留分數最高的
    kept_photos = []
    hashes = []
    for c in photos:
        if cancelled():
            return clips
        try:
            s, hsh = photo_score(c.path)
        except Exception:
            step()
            continue
        dup = None
        for i, (h2, s2, _) in enumerate(hashes):
            if bin(hsh ^ h2).count("1") <= 6:
                dup = i
                break
        if dup is None:
            hashes.append((hsh, s, c))
        elif s > hashes[dup][1]:
            hashes[dup] = (hsh, s, c)
        step()
    kept_photos = [c for _, s, c in hashes if s > 0.3 or c.star]
    keep_set = {id(c) for c in kept_photos}
    kept_photos = [c for c in photos if id(c) in keep_set]

    # 影片需要多少秒
    photo_time = sum(c.seconds or DEFAULT_PHOTO_SEC for c in kept_photos)
    need = max(0.0, (song_len or 0) - photo_time) if song_len else None

    analyses = []
    for c in vids:
        if cancelled():
            return clips
        if log:
            log(f"分析影片：{os.path.basename(c.path)}")
        base = done
        scored, cuts, dur = analyze_video(
            c.path, cancelled,
            lambda f: progress and progress(min(1.0, (base + f * 10) / total_steps)))
        analyses.append((c, scored, cuts, dur))
        step(10)

    total_vid = sum(a[3] for a in analyses) or 1
    picked: list[Clip] = []
    for c, scored, cuts, dur in analyses:
        if need is None:
            count = max(1, int(dur // 30) + 1)
        else:
            count = max(1, round(need * dur / total_vid / seg_len))
        count = min(count, max(1, int(dur // seg_len)))
        segs = pick_segments(scored, cuts, dur, seg_len, count)
        if not segs and dur > 0:
            segs = [(0.0, min(dur, seg_len), 0)]
        for t0, t1, _ in segs:
            picked.append(Clip(c.path, "video", round(t0, 1), round(t1, 1), None,
                               star=c.path in starred_paths, keep_audio=c.keep_audio))

    # 依檔案原本順序（通常是拍攝順序）合併照片和影片片段
    order = {}
    for i, c in enumerate(clips):
        order.setdefault(c.path, i)
    merged = kept_photos + picked
    merged.sort(key=lambda c: (order.get(c.path, 0), c.start or 0))
    if progress:
        progress(1.0)
    return merged
