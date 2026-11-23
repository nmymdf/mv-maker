"""產生自動測試用的範例素材：歌、歌詞、照片、影片。用法：python tools/make_demo.py 資料夾 ffmpeg路徑"""
import os
import subprocess
import sys

from PIL import Image, ImageDraw

d = sys.argv[1]
ff = sys.argv[2] if len(sys.argv) > 2 else "ffmpeg"
os.makedirs(d, exist_ok=True)


def run(*a):
    subprocess.run([ff, "-v", "error", "-y", *a], check=True)


run("-f", "lavfi", "-i", "sine=frequency=330:duration=20", "-c:a", "libmp3lame", os.path.join(d, "song.mp3"))
run("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=15", "-f", "lavfi", "-i",
    "sine=frequency=600:duration=15", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
    os.path.join(d, "clip1.mp4"))
run("-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30:duration=8", "-c:v", "libx264", "-preset", "ultrafast",
    os.path.join(d, "clip2_portrait.mp4"))
for name, (w, h), c in [("p1.jpg", (4000, 3000), (200, 90, 60)), ("p2.jpg", (3000, 4000), (60, 120, 200)),
                        ("p3.jpg", (4000, 3000), (70, 160, 90))]:
    im = Image.new("RGB", (w, h), c)
    dr = ImageDraw.Draw(im)
    for i in range(0, w, 200):
        dr.line([(i, 0), (i, h)], fill=(255, 255, 255), width=8)
    dr.ellipse([w // 3, h // 3, 2 * w // 3, 2 * h // 3], fill=(250, 230, 80))
    im.save(os.path.join(d, name), quality=90)
with open(os.path.join(d, "lyr.lrc"), "w", encoding="utf-8") as f:
    f.write("[00:01.00]回到此刻\n[00:04.00]聽心如初\n"
            "[00:07.50]放下手中的忙碌，讓風輕輕吹過每一個疲憊的角落和遙遠的山谷\n"
            "[00:12.00]回到此刻\n[00:15.00]聽心如初\n[00:18.50]\n")
print("demo ready", sorted(os.listdir(d)))
