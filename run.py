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

SUBJECTS_EN = [
    "Free help for families at {church}",
    "A free link for {city} families",
    "{church}: help for families in need",
]
SUBJECTS_ES = [
    "Ayuda gratuita para las familias de {church}",
    "Un enlace gratuito para familias de {city}",
]

T = {
    "en": dict(
        lang="en",
        preheader="Free help with groceries, medicine and bills. One link to share with families.",
        headline="Help for families,<br>one need at a time.",
        intro="CouponDonation connects families in need with donors who cover a specific need: groceries, medicine, a ride or a bill. It's free to apply, and every donated dollar goes to the need.",
        ask="Could {church} share this link with families who need help?",
        cta="Apply for help",
        facts=["Free", "3-minute form", "2–3 day review"],
        fine="Help arrives as a coupon, gift card or credit, never cash. Not every request can be approved.",
        partner="Supporting specific households? <a href=\"{partners}\" style=\"color:#0f5c4c;\">Start a fundraiser for them</a>.",
        partner_txt="Supporting specific households? Start a fundraiser for them: {partners}",
        reply="Questions? Just reply. A real person will answer.",
        sign="The CouponDonation team",
        why="You're receiving this because {church}'s email is on its public listing.",
        unsub="Unsubscribe",
        unsub_txt="To stop these emails, reply \"unsubscribe\" or write to {u}.",
    ),
    "es": dict(
        lang="es",
        preheader="Ayuda gratuita con comida, medicinas y recibos. Un enlace para compartir con las familias.",
        headline="Ayuda para las familias,<br>una necesidad a la vez.",
        intro="CouponDonation conecta a familias necesitadas con donantes que cubren una necesidad concreta: comida, medicinas, transporte o un recibo. Solicitar es gratis y cada dólar donado va a esa necesidad.",
        ask="¿Podría {church} compartir este enlace con las familias que necesitan ayuda?",
        cta="Solicitar ayuda",
        facts=["Gratis", "3 minutos", "Revisión en 2–3 días"],
        fine="La ayuda llega como cupón, tarjeta de regalo o crédito, nunca en efectivo. No todas las solicitudes pueden aprobarse.",
        partner="",
        partner_txt="",
        reply="¿Preguntas? Responda a este correo; le contesta una persona real, en español si prefiere.",
        sign="El equipo de CouponDonation",
        why="Recibe este correo porque el email de {church} aparece en su listado público.",
        unsub="Cancelar suscripción",
        unsub_txt="Para no recibir más correos, responda \"unsubscribe\" o escriba a {u}.",
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
    opts = SUBJECTS_ES if lang == "es" else SUBJECTS_EN
    s = opts[h(q["id"] + "s", len(opts))].format(church=church, city=city)
    if len(s) > 70:
        s = (f"Ayuda gratuita para familias de {city}" if lang == "es" else f"A free link for {city} families")
    return s

def compose(q):
    lang = "es" if SPANISH(q) else "en"
    t = T[lang]
    church = clean_name(q["name"])
    city = q["city"] or ("su comunidad" if lang == "es" else "your area")
    subj = subject(q, church, city, lang)
    hello = greeting(q, church, lang)
    ask = t["ask"].format(church=church)
    addr = CFG["postal_address"]
    u = CFG["unsubscribe_address"]
    unsub_link = f"mailto:{u}?subject=unsubscribe"

    # ---------- plain text ----------
    txt = [hello, "", t["intro"], "", ask, "", f"{t['cta']}: {APPLY}", "",
           " · ".join(t["facts"]) + ". " + t["fine"], ""]
    if t["partner_txt"]:
        txt += [t["partner_txt"].format(partners=PARTNERS), ""]
    txt += [t["reply"], "", t["sign"], "hello@coupondonation.com · coupondonation.com", "",
            "--", f"CouponDonation Inc., {addr}", t["why"].format(church=church), t["unsub_txt"].format(u=u)]
    text = "\n".join(txt)

    # ---------- HTML ----------
    F = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;"
    facts = "".join(
        f'<td align="center" style="{F}font-size:13px;line-height:18px;color:#0f5c4c;padding:10px 6px;'
        f'border-left:{"0" if i == 0 else "1px solid #d7e7e1"};">{esc(f)}</td>'
        for i, f in enumerate(t["facts"]))
    partner = (f'<p style="{F}margin:0 0 14px;font-size:14px;line-height:22px;color:#4a5560;">'
               f'{t["partner"].format(partners=PARTNERS)}</p>') if t["partner"] else ""
    html = f"""<!doctype html>
<html lang="{t['lang']}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{esc(subj)}</title></head>
<body style="margin:0;padding:0;background:#f4f2ee;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{esc(t['preheader'])}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f2ee;">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;">
  <tr><td style="padding:0 4px 18px;{F}font-size:15px;font-weight:700;letter-spacing:.2px;color:#0f5c4c;">CouponDonation</td></tr>
  <tr><td style="background:#ffffff;border-radius:14px;border:1px solid #e6e2da;padding:36px 36px 30px;">
    <p style="{F}margin:0 0 18px;font-size:15px;line-height:22px;color:#4a5560;">{esc(hello)}</p>
    <h1 style="{F}margin:0 0 16px;font-size:26px;line-height:32px;font-weight:700;color:#14202b;letter-spacing:-.3px;">{t['headline']}</h1>
    <p style="{F}margin:0 0 22px;font-size:16px;line-height:25px;color:#33404c;">{esc(t['intro'])}</p>
    <p style="{F}margin:0 0 22px;font-size:16px;line-height:25px;color:#14202b;font-weight:600;">{esc(ask)}</p>
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 22px;"><tr>
      <td align="center" bgcolor="#0f5c4c" style="border-radius:10px;">
        <a href="{APPLY}" style="{F}display:inline-block;padding:14px 28px;font-size:16px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:10px;">{esc(t['cta'])} &rarr;</a>
      </td></tr></table>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#eef6f3;border-radius:10px;margin:0 0 14px;"><tr>{facts}</tr></table>
    <p style="{F}margin:0 0 20px;font-size:13px;line-height:20px;color:#6b7680;">{esc(t['fine'])}</p>
    {partner}
    <p style="{F}margin:0 0 4px;font-size:14px;line-height:22px;color:#4a5560;">{esc(t['reply'])}</p>
    <p style="{F}margin:16px 0 0;font-size:15px;line-height:22px;color:#14202b;font-weight:600;">{esc(t['sign'])}</p>
  </td></tr>
  <tr><td style="padding:20px 8px 0;{F}font-size:12px;line-height:18px;color:#8a8f94;text-align:center;">
    CouponDonation Inc. · {esc(addr)}<br>
    {esc(t['why'].format(church=church))}<br>
    <a href="{unsub_link}" style="color:#8a8f94;text-decoration:underline;">{esc(t['unsub'])}</a> · <a href="https://www.coupondonation.com" style="color:#8a8f94;text-decoration:underline;">coupondonation.com</a>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""
    return subj, text, html

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
        subj, body, html = compose(q)
        p = os.path.join(HERE, "previews", f"{q['seq']:05d}.txt")
        open(p, "w", encoding="utf-8").write(f"To: {q['email']}\nSubject: {subj}\n\n{body}\n")
        open(p[:-4] + ".html", "w", encoding="utf-8").write(html)
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
        subj, body, html = compose(q)
        payload = {"from": CFG["from"], "to": [q["email"]], "reply_to": [CFG["reply_to"]], "subject": subj, "text": body, "html": html,
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
