"""Shared Instagram Graph API + temporary-hosting helpers for reels and stories.

Env: IG_TOKEN, IG_USER_ID (optional), GITHUB_REPOSITORY, GH_TOKEN (for the gh CLI)
"""
import json, os, re, subprocess, time, urllib.error, urllib.parse, urllib.request

TOKEN = os.environ.get("IG_TOKEN", "").strip()
IG_ID = os.environ.get("IG_USER_ID", "17841462150090981")
API = "https://graph.instagram.com/v26.0" if TOKEN.startswith("IG") else "https://graph.facebook.com/v26.0"
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TAG = "reels-tmp"                       # public pre-release used as short-lived hosting
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


def host(*files):
    """Upload files to the temporary release; returns their public URLs."""
    if gh("release", "view", TAG, check=False).returncode != 0:
        gh("release", "create", TAG, "--title", "Temporary reel uploads",
           "--notes", "Videos here are deleted automatically right after publishing.", "--prerelease")
    gh("release", "upload", TAG, *[str(f) for f in files], "--clobber")
    return [f"https://github.com/{REPO}/releases/download/{TAG}/{f.name}" for f in files]


def unhost(*files):
    for f in files:
        gh("release", "delete-asset", TAG, f.name, "--yes", check=False)


def wait_finished(container, tries=90, pause=10):
    for _ in range(tries):
        st = call("GET", container, fields="status_code,status")
        if st.get("status_code") == "FINISHED":
            return
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise ApiError(f"Meta could not process the media: {st}")
        time.sleep(pause)
    raise ApiError("media processing timed out")


def publish_story(image_url=None, video_url=None):
    """Publish one story (image or video). Returns the media dict after checking it is a STORY."""
    params = {"media_type": "STORIES"}
    if image_url:
        params["image_url"] = image_url
    else:
        params["video_url"] = video_url
    container = call("POST", f"{IG_ID}/media", **params)["id"]
    wait_finished(container, tries=60 if video_url else 30, pause=10 if video_url else 5)
    media_id = call("POST", f"{IG_ID}/media_publish", retries=1, creation_id=container)["id"]
    media = call("GET", media_id, fields="id,media_type,media_product_type,timestamp")
    if media.get("media_product_type") != "STORY":
        raise ApiError(f"published item is not a story: {media}")
    return media


def remember_story(state, key, keep=150):
    s = state.setdefault("stories", [])
    s.append(key)
    del s[:-keep]


# ---------------------------------------------------------------- account safety
import datetime as _dt


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def is_paused(state):
    """Publishing is paused after a block/limit error until state['paused_until'] (UTC ISO)."""
    until = state.get("paused_until")
    if until and _dt.datetime.fromisoformat(until) > _now():
        print(f"Publishing paused until {until} ({state.get('pause_reason', '')}). Skipping.")
        return True
    return False


def classify_error(msg):
    """Return hours to pause for errors that mean 'stop for a while', else None."""
    m = str(msg).lower()
    if "2207051" in m or '"code":368' in m or "code 368" in m or "blocked" in m or "spam" in m:
        return 48
    if "2207042" in m or "rate limited" in m or "maximum number of posts" in m:
        return 24
    return None


def pause(state, hours, reason):
    state["paused_until"] = (_now() + _dt.timedelta(hours=hours)).isoformat(timespec="minutes")
    state["pause_reason"] = str(reason)[:200]
    print(f"::warning::Publishing paused for {hours} h: {str(reason)[:200]}")


def quota_ok(margin=3):
    """Check the 24-hour content publishing limit before creating a container."""
    try:
        d = call("GET", f"{IG_ID}/content_publishing_limit", fields="quota_usage,config")["data"][0]
        used, total = d.get("quota_usage", 0), (d.get("config") or {}).get("quota_total", 50)
        print(f"Publishing quota: {used}/{total} in the last 24 h")
        return used < total - margin
    except Exception as e:                       # never block publishing because the check itself failed
        print("Quota check unavailable:", str(e)[:200])
        return True
