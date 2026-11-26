#!/usr/bin/env python3
"""
hookscore_ar.py - score Arabic on-screen hooks (spec "title" / "question").

Adapted from the MIT-licensed hookscore.py in Jakeschincariol/instagram-agent-skill
(the original only reads Latin script). Same idea: cheap text heuristics that catch
weak hooks - too long, nothing concrete, nothing at stake, payload at the back,
or a wasted opener. It does NOT predict views; it flags hooks to rewrite.

Checks (0-100 each, total = mean):
  length       4-9 words and <= 42 chars fits the reel title box in ~2 seconds
  concrete     a number, a quantity word, a named place/thing in the hook
  stakes       a curiosity/stake word (لماذا، فقط، أكثر، الوحيد، لا، خطأ ...)
  front        the concrete/stake marker sits in the first half of the line
  opener       no wasted opener (هل تعلم، تعرف على، اكتشف، معلومة ...)

Usage:
  python3 tools/hookscore_ar.py --hook "خلايا دمك تعيش 120 يوماً فقط"
  python3 tools/hookscore_ar.py specs/*.json            # rank spec titles, worst first
  python3 tools/hookscore_ar.py specs/*.json --json
"""
import argparse, json, pathlib, re, sys

AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
WORD_RE = re.compile(r"[\w؀-ۿ%]+")
NUM_RE = re.compile(r"\d[\d.,]*")
NUMBER_WORDS = {"واحد", "اثنان", "اثنين", "ثلاث", "ثلاثة", "أربع", "أربعة", "خمس", "خمسة", "ست", "ستة",
                "سبع", "سبعة", "ثماني", "ثمانية", "تسع", "تسعة", "عشر", "عشرة", "عشرين", "عشرون", "مئة",
                "مائة", "مئات", "ألف", "آلاف", "مليون", "ملايين", "مليار", "نصف", "ربع", "ثلث", "ضعف",
                "أضعاف", "مرتين", "مرة", "دقيقة", "ثانية", "ساعة", "يوم", "يوماً", "أيام", "سنة", "عام",
                "قرن", "قرون", "درجة", "متر", "كيلو", "كيلومتر", "بالمئة", "٪"}
STAKES = {"لماذا", "كيف", "لا", "لم", "لن", "ليس", "أبداً", "أبدا", "فقط", "وحده", "الوحيد", "الوحيدة",
          "أكثر", "أقل", "أسرع", "أبطأ", "أكبر", "أصغر", "أقدم", "أطول", "أخطر", "أغرب", "أول", "آخر",
          "سر", "أسرار", "خطأ", "يخدعك", "يخفي", "مخفي", "يموت", "يعيش", "بدون", "دون", "رغم", "حتى",
          "يتوقف", "يكذب", "مستحيل", "أخطأت", "خطير", "يقتل", "ينقذ", "يتغير", "يكبر", "يصغر", "أين",
          "متى", "ماذا", "من", "هذا", "هذه", "أنت", "دمك", "جسمك", "عينك", "قلبك", "دماغك"}
WEAK_OPENERS = ["هل تعلم", "تعرف على", "اكتشف", "معلومة", "إليك", "شاهد", "تابع", "هيا", "مرحبا",
                "في هذا", "اليوم", "دعنا", "سنتعرف"]
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")


def words(t):
    return WORD_RE.findall(t.translate(AR_DIGITS))


def bare(w):
    w = re.sub(r"^(و|ف|ب|ل|ال|وال|بال|لل|فال)(?=\w{3,})", "", w)
    return w


def score(hook):
    t = (hook or "").strip()
    w = words(t)
    n, chars = len(w), len(t)
    out = {}
    # length
    s = 100 if 4 <= n <= 9 else (100 - (4 - n) * 25 if n < 4 else 100 - (n - 9) * 12)
    if chars > 42:
        s -= (chars - 42) * 2
    out["length"] = (max(0, min(100, s)), f"{n} كلمات، {chars} حرفاً")
    # concrete
    marks = [i for i, x in enumerate(w) if NUM_RE.fullmatch(x.rstrip("%")) or x in NUMBER_WORDS or bare(x) in NUMBER_WORDS]
    out["concrete"] = (15 if not marks else min(100, 55 + 25 * len(marks)), f"{len(marks)} علامة محددة")
    # stakes
    st = [i for i, x in enumerate(w) if x in STAKES or bare(x) in STAKES or t.endswith("؟")]
    out["stakes"] = (20 if not st else min(100, 60 + 20 * len(set(st))), f"{len(set(st))} كلمة رهان/فضول")
    # front-loaded
    first = min(marks + st) if (marks or st) else None
    out["front"] = (30 if first is None else (100 if first < max(1, n / 2) else 55), "الحمولة في النصف الأول" if first is not None and first < n / 2 else "الحمولة متأخرة أو غائبة")
    # opener
    bad = next((o for o in WEAK_OPENERS if t.startswith(o)), None)
    pen = 0 if not bad else 70
    if EMOJI_RE.search(t):
        pen += 20
    out["opener"] = (100 - pen, f"افتتاحية ضعيفة: {bad}" if bad else "افتتاحية سليمة")
    total = round(sum(v[0] for v in out.values()) / len(out))
    return total, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="*")
    ap.add_argument("--hook")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = []
    if a.hook:
        rows.append(("--hook", a.hook))
    for p in a.specs:
        d = json.loads(pathlib.Path(p).read_text(encoding="utf-8-sig"))
        h = (d.get("question") or d.get("title") or "").replace("خمّن:", "").strip()
        rows.append((pathlib.Path(p).stem, h))
    res = [(stem, h, *score(h)) for stem, h in rows]
    res.sort(key=lambda r: r[2])
    if a.json:
        print(json.dumps([{"stem": s, "hook": h, "score": t, "checks": {k: v[0] for k, v in c.items()}} for s, h, t, c in res],
                         ensure_ascii=False, indent=1))
        return
    for s, h, t, c in res:
        print(f"{t:3d}  {s:28s} {h}")
        if len(res) == 1:
            for k, v in c.items():
                print(f"     {k:9s} {v[0]:3.0f}  {v[1]}")


if __name__ == "__main__":
    sys.exit(main())
