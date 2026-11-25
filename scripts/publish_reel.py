"""Make and publish one Reel from a not-yet-published spec, then delete the video.

The video is never committed to git: it is uploaded as a temporary GitHub Release asset
(public URL Instagram can fetch), published, and the asset is deleted right after.
The spec used for a reel is recorded in state.json["reeled"], so it is not also posted as an image.

Env: IG_TOKEN, IG_USER_ID (optional), GITHUB_REPOSITORY, GH_TOKEN (for the gh CLI)
"""
import json, os, re, subprocess, sys, time, pathlib, urllib.parse, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from make_reel import make_reel  # noqa: E402

TOKEN = os.environ["IG_TOKEN"].strip()
IG_ID = os.environ.get("IG_USER_ID", "17841462150090981")
API = ("https://graph.instagram.com/v26.0" if TOKEN.startswith("IG") else "https://graph.facebook.com/v26.0")
REPO = os.environ["GITHUB_REPOSITORY"]
TAG = "reels-tmp"


def call(method, path, **params):
    params["access_token"] = TOKEN
    data = urllib.parse.urlencode(params).encode()
    url = f"{API}/{path}"
    req = urllib.request.Request(f"{url}?{data.decode()}") if method == "GET" else urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"API error {e.code} on {path}: {e.read().decode(errors='replace')}")


def gh(*args, check=True):
    return subprocess.run(["gh", *args, "--repo", REPO], capture_output=True, text=True, check=check)


def num(p):
    m = re.match(r"(\d+)_", p.name)
    return int(m.group(1)) if m else -1


def pick_spec(state):
    published = {pathlib.Path(p).stem for p in state.get("published", [])}
    reeled = set(state.get("reeled", []))
    specs = [s for s in (ROOT / "specs").glob("*.json")
             if s.stem not in published and s.stem not in reeled and num(s) >= 0]
    # newest first, so the image queue's near-term order is not disturbed; prefer specs with photos
    specs.sort(key=num, reverse=True)
    with_photos = [s for s in specs if json.loads(s.read_text(encoding="utf-8-sig")).get("photos")]
    return (with_photos or specs or [None])[0]


def main():
    state_f = ROOT / "state.json"
    state = json.loads(state_f.read_text(encoding="utf-8"))
    spec_f = pick_spec(state)
    if not spec_f:
        print("No unused spec for a reel.")
        return
    spec = json.loads(spec_f.read_text(encoding="utf-8-sig"))
    print("Reel from", spec_f.name)
    mp4 = pathlib.Path(f"/tmp/{spec_f.stem}.mp4")
    make_reel(spec_f, mp4)

    if gh("release", "view", TAG, check=False).returncode != 0:
        gh("release", "create", TAG, "--title", "Temporary reel uploads",
           "--notes", "Videos here are deleted automatically right after publishing.", "--prerelease")
    gh("release", "upload", TAG, str(mp4), "--clobber")
    video_url = f"https://github.com/{REPO}/releases/download/{TAG}/{mp4.name}"
    try:
        container = call("POST", f"{IG_ID}/media", media_type="REELS", video_url=video_url,
                         caption=spec["caption"], share_to_feed="true")["id"]
        for _ in range(60):
            status = call("GET", container, fields="status_code,status")
            if status.get("status_code") == "FINISHED":
                break
            if status.get("status_code") in ("ERROR", "EXPIRED"):
                sys.exit(f"Reel processing failed: {status}")
            time.sleep(10)
        else:
            sys.exit("Reel processing timed out")
        media = call("POST", f"{IG_ID}/media_publish", creation_id=container)
        print("Published reel id:", media["id"])
    finally:
        gh("release", "delete-asset", TAG, mp4.name, "--yes", check=False)
        print("Deleted temporary video")

    state.setdefault("reeled", []).append(spec_f.stem)
    state_f.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
