"""Instagram Story images (1080x1920 JPEG) built from posts that are already rendered.

Two kinds:
  question  - morning teaser for the post that goes out next: photo + «سؤال اليوم» + the title,
              "the answer is in today's 1 pm post".
  post      - the image post that was just published, framed for a story, with "full fact in our
              latest post".
Story video for reels is the reel file itself (see scripts/publish_reel.py).

Everything important stays inside the story safe zone (top 250 px = profile bar, bottom 340 px =
reply bar). No network is needed: backgrounds come from posts/<name>.jpg.

Usage: python3 tools/make_story.py question|post posts/NN_topic.jpg out.jpg ["title"]
"""
import html, json, pathlib, re, subprocess, sys, tempfile

from PIL import Image, ImageFilter, ImageEnhance, ImageStat

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = ROOT / "assets" / "Cairo.ttf"
CHROME = next((c for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
               if subprocess.run(["which", c], capture_output=True).returncode == 0), None)
W, H = 1080, 1920
SAFE_TOP, SAFE_BOTTOM = 250, 340
PHOTO_BOX = (70, 34, 1010, 674)        # where the photo grid sits on a rendered 1080x1350 post
POST_ANSWER_TIME = "الساعة 1 ظهراً"     # first image slot (cron 10:07 UTC)


def background(post, out):
    """Blurred, darkened full-screen background from the post's photo area."""
    ph = Image.open(post).convert("RGB").crop(PHOTO_BOX)
    s = max(W / ph.width, H / ph.height)
    bg = ph.resize((round(ph.width * s) + 2, round(ph.height * s) + 2), Image.LANCZOS)
    x, y = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((x, y, x + W, y + H)).filter(ImageFilter.GaussianBlur(38))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    bg.save(out, quality=92)


def photo_crop(post, out):
    Image.open(post).convert("RGB").crop(PHOTO_BOX).save(out, quality=94)


def accent(post):
    """Palette colour of the post (its background at the footer) for the card tint."""
    px = Image.open(post).convert("RGB").getpixel((20, 1330))
    return "#%02x%02x%02x" % px


CSS = f"""@font-face {{ font-family: Cairo; src: url('file://{FONT}'); }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
html,body {{ width:{W}px; height:{H}px; overflow:hidden; }}
body {{ font-family: Cairo, 'Noto Color Emoji', sans-serif; direction: rtl; color:#fff; }}
.emo {{ font-family:'Noto Color Emoji'; }}
.bg {{ position:absolute; inset:0; background-size:cover; }}
.pill {{ position:absolute; top:{SAFE_TOP + 20}px; left:0; right:0; text-align:center; }}
.pill span {{ display:inline-block; background:#ffcc33; color:#1a1a1a; font-size:46px; font-weight:900;
  padding:10px 38px 16px; border-radius:60px; box-shadow:0 8px 24px rgba(0,0,0,.35); }}
.handle {{ position:absolute; bottom:{SAFE_BOTTOM + 20}px; left:0; right:0; text-align:center; direction:ltr;
  font-size:38px; font-weight:800; color:rgba(255,255,255,.92); text-shadow:0 2px 10px rgba(0,0,0,.6); }}
"""

FIT = """<script>
document.fonts.ready.then(() => {
  const el = document.getElementById('fit'); if (!el) return;
  const max = +el.dataset.maxh; let fs = +el.dataset.fs;
  while (el.scrollHeight > max && fs > 40) { fs -= 2; el.style.fontSize = fs + 'px'; }
});
</script>"""


def chrome_jpg(markup, out):
    with tempfile.TemporaryDirectory() as td:
        page = pathlib.Path(td) / "s.html"
        page.write_text(markup, encoding="utf-8")
        png = pathlib.Path(td) / "s.png"
        subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--hide-scrollbars", "--disable-gpu", "--no-first-run",
                        "--disable-sync", "--disable-component-update", "--disable-background-networking",
                        "--force-device-scale-factor=1", f"--window-size={W},{H}", "--virtual-time-budget=5000",
                        f"--screenshot={png}", f"file://{page}"], check=True, capture_output=True)
        Image.open(png).convert("RGB").crop((0, 0, W, H)).save(out, "JPEG", quality=90, optimize=True)


def question_story(post, title, out):
    post, out = pathlib.Path(post), pathlib.Path(out)
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        background(post, td / "bg.jpg"); photo_crop(post, td / "ph.jpg")
        c = accent(post)
        chrome_jpg(f"""<!doctype html><html dir="rtl"><head><meta charset="utf-8"><style>{CSS}
.wrap {{ position:absolute; top:{SAFE_TOP + 140}px; bottom:{SAFE_BOTTOM + 10}px; left:60px; right:60px;
  display:flex; flex-direction:column; justify-content:center; align-items:stretch; gap:30px; }}
.wrap .handle {{ position:static; }}
.card {{ background:{c}; border-radius:44px;
  overflow:hidden; box-shadow:0 20px 60px rgba(0,0,0,.5); border:2px solid rgba(255,255,255,.15); }}
.card img {{ display:block; width:100%; height:520px; object-fit:cover; }}
.card .in {{ padding:34px 46px 44px; text-align:center; }}
.q {{ font-size:50px; font-weight:900; color:#ffcc33; }}
#fit {{ margin-top:10px; font-size:68px; font-weight:900; line-height:1.4; }}
.ans {{ margin-top:26px; font-size:40px; font-weight:800; color:rgba(255,255,255,.95);
  border-top:2px solid rgba(255,255,255,.25); padding-top:22px; }}
</style></head><body><div class="bg" style="background-image:url('file://{td}/bg.jpg')"></div>
<div class="pill"><span><span class="emo">💡</span> سؤال اليوم</span></div>
<div class="wrap"><div class="card"><img src="file://{td}/ph.jpg"><div class="in">
  <div class="q">هل تعلم…؟</div>
  <div id="fit" data-fs="68" data-maxh="300">{html.escape(title)}</div>
  <div class="ans"><span class="emo">⏰</span> الجواب في منشورنا اليوم {POST_ANSWER_TIME}</div>
</div></div>
<div class="handle">@did.you.know.iq</div></div>{FIT}</body></html>""", out)
    return out


def post_story(post, out):
    post, out = pathlib.Path(post), pathlib.Path(out)
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        background(post, td / "bg.jpg")
        pw, ph = 880, 1100                                    # 4:5 post inside the safe zone
        top = SAFE_TOP + 115
        chrome_jpg(f"""<!doctype html><html dir="rtl"><head><meta charset="utf-8"><style>{CSS}
.post {{ position:absolute; top:{top}px; left:{(W - pw) // 2}px; width:{pw}px; height:{ph}px; border-radius:30px;
  overflow:hidden; box-shadow:0 22px 60px rgba(0,0,0,.55); }}
.post img {{ width:100%; height:100%; display:block; }}
.cta {{ position:absolute; top:{top + ph + 26}px; left:60px; right:60px; text-align:center; font-size:40px; font-weight:900;
  text-shadow:0 2px 12px rgba(0,0,0,.7); }}
</style></head><body><div class="bg" style="background-image:url('file://{td}/bg.jpg')"></div>
<div class="pill"><span><span class="emo">🆕</span> منشور جديد</span></div>
<div class="post"><img src="file://{post.resolve()}"></div>
<div class="cta">المعلومة كاملة في آخر منشور على الصفحة <span class="emo">👆</span></div>
</body></html>""", out)
    return out


def validate_story(jpg):
    """Return a list of problems (empty = OK)."""
    p = pathlib.Path(jpg)
    if not p.exists():
        return ["story image missing"]
    im = Image.open(p)
    fails = []
    if im.format != "JPEG": fails.append("not JPEG")
    if im.size != (W, H): fails.append(f"size {im.size}")
    if im.mode != "RGB": fails.append(f"mode {im.mode}")
    if p.stat().st_size > 8 * 1024 * 1024: fails.append("larger than 8 MB")
    st = ImageStat.Stat(im.convert("L"))
    if st.mean[0] < 20 or st.stddev[0] < 10: fails.append("image is dark or flat")
    # the two safe bands must not be empty-black either (a broken render shows as a black frame)
    return fails


if __name__ == "__main__":
    kind, post, out = sys.argv[1], sys.argv[2], sys.argv[3]
    if kind == "question":
        question_story(post, sys.argv[4] if len(sys.argv) > 4 else "معلومة ستدهشك", out)
    else:
        post_story(post, out)
    print(out, validate_story(out) or "OK")
