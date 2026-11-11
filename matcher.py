"""Tippmix ↔ OddsPapi (Pinnacle) meccspárosítás: kezdési idő + csapat-/játékosnév hasonlóság.

A Tippmix magyarul írja az országokat, a teniszezőket „Keresztnév Vezetéknév”, a Pinnacle „Vezetéknév, Keresztnév”
formában adja; a tartalékcsapat a Tippmixnél „2”, máshol „B / II / Reserve”. Ezeket normalizáljuk, és
szó-szintű (sorrendfüggetlen) hasonlóságot számolunk. A téves párosítás ellen a bot az oddsok egyezését is ellenőrzi
(lásd odds_consistent).
"""
import datetime as dt
import difflib
import re
import unicodedata

COUNTRIES_HU = {
    "magyarorszag": "hungary", "ausztria": "austria", "nemetorszag": "germany", "spanyolorszag": "spain", "olaszorszag": "italy",
    "franciaorszag": "france", "anglia": "england", "skocia": "scotland", "wales": "wales", "irorszag": "ireland",
    "eszak irorszag": "northern ireland", "portugalia": "portugal", "hollandia": "netherlands", "belgium": "belgium",
    "svajc": "switzerland", "lengyelorszag": "poland", "csehorszag": "czech republic", "szlovakia": "slovakia",
    "szlovenia": "slovenia", "horvatorszag": "croatia", "szerbia": "serbia", "romania": "romania", "bulgaria": "bulgaria",
    "gorogorszag": "greece", "torokorszag": "turkey", "ukrajna": "ukraine", "oroszorszag": "russia",
    "feheroroszorszag": "belarus", "litvania": "lithuania", "lettorszag": "latvia", "esztorszag": "estonia",
    "finnorszag": "finland", "svedorszag": "sweden", "norvegia": "norway", "dania": "denmark", "izland": "iceland",
    "feroer szigetek": "faroe islands", "albania": "albania", "eszak macedonia": "north macedonia",
    "bosznia hercegovina": "bosnia and herzegovina", "montenegro": "montenegro", "koszovo": "kosovo", "moldova": "moldova",
    "grúzia": "georgia", "grucia": "georgia", "gruzia": "georgia", "ormenyorszag": "armenia", "azerbajdzsan": "azerbaijan",
    "kazahsztan": "kazakhstan", "ciprus": "cyprus", "malta": "malta", "luxemburg": "luxembourg", "andorra": "andorra",
    "san marino": "san marino", "liechtenstein": "liechtenstein", "gibraltar": "gibraltar", "izrael": "israel",
    "egyesult allamok": "usa", "usa": "usa", "kanada": "canada", "mexiko": "mexico", "brazilia": "brazil",
    "argentina": "argentina", "uruguay": "uruguay", "kolumbia": "colombia", "chile": "chile", "peru": "peru",
    "ecuador": "ecuador", "paraguay": "paraguay", "venezuela": "venezuela", "bolivia": "bolivia", "japan": "japan",
    "del korea": "south korea", "kina": "china", "ausztralia": "australia", "uj zeland": "new zealand",
    "szaud arabia": "saudi arabia", "egyiptom": "egypt", "marokko": "morocco", "algeria": "algeria", "tunezia": "tunisia",
    "nigeria": "nigeria", "ghana": "ghana", "szenegal": "senegal", "elefantcsontpart": "ivory coast", "kamerun": "cameroon",
    "del afrika": "south africa", "burkina faso": "burkina faso", "zambia": "zambia", "irán": "iran", "iran": "iran",
    "irak": "iraq", "katar": "qatar", "jordania": "jordan", "india": "india", "indonezia": "indonesia", "thaifold": "thailand",
    "vietnam": "vietnam",
}
STOP = {"fc", "cf", "sc", "ac", "fk", "sk", "nk", "cd", "ce", "ud", "ca", "if", "bk", "club", "de", "da", "del", "la", "el",
        "afc", "cfc", "sv", "tsv", "vfl", "vfb", "as", "us", "ss", "ssd", "sd", "fb", "futbol", "football", "calcio",
        "wfc", "lfc", "w", "women", "ladies", "kvinner", "noi", "afbc", "spor", "kulubu", "the", "and", "pe", "rj", "sp",
        "united", "utd", "city", "town"}
RESERVE = {"2", "ii", "b", "reserve", "reserves", "res"}


def _ascii(s):
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()


def tokens(name):
    s = _ascii(name)
    s = re.sub(r"\bu\s?(1[5-9]|2[0-3])\b|^u(1[5-9]|2[0-3])(?=[a-z])", " ", s)        # korosztály: U19Spanyolország
    s = re.sub(r"[.\-/,()']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for hu, en in sorted(COUNTRIES_HU.items(), key=lambda kv: -len(kv[0])):
        hu_a = _ascii(hu)
        if s == hu_a or s.startswith(hu_a + " ") or s.endswith(" " + hu_a):
            s = s.replace(hu_a, en)
    out = set()
    for t in s.split():
        if t in RESERVE:
            out.add("#res")
        elif t not in STOP and len(t) > 1:
            out.add(t)
    return out


def team_sim(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    hit = 0.0
    for t in small:
        best = max(difflib.SequenceMatcher(None, t, u).ratio() for u in big)
        hit += 1 if best >= 0.85 else (0.5 if best >= 0.7 else 0)
    tok = hit / len(small)
    if ("#res" in ta) != ("#res" in tb):          # tartalék vs. első csapat: büntetés
        tok -= 0.3
    seq = difflib.SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return max(tok, seq)


def pair_score(tm, fx):
    return min(team_sim(tm["home"], fx["participant1Name"]), team_sim(tm["away"], fx["participant2Name"])) * 0.4 + \
        (team_sim(tm["home"], fx["participant1Name"]) + team_sim(tm["away"], fx["participant2Name"])) / 2 * 0.6


def match_fixtures(tm_matches, fixtures, max_minutes=10, threshold=0.72):
    """Mohó, egy-egyértelmű párosítás. tm_matches: [{id, home, away, start(datetime)}], fixtures: OddsPapi fixture-ök.
    Visszaad: {tippmix_id: (fixture, pontszám)}."""
    cands = []
    for m in tm_matches:
        for f in fixtures:
            fst = dt.datetime.fromisoformat(str(f["startTime"]).replace("Z", "+00:00"))
            if abs((fst - m["start"]).total_seconds()) > max_minutes * 60:
                continue
            sc = pair_score(m, f)
            if sc >= threshold:
                cands.append((sc, m["id"], f["fixtureId"], f))
    out, used_tm, used_fx = {}, set(), set()
    for sc, mid, fid, f in sorted(cands, key=lambda c: -c[0]):
        if mid in used_tm or fid in used_fx:
            continue
        used_tm.add(mid)
        used_fx.add(fid)
        out[mid] = (f, round(sc, 3))
    return out


def odds_consistent(soft_odds, sharp_odds, max_diff=0.15):
    """Téves párosítás elleni védelem: a két iroda árrés-mentes valószínűségei kimenetelenként legfeljebb max_diff-fel
    térhetnek el (egy valódi value 2–10 százalékpont, egy rossz párosítás jellemzően sokkal több)."""
    if not soft_odds or not sharp_odds or len(soft_odds) != len(sharp_odds):
        return False
    ps = [1 / o for o in soft_odds]
    ph = [1 / o for o in sharp_odds]
    ps = [p / sum(ps) for p in ps]
    ph = [p / sum(ph) for p in ph]
    return max(abs(a - b) for a, b in zip(ps, ph)) <= max_diff
