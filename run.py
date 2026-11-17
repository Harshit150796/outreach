# -*- coding: utf-8 -*-
"""CouponDonation church outreach: compose one email per church and send the day's batch.

  python3 run.py preview --n 10                     # write previews, send nothing
  python3 run.py send --n 40 --key re_xxx            # send the next 40 unsent churches
  python3 run.py suppress --emails a@x.org,b@y.org   # never email these again

Files (same folder): queue.json (churches in send order), state.json (what was sent),
config.json (sender, reply-to, postal address, ramp)."""
import argparse, datetime as dt, hashlib, json, os, re, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
def load(name, default=None):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return default
    return json.load(open(p, encoding="utf-8"))

CFG = load("config.json")
APPLY = "https://www.coupondonation.com/apply"
PARTNERS = "https://www.coupondonation.com/partners"

def h(s, n):
    return int(hashlib.md5(s.encode()).hexdigest()[:8], 16) % n

def clean_name(n):
    n = re.sub(r"\s+", " ", n).strip(" -|,")
    if n.isupper() or n.islower():
        n = n.title()
    return n

def the_church(n):
    return n if re.match(r"^(the|la|el|los|las)\b", n, re.I) else n

SPANISH = lambda q: q["community"] == "Spanish" or q["denomination"].endswith("(Spanish-language)")

# Wording approved by the founder on Nov 15, 2026 (email playbook). One plain-text email from a named person
# with one ask: no headline, bullets, button or images, because a designed email reads as a newsletter.
# Help is for food, groceries, travel and health benefits. It says what CouponDonation is (a company, not a
# charity) and that the whole gift reaches the family. Founder request, Nov 17, 2026: every outreach email also
# offers a 30-minute introductory call (config.json book_call_url, the team's Calendly page).
SUBJECTS = {
    "en": ("When a family at {church} needs help", "When a family in {city} needs help"),
    "es": ("Cuando una familia de {church} necesita ayuda", "Cuando una familia de {city} necesita ayuda"),
}

T = {
    "en": dict(
        lang="en",
        body="""{hello}

I'm Harshit, the founder of CouponDonation.

When a family at {church} is in need of food, groceries, travel or health benefits, the people around them usually want to help and don't know how. We give them a way.

The family, or someone at the church for them, starts a free page. People give from $10. Each gift reaches the family as a store gift card, never cash, and the giver is told when it's received and used.

We're a company, not a charity, so gifts aren't tax-deductible. We take no fee from the gift. 100% goes to the families.

If a family comes to mind, or asks you for help, this is the page to pass on:
{apply}

Questions? Just reply. Or, if you'd like to know more about us, book a 30-minute call with us here:
{book}

Harshit Agrawal
Founder, CouponDonation

--
CouponDonation Inc., {addr}
{why}
{unsub}""",
        why="You're receiving this because {church}'s email is on its public listing.",
        unsub_txt="Reply \"no thanks\" or \"unsubscribe\" and I won't write again. You can also write to {u}.",
    ),
    "es": dict(
        lang="es",
        body="""{hello}

Soy Harshit, fundador de CouponDonation.

Cuando una familia de {church} necesita comida, despensa, transporte o ayuda con gastos de salud, la gente que la rodea casi siempre quiere ayudar y no sabe cómo. Nosotros les damos una manera.

La familia, o alguien de la iglesia en su nombre, crea una página gratis. Se puede dar desde $10. Cada aporte le llega a la familia como una tarjeta de regalo de una tienda, nunca en efectivo, y quien dio recibe un aviso cuando la familia la recibe y cuando la usa.

Somos una empresa, no una organización benéfica, así que los aportes no son deducibles de impuestos. No cobramos comisión sobre el aporte. El 100% llega a las familias.

Si piensa en alguna familia, o si alguna le pide ayuda, esta es la página para compartir (está en inglés):
{apply}

¿Preguntas? Solo responda a este correo. O, si quiere saber más sobre nosotros, reserve aquí una llamada de 30 minutos (en inglés):
{book}

Harshit Agrawal
Fundador, CouponDonation

--
CouponDonation Inc., {addr}
{why}
{unsub}""",
        why="Recibe este correo porque el email de {church} aparece en su listado público.",
        unsub_txt="Responda \"no, gracias\" o \"unsubscribe\" y no le volveré a escribir. También puede escribir a {u}.",
    ),
}

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def greeting(q, church, lang):
    sal = q["salutation"]
    if lang == "es":
        if sal:
            return f"Estimado {sal.replace('Father', 'Padre')}:"
        return f"Estimados hermanos de {church}:"
    if sal:
        return f"Dear {sal},"
    if q["denomination"] == "Catholic":
        return f"Dear {church} parish office,"
    base = re.sub(r"^the\s+", "", church, flags=re.I)
    return f"Hello {base} team,"

def subject(q, church, city, lang):
    full, short = SUBJECTS[lang]
    s = full.format(church=church)
    return s if len(s) <= 70 else short.format(city=city)

def compose(q):
    """(subject, plain text, None). Text only: there is no HTML version any more."""
    lang = "es" if SPANISH(q) else "en"
    t = T[lang]
    church = clean_name(q["name"])
    city = q["city"] or ("su comunidad" if lang == "es" else "your area")
    subj = subject(q, church, city, lang)
    text = t["body"].format(hello=greeting(q, church, lang), church=church, apply=APPLY, book=CFG["book_call_url"], addr=CFG["postal_address"],
                            why=t["why"].format(church=church), unsub=t["unsub_txt"].format(u=CFG["unsubscribe_address"]))
    return subj, text, None

def state():
    return load("state.json", {"sent": {}, "suppressed": [], "runs": []})

def save_state(s):
    tmp = os.path.join(HERE, "state.json.tmp")
    json.dump(s, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    os.replace(tmp, os.path.join(HERE, "state.json"))

def extra_taken():
    """Churches already emailed (or reserved) by the Gmail/Zoho sender, and its suppressions."""
    x = load("extra_state.json", {}) or {}
    ids = set(x.get("sent", {})) | {k.split(":", 1)[1] for k in x.get("pending", {}) if k.startswith("gmail_first:")}
    sup = set(e.lower() for e in x.get("suppressed", [])) | set(x.get("bounced", {}))
    return ids, sup

# Old ISP mailboxes (att.net, sbcglobal, roadrunner, msn, aol...) are often abandoned: in October 3 of 12 bounced
# (25%) vs about 6% for everything else. No first emails go to them (founder's call, Nov 3).
LEGACY_ISP = {"att.net", "sbcglobal.net", "pacbell.net", "prodigy.net", "flash.net", "swbell.net", "bellsouth.net",
              "roadrunner.com", "rr.com", "twc.com", "earthlink.net", "msn.com", "aol.com", "aim.com", "verizon.net",
              "comcast.net", "cox.net", "charter.net", "juno.com", "netzero.net", "netzero.com", "frontier.com",
              "frontiernet.net", "ymail.com", "rocketmail.com", "mindspring.com", "peoplepc.com", "q.com", "centurytel.net",
              "embarqmail.com", "windstream.net", "suddenlink.net", "optonline.net", "lycos.com", "excite.com"}

def risky(email):
    """True for addresses we skip for first emails (abandoned-ISP domains)."""
    return email.rsplit("@", 1)[-1].lower() in LEGACY_ISP

# Bounce review, Nov 15, 2026 (893 first emails, 63 addresses later suppressed, almost all bounces):
#   a church's own domain that is NOT its website's domain     41 sent, 8 bounced (20%)
#   a person's mailbox on the church's domain (not info@...)   21 sent, 4 bounced (19%)
#   everything else                                           831 sent, 51 bounced (6%)
# The first two groups are held back: they are only used once nothing else is left to send.
FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com", "me.com",
             "mac.com", "proton.me", "protonmail.com"}
SHARED_BOX = re.compile(r"^(info|office|admin|contact|church|parish|secretary|pastor|hello|mail|welcome|connect|reception|"
                        r"frontdesk|churchoffice|parishoffice|rectory|ministry|ministries|team|staff|email|inquir|general|"
                        r"iglesia|oficina|parroquia|secretaria)", re.I)

def _host(url):
    m = re.match(r"^(?:[a-z]+://)?(?:www\.)?([^/:?#]+)", (url or "").strip().lower())
    return m.group(1) if m else ""

def held_back(q):
    """Why this address waits until the safer ones are used up ('' when it doesn't)."""
    local, _, domain = q["email"].lower().rpartition("@")
    if domain in FREE_MAIL:
        return ""
    site = _host(q.get("website"))
    if site and not (domain == site or domain.endswith("." + site) or site.endswith("." + domain)):
        return "email domain is not the church website's domain"
    if not SHARED_BOX.match(local):
        return "a person's mailbox, not a shared church address"
    return ""

def web_verdict(q, checks):
    """'shown' (address is on the church's own website), 'not shown' (site read, address absent) or '' (unknown)."""
    v = checks.get(q["id"])
    if not v:
        return ""
    if v.get("found"):
        return "shown"
    return "not shown" if str(v.get("why", "")).startswith("not shown") else ""

def next_batch(n, look_ahead=100000):
    """The next n churches. Order (bounce review, Nov 15-16, 2026; see web_confirm.py):
       1. address shown on the church's own website (about 3% bounced)
       2. not checked yet, or the website could not be read (about 5-6%)
       3. held back: website read but the address isn't on it (about 15%), or a held_back() address (about 19%)."""
    s = state()
    xids, xsup = extra_taken()
    sup = set(e.lower() for e in s["suppressed"]) | xsup
    checks = load("web_checks.json", {}) or {}
    first, second, later, seen = [], [], [], 0
    for q in load("queue.json"):
        if q["id"] in s["sent"] or q["id"] in xids or q["email"].lower() in sup or risky(q["email"]) or q.get("check") == "drop":
            continue
        seen += 1
        w = web_verdict(q, checks)
        if w == "shown":
            first.append(q)
        elif w == "not shown" or held_back(q):
            if len(later) < n:
                later.append(q)
        elif len(second) < n:
            second.append(q)
        if len(first) >= n or seen >= look_ahead:
            break
    return (first + second + later)[:n]

def seeds():
    """The founder's test inboxes: each gets one copy of the day's first email, to see where it lands (inbox or spam)."""
    return [e.strip().lower() for e in (CFG.get("seed_emails") or []) if "@" in e]

def post(url, key, payload, idem):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "Idempotency-Key": idem, "User-Agent": "coupondonation-outreach/1.0"})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode(errors="replace")[:500]}

def cmd_preview(a):
    os.makedirs(os.path.join(HERE, "previews"), exist_ok=True)
    for q in next_batch(a.n):
        subj, body, html = compose(q)
        p = os.path.join(HERE, "previews", f"{q['seq']:05d}.txt")
        open(p, "w", encoding="utf-8").write(f"To: {q['email']}\nSubject: {subj}\n\n{body}\n")
        print(p)

def cmd_send(a):
    if CFG.get("paused"):
        sys.exit("PAUSED: config.json has paused=true. Nothing sent.")
    if not CFG.get("postal_address") or "TODO" in CFG["postal_address"]:
        sys.exit("STOP: postal_address is not set in config.json. Nothing sent.")
    s = state()
    batch = next_batch(a.n)
    today = dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=-5))).date().isoformat()
    sent, failed = 0, []
    # One copy of today's first email to each test inbox (config seed_emails). Never recorded as a church,
    # never counted in the totals, at most once a day (idempotency key).
    seeded = 0
    if batch:
        subj, body, _ = compose(batch[0])
        for seed in seeds():
            payload = {"from": CFG["from"], "to": [seed], "reply_to": [CFG["reply_to"]], "subject": subj, "text": body,
                       "headers": {"List-Unsubscribe": f"<mailto:{CFG['unsubscribe_address']}?subject=unsubscribe>"},
                       "tags": [{"name": "campaign", "value": "seed"}]}
            code, res = post("https://api.resend.com/emails", a.key, payload, "seed-" + today + "-" + hashlib.md5(seed.encode()).hexdigest()[:10])
            seeded += 1 if code in (200, 201) and res.get("id") else 0
            time.sleep(0.6)
    for q in batch:
        subj, body, _ = compose(q)
        payload = {"from": CFG["from"], "to": [q["email"]], "reply_to": [CFG["reply_to"]], "subject": subj, "text": body,
                   "headers": {"List-Unsubscribe": f"<mailto:{CFG['unsubscribe_address']}?subject=unsubscribe>"},
                   "tags": [{"name": "campaign", "value": "ca_churches_2026"}, {"name": "queue_id", "value": q["id"]}]}
        code, res = post("https://api.resend.com/emails", a.key, payload, "ca-churches-" + q["id"])
        if code in (200, 201) and res.get("id"):
            s["sent"][q["id"]] = {"email": q["email"], "name": q["name"], "county": q["county"], "date": today,
                                  "resend_id": res["id"], "subject": subj}
            sent += 1
            save_state(s)
        else:
            failed.append({"id": q["id"], "email": q["email"], "code": code, "res": res})
            if code in (401, 403, 429) or code >= 500:
                break
        time.sleep(0.6)
    s["runs"].append({"date": today, "sent": sent, "failed": len(failed), "requested": a.n})
    save_state(s)
    total = len(load("queue.json"))
    print(json.dumps({"date": today, "sent_today": sent, "seed_copies": seeded, "failed": failed[:5], "total_sent": len(s["sent"]),
                      "remaining": total - len(s["sent"]) - len(s["suppressed"])}, indent=1))

def cmd_suppress(a):
    s = state()
    for e in [x.strip().lower() for x in a.emails.split(",") if x.strip()]:
        if e not in s["suppressed"]:
            s["suppressed"].append(e)
    save_state(s)
    print("suppressed:", len(s["suppressed"]))

def cmd_plan(a):
    """How many to send today, from the ramp and the account's remaining quota."""
    s = state()
    days = len({r["date"] for r in s["runs"] if r.get("sent")})
    ramp = CFG["ramp"]
    target = ramp[-1]["per_day"]
    for step in ramp:
        if days < step["until_day"]:
            target = step["per_day"]; break
    if CFG.get("max_per_day"):
        target = min(target, int(CFG["max_per_day"]))  # hold the volume down after a resume
    n = max(0, min(target, a.daily_left - CFG["reserve_daily"], a.monthly_left - CFG["reserve_monthly"]))
    print(json.dumps({"sending_day": days + 1, "ramp_target": target, "send_today": n}))

if __name__ == "__main__":
  ap = argparse.ArgumentParser()
  sub = ap.add_subparsers(dest="cmd", required=True)
  p = sub.add_parser("preview"); p.add_argument("--n", type=int, default=10); p.set_defaults(f=cmd_preview)
  p = sub.add_parser("send"); p.add_argument("--n", type=int, required=True); p.add_argument("--key", required=True); p.set_defaults(f=cmd_send)
  p = sub.add_parser("suppress"); p.add_argument("--emails", required=True); p.set_defaults(f=cmd_suppress)
  p = sub.add_parser("plan"); p.add_argument("--daily-left", type=int, required=True); p.add_argument("--monthly-left", type=int, required=True); p.set_defaults(f=cmd_plan)
  a = ap.parse_args(); a.f(a)
