"""Render, validate and preview every case in tests/reel_cases/ (nothing is published).

Usage: python3 tools/test_reels.py [--local DIR] [--out reel_test_out] [case ...]
  --local DIR   use local images from DIR instead of downloading the Pexels photos (offline runs);
                the images are assigned by case so the 6_mixed_ratios case gets panorama/tall/landscape.
Writes <out>/<case>.mp4, .cover.jpg, .meta.json, .report.json, .preview.png and <out>/summary.md.
Exit code 1 if any case fails validation.
"""
import json, os, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from make_reel import make_reel          # noqa: E402
from validate_reel import validate        # noqa: E402
from reel_previews import sheet           # noqa: E402

LOCAL = {"1_one_photo": ["landscape"], "2_two_photos": ["portrait", "square"],
         "3_three_photos": ["landscape", "portrait", "square"], "4_long_text": ["portrait", "landscape"],
         "5_short_text": ["square"], "6_mixed_ratios": ["panorama", "tall", "landscape"], "7_no_photos": [],
         "8_question_hook": ["portrait", "square"]}


def main(argv):
    local, out, only = None, ROOT / "reel_test_out", []
    it = iter(argv)
    for a in it:
        if a == "--local": local = pathlib.Path(next(it))
        elif a == "--out": out = pathlib.Path(next(it))
        else: only.append(a)
    out.mkdir(parents=True, exist_ok=True)
    rows, failed = [], 0
    for spec in sorted((ROOT / "tests" / "reel_cases").glob("*.json")):
        if only and spec.stem not in only:
            continue
        photos = None
        if local is not None:
            photos = [str(next(local.glob(n + ".*"))) for n in LOCAL.get(spec.stem, [])]
        mp4 = out / f"{spec.stem}.mp4"
        t0 = time.time()
        try:
            meta = make_reel(spec, mp4, local_photos=photos)
            if local is not None and not photos:
                meta["photos_expected"] = 0
            ok, fails, warns, rep = validate(mp4, mp4.with_suffix(".cover.jpg"), meta)
            sheet(mp4, mp4.with_suffix(".cover.jpg"), mp4.with_suffix(".meta.json"))
        except Exception as e:  # a crash is a failed case, keep testing the rest
            import traceback; traceback.print_exc()
            ok, fails, warns, rep, meta = False, [f"crash: {type(e).__name__}: {e}"], [], {}, {}
        failed += not ok
        v = rep.get("video", {})
        rows.append(f"| {spec.stem} | {'PASS' if ok else 'FAIL'} | {rep.get('duration', 0):.1f}s | "
                    f"{v.get('width')}x{v.get('height')} SAR {v.get('sample_aspect_ratio')} DAR {v.get('display_aspect_ratio')} | "
                    f"{len([s for s in meta.get('scenes', []) if s['name'].startswith('s')])} | "
                    f"{meta.get('photos_loaded', 0)}/{meta.get('photos_expected', 0)} | "
                    f"{', '.join(meta.get('motions', []))} | {time.time() - t0:.0f}s | "
                    f"{'; '.join(fails + warns) or '—'} |")
        print(rows[-1])
        if os.environ.get("GITHUB_ACTIONS"):          # annotations are visible on the run page without signing in
            for msg in fails + warns:
                level = "error" if msg in fails else "warning"
                print(f"::{level} title={spec.stem}::{msg[:900]}")
            if ok:
                print(f"::notice title={spec.stem}::PASS {rep.get('duration', 0):.1f}s, photos "
                      f"{meta.get('photos_loaded', 0)}/{meta.get('photos_expected', 0)}, scenes "
                      f"{len([s for s in meta.get('scenes', []) if s['name'].startswith('s')])}")
    md = ["| case | result | length | video | text scenes | photos | camera | render | notes |",
          "|---|---|---|---|---|---|---|---|---|"] + rows
    (out / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n{len(rows) - failed}/{len(rows)} passed — previews in {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
