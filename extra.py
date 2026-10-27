# -*- coding: utf-8 -*-
"""Gmail + Zoho add-on to the Resend church outreach.

  connect.coupondonation@gmail.com (Gmail)  first emails to new churches + one follow-up in the same thread
  hello@coupondonation.com (Zoho)           one follow-up to churches that got the Resend email (no cold first emails)

This script never sends anything itself. It plans the day's emails, and records what was sent.
A daily scheduled Claude session sends the planned emails through the Composio Gmail/Zoho connections.

  python3 extra.py inbox --source gmail --file msgs.json   # replies, opt-outs, bounces seen in an inbox
  python3 extra.py plan --out plan.json                    # today's emails (and reserves them)
  python3 extra.py record --plan plan.json --results res.json
  python3 extra.py status
  python3 extra.py test --to you@example.com --sender gmail|zoho [--kind first|followup]

Shared state: state.json (Resend, read-only here) + extra_state.json (this sender).
run.py and daily.py read extra_state.json, so no church is emailed first by two senders."""
import argparse, datetime as dt, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run  # compose(), clean_name(), greeting(), SPANISH, CFG

GMAIL_FROM = "connect.coupondonation@gmail.com"
ZOHO_FROM = "hello@coupondonation.com"
ZOHO_ACCOUNT = "5347274000000002002"
ZOHO_REGION = "in"
GMAIL_ACCOUNT = "gmail_spitz-fixed"        # Composio connected-account alias
ZOHO_ACCOUNT_ALIAS = "zoho_mail_ungot-snail"

FOLLOWUP_AFTER_DAYS = 4
GMAIL_FIRST_RAMP = [(3, 10), (7, 15), (14, 20), (10**9, 30)]   # (up to sending day, per day)
GMAIL_FOLLOWUP_MAX = 25
GMAIL_DAILY_MAX = 50                                         # first + follow-ups, well under Gmail's 500
ZOHO_RAMP = [(3, 10), (7, 20), (10**9, 25)]
SKIP_AHEAD = 300   # Gmail takes churches this far behind Resend's next batch, so the two never race
BOUNCE_PAUSE = 5.0  # % over the last 14 days (min 20 sent) pauses that sender
FREE_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com", "me.com",
                "msn.com", "live.com", "comcast.net", "sbcglobal.net", "att.net", "verizon.net", "ymail.com",
                "protonmail.com", "mail.com", "cox.net", "charter.net", "earthlink.net", "pacbell.net"}
OPTOUT = re.compile(r"\b(unsubscribe|remove (me|us|our)|take (me|us) off|stop (emailing|sending|contacting)|do not (email|contact)|no more emails?|opt[ -]?out)\b", re.I)
BOUNCE_FROM = re.compile(r"(mailer-daemon|postmaster|mail delivery)", re.I)
BOUNCE_SUBJ = re.compile(r"(undeliver|delivery status|delivery (has )?failed|failure notice|returned mail|could not be delivered|address not found|mail delivery|non[- ]?delivery)", re.I)
EMAIL_RE = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+")

def today():
    return dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=-5))).date()

def xstate():
    s = run.load("extra_state.json", None) or {}
    for k, v in (("sent", {}), ("followups", {}), ("replied", {}), ("bounced", {}), ("suppressed", []),
                 ("pending", {}), ("paused", {}), ("runs", [])):
        s.setdefault(k, v)
    return s

def save(s):
    tmp = os.path.join(HERE, "extra_state.json.tmp")
    json.dump(s, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    os.replace(tmp, os.path.join(HERE, "extra_state.json"))

def dom(e):
    return e.rsplit("@", 1)[-1].lower()

def blocked(email, rs, xs):
    """True if we must not email this address again (opted out, bounced, replied)."""
    e = email.lower()
    if e in {x.lower() for x in rs["suppressed"]} or e in {x.lower() for x in xs["suppressed"]} or e in xs["bounced"]:
        return True
    if e in xs["replied"]:
        return True
    d = dom(e)
    if d not in FREE_DOMAINS and any(dom(r) == d for r in xs["replied"]):
        return True
    return False

def ramp(steps, day):
    for upto, n in steps:
        if day <= upto:
            return n
    return steps[-1][1]

def weekday_name(iso, lang):
    d = dt.date.fromisoformat(iso)
    en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    es = ["el lunes", "el martes", "el miércoles", "el jueves", "el viernes", "el sábado", "el domingo"]
    return (es if lang == "es" else en)[d.weekday()]

FU = {
    "en": dict(
        subj="Following up: {subject}",
        body="""{hello}

A quick follow-up to our note from {day}. Families can apply for free help with groceries, medicine, rides and bills at {apply}. It takes about 3 minutes, and requests are reviewed in 2–3 days.

Would {church} be willing to share the link in a bulletin, on your website, or with whoever helps families in need?

If now isn't the right time, no reply is needed. This is our only follow-up.

With gratitude,
The CouponDonation team
coupondonation.com

--
CouponDonation Inc., {addr}
{unsub}"""),
    "es": dict(
        subj="Seguimiento: {subject}",
        body="""{hello}

Le escribimos de nuevo sobre nuestro correo del {day}. Las familias pueden solicitar ayuda gratuita con comida, medicinas, transporte y recibos en {apply}. Toma unos 3 minutos y las solicitudes se revisan en 2–3 días.

¿Podría {church} compartir el enlace en su boletín, en su sitio web o con quien ayuda a las familias necesitadas?

Si no es buen momento, no hace falta responder. Este es nuestro único seguimiento.

Con gratitud,
El equipo de CouponDonation
coupondonation.com

--
CouponDonation Inc., {addr}
{unsub}"""),
}

def followup(q, orig_date, orig_subject):
    lang = "es" if run.SPANISH(q) else "en"
    church = run.clean_name(q["name"])
    t = run.T[lang]
    day = weekday_name(orig_date, lang)
    if lang == "es":
        day = day.replace("el ", "", 1)
    body = FU[lang]["body"].format(hello=run.greeting(q, church, lang), day=day, apply=run.APPLY, church=church,
                                   addr=run.CFG["postal_address"], unsub=t["unsub_txt"].format(u=run.CFG["unsubscribe_address"]))
    return FU[lang]["subj"].format(subject=orig_subject), body

def first_text(q):
    subj, text, _html = run.compose(q)
    return subj, text

def sender_stats(xs, sender, since):
    sent = [v for v in xs["sent"].values() if v["sender"] == sender and v["date"] >= since]
    sent += [v for v in xs["followups"].values() if v["sender"] == sender and v["date"] >= since]
    bounces = [b for b in xs["bounced"].values() if b.get("sender") == sender and b["date"] >= since]
    return len(sent), len(bounces)

# ---------------------------------------------------------------- inbox
def cmd_inbox(a):
    """msgs.json: list of {from, subject, text, date?} from the Gmail or Zoho inbox (newest ~2 days)."""
    msgs = json.load(open(a.file, encoding="utf-8"))
    rs, xs = run.state(), xstate()
    q_by_email = {}
    for k, v in rs["sent"].items():
        q_by_email[v["email"].lower()] = ("resend", k)
    for k, v in xs["sent"].items():
        q_by_email[v["email"].lower()] = (v["sender"], k)
    known_domains = {dom(e) for e in q_by_email if dom(e) not in FREE_DOMAINS}
    d = today().isoformat()
    out = {"replies": [], "optouts": [], "bounces": []}
    for m in msgs:
        frm = (EMAIL_RE.findall(m.get("from") or "") or [""])[0].lower()
        subj = m.get("subject") or ""
        text = (m.get("text") or "")[:4000]
        if frm in (GMAIL_FROM, ZOHO_FROM) or frm.endswith("@coupondonation.com") or frm.endswith(".coupondonation.com"):
            continue
        if BOUNCE_FROM.search(m.get("from") or "") or BOUNCE_SUBJ.search(subj):
            hits = [e.lower() for e in EMAIL_RE.findall(text) if e.lower() in q_by_email]
            for e in sorted(set(hits)):
                if e not in xs["bounced"]:
                    xs["bounced"][e] = {"date": d, "sender": a.source if q_by_email[e][0] != "resend" else "resend"}
                    out["bounces"].append(e)
            continue
        is_known = frm in q_by_email or (dom(frm) in known_domains)
        if not is_known:
            continue
        if frm not in xs["replied"]:
            xs["replied"][frm] = d
            out["replies"].append({"from": frm, "subject": subj[:120]})
        if OPTOUT.search(subj + "\n" + text[:1500]):
            if frm not in xs["suppressed"]:
                xs["suppressed"].append(frm)
                out["optouts"].append(frm)
    xs["last_inbox_" + a.source] = d
    save(xs)
    print(json.dumps(out, indent=1, ensure_ascii=False))

# ---------------------------------------------------------------- plan
def cmd_plan(a):
    rs, xs = run.state(), xstate()
    cfg = run.CFG
    td = today()
    tds = td.isoformat()
    res = {"date": tds, "items": [], "notes": []}
    if cfg.get("extra_paused"):
        res["notes"].append("PAUSED: config.json has extra_paused=true. Nothing planned.")
    elif td.weekday() >= 5:
        res["notes"].append("Weekend: Gmail/Zoho send on weekdays only. Nothing planned.")
    elif xs["pending"]:
        res["notes"].append(f"STOP: {len(xs['pending'])} emails from an earlier plan were never recorded. "
                            "Check the Gmail/Zoho Sent folders, then run `record` (or `clear-pending`). Nothing planned.")
    elif not cfg.get("postal_address") or "TODO" in cfg["postal_address"]:
        res["notes"].append("STOP: postal_address missing. Nothing planned.")
    if res["notes"]:
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(json.dumps({"items": 0, "notes": res["notes"]}, indent=1)); return

    since14 = (td - dt.timedelta(days=14)).isoformat()
    for sender in ("gmail", "zoho"):
        n, b = sender_stats(xs, sender, since14)
        if n >= 20 and 100.0 * b / n > BOUNCE_PAUSE and not xs["paused"].get(sender):
            xs["paused"][sender] = f"{tds}: {b} bounces of {n} sent in 14 days"
    queue = run.load("queue.json")
    qmap = {q["id"]: q for q in queue}
    cutoff = (td - dt.timedelta(days=FOLLOWUP_AFTER_DAYS)).isoformat()
    already_today = lambda sender: sum(1 for v in xs["sent"].values() if v["sender"] == sender and v["date"] == tds) + \
        sum(1 for v in xs["followups"].values() if v["sender"] == sender and v["date"] == tds)

    # Gmail follow-ups (its own threads)
    g_items = []
    if not xs["paused"].get("gmail"):
        for qid, v in sorted(xs["sent"].items(), key=lambda kv: kv[1]["date"]):
            if v["sender"] != "gmail" or qid in xs["followups"] or v["date"] > cutoff or not v.get("thread_id"):
                continue
            if blocked(v["email"], rs, xs) or qid not in qmap:
                continue
            subj, body = followup(qmap[qid], v["date"], v["subject"])
            g_items.append({"key": f"gmail_fu:{qid}", "tool": "GMAIL_REPLY_TO_THREAD", "account": GMAIL_ACCOUNT,
                            "to": v["email"], "args": {"thread_id": v["thread_id"], "recipient_email": v["email"],
                                                       "message_body": body, "is_html": False, "include_signature": False}})
            if len(g_items) >= GMAIL_FOLLOWUP_MAX:
                break
        # Gmail first emails
        gdays = len({v["date"] for v in xs["sent"].values() if v["sender"] == "gmail"})
        want = ramp(GMAIL_FIRST_RAMP, gdays + 1)
        room = max(0, min(want, GMAIL_DAILY_MAX - len(g_items) - already_today("gmail")))
        sent_ids = set(rs["sent"]) | set(xs["sent"])
        emailed = {v["email"].lower() for v in rs["sent"].values()} | {v["email"].lower() for v in xs["sent"].values()}
        skipped, firsts = 0, []
        for q in queue:
            if q["id"] in sent_ids or q["email"].lower() in emailed or blocked(q["email"], rs, xs):
                continue
            if skipped < SKIP_AHEAD:
                skipped += 1; continue
            if len(firsts) >= room:
                break
            subj, body = first_text(q)
            emailed.add(q["email"].lower())
            firsts.append({"key": f"gmail_first:{q['id']}", "tool": "GMAIL_SEND_EMAIL", "account": GMAIL_ACCOUNT,
                           "to": q["email"], "subject": subj,
                           "args": {"recipient_email": q["email"], "subject": subj, "body": body, "is_html": False,
                                    "include_signature": False}})
        g_items += firsts
        res["notes"].append(f"Gmail: sending day {gdays + 1}, {len(firsts)} first emails, {len(g_items) - len(firsts)} follow-ups.")
    else:
        res["notes"].append(f"Gmail PAUSED ({xs['paused']['gmail']}).")

    # Zoho follow-ups to Resend churches
    z_items = []
    if not xs["paused"].get("zoho"):
        zdays = len({v["date"] for v in xs["followups"].values() if v["sender"] == "zoho"})
        room = max(0, ramp(ZOHO_RAMP, zdays + 1) - already_today("zoho"))
        for qid, v in sorted(rs["sent"].items(), key=lambda kv: kv[1]["date"]):
            if len(z_items) >= room:
                break
            if qid in xs["followups"] or v["date"] > cutoff or qid not in qmap:
                continue
            if blocked(v["email"], rs, xs):
                continue
            subj, body = followup(qmap[qid], v["date"], v["subject"])
            z_items.append({"key": f"zoho_fu:{qid}", "tool": "ZOHO_MAIL_MESSAGES_SEND_EMAIL", "account": ZOHO_ACCOUNT_ALIAS,
                            "to": v["email"], "subject": subj,
                            "args": {"accountId": ZOHO_ACCOUNT, "region": ZOHO_REGION, "fromAddress": ZOHO_FROM,
                                     "toAddress": v["email"], "subject": subj, "content": body, "mailFormat": "plaintext"}})
        res["notes"].append(f"Zoho: sending day {zdays + 1}, {len(z_items)} follow-ups.")
    else:
        res["notes"].append(f"Zoho PAUSED ({xs['paused']['zoho']}).")

    res["items"] = g_items + z_items
    for it in res["items"]:
        xs["pending"][it["key"]] = {"date": tds, "to": it["to"]}
    save(xs)
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"items": len(res["items"]), "notes": res["notes"]}, indent=1))

# ---------------------------------------------------------------- record
def cmd_record(a):
    """res.json: {key: {"ok": true, "message_id": "...", "thread_id": "..."} | {"ok": false, "error": "..."}}"""
    plan = json.load(open(a.plan, encoding="utf-8"))
    results = json.load(open(a.results, encoding="utf-8"))
    rs, xs = run.state(), xstate()
    qmap = {q["id"]: q for q in run.load("queue.json")}
    counts = {"sent": 0, "failed": 0, "unknown": 0}
    for it in plan["items"]:
        kind, qid = it["key"].split(":", 1)
        r = results.get(it["key"])
        if r is None:
            counts["unknown"] += 1
            continue  # stays pending: blocks the next plan until someone checks the Sent folder
        xs["pending"].pop(it["key"], None)
        if not r.get("ok"):
            counts["failed"] += 1
            continue
        counts["sent"] += 1
        if kind == "gmail_first":
            q = qmap[qid]
            xs["sent"][qid] = {"email": q["email"], "name": q["name"], "county": q["county"], "date": plan["date"],
                               "sender": "gmail", "subject": it["subject"], "thread_id": r.get("thread_id"),
                               "message_id": r.get("message_id")}
        else:
            xs["followups"][qid] = {"email": it["to"], "date": plan["date"], "sender": "gmail" if kind == "gmail_fu" else "zoho",
                                    "message_id": r.get("message_id")}
    xs["runs"].append({"date": plan["date"], **counts,
                       "gmail_first": sum(1 for it in plan["items"] if it["key"].startswith("gmail_first:")),
                       "gmail_fu": sum(1 for it in plan["items"] if it["key"].startswith("gmail_fu:")),
                       "zoho_fu": sum(1 for it in plan["items"] if it["key"].startswith("zoho_fu:"))})
    save(xs)
    print(json.dumps(counts))

def cmd_batch(a):
    """Print items [start, start+n) as the exact `tools` array for COMPOSIO_MULTI_EXECUTE_TOOL."""
    plan = json.load(open(a.plan, encoding="utf-8"))
    part = plan["items"][a.start:a.start + a.n]
    print(json.dumps([{"tool_slug": it["tool"], "account": it["account"], "arguments": it["args"]} for it in part], ensure_ascii=False))
    print("KEYS (same order): " + json.dumps([it["key"] for it in part]), file=sys.stderr)

def cmd_clear_pending(a):
    xs = xstate()
    n = len(xs["pending"]); xs["pending"] = {}
    save(xs); print("cleared", n)

def cmd_status(a):
    rs, xs = run.state(), xstate()
    since14 = (today() - dt.timedelta(days=14)).isoformat()
    out = {"resend_sent": len(rs["sent"]), "gmail_first_sent": sum(1 for v in xs["sent"].values() if v["sender"] == "gmail"),
           "gmail_followups": sum(1 for v in xs["followups"].values() if v["sender"] == "gmail"),
           "zoho_followups": sum(1 for v in xs["followups"].values() if v["sender"] == "zoho"),
           "replies": len(xs["replied"]), "optouts": len(xs["suppressed"]), "bounces": len(xs["bounced"]),
           "pending": len(xs["pending"]), "paused": xs["paused"],
           "bounce_14d": {s: sender_stats(xs, s, since14) for s in ("gmail", "zoho")}}
    print(json.dumps(out, indent=1))

def cmd_test(a):
    """A plan with one sample church email to your own address. Records nothing."""
    q = dict(run.load("queue.json")[0])
    q["email"] = a.to
    if a.kind == "first":
        subj, body = first_text(q)
    else:
        subj, body = followup(q, (today() - dt.timedelta(days=4)).isoformat(), run.subject(q, run.clean_name(q["name"]), q["city"] or "your area", "en"))
    subj = "[TEST] " + subj
    if a.sender == "gmail":
        it = {"tool": "GMAIL_SEND_EMAIL", "account": GMAIL_ACCOUNT, "args": {"recipient_email": a.to, "subject": subj, "body": body,
                                                                               "is_html": False, "include_signature": False}}
    else:
        it = {"tool": "ZOHO_MAIL_MESSAGES_SEND_EMAIL", "account": ZOHO_ACCOUNT_ALIAS,
              "args": {"accountId": ZOHO_ACCOUNT, "region": ZOHO_REGION, "fromAddress": ZOHO_FROM, "toAddress": a.to,
                       "subject": subj, "content": body, "mailFormat": "plaintext"}}
    print(json.dumps(it, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inbox"); p.add_argument("--source", choices=["gmail", "zoho"], required=True); p.add_argument("--file", required=True); p.set_defaults(f=cmd_inbox)
    p = sub.add_parser("plan"); p.add_argument("--out", required=True); p.set_defaults(f=cmd_plan)
    p = sub.add_parser("record"); p.add_argument("--plan", required=True); p.add_argument("--results", required=True); p.set_defaults(f=cmd_record)
    p = sub.add_parser("batch"); p.add_argument("--plan", required=True); p.add_argument("--start", type=int, default=0); p.add_argument("--n", type=int, default=10); p.set_defaults(f=cmd_batch)
    p = sub.add_parser("clear-pending"); p.set_defaults(f=cmd_clear_pending)
    p = sub.add_parser("status"); p.set_defaults(f=cmd_status)
    p = sub.add_parser("test"); p.add_argument("--to", required=True); p.add_argument("--sender", choices=["gmail", "zoho"], required=True)
    p.add_argument("--kind", choices=["first", "followup"], default="first"); p.set_defaults(f=cmd_test)
    a = ap.parse_args(); a.f(a)
