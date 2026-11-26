"""Collect Instagram performance numbers and write a report (no tokens are ever written or printed).

    python3 scripts/metrics.py OUT_DIR

Writes OUT_DIR/media.csv (latest numbers per post, reel and story), OUT_DIR/account.csv (one row per
day) and OUT_DIR/report.md (medians by format and topic, best and worst posts).
Works with the basic permission (likes, comments, followers). Reach, saves, shares and watch time need
the instagram_manage_insights permission on the token; until then those columns stay empty and the
report says so.
"""
import csv, datetime as dt, json, pathlib, re, statistics, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ig_api                                                             # noqa: E402

FEED = ["reach", "views", "saved", "shares", "likes", "comments", "total_interactions", "follows", "profile_visits"]
REEL = ["reach", "views", "saved", "shares", "likes", "comments", "total_interactions",
        "ig_reels_avg_watch_time", "ig_reels_video_view_total_time", "reels_skip_rate"]
STORY = ["reach", "views", "shares", "follows"]
COLS = ["media_id", "stem", "format", "posted_utc", "hour_baghdad", "topic", "likes", "comments", "reach", "views",
        "saved", "shares", "total_interactions", "follows", "profile_visits", "ig_reels_avg_watch_time",
        "ig_reels_video_view_total_time", "reels_skip_rate", "score", "updated_utc"]
GENERIC = {"#هل_تعلم", "#معلومات_عامة", "#معلومة_كل_يوم", "#معلومات", "#معلومة_اليوم", "#ثقافة_عامة"}


def insights(media_id, metrics):
    """Ask for all metrics at once; if one is unsupported, fall back to one by one."""
    out, perm = {}, True
    try:
        for row in ig_api.call("GET", f"{media_id}/insights", retries=2, metric=",".join(metrics)).get("data", []):
            out[row["name"]] = (row.get("values") or [{}])[0].get("value", row.get("total_value", {}).get("value"))
        return out, perm
    except ig_api.ApiError as e:
        if "permission" in str(e).lower() or '"code":10' in str(e) or '"code":200' in str(e):
            return {}, False
    for m in metrics:
        try:
            for row in ig_api.call("GET", f"{media_id}/insights", retries=1, metric=m).get("data", []):
                out[row["name"]] = (row.get("values") or [{}])[0].get("value", row.get("total_value", {}).get("value"))
        except ig_api.ApiError:
            pass
    return out, perm


def all_media(limit_days=45):
    items, after = [], None
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=limit_days)
    while True:
        params = {"fields": "id,caption,media_type,media_product_type,timestamp,like_count,comments_count", "limit": "50"}
        if after:
            params["after"] = after
        r = ig_api.call("GET", f"{ig_api.IG_ID}/media", **params)
        batch = r.get("data", [])
        items += batch
        after = (r.get("paging") or {}).get("cursors", {}).get("after")      # cursor only, never the next URL
        if not batch or not after or dt.datetime.fromisoformat(batch[-1]["timestamp"].replace("+0000", "+00:00")) < cutoff:
            return items


def stem_for(m, ids_to_stem, captions):
    if m["id"] in ids_to_stem:
        return ids_to_stem[m["id"]]
    cap = re.sub(r"\s+", " ", (m.get("caption") or "")).strip()
    return captions.get(cap, "")


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main(out_dir):
    out = pathlib.Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    state = json.loads((ROOT / "state.json").read_text(encoding="utf-8"))
    ids_to_stem = {v: k for k, v in (state.get("media_ids") or {}).items() if v}
    captions = {}
    for f in (ROOT / "specs").glob("*.json"):
        try:
            captions[re.sub(r"\s+", " ", json.loads(f.read_text(encoding="utf-8-sig"))["caption"]).strip()] = f.stem
        except Exception:
            pass
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")

    acct = ig_api.call("GET", ig_api.IG_ID, fields="followers_count,media_count")
    acc_f = out / "account.csv"
    rows = list(csv.DictReader(acc_f.open(encoding="utf-8"))) if acc_f.exists() else []
    today = now[:10]
    rows = [r for r in rows if r["date"] != today] + [{"date": today, "followers": acct.get("followers_count", ""),
                                                      "media_count": acct.get("media_count", "")}]
    with acc_f.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "followers", "media_count"]); w.writeheader(); w.writerows(rows)

    media_rows, have_insights = [], True
    for m in all_media():
        fmt = "REELS" if m.get("media_product_type") == "REELS" else ("STORY" if m.get("media_product_type") == "STORY" else "FEED")
        vals, perm = insights(m["id"], {"REELS": REEL, "STORY": STORY}.get(fmt, FEED))
        have_insights &= perm
        ts = dt.datetime.fromisoformat(m["timestamp"].replace("+0000", "+00:00"))
        tags = re.findall(r"#\S+", m.get("caption") or "")
        topic = next((t for t in tags if t not in GENERIC), "")
        row = {"media_id": m["id"], "stem": stem_for(m, ids_to_stem, captions), "format": fmt,
               "posted_utc": ts.isoformat(timespec="minutes"), "hour_baghdad": (ts.hour + 3) % 24, "topic": topic,
               "likes": vals.get("likes", m.get("like_count", "")), "comments": vals.get("comments", m.get("comments_count", "")),
               "updated_utc": now}
        for c in COLS:
            if c not in row:
                row[c] = vals.get(c, "")
        reach = num(row["reach"])
        if reach:
            row["score"] = round((3 * (num(row["shares"]) or 0) + 2 * (num(row["saved"]) or 0) + (num(row["comments"]) or 0)
                                  + 5 * (num(row["follows"]) or 0)) / reach * 1000, 1)
        media_rows.append(row)
    with (out / "media.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS); w.writeheader(); w.writerows(media_rows)
    (out / "report.md").write_text(report(media_rows, rows, have_insights, now), encoding="utf-8")
    print(f"metrics: {len(media_rows)} media, followers {acct.get('followers_count')}, insights {'on' if have_insights else 'OFF'}")


def med(rows, key):
    v = [num(r[key]) for r in rows if num(r[key]) is not None]
    return round(statistics.median(v), 2) if v else "—"


def report(media, acct, have_insights, now):
    lines = [f"# تقرير الأداء — {now[:10]}", ""]
    if len(acct) >= 2:
        lines.append(f"المتابعون: {acct[-1]['followers']} (قبل {len(acct) - 1} يوم: {acct[0]['followers']})")
    else:
        lines.append(f"المتابعون: {acct[-1]['followers'] if acct else '—'}")
    if not have_insights:
        lines += ["", "> الوصول والحفظ والإرسال ومدة المشاهدة غير متاحة بعد: الرمز يحتاج صلاحية `instagram_manage_insights`.",
                  "> حتى ذلك الحين يعتمد التقرير على الإعجابات والتعليقات فقط."]
    lines += ["", "## حسب الشكل (الوسيط)", "| الشكل | العدد | الوصول | المشاهدات | الحفظ | الإرسال | الإعجاب | مدة المشاهدة (مللي ث) | التخطي % |",
              "|---|---|---|---|---|---|---|---|---|"]
    for fmt in ("FEED", "REELS", "STORY"):
        rs = [r for r in media if r["format"] == fmt]
        if rs:
            lines.append(f"| {fmt} | {len(rs)} | {med(rs,'reach')} | {med(rs,'views')} | {med(rs,'saved')} | {med(rs,'shares')} | "
                         f"{med(rs,'likes')} | {med(rs,'ig_reels_avg_watch_time')} | {med(rs,'reels_skip_rate')} |")
    topics = {}
    for r in media:
        if r["topic"] and r["format"] != "STORY":
            topics.setdefault(r["topic"], []).append(r)
    if any(len(rs) >= 3 for rs in topics.values()):
        lines += ["", "## حسب الموضوع (الوسيط، 3 منشورات على الأقل)", "| الموضوع | العدد | الوصول | الحفظ | الإرسال | الإعجاب |", "|---|---|---|---|---|---|"]
        for t, rs in sorted(topics.items(), key=lambda kv: -len(kv[1])):
            if len(rs) >= 3:
                lines.append(f"| {t} | {len(rs)} | {med(rs,'reach')} | {med(rs,'saved')} | {med(rs,'shares')} | {med(rs,'likes')} |")
    key = "score" if have_insights else "likes"
    ranked = sorted([r for r in media if num(r[key]) is not None and r["format"] != "STORY"], key=lambda r: -num(r[key]))
    if ranked:
        lines += ["", f"## الأفضل والأضعف (حسب {'المؤشر المركّب لكل 1000 وصول' if key == 'score' else 'الإعجابات'})"]
        for label, part in (("الأفضل", ranked[:5]), ("الأضعف", ranked[-5:][::-1])):
            lines.append(f"**{label}:** " + "، ".join(f"{r['stem'] or r['media_id']} ({r[key]})" for r in part))
    lines += ["", "قاعدة القرار: لا تغيير قبل 8-10 منشورات في الخانة الواحدة، ونقارن الوسيط لا المتوسط."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "metrics_out")
