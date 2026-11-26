"""Publish the next queued post to Instagram via the official Instagram API (Instagram Login).

Env:
  IG_TOKEN     Instagram user access token (from GitHub secret)
  GITHUB_REPOSITORY, GITHUB_REF_NAME  (set by GitHub Actions) used to build public image URLs
"""
import json, os, re, sys, time, pathlib, urllib.parse, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOKEN = os.environ["IG_TOKEN"].strip()
IG_ID = os.environ.get("IG_USER_ID", "17841462150090981")  # did.you.know.iq
# Instagram-login tokens start with "IG"; Unified/Facebook-login tokens only work on graph.facebook.com
API = ("https://graph.instagram.com/v26.0" if TOKEN.startswith("IG")
       else "https://graph.facebook.com/v26.0")
REPO = os.environ["GITHUB_REPOSITORY"]
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
sys.path.insert(0, str(ROOT / "scripts"))
import ig_api  # noqa: E402  (pause / quota helpers)


class PublishError(Exception):
    pass


def call(method, path, **params):
    params["access_token"] = TOKEN
    data = urllib.parse.urlencode(params).encode()
    url = f"{API}/{path}"
    if method == "GET":
        req = urllib.request.Request(f"{url}?{data.decode()}")
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise PublishError(f"API error {e.code} on {path}: {body[:600]}")


def main():
    queue = json.loads((ROOT / "queue.json").read_text(encoding="utf-8"))
    # Also pick up any image dropped into posts/ without editing queue.json.
    # Caption comes from a .txt file with the same name (posts/10_x.jpg -> posts/10_x.txt).
    listed = {p["image"] for p in queue}
    def order(f):
        m = re.match(r"(\d+)_", f.name)
        return (int(m.group(1)) if m else 10**9, f.name)
    for img in sorted((ROOT / "posts").glob("*"), key=order):
        if img.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        rel = f"posts/{img.name}"
        if rel in listed:
            continue
        cap_f = img.with_suffix(".txt")
        cap = cap_f.read_text(encoding="utf-8").strip() if cap_f.exists() else ""
        queue.append({"image": rel, "caption": cap})
    state_f = ROOT / "state.json"
    state = json.loads(state_f.read_text(encoding="utf-8"))
    if ig_api.is_paused(state):
        return
    if not ig_api.quota_ok():
        print("Near the 24-hour publishing limit; skipping this slot.")
        return
    done = set(state["published"])
    reeled = set(state.get("reeled", []))   # facts already published as a reel are skipped
    nxt = next((p for p in queue if p["image"] not in done and pathlib.Path(p["image"]).stem not in reeled), None)
    if not nxt:
        print("Queue is empty - nothing to publish.")
        return

    ig_id = IG_ID
    print(f"Publishing via {API} to {ig_id}: {nxt['image']}")

    image_url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{urllib.parse.quote(nxt['image'])}"
    try:
        container = call("POST", f"{ig_id}/media", image_url=image_url, caption=nxt["caption"])["id"]
        for _ in range(30):
            status = call("GET", container, fields="status_code")["status_code"]
            if status == "FINISHED":
                break
            if status in ("ERROR", "EXPIRED"):
                raise PublishError(f"Container failed: {status}")
            time.sleep(5)
        media = call("POST", f"{ig_id}/media_publish", creation_id=container)
    except PublishError as e:
        hours = ig_api.classify_error(e)
        if hours:
            ig_api.pause(state, hours, e)
            state_f.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(str(e))
    print("Published media id:", media["id"])

    state["published"].append(nxt["image"])
    state.setdefault("media_ids", {})[pathlib.Path(nxt["image"]).stem] = media["id"]
    state_f.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
