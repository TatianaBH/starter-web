#!/usr/bin/env python3
import os, sys, base64, calendar, requests
from collections import defaultdict
from datetime import datetime

# Load credentials from a local .env file (gitignored) if present, so the
# token never has to be pasted on the command line. Existing environment
# variables take precedence over .env values.
def _load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)

_load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

BASE = "https://thecruiseglobe.atlassian.net"

# Month from CLI arg "YYYY-MM" (defaults to current month)
arg = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m")
try:
    year, month = map(int, arg.split("-"))
    START = f"{year:04d}-{month:02d}-01"
    END   = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
except Exception:
    sys.exit('Usage: python3 worklog_report.py [YYYY-MM]   e.g. python3 worklog_report.py 2026-07')

print(f"# Worklog report — {datetime.strptime(START, '%Y-%m-%d').strftime('%B %Y')}\n")

auth = base64.b64encode(f'{os.environ["JIRA_EMAIL"]}:{os.environ["JIRA_API_TOKEN"]}'.encode()).decode()
H = {"Authorization": f"Basic {auth}", "Accept": "application/json"}

# 1) issues that have worklogs in the range (paginated)
jql = f'project = CM AND worklogDate >= "{START}" AND worklogDate <= "{END}"'
keys, token = [], None
while True:
    p = {"jql": jql, "fields": "summary", "maxResults": 100}
    if token: p["nextPageToken"] = token
    d = requests.get(f"{BASE}/rest/api/3/search/jql", headers=H, params=p).json()
    keys += [(i["key"], i["fields"]["summary"]) for i in d.get("issues", [])]
    token = d.get("nextPageToken")
    if not token: break

def comment(c):
    if not c: return ""
    return " ".join(t["text"] for b in c.get("content", [])
                    for t in b.get("content", []) if t.get("type") == "text")

# 2) ALL worklogs per issue (paginated), filtered to range
agg = defaultdict(lambda: defaultdict(list))
for key, summ in keys:
    start = 0
    while True:
        d = requests.get(f"{BASE}/rest/api/3/issue/{key}/worklog",
                         headers=H, params={"startAt": start, "maxResults": 100}).json()
        for w in d.get("worklogs", []):
            day = w["started"][:10]
            if START <= day <= END:
                agg[w["author"]["displayName"]][day].append(
                    (key, summ, w["timeSpentSeconds"], comment(w.get("comment"))))
        start += d.get("maxResults", 100)
        if start >= d.get("total", 0): break

# 3) format (hours/minutes only; no empty-description flags; only days with work)
def fmt(s):
    h, m = s // 3600, (s % 3600) // 60
    return f"{h}h {m}m" if h and m else (f"{h}h" if h else f"{m}m")

totals = {}
for person in sorted(agg):
    print(f"## {person}\n")
    g = 0
    for day in sorted(agg[person]):
        items = agg[person][day]
        dt = sum(i[2] for i in items); g += dt
        nice = datetime.strptime(day, "%Y-%m-%d").strftime("%-d %B %Y")
        print(f"**{nice} — daily total: {fmt(dt)}**")
        for key, summ, secs, cm in items:
            print(f"- {key} ({summ}) — {fmt(secs)}" + (f' — "{cm}"' if cm else ""))
        print()
    totals[person] = g
    print(f"**Grand total — {person}: {fmt(g)}**\n")

print("### Summary")
for p, s in sorted(totals.items(), key=lambda x: -x[1]):
    print(f"- {p}: {fmt(s)}")
print(f"\n**Combined total: {fmt(sum(totals.values()))}**")
