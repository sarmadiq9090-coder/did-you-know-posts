"""Render the next N reels (no publishing) and upload them to the public 'manual-pack' release,
so they can be uploaded by hand from the owner's browser. Each reel: <stem>.mp4 (<3.6 MB), <stem>.cover.jpg, <stem>.txt (caption)."""
import json, os, pathlib, subprocess, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts")); sys.argv = [sys.argv[0], "--dry-run"] + sys.argv[1:]
import publish_reel as p

TAG, LIMIT = "manual-pack", 3.6e6
N = int(os.environ.get("PACK_REELS", "2"))
OUT = pathlib.Path(os.environ.get("REEL_OUT", "/tmp/pack")); OUT.mkdir(parents=True, exist_ok=True)
p.OUT = OUT

def dur(f):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)]))

def shrink(src, dst):
    if src.stat().st_size <= LIMIT:
        dst.write_bytes(src.read_bytes()); return
    kbps = int(LIMIT * 8 / dur(src) / 1000 * 0.92) - 96
    for passn in (1, 2):
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c:v", "libx264", "-preset", "slow", "-b:v", f"{kbps}k",
               "-pass", str(passn), "-passlogfile", str(OUT / "x264"), "-pix_fmt", "yuv420p", "-color_range", "tv",
               "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
        cmd += ["-an", "-f", "mp4", "/dev/null"] if passn == 1 else ["-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-use_editlist", "0", str(dst)]
        subprocess.run(cmd, check=True)

state = json.loads((ROOT / "state.json").read_text(encoding="utf-8"))
files, done = [], []
for spec_f in p.candidates(state):
    if len(done) >= N:
        break
    ok, mp4, cover, fails = p.render_and_check(spec_f)
    if not ok:
        continue
    small = OUT / "pack" / mp4.name; small.parent.mkdir(exist_ok=True)
    shrink(mp4, small)
    spec = json.loads(spec_f.read_text(encoding="utf-8-sig"))
    txt = small.with_suffix(".txt"); txt.write_text(spec["caption"], encoding="utf-8")
    cov = small.with_name(spec_f.stem + ".cover.jpg"); cov.write_bytes(cover.read_bytes())
    files += [small, cov, txt]; done.append(spec_f.stem)
    print(f"packed {spec_f.stem}: {small.stat().st_size/1e6:.2f} MB")
if files:
    if p.gh("release", "view", TAG, check=False).returncode != 0:
        p.gh("release", "create", TAG, "--title", "Manual upload pack", "--notes", "Reels rendered for manual upload.", "--prerelease")
    p.gh("release", "upload", TAG, *map(str, files), "--clobber")
print("PACK:", " ".join(done))
