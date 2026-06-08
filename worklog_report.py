#!/usr/bin/env python3
import os, sys, base64, calendar, time, requests
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

def comment(c):
    if not c: return ""
    return " ".join(t["text"] for b in c.get("content", [])
                    for t in b.get("content", []) if t.get("type") == "text")

# --- Real-time worklog collection (no search-index lag) -------------------
# Instead of asking the (laggy) search index "which issues have worklogs?",
# we read worklogs straight from Jira's real-time worklog endpoints. This
# guarantees freshly-added and backdated entries show up immediately.

def epoch_ms(date_str):
    return int(time.mktime(time.strptime(date_str, "%Y-%m-%d")) * 1000)

# 1) IDs of all worklogs updated since well before the target month
#    (180-day buffer covers backdated / late-edited entries), paginated.
since = epoch_ms(START) - 180 * 86400 * 1000
worklog_ids = []
while True:
    u = requests.get(f"{BASE}/rest/api/3/worklog/updated",
                     headers=H, params={"since": since}).json()
    worklog_ids += [v["worklogId"] for v in u.get("values", [])]
    if u.get("lastPage", True): break
    since = u["until"]

# 2) Fetch worklog details in batches, keep only those started in the range.
in_range = []
for i in range(0, len(worklog_ids), 1000):
    det = requests.post(f"{BASE}/rest/api/3/worklog/list",
                        headers={**H, "Content-Type": "application/json"},
                        json={"ids": worklog_ids[i:i + 1000]}).json()
    in_range += [w for w in det if START <= w["started"][:10] <= END]

# 3) Map issueId -> (key, summary) for project CM only (worklog details
#    carry just the issueId, so we resolve keys/summaries in batches).
meta = {}
issue_ids = sorted({str(w["issueId"]) for w in in_range})
for i in range(0, len(issue_ids), 100):
    chunk = issue_ids[i:i + 100]
    jql = f'id in ({",".join(chunk)}) AND project = CM'
    d = requests.get(f"{BASE}/rest/api/3/search/jql", headers=H,
                     params={"jql": jql, "fields": "summary", "maxResults": 100}).json()
    for it in d.get("issues", []):
        meta[it["id"]] = (it["key"], it["fields"]["summary"])

# 4) Aggregate by person -> day (skipping any worklog not in project CM).
agg = defaultdict(lambda: defaultdict(list))
for w in in_range:
    iid = str(w["issueId"])
    if iid not in meta: continue
    key, summ = meta[iid]
    agg[w["author"]["displayName"]][w["started"][:10]].append(
        (key, summ, w["timeSpentSeconds"], comment(w.get("comment"))))

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
