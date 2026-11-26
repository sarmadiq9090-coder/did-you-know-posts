"""Turn one post spec into a full-screen vertical Reel (1080x1920, 9:16, SAR 1:1) plus a cover image.

Design (see docs in GPT_INSTRUCTIONS.md, section "الريلز"):
  * Full-bleed photos (cover-cropped, never stretched) with slow Ken-Burns zoom/pan and crossfades.
  * Scene 0 = hook: «هل تعلم؟» + a short title, fully visible from the very first frame (no fade from black).
  * The fact is split into short scenes that stay on screen long enough to read; numbers and <b> words are gold.
  * Last scene = call to action (save / follow).
  * All text lives inside the Instagram safe zone, so the like/comment rail, the caption and the
    profile-grid crop never cover it. Every text layer is measured and auto-fitted; the pixel bounds are
    returned in the metadata so tools/validate_reel.py can verify them.
  * Royalty-free music is generated procedurally (numpy), matched to the video length.

Usage: python3 tools/make_reel.py specs/NN_topic.json out.mp4 [local_photo ...]
       (writes out.cover.jpg and out.meta.json next to out.mp4)
"""
import hashlib, html, json, math, pathlib, random, re, subprocess, sys, tempfile, urllib.request, wave

import numpy as np
from PIL import Image, ImageOps

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = ROOT / "assets" / "Cairo.ttf"
CHROME = next((c for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
               if subprocess.run(["which", c], capture_output=True).returncode == 0), None)
W, H = 1080, 1920
FPS = 30
SR = 48000
SS = 2                      # photos are prepared at 2x so slow camera moves stay smooth

# Instagram Reels safe zone on 1080x1920 (Meta 9:16 guidance: ~14% top, ~35% bottom, ~6% sides;
# the right-hand button rail is ~120 px wide). Text is kept inside SAFE on every frame.
SAFE = (120, 270, W - 120, H - 672)          # x0, y0, x1, y1  -> 840 x 978 px
# Hook + cover box: inside SAFE *and* inside the middle 1:1 square (feed crop, y 420..1500)
# and the middle 3:4 rectangle (profile grid crop, y 240..1680).
HOOK = (120, 440, W - 120, 1230)

HOOK_DUR, CTA_DUR, MIN_SCENE = 2.8, 2.8, 3.4
XFADE = 0.8
PALETTE = [("#071C30", "#123D59"), ("#082D24", "#175344"), ("#20102F", "#48275F"), ("#300D1B", "#641D35"),
           ("#261B12", "#594026"), ("#111827", "#303B49"), ("#09282E", "#16505C"), ("#101010", "#292929")]


# ---------------------------------------------------------------- music (royalty-free, generated)
NOTE = {n: i for i, n in enumerate("C C# D D# E F F# G G# A A# B".split())}
PROGRESSIONS = [[0, 7, 9, 5], [9, 5, 0, 7], [0, 5, 9, 7], [2, 7, 0, 9], [0, 9, 5, 7]]
MINOR = {9, 2, 4}


def freq(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def make_music(path, rng, dur):
    key = NOTE[rng.choice(["C", "D", "E", "F", "G", "A"])] + 48
    prog = rng.choice(PROGRESSIONS)
    bpm = rng.choice([72, 80, 88, 96])
    beat = 60 / bpm
    n = int(round(SR * dur))
    t = np.arange(n) / SR
    out = np.zeros(n)
    n_chords = max(4, round(dur / (4 * beat)))
    chord_len = dur / n_chords
    for ci in range(n_chords):
        root = prog[ci % len(prog)]
        quality = [0, 3, 7] if (root % 12) in MINOR else [0, 4, 7]
        s, e = int(ci * chord_len * SR), min(int(((ci + 1) * chord_len + 1.2) * SR), n)
        tt = t[s:e] - t[s]
        env = np.minimum(1, tt / 0.8) * np.exp(-np.maximum(0, tt - chord_len) / 0.6)
        for q in quality + [12]:
            f = freq(key + root + q)
            for det in (-0.12, 0.12):
                out[s:e] += 0.05 * env * np.sin(2 * np.pi * f * (1 + det / 100) * tt)
        out[s:e] += 0.08 * env * np.sin(2 * np.pi * freq(key + root - 12) * tt)
        step, k = beat / 2, 0
        while k * step < chord_len:
            q = (quality + [12, 7])[k % 5]
            ps = s + int(k * step * SR)
            pe = min(ps + int(1.2 * SR), n)
            if ps < n:
                pt = t[ps:pe] - t[ps]
                f = freq(key + 12 + root + q)
                out[ps:pe] += 0.045 * np.exp(-pt * 4.5) * (np.sin(2 * np.pi * f * pt) + 0.3 * np.sin(4 * np.pi * f * pt))
            k += 1
    ir_len = int(0.9 * SR)
    ir = np.random.default_rng(rng.randint(0, 10**6)).standard_normal(ir_len) * np.exp(-np.arange(ir_len) / (0.25 * SR))
    wet = np.fft.irfft(np.fft.rfft(out, n + ir_len) * np.fft.rfft(ir, n + ir_len))[:n]
    out = out + 0.012 * wet
    fi, fo = int(0.3 * SR), int(1.5 * SR)
    out[:fi] *= np.linspace(0, 1, fi)
    out[-fo:] *= np.linspace(1, 0, fo)
    out = out / (np.max(np.abs(out)) + 1e-9) * 0.55
    pcm = (np.stack([out, out], axis=1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- text helpers
NUM = re.compile(r"(?<![\w>])([0-9٠-٩][0-9٠-٩.,٫٬]*(?:\s?[%٪])?)")


def rich(text):
    """Escape text, keep <b>…</b>, and make every number gold too."""
    s = re.sub(r"&lt;(/?)b&gt;", r"<\1b>", html.escape(text.strip()))
    parts = re.split(r"(<b>.*?</b>)", s)
    return "".join(p if p.startswith("<b>") else NUM.sub(r"<b>\1</b>", p) for p in parts)


def plain(text):
    return re.sub(r"</?b>", "", text)


def words(text):
    return len(plain(text).split())


def balance(chunks):
    """Re-open/close <b> across chunk borders so every chunk is valid markup."""
    out, carry = [], False
    for c in chunks:
        c = ("<b>" if carry else "") + c
        carry = c.count("<b>") > c.count("</b>")
        out.append(c + ("</b>" if carry else ""))
    return out


def split_scenes(text, max_words=20):
    text = text.strip()
    total = words(text)
    if total <= max_words + 4:
        return [text]
    n = math.ceil(total / max_words)
    target = total / n
    clauses = [c for c in re.split(r"(?<=[.!؟?؛،,:])\s+", text) if c]
    scenes, cur, cur_n = [], [], 0
    for c in clauses:
        cn = words(c)
        if cur and cur_n + cn > target * 1.25 and len(scenes) < n - 1:
            scenes.append(" ".join(cur)); cur, cur_n = [], 0
        cur.append(c); cur_n += cn
    if cur:
        scenes.append(" ".join(cur))
    return balance(scenes)


def split_half(text):
    toks = text.split()
    mid = len(toks) // 2
    return balance([" ".join(toks[:mid]), " ".join(toks[mid:])])


EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]+")


def hook_title(spec):
    t = (spec.get("title") or spec.get("hook") or "").strip()
    if not t:
        first = spec.get("caption", "").strip().split("\n")[0]
        first = EMOJI.split(first)[0]
        first = re.split(r"(?<=[.!؟?])\s", first)[0].strip(" .،")
        t = first
    toks = t.split()
    if len(toks) > 11:
        t = " ".join(toks[:10]) + "…"
    return t or "معلومة ستدهشك"


def rgba(hex_color, a):
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


# ---------------------------------------------------------------- chrome
CSS = f"""@font-face {{ font-family: Cairo; src: url('file://{FONT}'); }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
html,body {{ width:{W}px; height:{H}px; overflow:hidden; background:transparent; }}
body {{ font-family: Cairo, 'Noto Color Emoji', sans-serif; direction: rtl; }}
.emo {{ font-family: 'Noto Color Emoji'; }}
b {{ color:#ffcc33; font-weight:900; }}"""


def chrome(markup, png=None, dump=False, scale=1, transparent=True):
    with tempfile.TemporaryDirectory() as td:
        page = pathlib.Path(td) / "p.html"
        page.write_text(markup, encoding="utf-8")
        args = [CHROME, "--headless=new", "--no-sandbox", "--hide-scrollbars", "--disable-gpu", "--no-first-run",
                "--disable-sync", "--disable-component-update", "--disable-background-networking",
                f"--force-device-scale-factor={scale}", f"--window-size={W},{H}", "--virtual-time-budget=5000"]
        if transparent:
            args.append("--default-background-color=00000000")
        if dump:
            r = subprocess.run(args + ["--dump-dom", f"file://{page}"], check=True, capture_output=True, text=True)
            return r.stdout
        subprocess.run(args + [f"--screenshot={png}", f"file://{page}"], check=True, capture_output=True)


def box_css(box):
    x0, y0, x1, y1 = box
    return f"position:absolute; left:{x0}px; top:{y0}px; width:{x1-x0}px; height:{y1-y0}px;"


def card_html(sid, body_html, fs, c1, idx, count):
    dots = "".join(f'<i class="{"on" if i == idx else ""}"></i>' for i in range(count)) if count > 1 else ""
    return f"""<div class="card" id="{sid}" style="--fs:{fs}px">
  <div class="lab"><span class="emo">💡</span><span>هل تعلم؟</span><span class="dots">{dots}</span></div>
  <div class="txt">{body_html}</div></div>"""


def scene_css(c1):
    return f"""
.zone {{ {box_css(SAFE)} display:flex; align-items:center; justify-content:center; }}
.card {{ width:100%; background:{rgba(c1, .80)}; border:2px solid rgba(255,255,255,.14); border-radius:40px;
  padding:34px 44px 40px; box-shadow:0 18px 50px rgba(0,0,0,.45); }}
.lab {{ display:flex; align-items:center; gap:14px; color:#ffcc33; font-size:48px; font-weight:900; margin-bottom:10px; }}
.lab .emo {{ font-size:44px; }}
.dots {{ margin-inline-start:auto; display:flex; gap:10px; }}
.dots i {{ width:16px; height:16px; border-radius:50%; background:rgba(255,255,255,.3); }}
.dots i.on {{ background:#ffcc33; }}
.txt {{ color:#fff; font-size:var(--fs); font-weight:700; line-height:1.62; text-align:start;
  text-shadow:0 2px 6px rgba(0,0,0,.35); }}
.hook {{ {box_css(HOOK)} display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; }}
.hook .hin {{ display:flex; flex-direction:column; align-items:center; width:100%; }}
.hook .big {{ font-size:118px; font-weight:900; color:#ffcc33; line-height:1.25;
  text-shadow:0 6px 24px rgba(0,0,0,.65), 0 0 2px rgba(0,0,0,.8); }}
.hook .big .emo {{ font-size:100px; }}
.hook .hemo {{ font-size:190px; line-height:1.15; margin-bottom:6px; }}
.hook .title {{ margin-top:18px; background:{rgba(c1, .78)}; border-radius:34px; padding:22px 36px 28px;
  color:#fff; font-size:var(--fs); font-weight:800; line-height:1.5; max-width:100%;
  box-shadow:0 14px 40px rgba(0,0,0,.45); }}
.hook .handle {{ margin-top:22px; direction:ltr; color:rgba(255,255,255,.92); font-size:36px; font-weight:800;
  text-shadow:0 2px 10px rgba(0,0,0,.8); }}
.cta {{ {box_css(SAFE)} display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; }}
.cta .box {{ background:{rgba(c1, .82)}; border-radius:40px; padding:44px 50px; border:2px solid rgba(255,204,51,.45);
  box-shadow:0 18px 50px rgba(0,0,0,.45); }}
.cta .l1 {{ color:#fff; font-size:64px; font-weight:900; line-height:1.4; }}
.cta .l2 {{ color:#ffcc33; font-size:50px; font-weight:800; margin-top:18px; }}
.cta .l3 {{ color:rgba(255,255,255,.95); font-size:46px; font-weight:800; margin-top:14px; direction:ltr; }}
"""


def hook_html(title, fs, emoji, attrs=""):
    hemo = f'<div class="emo hemo">{html.escape(emoji)}</div>' if emoji else ""
    return f"""<div class="hook" id="hook" {attrs} style="--fs:{fs}px"><div class="hin" data-m>{hemo}
  <div class="big"><span class="emo">💡</span> هل تعلم؟</div>
  <div class="title">{html.escape(title)}</div>
  <div class="handle">@did.you.know.iq</div></div></div>"""


CTA_HTML = """<div class="cta"><div class="box" id="cta">
  <div class="l1"><span class="emo">🔖</span> احفظ المعلومة<br>وشاركها مع من تحب</div>
  <div class="l2">تابعنا لمعلومة جديدة كل يوم</div>
  <div class="l3">@did.you.know.iq</div></div></div>"""

FIT_JS = """<pre id="out"></pre><script>
document.fonts.ready.then(() => {
  const res = {};
  for (const el of document.querySelectorAll('[data-fit]')) {
    const sizes = JSON.parse(el.dataset.sizes), maxh = +el.dataset.maxh, m = el.querySelector('[data-m]') || el;
    let pick = null, h = 0;
    for (const s of sizes) {
      el.style.setProperty('--fs', s + 'px');
      h = m.getBoundingClientRect().height;
      if (h <= maxh) { pick = s; break; }
    }
    res[el.id] = {size: pick, h: Math.round(h)};
  }
  document.getElementById('out').textContent = JSON.stringify(res);
});
</script>"""


def fit(spec_title, scenes, c1, emoji):
    """Pick the largest font size for every scene that keeps it inside its box. None = does not fit."""
    parts = []
    text_sizes = list(range(70, 43, -2))
    for i, s in enumerate(scenes):
        parts.append(f'<div class="zone" data-fit data-sizes=\'{json.dumps(text_sizes)}\' data-maxh="{SAFE[3]-SAFE[1]}" id="s{i}">'
                     + card_html(f"c{i}", rich(s), 70, c1, i, len(scenes)).replace('class="card"', 'class="card" data-m', 1)
                     + "</div>")
    hook = hook_html(spec_title, 64, emoji, f"data-fit data-sizes='{json.dumps(list(range(66, 43, -2)))}' "
                                            f'data-maxh="{HOOK[3]-HOOK[1]}"')
    page = f"""<!doctype html><html dir="rtl"><head><meta charset="utf-8"><style>{CSS}{scene_css(c1)}</style></head>
<body>{''.join(parts)}{hook}{FIT_JS}</body></html>"""
    dom = chrome(page, dump=True)
    m = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
    return json.loads(html.unescape(m.group(1))) if m and m.group(1).strip() else {}


def layer(markup_body, c1, png):
    chrome(f"""<!doctype html><html dir="rtl"><head><meta charset="utf-8"><style>{CSS}{scene_css(c1)}</style></head>
<body>{markup_body}</body></html>""", png=png)
    im = Image.open(png).convert("RGBA").crop((0, 0, W, H))
    im.save(png)
    # content bounds: card backgrounds (alpha ~204) and text (255); soft shadows (< ~120) are ignored
    bbox = im.getchannel("A").point(lambda a: 255 if a > 180 else 0).getbbox()
    return list(bbox) if bbox else None


def scrim_layer(c1, png):
    chrome(f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}
.t {{ position:absolute; left:0; top:0; width:{W}px; height:420px; background:linear-gradient(to bottom, {rgba(c1, .55)}, {rgba(c1, 0)}); }}
.b {{ position:absolute; left:0; top:{H-820}px; width:{W}px; height:820px;
  background:linear-gradient(to bottom, {rgba(c1, 0)}, {rgba(c1, .55)} 45%, {rgba(c1, .88)}); }}
.v {{ position:absolute; inset:0; background:{rgba(c1, .16)}; }}
</style></head><body><div class="v"></div><div class="t"></div><div class="b"></div></body></html>""", png=png)
    Image.open(png).convert("RGBA").crop((0, 0, W, H)).save(png)


# ---------------------------------------------------------------- photos / backgrounds
def download(pid, out):
    # large enough for a full-screen 9:16 crop without visible upscaling
    url = f"https://images.pexels.com/photos/{pid}/pexels-photo-{pid}.jpeg?auto=compress&cs=tinysrgb&h=2400"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    out.write_bytes(data)
    Image.open(out).verify()


def prepare_photo(src, dst):
    """Scale (never stretch) so the photo covers a 2x 9:16 frame; returns pan room in px on each axis."""
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    fw, fh = W * SS, H * SS
    s = max(fw / im.width, fh / im.height)
    nw, nh = max(fw, round(im.width * s)), max(fh, round(im.height * s))
    im = im.resize((nw, nh), Image.LANCZOS)
    im.save(dst, quality=95)
    return nw - fw, nh - fh, (im.width, im.height)


def bg_plain(c1, c2, emoji, png):
    """Background for specs without photos: palette gradient, soft light, dot texture and a large emoji."""
    chrome(f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}
body {{ background: linear-gradient(165deg, {c2} 0%, {c1} 62%, {c2} 100%); }}
.glow {{ position:absolute; inset:0; background: radial-gradient(circle at 50% 40%, rgba(255,255,255,.16) 0%, rgba(255,255,255,0) 55%); }}
.g {{ position:absolute; inset:0; background-image: radial-gradient(rgba(255,255,255,.11) 3px, transparent 3px);
  background-size: 54px 54px; }}
.e {{ position:absolute; left:0; right:0; top:560px; text-align:center; font-family:'Noto Color Emoji';
  font-size:560px; opacity:.34; }}
</style></head><body><div class="glow"></div><div class="g"></div><div class="e">{html.escape(emoji)}</div></body></html>""",
           png=png, scale=SS, transparent=False)
    im = Image.open(png).convert("RGB")
    im.crop((0, 0, W * SS, H * SS)).save(png)


def motion_filter(idx, room, frames, rng):
    """Filter for one background segment. Input is a single prepared image (>= 2x frame)."""
    rx, ry = room
    fw, fh = W * SS, H * SS
    d = frames
    big = max(rx / fw, ry / fh)
    kind = rng.choice(["pan", "pan", "zoomin", "zoomout"]) if big > 0.15 else rng.choice(["zoomin", "zoomout"])
    if kind == "pan":
        # slide across part of the extra width/height, centred on the middle of the photo
        if rx >= ry:
            travel = min(rx, int(fw * 0.55))
            x0 = (rx - travel) // 2
            a, b = (x0, x0 + travel) if rng.random() < .5 else (x0 + travel, x0)
            crop = f"crop={fw}:{fh}:x='{a}+({b}-{a})*n/{d}':y={ry // 2}"
        else:
            travel = min(ry, int(fh * 0.40))
            y0 = (ry - travel) // 2
            a, b = (y0, y0 + travel) if rng.random() < .5 else (y0 + travel, y0)
            crop = f"crop={fw}:{fh}:x={rx // 2}:y='{a}+({b}-{a})*n/{d}'"
        return f"[{idx}:v]loop=loop={d}:size=1:start=0,setpts=N/{FPS}/TB,{crop},scale={W}:{H}:flags=lanczos,setsar=1,format=yuv420p", kind
    # zoom on the centred 9:16 crop
    pre = f"crop={fw}:{fh}:{rx // 2}:{ry // 2}"
    z = "1+0.13*on/{d}" if kind == "zoomin" else "1.13-0.13*on/{d}"
    z = z.format(d=d)
    zp = (f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s={W}x{H}:fps={FPS}")
    return f"[{idx}:v]{pre},{zp},setsar=1,format=yuv420p", kind


# ---------------------------------------------------------------- main
def make_reel(spec_path, out_mp4, local_photos=None, cover_out=None):
    spec_path, out_mp4 = pathlib.Path(spec_path), pathlib.Path(out_mp4)
    cover_out = pathlib.Path(cover_out) if cover_out else out_mp4.with_suffix(".cover.jpg")
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    seed = int(hashlib.md5(spec_path.stem.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    c1, c2 = spec.get("colors") or rng.choice(PALETTE)
    emoji = spec.get("emoji") or "💡"
    title = hook_title(spec)
    meta = {"spec": spec_path.name, "title": title, "photos_expected": len((spec.get("photos") or [])[:3]),
            "photos_loaded": 0, "photo_errors": [], "scenes": [], "safe": SAFE, "hook_box": HOOK, "fit_ok": True}

    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        raw = [pathlib.Path(p) for p in (local_photos or [])][:3]
        if not raw:
            for i, pid in enumerate((spec.get("photos") or [])[:3]):
                p = td / f"raw{i}.jpg"
                try:
                    download(int(pid), p); raw.append(p)
                except Exception as e:
                    meta["photo_errors"].append(f"{pid}: {e}")
                    print("photo failed", pid, e)
        prepared = []
        for i, p in enumerate(raw):
            dst = td / f"bg{i}.jpg"
            try:
                rx, ry, size = prepare_photo(p, dst)
                prepared.append((dst, (rx, ry)))
            except Exception as e:
                meta["photo_errors"].append(f"{p.name}: {e}")
        meta["photos_loaded"] = len(prepared)
        if not prepared:
            dst = td / "bg_plain.png"
            bg_plain(c1, c2, emoji, dst)
            prepared = [(dst, (0, 0))]

        # ---- plan scenes and fit text
        scenes = split_scenes(spec["text"])
        for _ in range(4):
            fits = fit(title, scenes, c1, emoji if not meta["photos_loaded"] else "")
            bad = [i for i in range(len(scenes)) if not fits.get(f"s{i}", {}).get("size")]
            if not bad:
                break
            i = bad[0]
            scenes[i:i + 1] = split_half(scenes[i])
        hook_fs = fits.get("hook", {}).get("size")
        if not hook_fs:
            meta["fit_ok"] = False
            hook_fs = 44
        if any(not fits.get(f"s{i}", {}).get("size") for i in range(len(scenes))):
            meta["fit_ok"] = False

        # ---- timeline
        timeline = [("hook", 0.0, HOOK_DUR)]
        t = HOOK_DUR
        for i, s in enumerate(scenes):
            d = max(MIN_SCENE, 0.40 * words(s) + 1.4)
            timeline.append((f"s{i}", t, t + d)); t += d
        timeline.append(("cta", t, t + CTA_DUR)); t += CTA_DUR
        dur = round(t, 2)
        meta["duration"] = dur

        # ---- layers
        scrim = td / "scrim.png"; scrim_layer(c1, scrim)
        layers = []
        hook_png = td / "hook.png"
        bb = layer(hook_html(title, hook_fs, emoji if not meta["photos_loaded"] else ""), c1, hook_png)
        layers.append(("hook", hook_png, 0.0, HOOK_DUR, bb, hook_fs))
        for i, s in enumerate(scenes):
            fs = fits.get(f"s{i}", {}).get("size") or 44
            png = td / f"s{i}.png"
            bb = layer(f'<div class="zone">{card_html(f"c{i}", rich(s), fs, c1, i, len(scenes))}</div>', c1, png)
            _, a, b = next(x for x in timeline if x[0] == f"s{i}")
            layers.append((f"s{i}", png, a, b, bb, fs))
        cta_png = td / "cta.png"
        bb = layer(CTA_HTML, c1, cta_png)
        layers.append(("cta", cta_png, dur - CTA_DUR, dur, bb, None))
        for name, png, a, b, bb, fs in layers:
            meta["scenes"].append({"name": name, "start": round(a, 2), "end": round(b, 2), "bbox": bb, "font": fs,
                                   "words": words(scenes[int(name[1:])]) if name.startswith("s") else None,
                                   "box": list(HOOK if name == "hook" else SAFE)})

        # ---- cover = first photo + scrim + hook layer (identical to frame 0)
        bg0 = Image.open(prepared[0][0]).convert("RGB")
        rx, ry = prepared[0][1]
        bg0 = bg0.crop((rx // 2, ry // 2, rx // 2 + W * SS, ry // 2 + H * SS)).resize((W, H), Image.LANCZOS)
        cover = bg0.convert("RGBA")
        cover.alpha_composite(Image.open(scrim))
        cover.alpha_composite(Image.open(hook_png))
        cover.convert("RGB").save(cover_out, "JPEG", quality=90, optimize=True)
        meta["cover"] = cover_out.name

        # ---- background segments (one per photo), switching on scene borders
        k = len(prepared)
        borders = [a for name, a, b in timeline[1:]]
        switches = []
        for j in range(1, k):
            ideal = j * dur / k
            s = min(borders, key=lambda x: abs(x - ideal)) if borders else ideal
            if abs(s - ideal) > 2.5 or (switches and s - switches[-1] < 3) or s in switches:
                s = ideal
            switches.append(s)
        starts = [0.0] + [s - XFADE / 2 for s in switches]
        ends = [s + XFADE / 2 for s in switches] + [dur]
        music = td / "music.wav"; make_music(music, rng, dur)

        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        fc = []
        for j, (img, room) in enumerate(prepared):
            cmd += ["-framerate", str(FPS), "-i", str(img)]
            frames = int(math.ceil((ends[j] - starts[j]) * FPS)) + 2
            f, kind = motion_filter(j, room, frames, rng)
            fc.append(f + f",settb=1/{FPS},trim=end_frame={frames}[bg{j}]")
            meta.setdefault("motions", []).append(kind)
        last = "bg0"
        for j in range(1, k):
            tr = rng.choice(["fade", "smoothleft", "smoothright", "circleopen", "dissolve"])
            fc.append(f"[{last}][bg{j}]xfade=transition={tr}:duration={XFADE}:offset={starts[j]:.3f}[xb{j}]")
            last = f"xb{j}"
        n_in = k
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur}", "-i", str(scrim)]
        fc.append(f"[{n_in}:v]format=rgba[scr]")
        fc.append(f"[{last}][scr]overlay=0:0:format=auto[v0]")
        n_in += 1
        cur = "v0"
        for li, (name, png, a, b, bb, fs) in enumerate(layers):
            cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur}", "-i", str(png)]
            chain = f"[{n_in}:v]format=rgba"
            if name != "hook":
                chain += f",fade=t=in:st={a:.3f}:d=0.35:alpha=1"
            if name != "cta":
                chain += f",fade=t=out:st={b - 0.3:.3f}:d=0.3:alpha=1"
            fc.append(chain + f"[l{li}]")
            y = "0" if name == "hook" else f"'if(lt(t-{a:.3f},0.45),34*(1-(t-{a:.3f})/0.45),0)'"
            fc.append(f"[{cur}][l{li}]overlay=x=0:y={y}:format=auto:enable='between(t,{a:.3f},{b:.3f})'[v{li+1}]")
            cur = f"v{li+1}"; n_in += 1
        # photos are full-range JPEG (yuvj420p); deliver standard limited-range BT.709 yuv420p
        fc.append(f"[{cur}]scale=out_range=tv:out_color_matrix=bt709,format=yuv420p,setsar=1:1[v]")
        cmd += ["-i", str(music)]
        cmd += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", f"{n_in}:a",
                "-t", f"{dur}", "-r", str(FPS),
                "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-profile:v", "high", "-level:v", "4.1",
                "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709",
                "-color_primaries", "bt709", "-color_trc", "bt709", "-g", str(FPS * 2), "-x264-params", "open-gop=0",
                "-maxrate", "12M", "-bufsize", "24M",
                "-c:a", "aac", "-b:a", "128k", "-ar", str(SR), "-ac", "2",
                "-use_editlist", "0", "-movflags", "+faststart", str(out_mp4)]
        subprocess.run(cmd, check=True)
    out_mp4.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("reel ready:", out_mp4, f"{dur}s", "scenes:", len(scenes), "photos:", meta["photos_loaded"])
    return meta


if __name__ == "__main__":
    make_reel(sys.argv[1], sys.argv[2], sys.argv[3:] or None)
