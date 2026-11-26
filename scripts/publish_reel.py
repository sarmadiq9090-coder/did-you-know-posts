"""Make, validate and publish one Reel from a not-yet-used spec, then delete the temporary files.

    python3 scripts/publish_reel.py            # real run (GitHub Actions, scheduled)
    python3 scripts/publish_reel.py --dry-run  # render + validate + previews only; no upload, no API, no state change

Flow (real run)
  1. pick the newest spec not yet published as an image or used as a reel (photos preferred)
  2. render video + cover (tools/make_reel.py) and run the quality gate (tools/validate_reel.py);
     a spec that fails the gate is recorded in state["reel_rejected"] and the next spec is tried (max 3)
  3. upload video + cover as temporary assets of the public "reels-tmp" release (never committed to git)
  4. skip if the same caption is already on the account (protects against double posting)
  5. create the REELS container with cover_url, wait until Meta finished processing, publish
  6. read the new media back and check it is a REELS item; only then add the spec to state["reeled"]
  7. delete the temporary assets (after processing and verification are done)
Previews, report and video are left in $REEL_OUT (default /tmp/reel_out) for the workflow artifact.

Env: IG_TOKEN, IG_USER_ID (optional), GITHUB_REPOSITORY, GH_TOKEN (for the gh CLI), REEL_OUT (optional)
"""
import json, os, pathlib, re, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from make_reel import make_reel           # noqa: E402
from validate_reel import validate         # noqa: E402
from reel_previews import sheet            # noqa: E402
sys.path.insert(0, str(ROOT / "scripts"))
import ig_api                              # noqa: E402

DRY = "--dry-run" in sys.argv
TOKEN = os.environ.get("IG_TOKEN", "").strip()
IG_ID = os.environ.get("IG_USER_ID", "17841462150090981")
API = "https://graph.instagram.com/v26.0" if TOKEN.startswith("IG") else "https://graph.facebook.com/v26.0"
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TAG = "reels-tmp"
OUT = pathlib.Path(os.environ.get("REEL_OUT", "/tmp/reel_out"))
RATE_LIMIT_CODES = {4, 17, 32, 613, 80002}


class ApiError(Exception):
    def __init__(self, msg, code=None, transient=False):
        super().__init__(msg)
        self.code, self.transient = code, transient


def call(method, path, retries=3, **params):
    """Graph API call with retries on network errors and 5xx. The token is never printed."""
    params["access_token"] = TOKEN
    data = urllib.parse.urlencode(params).encode()
    url = f"{API}/{path}"
    for attempt in range(retries):
        req = (urllib.request.Request(f"{url}?{data.decode()}") if method == "GET"
               else urllib.request.Request(url, data=data, method="POST"))
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            try:
                err = json.loads(body).get("error", {})
            except ValueError:
                err = {}
            code = err.get("code")
            if code in RATE_LIMIT_CODES or e.code == 429:
                raise ApiError(f"rate limited on {path}: {err.get('message', body)[:300]}", code)
            if e.code >= 500 or err.get("is_transient"):
                if attempt < retries - 1:
                    time.sleep(15 * (attempt + 1)); continue
                raise ApiError(f"server error {e.code} on {path}: {body[:300]}", code, transient=True)
            raise ApiError(f"API error {e.code} on {path}: {body[:500]}", code)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries - 1:
                time.sleep(15 * (attempt + 1)); continue
            raise ApiError(f"network error on {path}: {e}", transient=True)


def gh(*args, check=True):
    return subprocess.run(["gh", *args, "--repo", REPO], capture_output=True, text=True, check=check)


def num(p):
    m = re.match(r"(\d+)_", p.name)
    return int(m.group(1)) if m else -1


def candidates(state):
    published = {pathlib.Path(p).stem for p in state.get("published", [])}
    skip = set(state.get("reeled", [])) | set(state.get("reel_rejected", {}))
    specs = [s for s in (ROOT / "specs").glob("*.json")
             if s.stem not in published and s.stem not in skip and num(s) >= 0]
    specs.sort(key=num, reverse=True)       # newest first, so the image queue's near-term order is untouched
    has_photos = lambda s: bool(json.loads(s.read_text(encoding="utf-8-sig")).get("photos"))
    return [s for s in specs if has_photos(s)] + [s for s in specs if not has_photos(s)]


def norm(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def already_posted(caption):
    try:
        recent = call("GET", f"{IG_ID}/media", fields="id,caption,media_product_type,permalink", limit="15")
    except ApiError as e:
        print("could not read recent media:", e)
        return None
    return next((m for m in recent.get("data", []) if norm(m.get("caption")) == norm(caption)), None)


def render_and_check(spec_f):
    OUT.mkdir(parents=True, exist_ok=True)
    mp4 = OUT / f"{spec_f.stem}.mp4"
    cover = mp4.with_suffix(".cover.jpg")
    meta = make_reel(spec_f, mp4, cover_out=cover)
    ok, fails, warns, rep = validate(mp4, cover, meta, frames_dir=OUT / "frames")
    try:
        sheet(mp4, cover, mp4.with_suffix(".meta.json"))
    except Exception as e:
        print("preview failed:", e)
    print(("PASS " if ok else "FAIL ") + spec_f.name)
    for f in fails:
        print("  ✗", f)
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error title=reel {spec_f.stem}::{f[:900]}")
    for w in warns:
        print("  !", w)
    return ok, mp4, cover, fails


def publish(spec, mp4, cover):
    """Returns the published media dict, or raises ApiError."""
    dup = already_posted(spec["caption"])
    if dup:
        print("Already on the account, not posting again:", dup.get("permalink") or dup.get("id"))
        return dup
    if gh("release", "view", TAG, check=False).returncode != 0:
        gh("release", "create", TAG, "--title", "Temporary reel uploads",
           "--notes", "Videos here are deleted automatically right after publishing.", "--prerelease")
    gh("release", "upload", TAG, str(mp4), str(cover), "--clobber")
    base = f"https://github.com/{REPO}/releases/download/{TAG}"
    try:
        container = call("POST", f"{IG_ID}/media", media_type="REELS", video_url=f"{base}/{mp4.name}",
                         cover_url=f"{base}/{cover.name}", caption=spec["caption"], share_to_feed="true")["id"]
        for _ in range(90):                     # up to 15 minutes of processing
            st = call("GET", container, fields="status_code,status")
            if st.get("status_code") == "FINISHED":
                break
            if st.get("status_code") in ("ERROR", "EXPIRED"):
                raise ApiError(f"Meta could not process the video: {st}")
            time.sleep(10)
        else:
            raise ApiError("video processing timed out (15 min)")
        try:
            media_id = call("POST", f"{IG_ID}/media_publish", retries=1, creation_id=container)["id"]
        except ApiError as e:
            if not e.transient:
                raise
            time.sleep(20)                      # the publish may have gone through: look before retrying
            dup = already_posted(spec["caption"])
            if dup:
                media_id = dup["id"]
            else:
                media_id = call("POST", f"{IG_ID}/media_publish", retries=2, creation_id=container)["id"]
        media = call("GET", media_id, fields="id,media_type,media_product_type,permalink,timestamp")
        if media.get("media_product_type") != "REELS":
            raise ApiError(f"published item is not a reel: {media}")
        if os.environ.get("REEL_STORY") == "1":     # reels as stories: off for now (one story a day)
            media["story"] = share_as_story(f"{base}/{mp4.name}", mp4)   # video still hosted here
        return media
    finally:
        for f in (mp4, cover):                  # processing and verification are done at this point
            gh("release", "delete-asset", TAG, f.name, "--yes", check=False)
        print("Deleted temporary video and cover")


def share_as_story(video_url, mp4):
    """Also post the reel video as a story (stories allow 3-60 s). A failure never affects the reel."""
    try:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    str(mp4)], capture_output=True, text=True).stdout.strip() or 0)
        if not 3 <= dur <= 59.5:
            print(f"Reel is {dur:.1f}s, too long for a story; skipped"); return None
        story = ig_api.publish_story(video_url=video_url)
        print("Published reel as story:", story.get("id"))
        return story.get("id")
    except Exception as e:
        print("Story for this reel NOT published (reel is fine):", e)
        return None


def main():
    state_f = ROOT / "state.json"
    state = json.loads(state_f.read_text(encoding="utf-8"))
    tried = 0
    for spec_f in candidates(state):
        if tried == 3:
            break
        tried += 1
        spec = json.loads(spec_f.read_text(encoding="utf-8-sig"))
        print("Reel from", spec_f.name)
        try:
            ok, mp4, cover, fails = render_and_check(spec_f)
        except Exception as e:
            ok, fails = False, [f"render crashed: {type(e).__name__}: {e}"]
            print("  ✗", fails[0])
            if os.environ.get("GITHUB_ACTIONS"):
                print(f"::error title=reel {spec_f.stem}::{fails[0][:900]}")
        if not ok:
            if not DRY:
                state.setdefault("reel_rejected", {})[spec_f.stem] = "; ".join(fails)[:300]
                state_f.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            continue
        if DRY:
            print("Dry run: not uploaded, not published, state.json unchanged. Output:", OUT)
            return 0
        if not TOKEN or not REPO:
            sys.exit("IG_TOKEN and GITHUB_REPOSITORY are required for a real run")
        try:
            media = publish(spec, mp4, cover)
        except ApiError as e:
            print("NOT published:", e)
            return 1                            # state untouched: the spec stays available
        print("Published reel:", media.get("permalink") or media.get("id"))
        state.setdefault("reeled", []).append(spec_f.stem)
        if media.get("story"):
            ig_api.remember_story(state, f"r:{spec_f.stem}")
        state_f.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    print("No spec passed the quality gate." if tried else "No unused spec for a reel.")
    return 1 if tried else 0


if __name__ == "__main__":
    sys.exit(main())
