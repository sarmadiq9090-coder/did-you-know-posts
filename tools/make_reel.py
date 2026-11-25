"""Turn one post spec into a vertical Reel (1080x1920, ~14 s, H.264 + AAC) with royalty-free generated music.

Usage: python3 tools/make_reel.py specs/NN_topic.json out.mp4 [local_photo ...]
Each reel varies by spec name: camera motion, music key/progression/tempo.
"""
import hashlib, html, json, pathlib, random, re, subprocess, sys, tempfile, urllib.request, wave

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = ROOT / "assets" / "Cairo.ttf"
CHROME = next((c for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
               if subprocess.run(["which", c], capture_output=True).returncode == 0), None)
W, H, PH = 1080, 1920, 1080   # frame size; photo area is the top PH pixels
FPS, DUR = 30, 14.0
SR = 48000


# ---------------------------------------------------------------- music
NOTE = {n: i for i, n in enumerate("C C# D D# E F F# G G# A A# B".split())}
PROGRESSIONS = [[0, 7, 9, 5], [9, 5, 0, 7], [0, 5, 9, 7], [2, 7, 0, 9], [0, 9, 5, 7]]  # semitone roots (I-V-vi-IV ...)
MINOR = {9, 2, 4}   # chord roots that sound best as minor triads in a major key


def freq(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def make_music(path, rng):
    key = NOTE[rng.choice(["C", "D", "E", "F", "G", "A"])] + 48
    prog = rng.choice(PROGRESSIONS)
    bpm = rng.choice([72, 80, 88, 96])
    beat = 60 / bpm
    n = int(SR * DUR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    chord_len = DUR / 4
    for ci, root in enumerate(prog):
        quality = [0, 3, 7] if (root % 12) in MINOR else [0, 4, 7]
        s, e = int(ci * chord_len * SR), int(min((ci + 1) * chord_len + 1.2, DUR) * SR)
        tt = t[s:e] - t[s]
        env = np.minimum(1, tt / 0.8) * np.exp(-np.maximum(0, tt - chord_len) / 0.6)
        for q in quality + [12]:
            f = freq(key + root + q)
            for det in (-0.12, 0.12):           # soft chorus pad
                out[s:e] += 0.05 * env * np.sin(2 * np.pi * f * (1 + det / 100) * tt)
        fb = freq(key + root - 12)               # bass
        out[s:e] += 0.08 * env * np.sin(2 * np.pi * fb * tt)
        # plucked arpeggio, 8th notes
        step = beat / 2
        k = 0
        while k * step < chord_len:
            q = (quality + [12, 7])[k % 5]
            ps = s + int(k * step * SR)
            pe = min(ps + int(1.2 * SR), n)
            pt = t[ps:pe] - t[ps]
            f = freq(key + 12 + root + q)
            out[ps:pe] += 0.045 * np.exp(-pt * 4.5) * (np.sin(2 * np.pi * f * pt) + 0.3 * np.sin(4 * np.pi * f * pt))
            k += 1
    # simple reverb
    ir_len = int(0.9 * SR)
    ir = rng_noise = np.random.default_rng(rng.randint(0, 10**6)).standard_normal(ir_len) * np.exp(-np.arange(ir_len) / (0.25 * SR))
    wet = np.fft.irfft(np.fft.rfft(out, n + ir_len) * np.fft.rfft(ir, n + ir_len))[:n]
    out = out + 0.012 * wet
    fade = int(1.5 * SR)
    out[:int(0.5 * SR)] *= np.linspace(0, 1, int(0.5 * SR))
    out[-fade:] *= np.linspace(1, 0, fade)
    out = out / (np.max(np.abs(out)) + 1e-9) * 0.55
    pcm = (np.stack([out, out], axis=1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- layers
def clean(text):
    return re.sub(r"&lt;(/?)b&gt;", r"<\1b>", html.escape(text))


def chrome_png(markup, out, transparent):
    with tempfile.TemporaryDirectory() as td:
        page = pathlib.Path(td) / "p.html"
        page.write_text(markup, encoding="utf-8")
        args = [CHROME, "--headless=new", "--no-sandbox", "--hide-scrollbars", "--force-device-scale-factor=1",
                f"--window-size={W},{H}", f"--screenshot={out}", "--virtual-time-budget=3000"]
        if transparent:
            args.append("--default-background-color=00000000")
        subprocess.run(args + [f"file://{page}"], check=True, capture_output=True)


CSS = f"""@font-face {{ font-family: Cairo; src: url('file://{FONT}'); }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
html,body {{ width:{W}px; height:{H}px; overflow:hidden; }}
body {{ font-family: Cairo, 'Noto Color Emoji', sans-serif; direction: rtl; }}"""


def base_layer(spec, c1, c2, out):
    emoji = "" if spec.get("photos") else (
        f'<div style="position:absolute;top:0;left:0;width:{W}px;height:{PH}px;display:flex;align-items:center;'
        f'justify-content:center;font-size:420px;font-family:\'Noto Color Emoji\'">{html.escape(spec.get("emoji", "💡"))}</div>')
    chrome_png(f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}
body {{ background: linear-gradient(160deg, {c1} 0%, {c2} 100%); }}</style></head><body>{emoji}</body></html>""", out, False)


def text_layer(spec, c1, out):
    chrome_png(f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}
body {{ background: transparent; }}
.fade {{ position:absolute; top:{PH-260}px; left:0; width:{W}px; height:262px; background: linear-gradient(to bottom, transparent, {c1}); }}
.label {{ position:absolute; top:{PH+20}px; right:70px; left:70px; display:flex; align-items:center; gap:18px; }}
.label .t {{ font-size:88px; font-weight:900; color:#ffcc33; text-shadow:0 4px 14px rgba(0,0,0,.4); }}
.label .b {{ font-family:'Noto Color Emoji'; font-size:76px; }}
.label .l {{ flex:1; height:5px; background:rgba(255,255,255,.35); border-radius:5px; }}
.text {{ position:absolute; top:{PH+170}px; right:70px; left:70px; color:#fff; font-size:52px; font-weight:600;
  line-height:1.75; text-align:justify; text-shadow:0 2px 8px rgba(0,0,0,.35); }}
.text b {{ color:#ffcc33; font-weight:900; }}
.foot {{ position:absolute; bottom:70px; right:70px; left:70px; display:flex; justify-content:space-between;
  color:rgba(255,255,255,.9); font-size:40px; font-weight:800; border-top:3px solid rgba(255,255,255,.25); padding-top:22px; }}
.foot .h {{ direction:ltr; }}
</style></head><body>
<div class="fade"></div>
<div class="label"><span class="b">💡</span><span class="t">هل تعلم؟</span><span class="l"></span></div>
<div class="text">{clean(spec["text"])}</div>
<div class="foot"><span>🔖 احفظ وشارك</span><span class="h">@did.you.know.iq</span></div>
</body></html>""", out, True)


def download(pid, out):
    url = f"https://images.pexels.com/photos/{pid}/pexels-photo-{pid}.jpeg?auto=compress&cs=tinysrgb&w=1600"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out.write_bytes(r.read())


# ---------------------------------------------------------------- video
MOTIONS = [
    "z='min(zoom+0.0009,1.25)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",            # zoom in
    "z='if(eq(on,0),1.25,max(zoom-0.0009,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",  # zoom out
    "z='1.18':x='(iw-iw/zoom)*on/{d}':y='ih/2-(ih/zoom/2)'",                               # pan right
    "z='1.18':x='(iw-iw/zoom)*(1-on/{d})':y='ih/2-(ih/zoom/2)'",                           # pan left
]


def make_reel(spec_path, out_mp4, local_photos=None):
    spec_path = pathlib.Path(spec_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    seed = int(hashlib.md5(spec_path.stem.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    c1, c2 = spec.get("colors") or rng.choice([("#071C30", "#123D59"), ("#20102F", "#48275F"), ("#082D24", "#175344")])
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        photos = []
        for i, src in enumerate(local_photos or []):
            photos.append(pathlib.Path(src))
        if not photos:
            for i, pid in enumerate((spec.get("photos") or [])[:3]):
                try:
                    p = td / f"p{i}.jpg"; download(int(pid), p); photos.append(p)
                except Exception as e:
                    print("photo failed", pid, e)
        if not photos:
            spec = dict(spec); spec.pop("photos", None)
        base, text, music = td / "base.png", td / "text.png", td / "music.wav"
        base_layer(spec, c1, c2, base)
        text_layer(spec, c1, text)
        make_music(music, rng)

        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", str(DUR), "-i", str(base)]
        fc = []
        if photos:
            xf = 0.8
            seg = (DUR + xf * (len(photos) - 1)) / len(photos)
            frames = int(seg * FPS)
            motions = rng.sample(MOTIONS, len(MOTIONS))
            for i, p in enumerate(photos):
                cmd += ["-i", str(p)]
                m = motions[i % len(motions)].format(d=frames)
                fc.append(f"[{i+1}:v]scale=2160:2160:force_original_aspect_ratio=increase,crop=2160:2160,"
                          f"zoompan={m}:d={frames}:s={W}x{PH}:fps={FPS},setsar=1,format=yuv420p[s{i}]")
            last = "s0"
            for i in range(1, len(photos)):
                off = round(i * (seg - xf), 3)
                fc.append(f"[{last}][s{i}]xfade=transition={rng.choice(['fade','smoothleft','fadeblack','circleopen'])}:"
                          f"duration={xf}:offset={off}[x{i}]")
                last = f"x{i}"
            fc.append(f"[0:v][{last}]overlay=0:0:shortest=0[bg]")
            ti = len(photos) + 1
        else:
            fc.append("[0:v]copy[bg]")
            ti = 1
        cmd += ["-loop", "1", "-t", str(DUR), "-i", str(text), "-i", str(music)]
        fc.append(f"[{ti}:v]format=rgba,fade=t=in:st=0.6:d=1.0:alpha=1[tx]")
        fc.append("[bg][tx]overlay=0:0,fade=t=in:st=0:d=0.4,fade=t=out:st=%.2f:d=0.6,format=yuv420p[v]" % (DUR - 0.6))
        cmd += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", f"{ti+1}:a",
                "-t", str(DUR), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", str(SR), "-movflags", "+faststart",
                str(out_mp4)]
        subprocess.run(cmd, check=True)
    print("reel ready:", out_mp4)


if __name__ == "__main__":
    make_reel(sys.argv[1], sys.argv[2], sys.argv[3:] or None)
