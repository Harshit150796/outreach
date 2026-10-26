# -*- coding: utf-8 -*-
"""Daily CouponDonation church outreach, run by GitHub Actions.

Safety checks first, then the send via run.py. Any check that can't complete stops the run
(nothing is sent) so a broken API call never turns into an unchecked send.

Env: RESEND_SEND_KEY (sending-only key), RESEND_FULL_KEY (full-access key, used only for reads).
"""
import datetime as dt, json, os, re, subprocess, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.resend.com"
OUTREACH_DOMAIN = "outreach.coupondonation.com"
OWN_DOMAIN = "coupondonation.com"
DAILY_CAP, MONTHLY_CAP = 100, 3000
SUMMARY = []

def log(msg):
    print(msg)
    SUMMARY.append(msg)

def finish(code=0):
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        open(p, "a", encoding="utf-8").write("\n\n".join(SUMMARY) + "\n")
    sys.exit(code)

def load(name):
    return json.load(open(os.path.join(HERE, name), encoding="utf-8"))

def get(path, key):
    for attempt in range(4):
        req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {key}",
                                                          "User-Agent": "coupondonation-outreach/1.0"})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=60).read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(2 + attempt * 2); continue
            raise RuntimeError(f"GET {path} -> {e.code}: {e.read().decode(errors='replace')[:300]}")
    raise RuntimeError(f"GET {path} failed")

def paged(path, key, stop_before=None, max_pages=30):
    """Yield items from a Resend list endpoint, newest first, until created_at < stop_before."""
    after = None
    for _ in range(max_pages):
        sep = "&" if "?" in path else "?"
        res = get(path + sep + "limit=100" + (f"&after={after}" if after else ""), key)
        items = res.get("data", [])
        for it in items:
            if stop_before and parse_ts(it.get("created_at")) < stop_before:
                return
            yield it
        if not res.get("has_more") or not items:
            return
        after = items[-1]["id"]
        time.sleep(0.15)

def parse_ts(s):
    if not s:
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    s = s.replace(" ", "T").replace("Z", "+00:00")
    s = re.sub(r"\+00$", "+00:00", s)
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)

def addrs(v):
    if isinstance(v, str):
        v = [v]
    out = []
    for x in v or []:
        m = re.findall(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+", x or "")
        out += [a.lower() for a in m]
    return out

def is_own(a):
    return a.endswith("@" + OWN_DOMAIN) or a.endswith("." + OWN_DOMAIN)

def run_py(*args):
    r = subprocess.run([sys.executable, os.path.join(HERE, "run.py"), *args], capture_output=True, text=True, cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError(f"run.py {args[0]} failed: {r.stdout}{r.stderr}")
    return r.stdout

def main():
    send_key = os.environ.get("RESEND_SEND_KEY", "").strip()
    full_key = os.environ.get("RESEND_FULL_KEY", "").strip()
    if not send_key or not full_key:
        log("STOP: RESEND_SEND_KEY and RESEND_FULL_KEY secrets are both required. Nothing sent.")
        finish(1)

    # 1. Gate
    cfg = load("config.json")
    if cfg.get("paused"):
        log("Outreach waiting: config.json has paused=true. Nothing sent."); finish(0)
    if not cfg.get("postal_address") or "TODO" in cfg["postal_address"]:
        log("Outreach waiting: postal_address not set in config.json. Nothing sent."); finish(0)

    now = dt.datetime.now(dt.timezone.utc)

    # Sent emails (last 7 days) - used for deliverability, duplicate guard and today's quota.
    sent7 = list(paged("/emails", full_key, stop_before=now - dt.timedelta(days=7)))
    known = {v["email"].lower() for v in load("state.json")["sent"].values()}
    outreach7 = [e for e in sent7 if OUTREACH_DOMAIN in (e.get("from") or "").lower()
                 or set(addrs(e.get("to"))) & known]

    # 2. Deliverability check
    n = len(outreach7)
    bounced = sum(1 for e in outreach7 if (e.get("last_event") or "") == "bounced")
    complained = sum(1 for e in outreach7 if (e.get("last_event") or "") == "complained")
    br = 100.0 * bounced / n if n else 0.0
    cr = 100.0 * complained / n if n else 0.0
    log(f"Last 7 days on {OUTREACH_DOMAIN}: {n} sent, {bounced} bounced ({br:.2f}%), {complained} complaints ({cr:.2f}%).")
    if n >= 40 and (br > 4.0 or cr > 0.1):
        cfg["paused"] = True
        json.dump(cfg, open(os.path.join(HERE, "config.json"), "w", encoding="utf-8"), indent=1)
        log("PAUSED: bounce or complaint rate over the limit. Set paused=false in config.json after reviewing.")
        finish(1)  # failing the job makes GitHub email you

    # 3. Opt-outs
    optouts = set()
    for m in paged("/emails/receiving", full_key, stop_before=now - dt.timedelta(days=30)):
        to = addrs(m.get("to"))
        subj = (m.get("subject") or "")
        detail = get(f"/emails/receiving/{m['id']}", full_key)
        body = (detail.get("text") or re.sub(r"<[^>]+>", " ", detail.get("html") or ""))
        asks = re.search(r"\b(unsubscribe|stop|remove)\b", subj + "\n" + body[:2000], re.I)
        if cfg["unsubscribe_address"].lower() not in to and not asks:
            continue
        senders = addrs(m.get("from") or detail.get("from"))
        sender = senders[0] if senders else ""
        if sender and is_own(sender):
            # forwarded from our own inbox: take the first outside address in the forwarded content
            found = [a for a in addrs(body) if not is_own(a)]
            sender = found[0] if found else ""
        if sender and not is_own(sender):
            optouts.add(sender)
        time.sleep(0.15)
    if optouts:
        run_py("suppress", "--emails", ",".join(sorted(optouts)))
    log(f"Opt-outs processed: {len(optouts)}" + (f" ({', '.join(sorted(optouts))})" if optouts else ""))

    # 4. Duplicate guard: next 80 recipients vs. anything sent in the last 3 days
    recent = set()
    for e in sent7:
        if parse_ts(e.get("created_at")) >= now - dt.timedelta(days=3) and "test" not in (e.get("subject") or "").lower():
            recent.update(addrs(e.get("to")))
    state = load("state.json")
    sup = set(x.lower() for x in state["suppressed"])
    upcoming = []
    for q in load("queue.json"):
        if q["id"] in state["sent"] or q["email"].lower() in sup:
            continue
        upcoming.append(q["email"].lower())
        if len(upcoming) >= 80:
            break
    dups = sorted(set(upcoming) & recent)
    if dups:
        run_py("suppress", "--emails", ",".join(dups))
        log(f"Duplicate guard: suppressed {len(dups)} already-emailed address(es).")

    # 5. Quota
    today = now.date()
    sent_today = sum(1 for e in sent7 if parse_ts(e.get("created_at")).date() == today)
    month_start = now - dt.timedelta(days=30)
    sent_month = sum(1 for v in state["sent"].values()
                     if dt.date.fromisoformat(v["date"]) >= month_start.date())
    other_7d = len(sent7) - len(outreach7)  # transactional mail from the main site
    sent_month_est = sent_month + other_7d * 5  # rough 30-day estimate of non-outreach mail
    plan = json.loads(run_py("plan", "--daily-left", str(DAILY_CAP - sent_today),
                             "--monthly-left", str(MONTHLY_CAP - sent_month_est)))
    k = plan["send_today"]
    if k <= 0:
        log(f"Quota used up for today (sent today: {sent_today}). Nothing sent."); finish(0)

    # 6. Send
    out = run_py("send", "--n", str(k), "--key", send_key)
    res = json.loads(out[out.index("{"):])
    log(f"Sending day {plan['sending_day']}: sent {res['sent_today']} of {k}. "
        f"Total sent {res['total_sent']}, remaining {res['remaining']}.")
    if res.get("failed"):
        log("Failures: " + json.dumps(res["failed"])[:1500])
        finish(1)
    finish(0)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"STOP: {e}. Nothing (more) was sent.")
        finish(1)
