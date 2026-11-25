"""Extend the long-lived Instagram token (valid 60 days) and print only its new lifetime.

The refreshed token is written to 'new_token.txt' (never printed),
so the workflow can store it back into the IG_TOKEN secret when a GH_PAT secret exists.
"""
import json, os, sys, urllib.parse, urllib.request

token = os.environ["IG_TOKEN"].strip()
url = "https://graph.instagram.com/refresh_access_token?" + urllib.parse.urlencode(
    {"grant_type": "ig_refresh_token", "access_token": token})
try:
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.load(r)
except urllib.error.HTTPError as e:
    sys.exit(f"Refresh failed {e.code}: {e.read().decode(errors='replace')}")

with open("new_token.txt", "w") as f:
    f.write(data["access_token"])
print(f"Token refreshed, valid for {int(data.get('expires_in', 0)) // 86400} more days")
