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

TRADITION_EN = {
    "Catholic": "The corporal works of mercy your parish practices every week, feeding the hungry and caring for those in need, are exactly where we hope to help.",
    "United Methodist": "In the spirit of doing all the good you can, for all the people you can, we'd love to put one more practical tool in your hands.",
    "Methodist (AME / CME)": "Your church's long tradition of caring for the whole family, body and spirit, is exactly where we hope to help.",
    "Lutheran": "Your congregation's care for neighbors in need is exactly where we hope to help, with something practical and easy to share.",
    "Presbyterian": "Your congregation's care for neighbors in need is exactly where we hope to help, with something practical and easy to share.",
    "Episcopal / Anglican": "Your parish's care for neighbors in need is exactly where we hope to help, with something practical and easy to share.",
    "Baptist": "James wrote that faith shows itself when a brother or sister lacks daily food and someone meets that need. That is the kind of help we built CouponDonation to deliver.",
    "Southern / Missionary Baptist": "James wrote that faith shows itself when a brother or sister lacks daily food and someone meets that need. That is the kind of help we built CouponDonation to deliver.",
    "Seventh-day Adventist": "Your church's commitment to health and to caring for the whole person is exactly where we hope to help.",
    "Salvation Army": "Your corps already meets people at their point of need, and we'd love to add one more practical option for the families you see.",
    "Orthodox": "Your parish's care for neighbors in need is exactly where we hope to help, with something practical and easy to share.",
}
OPENERS_EN = [
    "Churches are often the first place a family turns when the groceries, the prescription or the utility bill doesn't stretch to the end of the month.",
    "When a family in {city} runs short on groceries or medicine, a church is often the first door they knock on.",
    "Pastors and church offices hear every week from families who are a few dollars short on groceries, medicine or a bill.",
]
SUBJECTS_EN = [
    "{church}: free help for families in {city}",
    "A free resource for families at {church}",
    "Grocery and medicine help for {city} families",
    "For the {church} office: a free link to share",
]

def greeting_en(q, church):
    if q["salutation"]:
        return f"Dear {q['salutation']},"
    if q["denomination"] == "Catholic":
        return f"Dear {church} parish office,"
    base = re.sub(r"^the\s+", "", church, flags=re.I)
    return f"Hello {base} team,"

def compose_en(q):
    church = clean_name(q["name"])
    city = q["city"] or "your area"
    county = q["county"]
    place = f"{city}" + (f" and across {county} County" if county and county.lower() != city.lower() else "")
    who = "your parish" if q["denomination"] == "Catholic" else "your congregation"
    opener = OPENERS_EN[h(q["id"], len(OPENERS_EN))].format(city=city)
    trad = TRADITION_EN.get(q["denomination"], "")
    community = ""
    if q["community"] and q["community"] not in ("Spanish",):
        community = f" That includes the {q['community']} families {who} walks alongside."
    subj = SUBJECTS_EN[h(q["id"] + "s", len(SUBJECTS_EN))].format(church=church, city=city)
    if len(subj) > 78:
        subj = f"A free resource for families in {city}"
    lines = [
        greeting_en(q, church),
        "",
        f"I'm writing to {church} in {city} because {opener[0].lower() + opener[1:]}",
        "",
        "CouponDonation is a free way for people in need to ask for help with a specific need: groceries, medicine, a ride, clothing or a utility bill. "
        "Donors fund those needs directly, 100% of each donation goes to the family, and every dollar is tracked from gift to receipt.",
        "",
        (trad + " " if trad else "") + f"Would you be willing to share our application link with families {who} serves?{community}",
        "",
        APPLY,
        "",
        "The application is free for US residents and takes about 3 minutes. Verification usually takes 2 to 3 business days. "
        "We can't promise every application will be approved, and help comes as a coupon, gift card or credit for the stated need, not cash.",
        "",
        f"If {church} already supports particular households, you can also run a fundraiser for them, with each family's consent: {PARTNERS}",
        "",
        f"Thank you for all you do for {place}. If you have any questions, just reply to this email and a real person will answer.",
        "",
        "With gratitude,",
        "The CouponDonation team",
        "hello@coupondonation.com · +1 (315) 898-6745",
        "https://www.coupondonation.com",
    ]
    return subj, "\n".join(lines)

SUBJECTS_ES = [
    "{church}: ayuda gratuita para familias en {city}",
    "Un recurso gratuito para las familias de {church}",
    "Ayuda con comida y medicinas para familias de {city}",
]
def compose_es(q):
    church = clean_name(q["name"])
    city = q["city"] or "su comunidad"
    sal = f"Estimado {q['salutation'].replace('Father', 'Padre')}," if q["salutation"] else f"Estimados hermanos de {church}:"
    if q["salutation"].startswith("Pastor"):
        sal = f"Estimado {q['salutation']}:"
    subj = SUBJECTS_ES[h(q["id"] + "s", len(SUBJECTS_ES))].format(church=church, city=city)
    if len(subj) > 78:
        subj = f"Ayuda gratuita para familias en {city}"
    lines = [
        sal,
        "",
        f"Le escribo a {church} en {city} porque muchas veces la iglesia es la primera puerta que toca una familia cuando no le alcanza para la comida, las medicinas o un recibo de luz.",
        "",
        "CouponDonation es una forma gratuita de pedir ayuda para una necesidad concreta: comida, medicinas, transporte, ropa o un servicio público. "
        "Los donantes cubren esas necesidades directamente, el 100% de cada donación llega a la familia y cada dólar se puede rastrear.",
        "",
        "¿Podrían compartir este enlace con las familias de su congregación que lo necesiten?",
        "",
        APPLY,
        "",
        "La solicitud es gratuita para residentes de EE. UU. y toma unos 3 minutos. La verificación suele tardar de 2 a 3 días hábiles. "
        "No podemos prometer que toda solicitud sea aprobada, y la ayuda llega como cupón, tarjeta de regalo o crédito, no en efectivo.",
        "",
        "Si prefieren responder en español, con gusto lo hacemos. Solo responda a este correo.",
        "",
        "Con gratitud,",
        "El equipo de CouponDonation",
        "hello@coupondonation.com · +1 (315) 898-6745",
        "",
        "— In English —",
        f"We're writing to {church} because churches are often where families turn when groceries or medicine run short. "
        f"Please consider sharing our free application link with families who need help: {APPLY}",
    ]
    return subj, "\n".join(lines)

def footer(q):
    church = clean_name(q["name"])
    addr = CFG["postal_address"]
    return ("\n\n--\n"
            f"CouponDonation Inc., {addr}\n"
            f"We found {church}'s email on its public listing. If you'd rather not hear from us, reply \"unsubscribe\" "
            f"or email {CFG['unsubscribe_address']} and we won't write again.")

def compose(q):
    subj, body = compose_es(q) if SPANISH(q) else compose_en(q)
    return subj, body + footer(q)

def state():
    return load("state.json", {"sent": {}, "suppressed": [], "runs": []})

def save_state(s):
    tmp = os.path.join(HERE, "state.json.tmp")
    json.dump(s, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    os.replace(tmp, os.path.join(HERE, "state.json"))

def next_batch(n):
    s = state()
    sup = set(e.lower() for e in s["suppressed"])
    out = []
    for q in load("queue.json"):
        if q["id"] in s["sent"] or q["email"] in sup:
            continue
        out.append(q)
        if len(out) >= n:
            break
    return out

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
        subj, body = compose(q)
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
    for q in batch:
        subj, body = compose(q)
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
    print(json.dumps({"date": today, "sent_today": sent, "failed": failed[:5], "total_sent": len(s["sent"]),
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
    n = max(0, min(target, a.daily_left - CFG["reserve_daily"], a.monthly_left - CFG["reserve_monthly"]))
    print(json.dumps({"sending_day": days + 1, "ramp_target": target, "send_today": n}))

ap = argparse.ArgumentParser()
sub = ap.add_subparsers(dest="cmd", required=True)
p = sub.add_parser("preview"); p.add_argument("--n", type=int, default=10); p.set_defaults(f=cmd_preview)
p = sub.add_parser("send"); p.add_argument("--n", type=int, required=True); p.add_argument("--key", required=True); p.set_defaults(f=cmd_send)
p = sub.add_parser("suppress"); p.add_argument("--emails", required=True); p.set_defaults(f=cmd_suppress)
p = sub.add_parser("plan"); p.add_argument("--daily-left", type=int, required=True); p.add_argument("--monthly-left", type=int, required=True); p.set_defaults(f=cmd_plan)
a = ap.parse_args(); a.f(a)
