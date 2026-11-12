# -*- coding: utf-8 -*-
"""Founder outreach: emails to YC founders who published an address, from connect@coupondonation.com (Zoho).

Separate from the church emails (extra.py / run.py) and never sent through Resend.
This script never sends anything itself. It plans the day's emails and records what was sent;
the weekday scheduled Claude session carries them to Zoho through Composio (see FOUNDERS_RUNBOOK.md).

  python3 founders.py inbox --file msgs.json      # replies, "no thanks", bounces seen in connect@
  python3 founders.py plan --out plan.json        # today's emails (and reserves them as pending)
  python3 founders.py batch --plan plan.json --start 0 --n 5
  python3 founders.py record --plan plan.json --results res.json
  python3 founders.py reconcile --found found.json
  python3 founders.py status

Files: founders_queue.json (who, from the donor prospects sheet), founders_text.json (the approved wording),
founders_state.json (what happened). config.json: founders_enabled, founders_paused, founders_per_day,
founders_followup_days."""
import argparse, datetime as dt, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
FROM = "connect@coupondonation.com"
ACCOUNT_ID = "3837587000000002002"
REGION = "in"
ALIAS = "zoho_mail_termor-medial"          # Composio connected account for connect@
BOUNCE_STOP = 3                            # bounces seen in one day pause sending
OPTOUT = re.compile(r"(no thanks|unsubscribe|remove (me|us)|take (me|us) off|stop (emailing|sending|contacting)|do not (email|contact)|not interested|opt[ -]?out)", re.I)
BOUNCE_FROM = re.compile(r"(mailer-daemon|postmaster|mail delivery)", re.I)
BOUNCE_SUBJ = re.compile(r"(undeliver|delivery status|delivery (has )?failed|failure notice|returned mail|could not be delivered|address not found|non[- ]?delivery|not delivered)", re.I)
EMAIL_RE = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+")


def load(name, default=None):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return default
    return json.load(open(p, encoding="utf-8"))


def cfg():
    return load("config.json", {}) or {}


def today():
    return dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=-5))).date()


def fstate():
    s = load("founders_state.json", None) or {}
    for k, v in (("sent", {}), ("followups", {}), ("replied", {}), ("bounced", {}), ("suppressed", []),
                 ("pending", {}), ("paused", ""), ("runs", [])):
        s.setdefault(k, v)
    return s


def save(s):
    tmp = os.path.join(HERE, "founders_state.json.tmp")
    json.dump(s, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    os.replace(tmp, os.path.join(HERE, "founders_state.json"))


def dom(e):
    return e.rsplit("@", 1)[-1].lower()


def blocked(q, s):
    e = q["to"].lower()
    if q.get("status") == "Skip":
        return True
    if e in s["suppressed"] or e in s["bounced"] or e in s["replied"]:
        return True
    return any(dom(r) == dom(e) for r in s["replied"])  # someone at that company answered


def fill(t, q):
    return (t.replace("{Company}", q["company"]).replace("{Hi}", q["hi"])
             .replace("{Collaboration line}", q.get("collab_line") or ""))


# ---------------------------------------------------------------- inbox
def cmd_inbox(a):
    """msgs.json: list of {from, subject, text} from connect@ Inbox + Notification folders (last ~3 days)."""
    msgs = json.load(open(a.file, encoding="utf-8"))
    s = fstate()
    queue = load("founders_queue.json", [])
    known = {q["to"].lower() for q in queue}
    emailed = {v["to"].lower() for v in s["sent"].values()}
    emailed_domains = {dom(e) for e in emailed}
    d = today().isoformat()
    out = {"replies": [], "optouts": [], "bounces": []}
    for m in msgs:
        frm = (EMAIL_RE.findall(m.get("from") or "") or [""])[0].lower()
        subj = m.get("subject") or ""
        text = (m.get("text") or "")[:4000]
        if frm.endswith("@coupondonation.com"):
            continue
        if BOUNCE_FROM.search(m.get("from") or "") or BOUNCE_SUBJ.search(subj):
            for e in sorted({e.lower() for e in EMAIL_RE.findall(text + " " + subj) if e.lower() in known}):
                if e not in s["bounced"]:
                    s["bounced"][e] = {"date": d}
                    out["bounces"].append(e)
            continue
        if not (frm in emailed or dom(frm) in emailed_domains):
            continue
        if frm not in s["replied"]:
            s["replied"][frm] = d
            out["replies"].append({"from": frm, "subject": subj[:120]})
        if OPTOUT.search(subj + "\n" + text[:1500]) and frm not in s["suppressed"]:
            s["suppressed"].append(frm)
            out["optouts"].append(frm)
    today_bounces = sum(1 for b in s["bounced"].values() if b["date"] == d)
    if today_bounces >= BOUNCE_STOP and not s["paused"]:
        s["paused"] = f"{today_bounces} bounces on {d}"
        out["paused"] = s["paused"]
    s["last_inbox"] = d
    save(s)
    print(json.dumps(out, indent=1, ensure_ascii=False))


# ---------------------------------------------------------------- plan
def cmd_plan(a):
    c, s = cfg(), fstate()
    td = today(); tds = td.isoformat()
    res = {"date": tds, "items": [], "notes": []}
    per_day = int(c.get("founders_per_day", 15))
    fu_days = int(c.get("founders_followup_days", 5))
    if not c.get("founders_enabled"):
        res["notes"].append("OFF: config.json founders_enabled is not true. Nothing planned.")
    elif c.get("founders_paused") or s["paused"]:
        res["notes"].append(f"PAUSED: {c.get('founders_paused') or s['paused']}. Nothing planned.")
    elif td.weekday() >= 5:
        res["notes"].append("Weekend: founder emails go out on weekdays only. Nothing planned.")
    elif s["pending"]:
        res["notes"].append(f"STOP: {len(s['pending'])} emails from an earlier plan were never recorded. Run step 1b (reconcile).")
    elif s.get("last_inbox") != tds:
        res["notes"].append("STOP: read the connect@ inbox first (founders.py inbox) so replies and opt-outs are honored.")
    else:
        text = load("founders_text.json")
        queue = load("founders_queue.json", [])
        qmap = {q["id"]: q for q in queue}
        # follow-ups: one, in the same thread, fu_days+ after the first email, no reply
        cutoff = (td - dt.timedelta(days=fu_days)).isoformat()
        for qid, v in sorted(s["sent"].items(), key=lambda kv: kv[1]["date"]):
            q = qmap.get(qid)
            if not q or qid in s["followups"] or v["date"] > cutoff or blocked(q, s) or not v.get("message_id"):
                continue
            body = fill(text["Follow-up"]["body"], q)
            res["items"].append({"key": f"fu:{qid}", "to": q["to"], "tool": "ZOHO_MAIL_MESSAGES_REPLY_TO_EMAIL",
                                 "args": {"accountId": ACCOUNT_ID, "region": REGION, "messageId": v["message_id"],
                                          "fromAddress": FROM, "toAddress": q["to"], "subject": "Re: " + v["subject"],
                                          "content": body, "mailFormat": "plaintext"}})
        # first emails
        n = 0
        per_day -= sum(1 for v in s["sent"].values() if v["date"] == tds)  # never more than per_day in one day
        for q in queue:
            if n >= per_day:
                break
            if q["id"] in s["sent"] or blocked(q, s):
                continue
            t = text[q["email"]]
            subj, body = fill(t["subject"], q), fill(t["body"], q)
            res["items"].append({"key": f"first:{q['id']}", "to": q["to"], "subject": subj,
                                 "tool": "ZOHO_MAIL_MESSAGES_SEND_EMAIL",
                                 "args": {"accountId": ACCOUNT_ID, "region": REGION, "fromAddress": FROM,
                                          "toAddress": q["to"], "subject": subj, "content": body, "mailFormat": "plaintext"}})
            n += 1
        nf = sum(1 for i in res["items"] if i["key"].startswith("fu:"))
        left = sum(1 for q in queue if q["id"] not in s["sent"] and not blocked(q, s)) - n
        res["notes"].append(f"connect@: {n} first emails, {nf} follow-ups. {left} founders still queued after today.")
        for it in res["items"]:
            s["pending"][it["key"]] = {"date": tds, "to": it["to"]}
        save(s)
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(res, open(os.path.join(HERE, "founders_last_plan.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"items": len(res["items"]), "notes": res["notes"]}, indent=1))


def cmd_batch(a):
    plan = json.load(open(a.plan, encoding="utf-8"))
    part = plan["items"][a.start:a.start + a.n]
    print(json.dumps([{"tool_slug": it["tool"], "account": ALIAS, "arguments": it["args"]} for it in part], ensure_ascii=False))
    print("KEYS (same order): " + json.dumps([it["key"] for it in part]), file=sys.stderr)


# ---------------------------------------------------------------- record
def cmd_record(a):
    """res.json: {key: {"ok": true, "message_id": "..."} | {"ok": false, "error": "..."}}"""
    plan = json.load(open(a.plan, encoding="utf-8"))
    results = json.load(open(a.results, encoding="utf-8"))
    s = fstate()
    counts = {"sent": 0, "failed": 0, "unknown": 0}
    for it in plan["items"]:
        kind, qid = it["key"].split(":", 1)
        r = results.get(it["key"])
        if r is None:
            counts["unknown"] += 1
            continue
        s["pending"].pop(it["key"], None)
        if not r.get("ok"):
            counts["failed"] += 1
            continue
        counts["sent"] += 1
        if kind == "first":
            s["sent"][qid] = {"to": it["to"], "date": plan["date"], "subject": it["subject"], "message_id": r.get("message_id")}
        else:
            s["followups"][qid] = {"to": it["to"], "date": plan["date"], "message_id": r.get("message_id")}
    if not getattr(a, "reconcile", False):
        s["runs"].append({"date": plan["date"], **counts})
    save(s)
    print(json.dumps(counts))


def cmd_reconcile(a):
    """found.json: {"<address>": {"message_id": "..."}} for pending addresses found in connect@ Sent. Others are released."""
    s = fstate()
    if not s["pending"]:
        print(json.dumps({"pending": 0})); return
    found = {k.lower(): v for k, v in json.load(open(a.found, encoding="utf-8")).items()}
    plan = load("founders_last_plan.json", {"items": []})
    items = [it for it in plan["items"] if it["key"] in s["pending"]]
    results = {it["key"]: ({"ok": True, **found[it["to"].lower()]} if it["to"].lower() in found
                           else {"ok": False, "error": "not in Sent"}) for it in items}
    for k in list(s["pending"]):
        if k not in results:
            s["pending"].pop(k)
    save(s)
    tp, tr = os.path.join(HERE, ".f_plan.json"), os.path.join(HERE, ".f_res.json")
    json.dump({**plan, "items": items}, open(tp, "w")); json.dump(results, open(tr, "w"))
    try:
        cmd_record(argparse.Namespace(plan=tp, results=tr, reconcile=True))
    finally:
        os.remove(tp); os.remove(tr)


def cmd_status(a):
    s, q = fstate(), load("founders_queue.json", [])
    print(json.dumps({"queue": len(q), "first_sent": len(s["sent"]), "followups": len(s["followups"]),
                      "replies": s["replied"], "optouts": s["suppressed"], "bounces": list(s["bounced"]),
                      "pending": len(s["pending"]), "paused": s["paused"] or cfg().get("founders_paused") or "",
                      "enabled": bool(cfg().get("founders_enabled"))}, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(required=True)
    p = sub.add_parser("inbox"); p.add_argument("--file", required=True); p.set_defaults(f=cmd_inbox)
    p = sub.add_parser("plan"); p.add_argument("--out", required=True); p.set_defaults(f=cmd_plan)
    p = sub.add_parser("batch"); p.add_argument("--plan", required=True); p.add_argument("--start", type=int, default=0); p.add_argument("--n", type=int, default=5); p.set_defaults(f=cmd_batch)
    p = sub.add_parser("record"); p.add_argument("--plan", required=True); p.add_argument("--results", required=True); p.set_defaults(f=cmd_record)
    p = sub.add_parser("reconcile"); p.add_argument("--found", required=True); p.set_defaults(f=cmd_reconcile)
    p = sub.add_parser("status"); p.set_defaults(f=cmd_status)
    a = ap.parse_args(); a.f(a)
