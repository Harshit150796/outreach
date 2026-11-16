# -*- coding: utf-8 -*-
"""Is a church's email address shown on its own website today? A listed address is rarely a dead one.

Bounce review, Nov 15-16, 2026 (first emails already sent, checked afterwards against what bounced):
  address shown on the church's website        about 3% bounced
  website could not be read                    about 5% bounced
  website read, address NOT shown on it        about 15% bounced
So run.py sends to the first group first, then the second, and holds the third back.

  python3 web_confirm.py next --n 150     # check the next unsent churches that have no result yet -> web_checks.json
  python3 web_confirm.py report           # counts

Only reads public pages (home page plus up to three contact/about pages). Needs curl."""
import concurrent.futures as cf, json, re, ssl, sys, urllib.parse, urllib.request

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept": "text/html,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}
LINK = re.compile(r'href=["\']([^"\'#]+)["\']', re.I)

def get(url, timeout=9):
    """Fetch with curl so every request has a hard deadline (DNS, connect and slow bodies included)."""
    import subprocess
    r = subprocess.run(["curl", "-sSL", "--max-time", str(timeout), "--max-redirs", "5", "--max-filesize", "3000000", "-k", "--compressed",
                        "-A", UA["User-Agent"], "-H", "Accept: text/html,*/*;q=0.8", "-H", "Accept-Language: en-US,en;q=0.9",
                        "-w", "\n%{url_effective}\n%{http_code}", url], capture_output=True, timeout=timeout + 5)
    out = r.stdout.decode("utf-8", "replace")
    body, _, tail = out.rpartition("\n")
    body, _, final = body.rpartition("\n")
    if r.returncode != 0 and not body.strip():
        raise RuntimeError(f"curl {r.returncode}")
    if not tail.strip().startswith(("2", "3")) and not body.strip():
        raise RuntimeError(f"http {tail.strip()}")
    return final.strip() or url, body[:900_000]

def cf_decode(hexs):
    try:
        k = int(hexs[:2], 16)
        return "".join(chr(int(hexs[i:i + 2], 16) ^ k) for i in range(2, len(hexs), 2))
    except Exception:
        return ""

def has(html, email):
    low = html.lower()
    local, dom = email.split("@")
    if email in low:
        return True
    for m in re.finditer(r'data-cfemail="([0-9a-f]+)"|email-protection#([0-9a-f]+)', low):
        if cf_decode(m.group(1) or m.group(2)).lower() == email:
            return True
    loose = re.sub(r"\s*(\[at\]|\(at\)|\sat\s|&#64;|&#x40;|%40)\s*", "@", low)
    loose = re.sub(r"\s*(\[dot\]|\(dot\))\s*", ".", loose)
    return email in loose

def check(e):
    email = e["email"].lower().strip()
    site = (e.get("website") or "").strip()
    if not site:
        return e["id"], {"found": False, "pages": 0, "why": "no website"}
    if "//" not in site:
        site = "http://" + site
    pages, seen, errs = 0, set(), ""
    try:
        final, html = get(site)
    except Exception as ex:
        return e["id"], {"found": False, "pages": 0, "why": "site did not load: " + type(ex).__name__}
    pages += 1
    if has(html, email):
        return e["id"], {"found": True, "pages": pages, "why": "home page"}
    host = urllib.parse.urlparse(final).hostname or ""
    cands = []
    for href in LINK.findall(html):
        if re.search(r"(contact|about|staff|connect|visit|who-we-are|leadership|pastor|info|contacto|nosotros)", href, re.I):
            u = urllib.parse.urljoin(final, href)
            p = urllib.parse.urlparse(u)
            if p.scheme in ("http", "https") and (p.hostname or "") == host and u not in seen:
                seen.add(u); cands.append(u)
    cands.sort(key=lambda u: (0 if "contact" in u.lower() else 1, len(u)))
    for u in cands[:3]:
        try:
            _, h2 = get(u, 7)
            pages += 1
            if has(h2, email):
                return e["id"], {"found": True, "pages": pages, "why": "contact or about page"}
        except Exception:
            pass
    return e["id"], {"found": False, "pages": pages, "why": "not shown on the pages read"}


HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
OUT_F = __import__("os").path.join(HERE, "web_checks.json")

def load_checks():
    try:
        return json.load(open(OUT_F, encoding="utf-8"))
    except Exception:
        return {}

def safe(e):
    try:
        return check(e)
    except Exception as ex:  # noqa: BLE001
        return e["id"], {"found": False, "pages": 0, "why": "site did not load: " + type(ex).__name__}

def run_checks(rows, threads=32, budget_s=420):
    """Check rows within a time budget; returns {id: result} for the ones that finished."""
    import time
    out, t0 = {}, time.time()
    ex = cf.ThreadPoolExecutor(threads)
    futs = [ex.submit(safe, e) for e in rows]
    try:
        for f in cf.as_completed(futs, timeout=budget_s):
            k, v = f.result()
            out[k] = v
    except cf.TimeoutError:
        pass
    ex.shutdown(wait=False, cancel_futures=True)
    return out

def cmd_next(n):
    import datetime as dt
    import run
    checks = load_checks()
    s = run.state()
    xids, xsup = run.extra_taken()
    sup = set(e.lower() for e in s["suppressed"]) | xsup
    rows = []
    for q in run.load("queue.json"):
        if q["id"] in s["sent"] or q["id"] in xids or q["email"].lower() in sup or run.risky(q["email"]) or q.get("check") == "drop":
            continue
        if q["id"] in checks:
            continue
        rows.append({"id": q["id"], "email": q["email"], "website": q.get("website") or ""})
        if len(rows) >= n:
            break
    got = run_checks(rows)
    today = dt.date.today().isoformat()
    for k, v in got.items():
        checks[k] = {"found": bool(v["found"]), "why": v["why"].split(":")[0], "date": today}
    json.dump(checks, open(OUT_F, "w", encoding="utf-8"), indent=0, sort_keys=True)
    print(json.dumps({"asked": len(rows), "checked": len(got), "shown_on_site": sum(1 for v in got.values() if v["found"]),
                      "not_shown": sum(1 for v in got.values() if not v["found"] and v["why"].startswith("not shown")),
                      "total_on_file": len(checks)}))

if __name__ == "__main__":
    import argparse, collections
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("next"); p.add_argument("--n", type=int, default=150)
    sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "next":
        cmd_next(a.n)
    else:
        c = collections.Counter(("shown on site" if v["found"] else v["why"]) for v in load_checks().values())
        print(json.dumps(c, indent=1))
