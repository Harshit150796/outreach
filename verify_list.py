# -*- coding: utf-8 -*-
"""Check every church in queue.json that hasn't been emailed yet, to cut bounces and drop fake or dead entries.

  python3 verify_list.py run      # all checks, writes list_checks.json (cached DNS/web results in .verify_cache.json)
  python3 verify_list.py apply    # writes the verdicts into queue.json (check / check_why / phone) and reorders it
  python3 verify_list.py report   # summary

Checks (no paid service; mailbox-level checks aren't possible without SMTP access):
  1. email syntax, placeholder/junk addresses, no-reply/system addresses, misspelled domains, disposable domains
  2. email domain DNS: exists, has MX (or at least A), not null-MX, not a parked/for-sale domain
  3. church website: domain resolves, page loads, not parked / for sale (a dead site usually means a closed church)
  4. phone: real US number, California area code, not shared by many churches
  5. name looks like a church, entry isn't an empty shell (no phone, no website, low source score)
  6. domains that already bounced: other addresses on them are dropped too
Verdict per church: ok, risky (sent last), drop (never sent)."""
import concurrent.futures as cf, json, os, re, sys, time, urllib.request, urllib.error, urllib.parse, ssl

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_F = os.path.join(HERE, ".verify_cache.json")
OUT_F = os.path.join(HERE, "list_checks.json")

FREE = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com", "me.com", "msn.com", "live.com",
        "comcast.net", "sbcglobal.net", "att.net", "verizon.net", "ymail.com", "protonmail.com", "proton.me", "mail.com",
        "cox.net", "charter.net", "earthlink.net", "pacbell.net", "juno.com", "roadrunner.com", "rocketmail.com",
        "googlemail.com", "mac.com", "frontier.com", "netzero.net", "prodigy.net", "aim.com", "gmx.com", "zoho.com"}
TYPOS = {"gmial.com", "gmai.com", "gamil.com", "gnail.com", "gmail.co", "gmail.con", "gmail.cm", "gmaill.com", "gmal.com",
         "yaho.com", "yahooo.com", "yahoo.co", "yahoo.con", "yhoo.com", "hotmial.com", "hotmai.com", "hotmail.co",
         "hotmail.con", "outlok.com", "outlook.co", "aol.co", "aol.con", "icloud.co", "sbcglobal.com", "sbcgloabl.net",
         "comcast.com", "att.com", "verizon.com", "earthlink.com"}
DISPOSABLE = {"mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "yopmail.com", "trashmail.com",
              "sharklasers.com", "getnada.com", "dispostable.com", "maildrop.cc"}
JUNK_DOMAIN = re.compile(r"(^|\.)(example\.(com|org|net)|domain\.com|email\.com|yourdomain|yoursite|website\.com|test\.com|"
                         r"sentry\.io|sentry-next\.wixpress\.com|wixpress\.com|wix\.com|squarespace\.com|godaddy\.com|"
                         r"weebly\.com|wordpress\.com|placeholder|localhost|invalid)$", re.I)
JUNK_LOCAL = re.compile(r"^(no-?reply|do-?not-?reply|donotreply|noreply|mailer-daemon|postmaster|abuse|bounce[s]?|"
                        r"example|test|user|username|yourname|your\.?name|name|email|someone|john\.?doe|jane\.?doe|"
                        r"filler|null|none|na|n/a)$", re.I)
EMAIL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._%+'-]{0,62}[a-z0-9_'-])?@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$", re.I)
PARKED_MX = re.compile(r"(^_dc-mx\.|parking|parkingcrew|sedoparking|bodis|above\.com|park-mx|domaincontrol\.com\.$|"
                       r"h-email\.net|hugedomains|dan\.com|afternic|undeveloped|namebright)", re.I)
PARKED_PAGE = re.compile(r"(this domain (is|may be) for sale|buy this domain|domain is for sale|the domain .{0,40} is for sale|"
                         r"hugedomains|dan\.com/buy|afternic|sedo\.com|parked (free|domain)|domain parking|"
                         r"this (web ?site|site|domain) (is|has been) (suspended|expired)|account (has been )?suspended|"
                         r"domain (has )?expired|renew (this|your) domain|future home of|coming soon.{0,40}godaddy|"
                         r"godaddy.{0,80}(parked|coming soon)|is available for purchase|inquire about this domain)", re.I | re.S)
CHURCHY = re.compile(r"(church|parish|chapel|fellowship|ministr|temple|iglesia|templo|cathedral|basilica|mission|misi[oó]n|"
                     r"assembl|asamblea|congregat|tabernacle|tabern[aá]culo|worship|christ|cristo|crist|baptist|bautista|"
                     r"catholic|cat[oó]lica|lutheran|methodist|presbyterian|episcopal|adventist|apostolic|apost[oó]lica|"
                     r"pentecost|nazarene|evangel|gospel|evangelio|bible|biblia|sanctuary|santuario|saint|\bst\.? |our lady|"
                     r"nuestra se[nñ]ora|holy|santa|santo|san |sacred|grace|gracia|faith|fe |calvary|calvario|vineyard|"
                     r"covenant|reformed|orthodox|zion|sion|bethel|bethany|emmanuel|immanuel|trinity|trinidad|redeem|"
                     r"salvation|community|comunidad|center|centro|life|vida|hope|esperanza|harvest|cosecha|light|luz|"
                     r"kingdom|reino|cogic|ame\b|umc\b|ucc\b|god|dios|jesus|jes[uú]s|lord|se[nñ]or|spirit|esp[ií]ritu|"
                     r"word|palabra|house of|casa de|family|familia|outreach|alliance|alianza|friends|quaker|mennonite|"
                     r"brethren|disciples|cumberland|wesleyan|free will|church of|first |new |abundant|living|victory|"
                     r"victoria|praise|alabanza|glory|gloria|crossing|rock|roca|cornerstone|journey|mosaic|refuge|refugio)", re.I)
GENERIC_LOCAL = re.compile(r"^(info|office|admin|contact|church|parish|secretary|pastor|hello|mail|welcome|connect|"
                           r"reception|frontdesk|churchoffice|parishoffice|rectory|ministry|ministries|team|staff|email|"
                           r"inquir|general|iglesia|oficina|parroquia|secretaria)", re.I)
# California area codes (incl. overlays)
CA_AREA = {"209", "213", "279", "310", "323", "341", "350", "369", "408", "415", "424", "442", "510", "530", "559", "562",
           "619", "626", "628", "650", "657", "661", "669", "707", "714", "738", "747", "760", "805", "818", "820", "831",
           "840", "858", "909", "916", "925", "949", "951"}

def load(name, default=None):
    p = os.path.join(HERE, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default

def dom(e):
    return e.rsplit("@", 1)[-1].lower()

def site_host(url):
    m = re.match(r"^\s*(?:https?://)?(?:www\d?\.)?([^/:?#\s]+)", url or "", re.I)
    return m.group(1).lower().rstrip(".") if m else ""

def base(host):
    """Registrable-ish part: last two labels (good enough for .org/.com/.church)."""
    p = host.split(".")
    return ".".join(p[-2:]) if len(p) >= 2 else host

# ------------------------------------------------------------------ network
CTX = ssl.create_default_context()

def doh(name, rtype):
    """DNS over HTTPS (Google, then Cloudflare). Returns (status, [data]); status 0 ok, 3 NXDOMAIN, -1 error."""
    for url in (f"https://dns.google/resolve?name={urllib.parse.quote(name)}&type={rtype}",
                f"https://cloudflare-dns.com/dns-query?name={urllib.parse.quote(name)}&type={rtype}"):
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers={"accept": "application/dns-json", "User-Agent": "list-check/1.0"})
                j = json.loads(urllib.request.urlopen(req, timeout=12, context=CTX).read())
                want = 15 if rtype == "MX" else 1
                return j.get("Status", -1), [a["data"] for a in j.get("Answer", []) if a.get("type") == want]
            except Exception:  # noqa: BLE001
                time.sleep(0.5 + attempt)
    return -1, []

def mail_dns(d):
    st, mx = doh(d, "MX")
    if st == -1:
        return {"mail": "error"}
    if st == 3:
        return {"mail": "nxdomain"}
    if mx:
        hosts = [m.split()[-1].lower() for m in mx]
        if all(h in (".", "") for h in hosts):
            return {"mail": "null_mx", "mx": hosts}
        if any(PARKED_MX.search(h) for h in hosts) and not any(re.search(r"google|outlook|protection|zoho|secureserver|"
                                                                          r"pphosted|mimecast|barracuda|icloud|yahoo|"
                                                                          r"messagingengine|emailsrvr|mailgun|ionos|"
                                                                          r"hostinger|titan|mxroute", h) for h in hosts):
            return {"mail": "parked_mx", "mx": hosts[:3]}
        return {"mail": "mx", "mx": hosts[:3]}
    st2, a = doh(d, "A")
    if st2 == -1:
        return {"mail": "error"}
    return {"mail": "a_only" if a else "none"}

def web(host, url):
    st, a = doh(host, "A")
    if st == -1:
        return {"web": "error"}
    if st == 3:
        return {"web": "nxdomain"}
    if not a:
        st6, a6 = doh(host, "AAAA")
        if not a6:
            return {"web": "no_address"}
    u = url.strip()
    if not re.match(r"^https?://", u, re.I):
        u = "http://" + u
    last = "error"
    for target in (u, "https://" + host + "/", "http://www." + host + "/"):
        try:
            req = urllib.request.Request(target, headers={"User-Agent": "Mozilla/5.0 (compatible; church-list-check/1.0)",
                                                          "Accept": "text/html,*/*"})
            r = urllib.request.urlopen(req, timeout=15, context=CTX)
            body = r.read(200000).decode("utf-8", "replace")
            final = site_host(r.geturl())
            if PARKED_PAGE.search(body[:60000]) and len(re.sub(r"<[^>]+>", " ", body)) < 40000:
                return {"web": "parked", "final": final}
            if final and base(final) != base(host) and re.search(r"(godaddy|hugedomains|sedo|afternic|dan\.com|"
                                                                    r"parkingcrew|bodis|namecheap|squadhelp)", final):
                return {"web": "parked", "final": final}
            return {"web": "ok", "final": final}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 405, 406, 429, 503):  # alive, just blocks bots
                return {"web": "ok_blocked", "code": e.code}
            last = f"http_{e.code}"
        except Exception as e:  # noqa: BLE001
            last = "unreachable"
    return {"web": last}

def phone_check(p):
    d = re.sub(r"\D", "", p or "")
    if not d:
        return "", "none"
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    if len(d) != 10:
        return "", "bad_length"
    a, x, n = d[:3], d[3:6], d[6:]
    if a[0] in "01" or a[1:] == "11" or x[0] in "01" or x[1:] == "11" or (x == "555" and n.startswith("01")) \
            or len(set(d)) <= 2 or d in ("1234567890", "0123456789"):
        return "", "invalid"
    fmt = f"({a}) {x}-{n}"
    return fmt, ("ca" if a in CA_AREA else "out_of_state")

# ------------------------------------------------------------------ run
def cmd_run(a):
    q = load("queue.json")
    rs = load("state.json", {"sent": {}, "suppressed": []})
    xs = load("extra_state.json", {}) or {}
    done = set(rs["sent"]) | set(xs.get("sent", {}))
    todo = [c for c in q if c["id"] not in done]
    cache = load(".verify_cache.json", {"mail": {}, "web": {}})
    mail_doms = sorted({dom(c["email"]) for c in todo if dom(c["email"]) not in FREE})
    hosts = {}
    for c in todo:
        h = site_host(c.get("website"))
        if h and h not in hosts:
            hosts[h] = c["website"]
    t0 = time.time()

    def save_cache():
        tmp = CACHE_F + ".tmp"
        json.dump(cache, open(tmp, "w"), separators=(",", ":")); os.replace(tmp, CACHE_F)

    need = [d for d in mail_doms if cache["mail"].get(d, {}).get("mail") in (None, "error")]
    print(f"email domains: {len(mail_doms)} ({len(need)} to check)", flush=True)
    with cf.ThreadPoolExecutor(40) as ex:
        for i, (d, r) in enumerate(zip(need, ex.map(mail_dns, need)), 1):
            cache["mail"][d] = r
            if i % 500 == 0:
                save_cache(); print(f"  {i}/{len(need)}  {time.time() - t0:.0f}s", flush=True)
    save_cache()
    needw = [h for h in hosts if cache["web"].get(h, {}).get("web") in (None, "error")]
    if a.max_web:
        needw = needw[:a.max_web]
    print(f"websites: {len(hosts)} ({len(needw)} to check)", flush=True)
    with cf.ThreadPoolExecutor(48) as ex:
        futs = {ex.submit(web, h, hosts[h]): h for h in needw}
        for i, f in enumerate(cf.as_completed(futs), 1):
            cache["web"][futs[f]] = f.result()
            if i % 500 == 0:
                save_cache(); print(f"  {i}/{len(needw)}  {time.time() - t0:.0f}s", flush=True)
    save_cache()
    judge(q, todo, rs, xs, cache)

def judge(q, todo, rs, xs, cache):
    bounced = {e.lower() for e in xs.get("bounced", {})}
    # Resend suppressions are bounces + complaints + opt-outs; opt-outs are on free domains or listed in runs, so only
    # domain-level bounces on church domains are used here.
    bounced |= {e.lower() for e in rs.get("suppressed", [])}
    bad_doms = {dom(e) for e in bounced if dom(e) not in FREE}
    phones = {}
    for c in q:
        f, _ = phone_check(c.get("phone"))
        if f:
            phones.setdefault(f, set()).add(c["name"].lower().strip())
    out = {}
    for c in todo:
        e = c["email"].strip().lower()
        d = dom(e)
        local = e.split("@")[0]
        drop, risky = [], []
        if not EMAIL_RE.match(e) or ".." in e or local.endswith(".") or local.startswith("."):
            drop.append("invalid email address")
        if JUNK_LOCAL.match(local) or JUNK_DOMAIN.search(d) or re.search(r"\.(png|jpe?g|gif|webp|svg)$", e) \
                or re.fullmatch(r"[0-9a-f]{24,}", local) or "%" in local:
            drop.append("placeholder or system address")
        if d in TYPOS:
            drop.append("misspelled email domain")
        if d in DISPOSABLE:
            drop.append("disposable email domain")
        if d in bad_doms:
            drop.append("another address on this domain already bounced")
        if d not in FREE:
            m = cache["mail"].get(d, {}).get("mail")
            if m == "nxdomain":
                drop.append("email domain does not exist")
            elif m in ("none", "null_mx"):
                drop.append("email domain can't receive mail")
            elif m == "parked_mx":
                drop.append("email domain is parked")
            elif m == "a_only":
                risky.append("email domain has no mail server record")
            elif m in (None, "error"):
                risky.append("email domain not checked")
        h = site_host(c.get("website"))
        w = cache["web"].get(h, {}).get("web") if h else None
        same = h and d not in FREE and base(h) == base(d)
        if h:
            if w in ("nxdomain", "no_address", "parked"):
                (drop if same else risky).append("church website is gone or parked" + ("" if same else " (church may have closed)"))
            elif same and cache["web"][h].get("final") and base(cache["web"][h]["final"]) != base(h):
                risky.append("church moved to a new website domain (old email domain)")
            elif w in ("unreachable", "http_404", "http_410", "http_500", "http_502", "http_504") or (w or "").startswith("http_5"):
                risky.append("church website doesn't load")
        fmt, pk = phone_check(c.get("phone"))
        if pk in ("bad_length", "invalid"):
            risky.append("phone number is not valid")
        elif pk == "out_of_state":
            risky.append("phone number is outside California")
        elif pk == "none":
            risky.append("no phone number")
        if fmt and len(phones.get(fmt, ())) >= 4:
            risky.append("phone shared by many churches")
        if not CHURCHY.search(c["name"] + " ") and c.get("denomination") in ("", "Not stated"):
            risky.append("name doesn't look like a church")
        if d not in FREE and h and not same and not re.search(r"(diocese|archdiocese|church-4-2000|lcms|elca|umc|pcusa|"
                                                               r"episcopal|adventist|catholic|sbc|nazarene|ag\.org|"
                                                               r"calvarychapel|vineyard|parish|dioc)", d):
            risky.append("email domain differs from church website")
        if d not in FREE and not GENERIC_LOCAL.match(local):
            # Oct–Nov sends: 10% of named-staff addresses at church domains bounced vs 3% of info@/office@ ones
            risky.append("addressed to a named staff member (may have left)")
        if c.get("score", 0) <= 3 and not fmt and not h:
            drop.append("no phone or website to confirm the church exists")
        elif c.get("score", 0) <= 1:
            risky.append("weak source listing")
        verdict = "drop" if drop else ("risky" if len(risky) >= 2 or any("gone" in r or "differs" in r or "not valid" in r
                                                                            or "outside" in r or "domain has no mail" in r or "moved" in r
                                                                            or "named staff" in r
                                                                            for r in risky) else "ok")
        out[c["id"]] = {"check": verdict, "why": drop + risky, "phone": fmt}
    json.dump(out, open(OUT_F, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    summary(out)

def summary(out):
    from collections import Counter
    v = Counter(r["check"] for r in out.values())
    why = Counter(w for r in out.values() for w in r["why"] if r["check"] == "drop")
    whyr = Counter(w for r in out.values() for w in r["why"] if r["check"] == "risky")
    print(json.dumps({"checked": len(out), "verdicts": v, "drop_reasons": why.most_common(), "risky_reasons": whyr.most_common()},
                     indent=1, ensure_ascii=False))

def cmd_report(a):
    summary(load("list_checks.json"))

def cmd_judge(a):
    q = load("queue.json")
    rs = load("state.json", {"sent": {}, "suppressed": []})
    xs = load("extra_state.json", {}) or {}
    done = set(rs["sent"]) | set(xs.get("sent", {}))
    judge(q, [c for c in q if c["id"] not in done], rs, xs, load(".verify_cache.json"))

def cmd_apply(a):
    """Write verdicts into queue.json. Sent churches stay where they are; unsent: ok first, then risky; drops are
    kept in the file (check=drop, never sent) so nothing is lost and the decision is visible."""
    q = load("queue.json")
    res = load("list_checks.json")
    rank = {"ok": 0, "risky": 1, "drop": 2}
    for c in q:
        r = res.get(c["id"])
        if not r:
            continue
        c["check"], c["check_why"] = r["check"], r["why"]
        if "phone_raw" not in c:
            c["phone_raw"] = c.get("phone", "")
        c["phone"] = r["phone"]  # valid, formatted number or "" (call list shows only real numbers)
    sent = [c for c in q if c["id"] not in res]
    rest = sorted((c for c in q if c["id"] in res), key=lambda c: (rank[c["check"]], -c.get("score", 0), c.get("order", 0)))
    q2 = sent + rest
    for i, c in enumerate(q2, 1):
        c["seq"] = i
    tmp = os.path.join(HERE, "queue.json.tmp")
    json.dump(q2, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, os.path.join(HERE, "queue.json"))
    print("queue.json updated:", {k: sum(1 for c in rest if c["check"] == k) for k in rank})

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run"); p.add_argument("--max-web", type=int, default=0); p.set_defaults(f=cmd_run)
    sub.add_parser("judge").set_defaults(f=cmd_judge)
    sub.add_parser("apply").set_defaults(f=cmd_apply)
    sub.add_parser("report").set_defaults(f=cmd_report)
    a = ap.parse_args(); a.f(a)
