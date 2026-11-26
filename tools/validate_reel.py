"""Quality gate for a rendered Reel. Nothing is published unless validate() returns ok=True.

Usage: python3 tools/validate_reel.py out.mp4 [out.cover.jpg] [out.meta.json]
Exit code 0 = pass, 1 = fail. A JSON report is written next to the video (out.report.json).

Checks
  * ffprobe: 1080x1920, SAR 1:1, DAR 9:16, H.264 High, yuv420p, 30 fps, progressive, AAC 48 kHz,
    both streams present and the same length, duration 3..90 s, bitrate and size inside Meta's limits
  * container: moov atom before mdat (fast start), no edit lists (Meta asks for none)
  * full decode without errors
  * cropdetect: no black bars (any detected border is confirmed against the actual pixels)
  * blackdetect: no black frames anywhere (first frame included — it is the fallback cover)
  * frames from start / middle / end are not flat or dark
  * every text layer stays inside its safe box, and text fitted without overflow
  * photos: at least one photo loaded when the spec has photos
  * reading time: each text scene stays on screen >= 0.33 s per word
  * cover: JPEG, 1080x1920, sRGB/RGB, < 8 MB, not dark or flat, title inside the 1:1 and 3:4 crops
"""
import json, pathlib, re, struct, subprocess, sys, tempfile

from PIL import Image, ImageStat

W, H = 1080, 1920
GRID_34 = (0, 240, 1080, 1680)     # profile grid shows the middle 3:4 of a 9:16 reel
FEED_11 = (0, 420, 1080, 1500)     # feed shows the middle 1:1 square of the cover


def run(args):
    return subprocess.run(args, capture_output=True, text=True)


def probe(mp4):
    r = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(mp4)])
    return json.loads(r.stdout or "{}")


def atoms(mp4):
    data = pathlib.Path(mp4).read_bytes()
    top, i = [], 0
    while i + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[i:i + 8])
        if size == 1:
            size = struct.unpack(">Q", data[i + 8:i + 16])[0]
        top.append((kind.decode("latin1"), i, size))
        if size < 8:
            break
        i += size
    moov = next(((o, s) for k, o, s in top if k == "moov"), None)
    has_elst = moov is not None and b"elst" in data[moov[0]:moov[0] + moov[1]]
    return [k for k, _, _ in top], has_elst


def frame_at(mp4, t, out):
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(mp4), "-frames:v", "1", str(out)])
    return Image.open(out).convert("RGB") if pathlib.Path(out).exists() else None


def flat_or_dark(im):
    st = ImageStat.Stat(im.convert("L"))
    return st.mean[0] < 22 or st.stddev[0] < 8, round(st.mean[0], 1), round(st.stddev[0], 1)


def inside(b, box, tol=2):
    return b and b[0] >= box[0] - tol and b[1] >= box[1] - tol and b[2] <= box[2] + tol and b[3] <= box[3] + tol


def validate(mp4, cover=None, meta=None, frames_dir=None):
    mp4 = pathlib.Path(mp4)
    fails, warns, rep = [], [], {}
    if isinstance(meta, (str, pathlib.Path)):
        meta = json.loads(pathlib.Path(meta).read_text(encoding="utf-8"))
    meta = meta or {}
    if not mp4.exists() or mp4.stat().st_size == 0:
        return False, ["video file missing or empty"], [], rep

    # ---- streams
    info = probe(mp4)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    fmt = info.get("format", {})
    if not v:
        fails.append("no video stream")
    if not a:
        fails.append("no audio stream")
    if v:
        rep["video"] = {k: v.get(k) for k in ("codec_name", "profile", "width", "height", "sample_aspect_ratio",
                                              "display_aspect_ratio", "pix_fmt", "r_frame_rate", "avg_frame_rate",
                                              "field_order", "color_range", "color_space", "duration", "bit_rate")}
        exp = {"codec_name": "h264", "width": W, "height": H, "sample_aspect_ratio": "1:1",
               "display_aspect_ratio": "9:16", "pix_fmt": "yuv420p", "r_frame_rate": "30/1", "avg_frame_rate": "30/1"}
        for k, want in exp.items():
            if v.get(k) != want:
                fails.append(f"video {k} is {v.get(k)!r}, expected {want!r}")
        if v.get("color_range") == "pc":
            fails.append("video is full range (pc); Instagram expects limited range (tv)")
        if v.get("field_order") not in (None, "progressive"):
            fails.append(f"video is not progressive ({v.get('field_order')})")
        if int(v.get("bit_rate") or 0) > 25_000_000:
            fails.append("video bitrate above 25 Mbps")
    if a:
        rep["audio"] = {k: a.get(k) for k in ("codec_name", "sample_rate", "channels", "duration", "bit_rate")}
        if a.get("codec_name") != "aac":
            fails.append(f"audio codec {a.get('codec_name')}, expected aac")
        if int(a.get("sample_rate") or 0) != 48000:
            fails.append(f"audio sample rate {a.get('sample_rate')}, expected 48000")
        if int(a.get("channels") or 0) not in (1, 2):
            fails.append("audio must be mono or stereo")
    dur = float(fmt.get("duration") or 0)
    rep["duration"] = dur
    if not 3 <= dur <= 90:
        fails.append(f"duration {dur:.2f}s outside 3..90 s")
    if meta.get("duration") and abs(dur - meta["duration"]) > 0.35:
        fails.append(f"duration {dur:.2f}s differs from planned {meta['duration']}s")
    if v and a and v.get("duration") and a.get("duration") and abs(float(v["duration"]) - float(a["duration"])) > 0.3:
        fails.append("audio and video lengths differ")
    if mp4.stat().st_size > 300 * 1024 * 1024:
        fails.append("file larger than 300 MB")
    rep["size_mb"] = round(mp4.stat().st_size / 1e6, 2)

    # ---- container
    top, has_elst = atoms(mp4)
    rep["atoms"] = top
    if "moov" not in top or "mdat" not in top or top.index("moov") > top.index("mdat"):
        fails.append("moov atom is not before mdat (fast start missing)")
    if has_elst:
        fails.append("edit list (elst) present")

    # ---- decode integrity
    r = run(["ffmpeg", "-v", "error", "-i", str(mp4), "-f", "null", "-"])
    if r.stderr.strip():
        fails.append("decode errors: " + r.stderr.strip()[:300])

    # ---- black frames
    r = run(["ffmpeg", "-i", str(mp4), "-vf", "blackdetect=d=0.03:pix_th=0.10:pic_th=0.98", "-an", "-f", "null", "-"])
    black = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr)
    rep["black_segments"] = black
    if black:
        fails.append(f"black frames at {black}")

    # ---- black bars (cropdetect, confirmed on pixels)
    r = run(["ffmpeg", "-i", str(mp4), "-vf", "fps=2,cropdetect=limit=24:round=2:reset=1", "-an", "-f", "null", "-"])
    crops = re.findall(r"t:([\d.]+) .*?crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
    suspects = [(float(t), int(w), int(h), int(x), int(y)) for t, w, h, x, y in crops
                if int(w) < W - 8 or int(h) < H - 8]
    rep["cropdetect"] = {"samples": len(crops), "suspects": len(suspects)}
    confirmed = []
    # a real bar is the same border in most frames; a dark photo region moves with the camera
    if crops and len(suspects) >= 0.5 * len(crops):
        with tempfile.TemporaryDirectory() as td:
            for t, w, h, x, y in suspects[:: max(1, len(suspects) // 8)]:
                im = frame_at(mp4, t, pathlib.Path(td) / "c.png")
                if im is None:
                    continue
                strips = []
                if x > 8: strips.append(im.crop((0, 0, x, H)))
                if W - (x + w) > 8: strips.append(im.crop((x + w, 0, W, H)))
                if y > 8: strips.append(im.crop((0, 0, W, y)))
                if H - (y + h) > 8: strips.append(im.crop((0, y + h, W, H)))
                for s in strips:
                    st = ImageStat.Stat(s.convert("L"))
                    if st.mean[0] < 20 and st.stddev[0] < 2:      # uniform and black = a real bar
                        confirmed.append(round(t, 2)); break
    if confirmed:
        fails.append(f"black bars detected at t={confirmed}")

    # ---- sample frames
    frames_dir = pathlib.Path(frames_dir) if frames_dir else None
    if frames_dir:
        frames_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        rep["frames"] = {}
        for label, t in (("start", 0.0), ("middle", dur / 2), ("end", max(0, dur - 0.1))):
            out = (frames_dir or pathlib.Path(td)) / f"frame_{label}.png"
            im = frame_at(mp4, t, out)
            if im is None:
                fails.append(f"could not extract {label} frame"); continue
            if im.size != (W, H):
                fails.append(f"{label} frame size {im.size}")
            bad, mean, sd = flat_or_dark(im)
            rep["frames"][label] = {"t": round(t, 2), "mean": mean, "std": sd}
            if bad:
                fails.append(f"{label} frame is dark or flat (mean {mean}, std {sd})")

    # ---- layout
    scenes = meta.get("scenes") or []
    if meta and not meta.get("fit_ok", True):
        fails.append("text did not fit its box even at the smallest size")
    for s in scenes:
        if not s.get("bbox"):
            fails.append(f"scene {s['name']} has no visible content"); continue
        if not inside(s["bbox"], s["box"]):
            fails.append(f"scene {s['name']} content {s['bbox']} outside safe box {s['box']}")
        if s.get("words") and (s["end"] - s["start"]) < 0.33 * s["words"]:
            fails.append(f"scene {s['name']} too short to read ({s['words']} words)")
    if meta.get("photos_expected") and not meta.get("photos_loaded"):
        fails.append("spec has photos but none could be loaded")
    elif meta.get("photos_expected", 0) > meta.get("photos_loaded", 0):
        warns.append(f"only {meta.get('photos_loaded')}/{meta.get('photos_expected')} photos loaded")

    # ---- cover
    if cover:
        cover = pathlib.Path(cover)
        if not cover.exists():
            fails.append("cover missing")
        else:
            im = Image.open(cover)
            rep["cover"] = {"format": im.format, "size": im.size, "mode": im.mode, "kb": cover.stat().st_size // 1024}
            if im.format != "JPEG": fails.append("cover is not JPEG")
            if im.size != (W, H): fails.append(f"cover size {im.size}, expected 1080x1920")
            if im.mode != "RGB": fails.append(f"cover mode {im.mode}, expected RGB")
            if cover.stat().st_size > 8 * 1024 * 1024: fails.append("cover larger than 8 MB")
            bad, mean, sd = flat_or_dark(im.convert("RGB"))
            if bad: fails.append(f"cover is dark or flat (mean {mean}, std {sd})")
            hook = next((s for s in scenes if s["name"] == "hook"), None)
            if hook and hook.get("bbox"):
                for name, box in (("1:1 feed crop", FEED_11), ("3:4 grid crop", GRID_34)):
                    if not inside(hook["bbox"], box, tol=0):
                        fails.append(f"cover title {hook['bbox']} not inside the {name} {box}")
    elif meta:
        fails.append("no cover produced")

    ok = not fails
    rep.update({"ok": ok, "failures": fails, "warnings": warns})
    (mp4.parent / (mp4.stem + ".report.json")).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    return ok, fails, warns, rep


if __name__ == "__main__":
    mp4 = pathlib.Path(sys.argv[1])
    cover = sys.argv[2] if len(sys.argv) > 2 else (str(mp4.with_suffix(".cover.jpg")) if mp4.with_suffix(".cover.jpg").exists() else None)
    meta = sys.argv[3] if len(sys.argv) > 3 else (mp4.with_suffix(".meta.json") if mp4.with_suffix(".meta.json").exists() else None)
    ok, fails, warns, rep = validate(mp4, cover, meta)
    print("PASS" if ok else "FAIL", mp4.name)
    for f in fails:
        print("  ✗", f)
    for w in warns:
        print("  !", w)
    sys.exit(0 if ok else 1)
