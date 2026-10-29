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

LOGO = "https://coupondonation.com/favicon-192.png"
GREEN, BLUE, INK = "#2e7d32", "#1565c0", "#16231a"

T = {
    "en": dict(
        lang="en",
        preheader="Free help with groceries, medicine, rides and bills, for the families your church serves.",
        eyebrow="For churches in California",
        headline="A new way to give:<br>trackable, transparent, real.",
        headline_txt="A new way to give: trackable, transparent, real.",
        points=["No guessing where your money went. Each gift becomes coupons for groceries and essentials, and you can see it reach someone in need.",
                "Make someone happy today. Donate to a family near you, or apply if your family needs help.",
                "Give or get help, both in one place: donate a gift you can track, or apply for groceries and essentials."],
        intro="CouponDonation turns donations into gift cards for groceries, medicine, rides and bills. Families apply for free, and every donated dollar covers their need.",
        ask="Could {church} share our link with families who need help?",
        cta="Apply for help",
        share_label="Or share the link",
        steps=[("Apply", "Free, 3-minute form"),
               ("Verify", "Reviewed in 2–3 days"),
               ("Receive", "Gift card for the need")],
        fine="Help comes as a coupon, gift card or credit, never cash. Not every request can be approved.",
        partner="Already caring for a family? <a href=\"{partners}\" style=\"color:#1565c0;text-decoration:none;font-weight:600;\">Start a fundraiser for them&nbsp;&rarr;</a>",
        partner_txt="Already caring for a family? Start a fundraiser for them: {partners}",
        reply="Questions? Just reply. A real person will answer.",
        sign="With gratitude,",
        team="The CouponDonation team",
        why="You're receiving this because {church}'s email is on its public listing.",
        unsub="Unsubscribe",
        unsub_txt="To stop these emails, reply \"unsubscribe\" or write to {u}.",
    ),
    "es": dict(
        lang="es",
        preheader="Ayuda gratuita con comida, medicinas, transporte y recibos para las familias de su iglesia.",
        eyebrow="Para iglesias de California",
        headline="Una nueva forma de dar:<br>rastreable, transparente, real.",
        headline_txt="Una nueva forma de dar: rastreable, transparente, real.",
        points=["Sin dudas sobre adónde fue su dinero. Cada donación se convierte en cupones para comida y artículos esenciales, y puede ver cómo llega a alguien que lo necesita.",
                "Haga feliz a alguien hoy. Done a una familia cerca de usted, o solicite ayuda si su familia la necesita.",
                "Dar o recibir ayuda, todo en un solo lugar: done un regalo que puede rastrear, o solicite ayuda con comida y artículos esenciales."],
        intro="CouponDonation convierte donaciones en tarjetas de regalo para comida, medicinas, transporte y recibos. Las familias solicitan gratis y cada dólar donado cubre su necesidad.",
        ask="¿Podría {church} compartir nuestro enlace con las familias que necesitan ayuda?",
        cta="Solicitar ayuda",
        share_label="O comparta el enlace",
        steps=[("Solicitar", "Gratis, 3 minutos"),
               ("Verificar", "Revisión en 2–3 días"),
               ("Recibir", "Tarjeta para la necesidad")],
        fine="La ayuda llega como cupón, tarjeta de regalo o crédito, nunca en efectivo. No todas las solicitudes pueden aprobarse.",
        partner="",
        partner_txt="",
        reply="¿Preguntas? Responda a este correo; le contesta una persona real, en español si prefiere.",
        sign="Con gratitud,",
        team="El equipo de CouponDonation",
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
    short_apply = APPLY.replace("https://www.", "")

    # ---------- plain text ----------
    txt = [hello, "", t["headline_txt"], "", t["intro"], ""] + [f"• {x}" for x in t["points"]] + ["", ask, "", f"{t['cta']}: {APPLY}", ("Donar" if lang == "es" else "Donate") + ": https://www.coupondonation.com/donate", "",
           " → ".join(f"{a} ({b})" for a, b in t["steps"]), t["fine"], ""]
    if t["partner_txt"]:
        txt += [t["partner_txt"].format(partners=PARTNERS), ""]
    txt += [t["reply"], "", t["sign"], t["team"], "hello@coupondonation.com · coupondonation.com", "",
            "--", f"CouponDonation Inc., {addr}", t["why"].format(church=church), t["unsub_txt"].format(u=u)]
    text = "\n".join(txt)

    # ---------- HTML ----------
    SANS = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;"
    SERIF = "font-family:Georgia,'Times New Roman',Times,serif;"
    steps = ""
    for i, (a, b) in enumerate(t["steps"], 1):
        steps += (
            f'<td class="step" width="33%" valign="top" style="padding:0 6px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="background:#f6f8f5;border-radius:12px;padding:18px 14px 16px;">'
            f'<div style="{SERIF}font-size:22px;line-height:22px;color:{GREEN};font-weight:400;">0{i}</div>'
            f'<div style="{SANS}font-size:15px;line-height:20px;color:{INK};font-weight:700;padding-top:10px;">{esc(a)}</div>'
            f'<div style="{SANS}font-size:13px;line-height:18px;color:#5d6b62;padding-top:3px;">{esc(b)}</div>'
            f'</td></tr></table></td>')
    partner = (f'<tr><td class="px" style="padding:0 44px 6px;{SANS}font-size:14px;line-height:22px;color:#46524a;">'
               f'{t["partner"].format(partners=PARTNERS)}</td></tr>') if t["partner"] else ""
    html = f"""<!doctype html>
<html lang="{t['lang']}" xmlns="http://www.w3.org/1999/xhtml"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting"><meta name="color-scheme" content="light only"><meta name="supported-color-schemes" content="light only">
<title>{esc(subj)}</title>
<style>
  body{{margin:0;padding:0;-webkit-text-size-adjust:100%;}}
  a{{text-decoration:none;}}
  @media (max-width:600px){{
    .px{{padding-left:24px!important;padding-right:24px!important;}}
    .hero{{font-size:38px!important;line-height:42px!important;}}
    .step{{display:block!important;width:100%!important;padding:0 0 10px!important;box-sizing:border-box;}}
    .steps{{padding-left:24px!important;padding-right:24px!important;}}
    .btn a{{display:block!important;}}
  }}
</style></head>
<body style="margin:0;padding:0;background:#eef1ec;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;mso-hide:all;">{esc(t['preheader'])}&#8199;&#65279;&#847;&#8199;&#65279;&#847;&#8199;&#65279;&#847;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#eef1ec;">
<tr><td align="center" style="padding:36px 12px 40px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;background:#ffffff;border-radius:20px;overflow:hidden;box-shadow:0 1px 3px rgba(22,35,26,.06);">

  <!-- brand bar -->
  <tr><td style="height:5px;line-height:5px;font-size:0;background:{GREEN};background-image:linear-gradient(90deg,{GREEN},{BLUE});">&nbsp;</td></tr>
  <tr><td class="px" style="padding:30px 44px 0;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
      <td valign="middle" style="padding-right:12px;"><img src="{LOGO}" width="44" height="44" alt="CouponDonation" style="display:block;border:0;width:44px;height:44px;"></td>
      <td valign="middle" style="{SANS}font-size:21px;line-height:24px;font-weight:800;letter-spacing:-.3px;"><span style="color:{GREEN};">Coupon</span><span style="color:{BLUE};">Donation</span></td>
    </tr></table>
  </td></tr>

  <!-- hero -->
  <tr><td class="px" style="padding:40px 44px 0;">
    <div style="{SANS}font-size:12px;line-height:16px;letter-spacing:1.6px;text-transform:uppercase;color:{GREEN};font-weight:700;">{esc(t['eyebrow'])}</div>
    <h1 class="hero" style="{SERIF}margin:14px 0 0;font-size:48px;line-height:52px;font-weight:400;color:{INK};letter-spacing:-1px;">{t['headline']}</h1>
  </td></tr>

  <tr><td class="px" style="padding:28px 44px 0;{SANS}font-size:16px;line-height:26px;color:#46524a;">
    <p style="margin:0 0 14px;color:{INK};">{esc(hello)}</p>
    <p style="margin:0 0 14px;">{esc(t['intro'])}</p>
    <ul style="margin:0 0 14px;padding-left:20px;">{''.join(f'<li style="margin:0 0 8px;">{esc(x)}</li>' for x in t['points'])}</ul>
    <p style="margin:0;color:{INK};font-weight:600;">{esc(ask)}</p>
  </td></tr>

  <!-- CTA -->
  <tr><td class="px" style="padding:28px 44px 0;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" class="btn"><tr>
      <td align="center" bgcolor="{GREEN}" style="border-radius:999px;">
        <a href="{APPLY}" style="{SANS}display:inline-block;padding:16px 34px;font-size:16px;line-height:20px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:999px;letter-spacing:.2px;">{esc(t['cta'])}&nbsp;&nbsp;&rarr;</a>
      </td></tr></table>
    <div style="{SANS}font-size:13px;line-height:20px;color:#7a857d;padding-top:14px;"><a href="https://www.coupondonation.com/donate" style="color:{GREEN};font-weight:600;text-decoration:none;">{"Donar" if lang == "es" else "Donate to a family"}</a> &middot; {esc(t['share_label'])}: <a href="{APPLY}" style="color:{BLUE};font-weight:600;text-decoration:none;">{esc(short_apply)}</a></div>
  </td></tr>

  <!-- steps -->
  <tr><td class="steps" style="padding:34px 38px 0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>{steps}</tr></table>
  </td></tr>
  <tr><td class="px" style="padding:14px 44px 26px;{SANS}font-size:12px;line-height:18px;color:#8a948d;">{esc(t['fine'])}</td></tr>

  <tr><td class="px" style="padding:0 44px;"><div style="height:1px;line-height:1px;font-size:0;background:#e7ebe6;">&nbsp;</div></td></tr>
  <tr><td style="height:24px;line-height:24px;font-size:0;">&nbsp;</td></tr>
  {partner}
  <tr><td class="px" style="padding:0 44px 34px;{SANS}font-size:14px;line-height:22px;color:#46524a;">
    {esc(t['reply'])}<br><br>
    <span style="color:#7a857d;">{esc(t['sign'])}</span><br>
    <span style="color:{INK};font-weight:700;">{esc(t['team'])}</span><br>
    <a href="https://www.coupondonation.com" style="color:{GREEN};text-decoration:none;">coupondonation.com</a>
  </td></tr>
</table>

<!-- footer -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;">
  <tr><td align="center" style="padding:22px 20px 0;{SANS}font-size:12px;line-height:19px;color:#8a948d;">
    CouponDonation Inc. &middot; {esc(addr)}<br>
    {esc(t['why'].format(church=church))}<br>
    <a href="{unsub_link}" style="color:#8a948d;text-decoration:underline;">{esc(t['unsub'])}</a>
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

def extra_taken():
    """Churches already emailed (or reserved) by the Gmail/Zoho sender, and its suppressions."""
    x = load("extra_state.json", {}) or {}
    ids = set(x.get("sent", {})) | {k.split(":", 1)[1] for k in x.get("pending", {}) if k.startswith("gmail_first:")}
    sup = set(e.lower() for e in x.get("suppressed", [])) | set(x.get("bounced", {}))
    return ids, sup

def next_batch(n):
    s = state()
    xids, xsup = extra_taken()
    sup = set(e.lower() for e in s["suppressed"]) | xsup
    out = []
    for q in load("queue.json"):
        if q["id"] in s["sent"] or q["id"] in xids or q["email"].lower() in sup:
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

if __name__ == "__main__":
  ap = argparse.ArgumentParser()
  sub = ap.add_subparsers(dest="cmd", required=True)
  p = sub.add_parser("preview"); p.add_argument("--n", type=int, default=10); p.set_defaults(f=cmd_preview)
  p = sub.add_parser("send"); p.add_argument("--n", type=int, required=True); p.add_argument("--key", required=True); p.set_defaults(f=cmd_send)
  p = sub.add_parser("suppress"); p.add_argument("--emails", required=True); p.set_defaults(f=cmd_suppress)
  p = sub.add_parser("plan"); p.add_argument("--daily-left", type=int, required=True); p.add_argument("--monthly-left", type=int, required=True); p.set_defaults(f=cmd_plan)
  a = ap.parse_args(); a.f(a)
