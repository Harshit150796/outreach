# -*- coding: utf-8 -*-
"""Call list: after the day's outreach emails, email the founder every church that was just emailed,
with phone, pastor, city and a map link, so they can call while the email is fresh.

  python3 digest.py preview            # write call_list.html / call_list.txt, send nothing
  python3 digest.py send --key re_xxx  # send (only if there is something new and no send is half-done)

One row per church. A church appears in the call list once per new email it gets (first email, then follow-up).
digest_state.json remembers what was already listed, so nothing is listed twice and nothing is missed."""
import argparse, hashlib, datetime as dt, html, json, os, sys, urllib.parse, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
TO = "connect.coupondonation@gmail.com"
FROM = "CouponDonation outreach <hello@outreach.coupondonation.com>"

def load(name, default=None):
    p = os.path.join(HERE, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default

def today():
    return dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=-5))).date()

def events():
    """Every outreach email ever sent, as (key, church id, date, label)."""
    rs = load("state.json", {"sent": {}})
    xs = load("extra_state.json", {}) or {}
    out = []
    for qid, v in rs["sent"].items():
        out.append((f"resend:{qid}", qid, v["date"], "First email (Resend)"))
    for qid, v in xs.get("sent", {}).items():
        out.append((f"gmail_first:{qid}", qid, v["date"], "First email (Gmail)"))
    for qid, v in xs.get("followups", {}).items():
        out.append((f"fu:{qid}", qid, v["date"], f"Follow-up ({'Gmail' if v['sender'] == 'gmail' else 'Zoho'})"))
    return out, rs, xs

def fmt_date(iso):
    d = dt.date.fromisoformat(iso)
    return d.strftime("%a %b ") + str(d.day)

def build():
    ev, rs, xs = events()
    done = set(load("digest_state.json", {"listed": []})["listed"])
    new = [e for e in ev if e[0] not in done]
    if not new:
        return None
    q = {c["id"]: c for c in load("queue.json")}
    sup = {e.lower() for e in rs.get("suppressed", [])} | {e.lower() for e in xs.get("suppressed", [])}
    replied = set(xs.get("replied", {}))
    hist = {}
    for key, qid, d, label in ev:
        hist.setdefault(qid, []).append((d, label))
    rows = {}
    for key, qid, d, label in new:
        if qid in q:
            rows.setdefault(qid, []).append((d, label))
    churches = []
    for qid, items in rows.items():
        c = q[qid]
        if c["email"].lower() in sup:
            continue  # asked us to stop: don't call them either
        churches.append((c, sorted(hist[qid]), max(d for d, _ in items), any(l.startswith("Follow-up") for _, l in items)))
    churches.sort(key=lambda t: (t[2], bool(t[0].get("phone"))), reverse=True)
    return {"new_keys": [e[0] for e in new], "churches": churches, "replied": replied}

def render(b):
    churches = b["churches"]
    with_phone = [t for t in churches if t[0].get("phone")]
    dates = sorted({t[2] for t in churches})
    span = fmt_date(dates[-1]) if len(dates) == 1 else f"{fmt_date(dates[0])} to {fmt_date(dates[-1])}"
    subj = f"Call list: {len(churches)} churches emailed ({span})"
    SANS = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;"
    e = html.escape
    def row(c, hist, fu):
        maps = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote_plus(f"{c['name']}, {c['city'] or c['county']}, CA")
        phone = (f'<a href="tel:{e("".join(ch for ch in c["phone"] if ch.isdigit() or ch == "+"))}" style="color:#1565c0;font-weight:700;text-decoration:none;font-size:16px;">{e(c["phone"])}</a>'
                 if c.get("phone") else '<span style="color:#8a948d;">No phone listed</span>')
        name = (f'<a href="{e(c["website"])}" style="color:#16231a;text-decoration:none;">{e(c["name"])}</a>' if c.get("website") else e(c["name"]))
        who = e(c["pastor_full"] or c["salutation"] or "")
        place = e(", ".join(x for x in (c["city"], f"{c['county']} County" if c["county"] else "") if x))
        h = " · ".join(f"{l} {fmt_date(d)}" for d, l in hist)
        tag = ' <span style="background:#e8f2e9;color:#2e7d32;border-radius:6px;padding:1px 6px;font-size:11px;">replied</span>' if c["email"].lower() in b["replied"] else ""
        return (f'<tr><td style="padding:12px 0;border-bottom:1px solid #e7ebe6;{SANS}font-size:14px;line-height:20px;color:#46524a;">'
                f'<div style="font-weight:700;color:#16231a;font-size:15px;">{name}{tag}</div>'
                f'<div>{phone}{" &nbsp;·&nbsp; " + who if who else ""}</div>'
                f'<div>{place} &nbsp;·&nbsp; <a href="{maps}" style="color:#2e7d32;">Address on map</a> &nbsp;·&nbsp; '
                f'<a href="mailto:{e(c["email"])}" style="color:#2e7d32;">{e(c["email"])}</a></div>'
                f'<div style="color:#8a948d;font-size:12px;">{e(h)}</div></td></tr>')
    body = "".join(row(*t[:2], t[3]) for t in churches)
    html_out = f"""<!doctype html><html><body style="margin:0;background:#f6f8f5;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:#fff;border-radius:16px;"><tr><td style="padding:24px 26px;">
<div style="{SANS}font-size:20px;font-weight:800;color:#16231a;">{len(churches)} churches to call</div>
<div style="{SANS}font-size:14px;line-height:21px;color:#46524a;padding:6px 0 10px;">Emailed {span}. {len(with_phone)} have a phone number. Tap a number to call. Churches with a follow-up have now had two emails; churches that asked us to stop are left out.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{body}</table>
</td></tr></table></td></tr></table></body></html>"""
    lines = [f"{len(churches)} churches to call (emailed {span}). {len(with_phone)} have a phone number.", ""]
    for c, hist, _, fu in churches:
        lines += [c["name"], f"  Phone: {c['phone'] or 'none listed'}" + (f"  ({c['pastor_full'] or c['salutation']})" if (c['pastor_full'] or c['salutation']) else ""),
                  f"  {c['city']}, {c['county']} County, CA", f"  {c['email']}" + (f"  {c['website']}" if c.get('website') else ""),
                  "  " + " / ".join(f"{l} {fmt_date(d)}" for d, l in hist), ""]
    return subj, html_out, "\n".join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["preview", "send"])
    ap.add_argument("--key")
    a = ap.parse_args()
    xs = load("extra_state.json", {}) or {}
    if xs.get("pending"):
        print("Gmail/Zoho send in progress (pending emails). Call list waits for the next push."); return
    b = build()
    if not b:
        print("Nothing new to list."); return
    subj, h, t = render(b)
    if a.cmd == "preview":
        open(os.path.join(HERE, "call_list.html"), "w", encoding="utf-8").write(h)
        open(os.path.join(HERE, "call_list.txt"), "w", encoding="utf-8").write(t)
        print(subj); return
    payload = {"from": FROM, "to": [TO], "subject": subj, "html": h, "text": t,
               "tags": [{"name": "campaign", "value": "call_list"}]}
    req = urllib.request.Request("https://api.resend.com/emails", data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {a.key}", "Content-Type": "application/json",
                                          "Idempotency-Key": "call-list-" + hashlib.sha1("|".join(sorted(b["new_keys"])).encode()).hexdigest()[:32],
                                          "User-Agent": "coupondonation-outreach/1.0"})
    try:
        r = urllib.request.urlopen(req, timeout=60); res = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"Call list not sent: {e.code} {e.read().decode(errors='replace')[:300]}")
    st = load("digest_state.json", {"listed": [], "sent": []})
    st["listed"] = sorted(set(st["listed"]) | set(b["new_keys"]))
    st.setdefault("sent", []).append({"date": today().isoformat(), "churches": len(b["churches"]), "resend_id": res.get("id")})
    json.dump(st, open(os.path.join(HERE, "digest_state.json"), "w", encoding="utf-8"), indent=0)
    print(subj, "->", TO)

if __name__ == "__main__":
    main()
