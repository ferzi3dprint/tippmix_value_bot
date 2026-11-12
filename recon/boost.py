"""Oddsrakéta (Tippmixpro odds-emelés, discipline 101): bajnokságok, meccsek, piacok, kimenetelek, oddsok."""
import collections, datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tippmixfeed

with tippmixfeed.Session() as s:
    tours = [r for r in (s.dump("/sports/2901/hu/tournaments/101") or []) if r.get("_type") == "TOURNAMENT"]
    print("Oddsrakéta-bajnokságok:", [(t["id"], t.get("name"), t.get("numberOfUpcomingMatches")) for t in tours])
    for t in tours:
        for topic in (f"/sports/2901/hu/{t['id']}/tournament-odds/7/1", f"/sports/2901/hu/{t['id']}/tournament-odds/1/1",
                      f"/sports/2901/hu/{t['id']}/tournament-odds/0/1"):
            recs = s.dump(topic) or []
            if not recs:
                continue
            print(f"\n== {t.get('name')} [{topic.split('/')[-3:]}]:", dict(collections.Counter(r.get("_type") for r in recs)))
            outs = {r["id"]: r for r in recs if r.get("_type") == "OUTCOME"}
            offs = {r["outcomeId"]: r for r in recs if r.get("_type") == "BETTING_OFFER"}
            rel = collections.defaultdict(list)
            for x in recs:
                if x.get("_type") == "MARKET_OUTCOME_RELATION":
                    rel[x["marketId"]].append(x["outcomeId"])
            for mk in [r for r in recs if r.get("_type") == "MARKET"]:
                print("   PIAC", mk.get("bettingTypeId"), mk.get("eventPartId"), mk.get("name"), "|",
                      [(outs.get(o, {}).get("translatedName"), outs.get(o, {}).get("code"), offs.get(o, {}).get("odds")) for o in rel.get(mk["id"], [])])
            ex = next(iter(outs.values()), {})
            print("   OUTCOME minta:", json.dumps(ex, ensure_ascii=False)[:700])
            print("   TOURNAMENT:", json.dumps(t, ensure_ascii=False)[:500])
            break
    n = 0
    for t in []:
        ms = [r for r in (s.dump(f"/sports/2901/hu/matches/{t['id']}") or []) if r.get("_type") == "MATCH"]
        for m in ms[:6]:
            print(f"\n== {t.get('name')} | MATCH: {m.get('name')} | start {dt.datetime.fromtimestamp(m['startTime']/1000)} | "
                  f"home={m.get('homeParticipantName')} away={m.get('awayParticipantName')} venue={m.get('venueName')} cat={m.get('categoryName')}")
            for grp in ("", "market-group/Main_BannerMatrix"):
                recs = s.dump(f"/sports/2901/hu/{m['id']}/match-odds/{grp}") or []
                if not recs:
                    continue
                outs = {r["id"]: r for r in recs if r.get("_type") == "OUTCOME"}
                offs = {r["outcomeId"]: r for r in recs if r.get("_type") == "BETTING_OFFER"}
                rel = collections.defaultdict(list)
                for x in recs:
                    if x.get("_type") == "MARKET_OUTCOME_RELATION":
                        rel[x["marketId"]].append(x["outcomeId"])
                for mk in [r for r in recs if r.get("_type") == "MARKET"][:5]:
                    print("   PIAC", mk.get("bettingTypeId"), mk.get("name"), "|", [(outs.get(o, {}).get("translatedName"), outs.get(o, {}).get("code"),
                          offs.get(o, {}).get("odds")) for o in rel.get(mk["id"], [])])
                if n == 0:
                    print("   MATCH teljes:", json.dumps(m, ensure_ascii=False)[:900])
                    o1 = next(iter(outs.values()), {})
                    print("   OUTCOME teljes:", json.dumps(o1, ensure_ascii=False)[:600])
                n += 1
                break
