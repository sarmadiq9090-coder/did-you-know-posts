"""Build a review sheet for a rendered Reel: key frames, the Instagram UI overlay, and the cover as it
appears in the Reels tab (9:16), the profile grid (middle 3:4) and the feed (middle 1:1).

Usage: python3 tools/reel_previews.py out.mp4 [out.cover.jpg] [out.meta.json] -> out.preview.png
Without a cover, frame 0 is shown as the cover (that is what Instagram uses when no cover is sent).
"""
import json, pathlib, subprocess, sys, tempfile

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
SAFE = (120, 270, 960, 1248)


def font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"):
        if pathlib.Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def grab(mp4, t, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(mp4), "-frames:v", "1", str(out)], check=True)
    return Image.open(out).convert("RGB")


def ui_overlay(im):
    """Approximate Instagram Reels UI: top bar, bottom caption/profile block, right action rail."""
    o = im.convert("RGBA")
    d = ImageDraw.Draw(o, "RGBA")
    d.rectangle((0, 0, W, 270), fill=(255, 0, 80, 70))
    d.rectangle((0, H - 672, W, H), fill=(255, 0, 80, 70))
    d.rectangle((W - 120, 700, W, H - 300), fill=(255, 0, 80, 90))
    for i, y in enumerate((760, 900, 1040, 1180)):               # like / comment / share / more
        d.ellipse((W - 95, y, W - 25, y + 70), outline=(255, 255, 255, 230), width=6)
    d.rectangle(SAFE, outline=(0, 255, 120, 255), width=6)
    d.text((40, H - 600), "@did.you.know.iq  caption…", fill=(255, 255, 255, 255), font=font(40))
    return o.convert("RGB")


def crop_view(im, box, size):
    c = im.crop(box)
    return c.resize(size, Image.LANCZOS)


def sheet(mp4, cover=None, meta=None, out=None):
    mp4 = pathlib.Path(mp4)
    meta = json.loads(pathlib.Path(meta).read_text(encoding="utf-8")) if meta and pathlib.Path(meta).exists() else {}
    out = pathlib.Path(out) if out else mp4.with_suffix(".preview.png")
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp4)],
                               capture_output=True, text=True).stdout.strip() or 0)
    times = [("t=0 (first frame)", 0.0)]
    for s in meta.get("scenes", []):
        if s["name"] != "hook":
            times.append((f"{s['name']} t={(s['start'] + s['end']) / 2:.1f}", (s["start"] + s["end"]) / 2))
    if len(times) == 1:
        times += [(f"t={dur * f:.1f}", dur * f) for f in (0.15, 0.5, 0.85)]
    times.append((f"end t={dur - 0.1:.1f}", max(0, dur - 0.1)))
    with tempfile.TemporaryDirectory() as td:
        frames = [(lbl, grab(mp4, t, pathlib.Path(td) / f"{i}.png")) for i, (lbl, t) in enumerate(times)]
    cov = Image.open(cover).convert("RGB") if cover and pathlib.Path(cover).exists() else frames[0][1]
    cov_lbl = "cover image" if cover and pathlib.Path(cover).exists() else "cover = frame 0"
    mid = frames[min(1, len(frames) - 1)][1]

    tw, th = 270, 480                                   # thumbnail size for 9:16
    row1 = [(lbl, f.resize((tw, th), Image.LANCZOS)) for lbl, f in frames]
    row2 = [("IG UI + safe zone", ui_overlay(mid).resize((tw, th), Image.LANCZOS)),
            (cov_lbl, cov.resize((tw, th), Image.LANCZOS)),
            ("profile grid 3:4", crop_view(cov, (0, 240, 1080, 1680), (tw, 360))),
            ("feed 1:1", crop_view(cov, (0, 420, 1080, 1500), (tw, tw)))]
    cols = max(len(row1), len(row2))
    pad, lab = 14, 34
    Wt = cols * (tw + pad) + pad
    Ht = pad + lab + th + pad + lab + th + pad + 40
    sh = Image.new("RGB", (Wt, Ht), (245, 245, 245))
    d = ImageDraw.Draw(sh)
    f = font(16)
    d.text((pad, Ht - 34), f"{mp4.name}  |  {dur:.1f}s", fill=(60, 60, 60), font=font(18))
    for r, row in enumerate((row1, row2)):
        y0 = pad + r * (lab + th + pad)
        for c, (lbl, im) in enumerate(row):
            x0 = pad + c * (tw + pad)
            d.text((x0, y0 + 6), lbl, fill=(20, 20, 20), font=f)
            sh.paste(im, (x0, y0 + lab))
    sh.save(out)
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    mp4 = pathlib.Path(a[0])
    cover = a[1] if len(a) > 1 else mp4.with_suffix(".cover.jpg")
    meta = a[2] if len(a) > 2 else mp4.with_suffix(".meta.json")
    print(sheet(mp4, cover, meta))
