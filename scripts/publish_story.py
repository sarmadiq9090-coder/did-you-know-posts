"""Publish one Instagram Story image.

    python3 scripts/publish_story.py question [--dry-run]   # morning teaser for the next image post
    python3 scripts/publish_story.py post [--dry-run]       # the image post that was just published

Each story is recorded in state.json["stories"] so it is never posted twice. The image is hosted on
the temporary "reels-tmp" release only while Instagram fetches it, then deleted.
Reel stories are published by scripts/publish_reel.py right after each reel.
Output images stay in $STORY_OUT (default /tmp/story_out) for the workflow artifact.
"""
import json, os, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT / "scripts"))
from make_story import question_story, post_story, validate_story   # noqa: E402
import ig_api                                                       # noqa: E402

DRY = "--dry-run" in sys.argv
OUT = pathlib.Path(os.environ.get("STORY_OUT", "/tmp/story_out"))
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]+")


def queue_items():
    """Same order as scripts/publish.py: queue.json first, then posts/*.jpg by number."""
    queue = json.loads((ROOT / "queue.json").read_text(encoding="utf-8"))
    listed = {p["image"] for p in queue}

    def order(f):
        m = re.match(r"(\d+)_", f.name)
        return (int(m.group(1)) if m else 10**9, f.name)
    for img in sorted((ROOT / "posts").glob("*"), key=order):
        if img.suffix.lower() in (".jpg", ".jpeg", ".png") and f"posts/{img.name}" not in listed:
            cap_f = img.with_suffix(".txt")
            queue.append({"image": f"posts/{img.name}",
                          "caption": cap_f.read_text(encoding="utf-8").strip() if cap_f.exists() else ""})
    return queue


def title_for(item):
    stem = pathlib.Path(item["image"]).stem
    spec = ROOT / "specs" / f"{stem}.json"
    if spec.exists():
        t = json.loads(spec.read_text(encoding="utf-8-sig")).get("title")
        if t:
            return t
    first = EMOJI.split(item.get("caption", "").strip().split("\n")[0])[0]
    first = re.split(r"(?<=[.!؟?])\s", first)[0].strip(" .،")
    words = first.split()
    return (" ".join(words[:10]) + "…") if len(words) > 11 else (first or "معلومة ستدهشك")


def main():
    kind = next((a for a in sys.argv[1:] if not a.startswith("--")), "post")
    state_f = ROOT / "state.json"
    state = json.loads(state_f.read_text(encoding="utf-8"))
    done = set(state.get("stories", []))
    OUT.mkdir(parents=True, exist_ok=True)

    if kind == "question":
        published, reeled = set(state["published"]), set(state.get("reeled", []))
        item = next((p for p in queue_items() if p["image"] not in published
                     and pathlib.Path(p["image"]).stem not in reeled), None)
        if not item:
            print("Queue empty: no question story."); return 0
        key = f"q:{pathlib.Path(item['image']).stem}"
        out = OUT / f"story_question_{pathlib.Path(item['image']).stem}.jpg"
        question_story(ROOT / item["image"], title_for(item), out)
    else:
        if not state["published"]:
            print("Nothing published yet."); return 0
        image = state["published"][-1]
        key = f"p:{pathlib.Path(image).stem}"
        out = OUT / f"story_post_{pathlib.Path(image).stem}.jpg"
        post_story(ROOT / image, out)

    if key in done and not DRY:
        print("Story already published for", key); return 0
    fails = validate_story(out)
    print(("PASS " if not fails else "FAIL ") + out.name, "; ".join(fails))
    if fails:
        return 1
    if DRY:
        print("Dry run: story not published, state.json unchanged."); return 0

    (url,) = ig_api.host(out)
    try:
        media = ig_api.publish_story(image_url=url)
    except ig_api.ApiError as e:
        print("Story NOT published:", e); return 1
    finally:
        ig_api.unhost(out)
    print("Published story", media.get("id"), key)
    ig_api.remember_story(state, key)
    state_f.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
