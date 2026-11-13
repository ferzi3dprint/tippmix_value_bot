"""Klasszikus (lottózós) Tippmix ajánlat a tippmix.hu nyilvános API-jából – csak olvasás.

A klasszikus Tippmix más oddsokat ad, mint a Tippmixpro. A szelvényt a tippmix.hu-n (vagy a lottózóban) kell összeállítani:
minden piacnak van egy esemény-kódja (marketRealNo), a kimeneteleknek sorszáma (01, 02, 03).
"""
import datetime as dt

import requests

API = "https://api.tippmix.hu"
HOME = "https://www.tippmix.hu/"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36", "Origin": "https://www.tippmix.hu",
     "Referer": "https://www.tippmix.hu/", "Content-Type": "application/json", "Accept": "application/json"}

# (sportág, marketSubType) -> fajta (ugyanazok a fajták, mint a Tippmixpro-nál, így a meglévő logika használja)
SUB = {
    ("Labdarúgás", 1): "main", ("Labdarúgás", 101): "ou", ("Labdarúgás", 97): "ou", ("Labdarúgás", 89): "btts",
    ("Labdarúgás", 96): "ah",
    ("Jégkorong", 21): "1x2reg", ("Jégkorong", 601): "main", ("Jégkorong", 164): "ou", ("Jégkorong", 161): "ah",
    ("Kosárlabda", 2): "main", ("Kosárlabda", 108): "1x2reg", ("Kosárlabda", 114): "ou", ("Kosárlabda", 113): "ah",
    ("Tenisz", 9): "main", ("Tenisz", 168): "ou",
}


def kind_of(sport, m):
    """A piac fajtája: a négy fő sportágnál pontos térkép, a többinél általános szabály (győztes / 1X2, néhol vonalak)."""
    k = SUB.get((sport, m.get("marketSubType")))
    if k or sport in ("Labdarúgás", "Tenisz", "Kosárlabda", "Jégkorong"):
        return k
    n = str(m.get("marketName") or "")
    if n == "A mérkőzés győztese":
        return "main"
    if n == "1X2":
        return "1x2reg"
    if sport in ("Kézilabda", "Rögbi"):
        if n.startswith(("Gólszám ", "Pontszám ")):
            return "ou"
        if n.startswith("Hendikep ") and "(" not in n:
            return "ah"
    return None


def _post(path, body, timeout=30):
    r = requests.post(API + path, json=body, headers=H, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _get(path, timeout=30):
    r = requests.get(API + path, headers=H, timeout=timeout)
    r.raise_for_status()
    return r.json()


def parse_time(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def events(now, until, sports, max_pages=40):
    """A meccs előtti (nem élő) események a megadott sportágakból, now és until között.
    Visszaad: [{id, home, away, start, sport, tournament, name}]."""
    out, page = [], 1
    while page <= max_pages:
        body = {"search": None, "sportId": None, "competitionGroupId": None, "competitionOrAliasId": None, "eventTypes": [],
                "marketTypes": [], "minDate": None, "maxDate": None, "pageSize": 100, "page": page, "minOdds": None, "maxOdds": None}
        r = _post("/v2/tippmix/events?compatibility=v1", body)
        for e in r.get("events") or []:
            if e.get("isLive") or e.get("isOutright") or e.get("sportName") not in sports:
                continue
            st = parse_time(e["eventDate"])
            parts = [p.get("participantName") for p in e.get("eventParticipants") or []]
            if not (now < st <= until) or len(parts) != 2:
                continue
            out.append({"id": e["eventId"], "home": parts[0], "away": parts[1], "start": st, "sport": e["sportName"],
                        "tournament": e.get("competitionName"), "name": e.get("eventName"), "betradar": e.get("betradarId")})
        if page >= ((r.get("_meta") or {}).get("pageCount") or 1):
            break
        page += 1
    return out


def event_markets(event_id, sport, names=None):
    """{(fajta, vonal): {"home"/"draw"/"away"/"over"/"under"/"yes"/"no": odds}} – csak nyitott, egyesben is megtehető piacok.
    names[(fajta, vonal)][kulcs] = szelvény-útmutató (piac → kimenetel, kód)."""
    e = _get(f"/v2/tippmix/event/{event_id}?compatibility=v1").get("event") or {}
    if e.get("isLive"):
        return {}
    seen, out = set(), {}
    for g in e.get("marketGroups") or []:
        for m in g.get("markets") or []:
            if m.get("marketId") in seen:
                continue
            seen.add(m.get("marketId"))
            kind = kind_of(sport, m)
            if not kind or m.get("marketStatus") != 1 or (m.get("minCombiCount") or 1) > 1 or not m.get("channelRetailer", 1):
                continue
            spec = m.get("specialOddsValue") or ""
            try:
                line = float(spec) if spec else 0.0
            except ValueError:
                continue
            if kind not in ("ou", "ah"):
                line = 0.0
            outs = [o for o in m.get("outcomes") or [] if (o.get("fixedOdds") or 0) > 1]
            prices, tips = {}, {}
            for o in outs:
                no, nm = str(o.get("outcomeRealNo") or ""), str(o.get("outcomeName") or "")
                if kind == "ou":
                    key = "under" if nm.startswith("Kevesebb") else "over" if nm.startswith("Több") else None
                elif kind == "btts":
                    key = "yes" if nm == "Igen" else "no" if nm == "Nem" else None
                elif kind == "ah":
                    key = {"01": "home", "02": "away"}.get(no)
                elif m.get("outcomeCount") == 3 or len(m.get("outcomes") or []) == 3:
                    key = {"01": "home", "02": "draw", "03": "away"}.get(no)
                else:
                    key = {"01": "home", "02": "away"}.get(no)
                if key:
                    prices[key] = float(o["fixedOdds"])
                    tips[key] = f"{m.get('marketName')} → {nm} (esemény-kód {m.get('marketRealNo')}, tipp {int(no) if no.isdigit() else no})"
            if kind == "ah" and not ("home" in prices and "away" in prices):
                continue
            if prices and (kind, line) not in out:
                out[(kind, line)] = prices
                if names is not None:
                    names[(kind, line)] = tips
    return out
