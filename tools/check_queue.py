"""Quality checks over the content queue; prints GitHub annotations, never fails the workflow.

Checks every spec not yet published: required fields, text length, bold count, number present,
5 hashtags in the house order, title/question, photo count, and photos reused from another spec.
"""
import json, os, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
GH = bool(os.environ.get("GITHUB_ACTIONS"))


def say(level, title, msg):
    print(f"::{level} title={title}::{msg}" if GH else f"[{level}] {title}: {msg}")


def main():
    state = json.loads((ROOT / "state.json").read_text(encoding="utf-8"))
    done = {pathlib.Path(p).stem for p in state.get("published", [])} | set(state.get("reeled", []))
    specs = sorted((ROOT / "specs").glob("*.json"), key=lambda p: (int(re.match(r"(\d+)", p.name).group(1))
                                                                  if re.match(r"\d+", p.name) else 10**9, p.name))
    first_use, problems = {}, 0
    for f in specs:
        try:
            d = json.loads(f.read_text(encoding="utf-8-sig"))
        except Exception as e:
            say("error", f.name, f"invalid JSON: {e}"); problems += 1; continue
        if not isinstance(d, dict):
            continue                                    # batch file, split by render_specs
        for p in d.get("photos") or []:
            if p in first_use and f.stem not in done:
                say("warning", f.stem, f"photo {p} already used by {first_use[p]}"); problems += 1
            first_use.setdefault(p, f.stem)
        if f.stem in done:
            continue
        issues = []
        t = re.sub(r"</?b>", "", d.get("text", ""))
        w = len(t.split())
        if not t.startswith("أن"): issues.append("text does not start with «أن»")
        if not 30 <= w <= 60: issues.append(f"text has {w} words")
        if not re.search(r"[0-9٠-٩]", t): issues.append("no number in text")
        tags = re.findall(r"#\S+", d.get("caption", "").partition("\n\n")[2])
        if len(tags) != 5 or tags[:1] != ["#هل_تعلم"]: issues.append(f"{len(tags)} hashtags / wrong order")
        if not d.get("title"): issues.append("no title")
        if not d.get("photos"): issues.append("no photos")
        for i in issues:
            say("warning", f.stem, i)
        problems += len(issues)
    print(f"queue check: {problems} issue(s)")


if __name__ == "__main__":
    main()
