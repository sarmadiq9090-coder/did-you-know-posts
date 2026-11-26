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

HOOK_DUR, CTA_DUR, MIN_SCENE = 2.0, 2.2, 2.6
XFADE = 0.6
SEG = 3.5                   # a new photo or camera move about every 3.5 s
SEC_PER_WORD, SEC_PER_SCENE = 0.32, 0.9
_STYLE = [0]                # card style of the reel being rendered (0 glass, 1 solid, 2 light)
PALETTE = [("#071C30", "#123D59"), ("#082D24", "#175344"), ("#20102F", "#48275F"), ("#300D1B", "#641D35"),
           ("#261B12", "#594026"), ("#111827", "#303B49"), ("#09282E", "#16505C"), ("#101010", "#292929")]


# ---------------------------------------------------------------- music (royalty-free, generated)
# Calm background bed for reading: slow tempo, soft attacks, mid register (audible on phone speakers),
# no sharp transients, normalised later to about -20 LUFS so it never competes with the text.
NOTE = {n: i for i, n in enumerate("C C# D D# E F F# G G# A A# B".split())}
PROGRESSIONS = [[0, 7, 9, 5], [9, 5, 0, 7], [0, 5, 9, 7], [2, 7, 0, 9], [0, 9, 5, 7], [5, 0, 7, 9]]
MINOR = {9, 2, 4}
MOODS = ["pad_bells", "soft_piano", "ambient"]


def freq(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _env(n, attack, release, total):
    t = np.arange(n) / SR
    a = np.clip(t / attack, 0, 1)
    r = np.clip((total - t) / release, 0, 1)
    return np.sin(a * np.pi / 2) ** 2 * r          # smooth (no clicks)


def make_music(path, rng, dur):
    mood = rng.choice(MOODS)
    key = NOTE[rng.choice(["C", "D", "E", "F", "G", "A"])] + 52          # around E3..A3
    prog = rng.choice(PROGRESSIONS)
    bpm = rng.choice([64, 70, 76, 80])
    beat = 60 / bpm
    n = int(round(SR * dur))
    out = np.zeros(n)
    n_chords = max(3, round(dur / (4 * beat)))
    chord_len = dur / n_chords
    nrng = np.random.default_rng(rng.randint(0, 10**6))
    for ci in range(n_chords):
        root = prog[ci % len(prog)]
        quality = [0, 3, 7] if (root % 12) in MINOR else [0, 4, 7]
        s = int(ci * chord_len * SR)
        L = min(int((chord_len + 1.5) * SR), n - s)
        if L <= 0:
            continue
        t = np.arange(L) / SR
        # warm pad: detuned sines, slow swell, gentle tremolo
        env = _env(L, 1.2, 1.4, L / SR) * (0.9 + 0.1 * np.sin(2 * np.pi * 0.25 * t))
        for q in quality:
            f = freq(key + root + q)
            for det in (-0.08, 0.08):
                out[s:s + L] += 0.040 * env * np.sin(2 * np.pi * f * (1 + det / 100) * t)
        out[s:s + L] += 0.030 * env * np.sin(2 * np.pi * freq(key + root - 12) * t)   # soft bass, not boomy
        if mood == "ambient":
            continue
        # sparse melody: quarter notes, some skipped, bell/piano-like decay
        step = beat if mood == "pad_bells" else beat * 2
        k = 0
        while k * step < chord_len - 0.2:
            if nrng.random() < (0.35 if mood == "pad_bells" else 0.2):
                k += 1; continue
            q = (quality + [12])[nrng.integers(0, 4)]
            ps = s + int(k * step * SR)
            pl = min(int(2.2 * SR), n - ps)
            if pl > 0:
                pt = np.arange(pl) / SR
                f = freq(key + 12 + root + q)
                decay = np.exp(-pt * (2.2 if mood == "pad_bells" else 1.6))
                att = np.clip(pt / 0.012, 0, 1)                     # 12 ms attack: soft, no click
                tone = np.sin(2 * np.pi * f * pt) + 0.12 * np.sin(4 * np.pi * f * pt)
                out[ps:ps + pl] += 0.028 * att * decay * tone
            k += 1
    # light room reverb
    ir_len = int(1.2 * SR)
    ir = nrng.standard_normal(ir_len) * np.exp(-np.arange(ir_len) / (0.35 * SR))
    wet = np.fft.irfft(np.fft.rfft(out, n + ir_len) * np.fft.rfft(ir, n + ir_len))[:n]
    out = out + 0.010 * wet
    # gentle tone shaping: remove rumble below ~70 Hz and soften above ~4 kHz
    F = np.fft.rfft(out); fr = np.fft.rfftfreq(n, 1 / SR)
    F *= 1 / np.sqrt(1 + (70 / np.maximum(fr, 1)) ** 4)
    F *= 1 / np.sqrt(1 + (fr / 4000) ** 4)
    out = np.fft.irfft(F, n)
    fi, fo = int(0.8 * SR), int(1.4 * SR)
    out[:fi] *= np.sin(np.linspace(0, np.pi / 2, fi)) ** 2
    out[-fo:] *= np.cos(np.linspace(0, np.pi / 2, fo)) ** 2
    out = out / (np.max(np.abs(out)) + 1e-9) * 0.5      # final level is set by loudnorm in the encoder
    pcm = (np.stack([out, out], axis=1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
    return mood


# ---------------------------------------------------------------- text helpers
NUM = re.compile(r"(?<![0-9٠-٩A-Za-z_>])([0-9٠-٩][0-9٠-٩.,٫٬]*(?:\s?[%٪])?)")  # also «و80», «بـ5»


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
""" + STYLE_CSS[_STYLE[0]](c1)


STYLE_CSS = [
    lambda c1: "",                                                    # 0: translucent card (default)
    lambda c1: f""".card {{ background:{rgba(c1, .96)}; border:none; border-inline-start:12px solid #ffcc33; border-radius:28px; }}
.hook .title {{ background:{rgba(c1, .96)}; border-inline-start:10px solid #ffcc33; border-radius:24px; }}""",
    lambda c1: f""".card {{ background:rgba(255,255,255,.94); border:none; }}
.card .txt {{ color:#1d1d1d; text-shadow:none; }} .card b {{ color:#a86b00; }}
.card .lab {{ color:#a86b00; }} .dots i {{ background:rgba(0,0,0,.18); }} .dots i.on {{ background:#a86b00; }}
.hook .title {{ background:rgba(255,255,255,.94); color:#1d1d1d; box-shadow:0 14px 40px rgba(0,0,0,.35); }}""",
]


def hook_html(title, fs, emoji, attrs="", question=False):
    hemo = f'<div class="emo hemo">{html.escape(emoji)}</div>' if emoji else ""
    label = '<span class="emo">🤔</span> خمّن…' if question else '<span class="emo">💡</span> هل تعلم؟'
    return f"""<div class="hook" id="hook" {attrs} style="--fs:{fs}px"><div class="hin" data-m>{hemo}
  <div class="big">{label}</div>
  <div class="title">{html.escape(title)}</div>
  <div class="handle">@did.you.know.iq</div></div></div>"""


CTA_HTML = """<div class="cta"><div class="box" id="cta">
  <div class="l1"><span class="emo">📩</span> أرسلها لصديق<br>لا يعرف هذه المعلومة</div>
  <div class="l2">وتابعنا لمعلومة جديدة كل يوم</div>
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


def fit(spec_title, scenes, c1, emoji, question=False):
    """Pick the largest font size for every scene that keeps it inside its box. None = does not fit."""
    parts = []
    text_sizes = list(range(70, 43, -2))
    for i, s in enumerate(scenes):
        parts.append(f'<div class="zone" data-fit data-sizes=\'{json.dumps(text_sizes)}\' data-maxh="{SAFE[3]-SAFE[1]}" id="s{i}">'
                     + card_html(f"c{i}", rich(s), 70, c1, i, len(scenes)).replace('class="card"', 'class="card" data-m', 1)
                     + "</div>")
    hook = hook_html(spec_title, 64, emoji, question=question, attrs=f"data-fit data-sizes='{json.dumps(list(range(66, 43, -2)))}' "
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


def motion_filter(idx, room, frames, rng, force=None):
    """Filter for one background segment. Input is a single prepared image (>= 2x frame)."""
    rx, ry = room
    fw, fh = W * SS, H * SS
    d = frames
    big = max(rx / fw, ry / fh)
    kind = force or (rng.choice(["pan", "pan", "zoomin", "zoomout"]) if big > 0.15 else rng.choice(["zoomin", "zoomout"]))
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
    question = re.sub(r"^\s*خم[ّ]?ن\s*[:：،]?\s*", "", (spec.get("question") or "").strip())   # label already says «خمّن…»
    hook_text = question or title                      # «خمّن…؟» hook when the spec has a question
    _STYLE[0] = int(re.match(r"(\d+)", spec_path.stem).group(1)) % 3 if re.match(r"\d+", spec_path.stem) else seed % 3
    meta = {"spec": spec_path.name, "title": title, "hook": hook_text, "style": _STYLE[0], "photos_expected": len((spec.get("photos") or [])[:3]),
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
            fits = fit(hook_text, scenes, c1, emoji if not meta["photos_loaded"] else "", bool(question))
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
            d = max(MIN_SCENE, SEC_PER_WORD * words(s) + SEC_PER_SCENE)
            timeline.append((f"s{i}", t, t + d)); t += d
        timeline.append(("cta", t, t + CTA_DUR)); t += CTA_DUR
        dur = round(t, 2)
        meta["duration"] = dur

        # ---- layers
        scrim = td / "scrim.png"; scrim_layer(c1, scrim)
        layers = []
        hook_png = td / "hook.png"
        bb = layer(hook_html(hook_text, hook_fs, emoji if not meta["photos_loaded"] else "", question=bool(question)), c1, hook_png)
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

        # ---- background: a new photo or camera move about every SEG seconds, cycling the photos.
        # The first segment starts on the cover framing (zoom in from 1.0) and the last one is photo 0
        # zooming back out to it, so the end flows into the start when the reel loops.
        k = len(prepared)
        nseg = max(k, int(round(dur / SEG)))
        seq = [j % k for j in range(nseg)]
        if nseg > 1 and k > 1 and seq[-1] != 0:
            seq[-1] = 0
            if seq[-2] == 0:
                seq[-2] = 1
        switches = [j * dur / nseg for j in range(1, nseg)]
        starts = [0.0] + [x - XFADE / 2 for x in switches]
        ends = [x + XFADE / 2 for x in switches] + [dur]
        music = td / "music.wav"; meta["music"] = make_music(music, rng, dur)

        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        fc = []
        for j in range(nseg):
            img, room = prepared[seq[j]]
            cmd += ["-framerate", str(FPS), "-i", str(img)]
            frames = int(math.ceil((ends[j] - starts[j]) * FPS)) + 2
            force = "zoomin" if j == 0 else ("zoomout" if j == nseg - 1 and nseg > 1 else None)
            f, kind = motion_filter(j, room, frames, rng, force)
            fc.append(f + f",settb=1/{FPS},trim=end_frame={frames}[bg{j}]")
            meta.setdefault("motions", []).append(kind)
        last = "bg0"
        for j in range(1, nseg):
            tr = rng.choice(["fade", "smoothleft", "smoothright", "circleopen", "dissolve"])
            fc.append(f"[{last}][bg{j}]xfade=transition={tr}:duration={XFADE}:offset={starts[j]:.3f}[xb{j}]")
            last = f"xb{j}"
        n_in = nseg
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
        fc.append(f"[{n_in}:a]loudnorm=I=-20:TP=-2:LRA=7,aresample={SR}[a]")   # calm, consistent level
        cmd += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
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
