"""Publish the next queued post to Instagram via the official Instagram API (Instagram Login).

Env:
  IG_TOKEN     Instagram user access token (from GitHub secret)
  GITHUB_REPOSITORY, GITHUB_REF_NAME  (set by GitHub Actions) used to build public image URLs
"""
import json, os, sys, time, pathlib, urllib.parse, urllib.request

API = "https://graph.instagram.com/v26.0"
ROOT = pathlib.Path(__file__).resolve().parent.parent
TOKEN = os.environ["IG_TOKEN"].strip()
REPO = os.environ["GITHUB_REPOSITORY"]
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")


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
        sys.exit(f"API error {e.code} on {path}: {body}")


def main():
    queue = json.loads((ROOT / "queue.json").read_text(encoding="utf-8"))
    state_f = ROOT / "state.json"
    state = json.loads(state_f.read_text(encoding="utf-8"))
    done = set(state["published"])
    nxt = next((p for p in queue if p["image"] not in done), None)
    if not nxt:
        print("Queue is empty - nothing to publish.")
        return

    me = call("GET", "me", fields="user_id,username")
    ig_id = me["user_id"]
    print(f"Publishing as @{me['username']}: {nxt['image']}")

    image_url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{urllib.parse.quote(nxt['image'])}"
    container = call("POST", f"{ig_id}/media", image_url=image_url, caption=nxt["caption"])["id"]

    for _ in range(30):
        status = call("GET", container, fields="status_code")["status_code"]
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            sys.exit(f"Container failed: {status}")
        time.sleep(5)

    media = call("POST", f"{ig_id}/media_publish", creation_id=container)
    print("Published media id:", media["id"])

    state["published"].append(nxt["image"])
    state_f.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
