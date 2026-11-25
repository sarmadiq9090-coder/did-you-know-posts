"""Read-only check: does IG_TOKEN work? Writes a short result to check_result.txt (never the token)."""
import json, os, urllib.request, urllib.error, datetime
tok = os.environ.get("IG_TOKEN", "").strip()
ig = os.environ.get("IG_USER_ID", "17841462150090981")
out = [f"checked_at: {datetime.datetime.utcnow().isoformat()}Z"]
if not tok:
    out.append("result: NO_SECRET (IG_TOKEN is empty or missing)")
else:
    out.append(f"token_prefix: {tok[:4]}... length {len(tok)}")
    for host in ("https://graph.instagram.com/v26.0", "https://graph.facebook.com/v26.0"):
        url = f"{host}/{ig}?fields=username&access_token={tok}"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                out.append(f"{host}: OK {json.load(r)}")
        except urllib.error.HTTPError as e:
            body = json.loads(e.read() or b"{}").get("error", {})
            out.append(f"{host}: ERROR {e.code} {body.get('type')} {body.get('message')}")
open("check_result.txt", "w").write("\n".join(out) + "\n")
print("\n".join(out))
