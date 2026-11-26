"""Normalise captions of everything still in the queue (safe to run again):

    opener + question            (kept as written)
    📩 send line                  (added if missing; rotates so posts do not look identical)
    #هل_تعلم #معلومات_عامة #<topic> #<topic> #معلومة_كل_يوم   (exactly 5 hashtags)

Published posts and reels are never touched. Updates specs/*.json, posts/*.txt and queue.json.
Usage: python3 tools/fix_captions.py
"""
import hashlib, json, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
GENERIC = {"#هل_تعلم", "#معلومات", "#معلومة_اليوم", "#ثقافة_عامة", "#معلومات_عامة", "#معلومة_كل_يوم",
           "#حقائق_علمية", "#حقائق", "#معلومات_مفيدة"}
SEND = ["📩 أرسلها لصديق يحب المعلومات الغريبة", "📩 أرسلها لشخص يجب أن يعرفها",
        "📩 شاركها مع صديق لن يصدّقها", "📩 أرسلها لأول شخص خطر ببالك",
        "📩 شاركها مع من يحب هذا النوع من المعلومات", "📩 أرسلها لصديقك الذي يعرف كل شيء"]


def fix(caption, key):
    head, _, tags = caption.strip().partition("\n\n")
    tl = re.findall(r"#\S+", tags) or re.findall(r"#\S+", head)
    head = re.sub(r"\s*#\S+", "", head).strip() if not tags else head.strip()
    lines = [l for l in head.split("\n") if l.strip() and l.strip() not in SEND]
    if not any(w in l for l in lines for w in ("أرسل", "📩", "شارك", "تاك", "منشن")):
        lines.append(SEND[int(hashlib.md5(key.encode()).hexdigest(), 16) % len(SEND)])
    topic = []
    for t in tl:
        t = t.rstrip("،,.")
        if t not in GENERIC and t not in topic:
            topic.append(t)
    while len(topic) < 2:
        topic.append("#ثقافة_عامة" if "#ثقافة_عامة" not in topic else "#معلومات")
    return "\n".join(lines) + "\n\n" + " ".join(["#هل_تعلم", "#معلومات_عامة", topic[0], topic[1], "#معلومة_كل_يوم"])


def main():
    state = json.loads((ROOT / "state.json").read_text(encoding="utf-8"))
    published = {pathlib.Path(p).stem for p in state.get("published", [])}
    reeled = set(state.get("reeled", []))
    n = 0
    for f in sorted((ROOT / "specs").glob("*.json")):
        if f.stem in published or f.stem in reeled:
            continue
        d = json.loads(f.read_text(encoding="utf-8-sig"))
        new = fix(d["caption"], f.stem)
        if new != d["caption"]:
            d["caption"] = new
            f.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); n += 1
        txt = ROOT / "posts" / f"{f.stem}.txt"
        if txt.exists():
            txt.write_text(new + "\n", encoding="utf-8")
    q = json.loads((ROOT / "queue.json").read_text(encoding="utf-8"))
    for item in q:
        if item["image"] not in state.get("published", []):
            item["caption"] = fix(item["caption"], pathlib.Path(item["image"]).stem); n += 1
    (ROOT / "queue.json").write_text(json.dumps(q, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("captions updated:", n)


if __name__ == "__main__":
    main()
