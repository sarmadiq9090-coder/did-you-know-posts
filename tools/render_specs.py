"""Render every specs/NN_topic.json that has no posts/NN_topic.jpg yet.

Spec format (UTF-8 JSON):
{
  "text": "أن ... <b>رقم مهم</b> ...",          # required, starts with «أن», 2-5 lines
  "caption": "الوصف\n\n#هل_تعلم ...",           # required
  "photos": [5986729, 3046629, 9718224],       # optional: 1-3 Pexels photo IDs
  "emoji": "🐙",                                 # optional: used when there are no photos
  "colors": ["#0b3d5c", "#1b6f8f"]              # optional: gradient
}
"""
import base64, json, pathlib, subprocess, sys, tempfile, urllib.request, html, re, random

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = ROOT / "assets" / "Cairo.ttf"
CHROME = next((c for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
               if subprocess.run(["which", c], capture_output=True).returncode == 0), None)
PALETTES = [("#071C30", "#123D59"), ("#082D24", "#175344"), ("#20102F", "#48275F"), ("#300D1B", "#641D35"),
            ("#261B12", "#594026"), ("#111827", "#303B49"), ("#09282E", "#16505C"), ("#101010", "#292929")]


def photo_uri(pid):
    url = f"https://images.pexels.com/photos/{pid}/pexels-photo-{pid}.jpeg?auto=compress&cs=tinysrgb&w=1200"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return "data:image/jpeg;base64," + base64.b64encode(r.read()).decode()


def clean(text):
    # allow only <b> tags; escape everything else
    t = html.escape(text)
    return re.sub(r"&lt;(/?)b&gt;", r"<\1b>", t)


LAYOUT1_CSS = """
body.l1 { background: linear-gradient(to bottom, rgba(0,0,0,0) 22%, {C1A} 48%, {C1} 100%), url({BG}) center/cover; }
body.l1 .grid, body.l1 .hero { display:none; }
body.l1 .card::before { content:''; flex:1; }
body.l1 .label { margin-top:0; }
body.l1 .text { text-shadow:0 2px 10px rgba(0,0,0,.55); }
body.l1 .foot { margin-top:22px; }
"""
LAYOUT2_CSS = """
body.l2 .label { order:1; margin-top:4px; }
body.l2 .text { order:2; }
body.l2 .grid, body.l2 .hero { order:3; margin-top:auto; height:560px; }
body.l2 .foot { order:4; margin-top:22px; }
"""


def rgba(hex_color, a):
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


def build_html(spec, idx):
    c1, c2 = spec.get("colors") or PALETTES[idx % len(PALETTES)]
    photos = []
    for pid in (spec.get("photos") or [])[:3]:
        try:
            photos.append(photo_uri(int(pid)))
        except Exception as e:
            print(f"  photo {pid} failed: {e}")
    if photos:
        cls = {1: "one", 2: "two", 3: ""}[len(photos)]
        tiles = "".join(f'<div class="g{i+1}" style="background-image:url({u})"></div>' for i, u in enumerate(photos))
        top = f'<div class="grid {cls}">{tiles}</div>'
    else:
        top = f'<div class="hero">{html.escape(spec.get("emoji", "💡"))}</div>'
    # 3 alternating layouts so the grid does not repeat one template (originality):
    # 0 photos on top / 1 full-bleed photo with the text over a gradient / 2 text first, photos below
    layout = spec.get("_layout", 0)
    if layout == 1 and not photos:
        layout = 0
    lay_css = {0: "", 2: LAYOUT2_CSS,
               1: LAYOUT1_CSS.replace("{BG}", photos[0] if photos else "").replace("{C1A}", rgba(c1, .86)).replace("{C1}", c1)}[layout]
    return f"""<!doctype html><html dir="rtl"><head><meta charset="utf-8"><style>
@font-face {{ font-family: Cairo; src: url('file://{FONT}'); }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
html,body {{ width:1080px; height:1350px; overflow:hidden; }}
body {{ font-family: Cairo, 'Noto Color Emoji', sans-serif; background: linear-gradient(135deg, {c1} 0%, {c2} 100%); padding:34px 70px; }}
.card {{ width:100%; height:100%; display:flex; flex-direction:column; }}
.grid {{ display:grid; grid-template-columns: 2fr 1fr; grid-template-rows: 1fr 1fr; gap:12px; height:640px; }}
.grid div {{ background-size:cover; background-position:center; border-radius:18px; box-shadow:0 6px 18px rgba(0,0,0,.35); }}
.g1 {{ grid-row: 1 / 3; }}
.grid.two {{ grid-template-columns: 1fr 1fr; grid-template-rows: 1fr; }}
.grid.two .g1, .grid.one .g1 {{ grid-row:auto; }}
.grid.one {{ grid-template-columns: 1fr; grid-template-rows: 1fr; }}
.hero {{ height:560px; display:flex; align-items:center; justify-content:center; font-size:300px;
  font-family:'Noto Color Emoji'; background: rgba(255,255,255,.08); border-radius:24px; }}
.label {{ display:flex; align-items:center; gap:16px; margin:26px 0 14px; }}
.label .t {{ font-size:62px; font-weight:900; color:#ffcc33; text-shadow:0 3px 10px rgba(0,0,0,.35); }}
.label .bulb {{ font-family:'Noto Color Emoji'; font-size:56px; }}
.label .line {{ flex:1; height:4px; background: rgba(255,255,255,.35); border-radius:4px; }}
.text {{ color:#fff; font-size:var(--fs, 41px); font-weight:600; line-height:1.75; text-align:justify; text-shadow:0 2px 6px rgba(0,0,0,.25); }}
.text b {{ color:#ffcc33; font-weight:900; }}
.foot {{ margin-top:auto; display:flex; justify-content:space-between; align-items:center; color:rgba(255,255,255,.85);
  font-size:30px; font-weight:800; border-top:2px solid rgba(255,255,255,.25); padding-top:14px; }}
.foot .h {{ direction:ltr; }}
{lay_css}
</style></head><body class="l{layout}"><div class="card">{top}
<div class="label"><span class="bulb">💡</span><span class="t">هل تعلم؟</span><span class="line"></span></div>
<div class="text">{clean(spec["text"])}</div>
<div class="foot"><span>🔖 احفظ المنشور وشاركه</span><span class="h">@did.you.know.iq</span></div>
</div><script>
// shrink the text (never below 32px) until the footer fits on the card
document.fonts.ready.then(() => {{
  const card = document.querySelector('.card'), t = document.querySelector('.text');
  let fs = 41;
  while (card.scrollHeight > card.clientHeight + 1 && fs > 32) {{ fs -= 1; t.style.setProperty('--fs', fs + 'px'); }}
}});
</script></body></html>"""


def render(spec_f, idx):
    spec = json.loads(spec_f.read_text(encoding="utf-8-sig"))
    m = re.match(r"(\d+)_", spec_f.name)
    spec["_layout"] = int(m.group(1)) % 3 if m else 0
    out_jpg = ROOT / "posts" / (spec_f.stem + ".jpg")
    with tempfile.TemporaryDirectory() as td:
        page = pathlib.Path(td) / "p.html"
        page.write_text(build_html(spec, idx), encoding="utf-8")
        png = pathlib.Path(td) / "p.png"
        subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--hide-scrollbars", "--force-device-scale-factor=1",
                        "--window-size=1080,1350", f"--screenshot={png}", "--virtual-time-budget=4000",
                        f"file://{page}"], check=True, capture_output=True)
        from PIL import Image
        Image.open(png).convert("RGB").crop((0, 0, 1080, 1350)).save(out_jpg, quality=92)
    (ROOT / "posts" / (spec_f.stem + ".txt")).write_text(spec["caption"].strip() + "\n", encoding="utf-8")
    print("rendered", out_jpg.name)


def explode_batches():
    """A spec file may hold a JSON list of posts (a batch). Split it into one file per post,
    numbered after the highest existing NN, then delete the batch file."""
    def next_num():
        nums = [int(m.group(1)) for d in ("specs", "posts") for f in (ROOT / d).iterdir()
                if (m := re.match(r"(\d+)_", f.name))]
        return max(nums, default=0) + 1
    for f in sorted((ROOT / "specs").glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
        except Exception as e:
            print(f"INVALID JSON {f.name}: {e}")
            continue
        if isinstance(data, dict) and isinstance(data.get("posts"), list):
            data = data["posts"]
        if not isinstance(data, list):
            continue
        n = next_num()
        for item in data:
            if not isinstance(item, dict) or not item.get("text") or not item.get("caption"):
                print(f"  skipped bad item in {f.name}")
                continue
            topic = re.sub(r"[^a-z0-9]+", "_", str(item.pop("name", item.pop("topic", "post"))).lower()).strip("_") or "post"
            (ROOT / "specs" / f"{n:02d}_{topic}.json").write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
            n += 1
        f.unlink()
        print(f"split {f.name} into {len(data)} specs")


def main():
    if not CHROME:
        sys.exit("No Chrome/Chromium found")
    explode_batches()
    specs = sorted((ROOT / "specs").glob("*.json"))
    for i, f in enumerate(specs):
        if not (ROOT / "posts" / (f.stem + ".jpg")).exists():
            try:
                render(f, i)
            except Exception as e:
                print(f"FAILED {f.name}: {e}")


if __name__ == "__main__":
    main()
