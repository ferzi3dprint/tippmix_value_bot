# Tippmix value-bot (szimuláció)

20 percenként összeveti a **Tippmixpro** oddsait a **Pinnacle** árrés nélküli (fair) áraival. Ha a Tippmix jobb oddsot ad, mint
amit a valódi esély indokol (pozitív várható érték), **szimulált fogadást** rak rá a matematikailag indokolt téttel. Telegramon
jelzi az új ajánlásokat, este napi kimutatást küld, és egy jelszóval védett dashboardon mutatja az eredményeket.

**A bot nem fogad valódi pénzzel.** Az a célja, hogy kiderüljön, van-e valódi, mérhető előny, mielőtt bárki pénzt tenne bele.
Nem fogadási vagy befektetési tanács.

## Hogyan dönt

1. **Fair ár:** a Pinnacle egy piacának összes kimenetelét árrés-mentesíti (alapból *power* módszer, ami a favorit–esélytelen
   torzítást is kezeli). Ha a Pinnacle árrése túl nagy (`max_sharp_overround`), a piacot kihagyja.
2. **EV** = Tippmix-odds × fair valószínűség − 1.
3. **Szűrők:** odds-sáv (alapból 1,30–2,50), odds-sávonkénti minimális EV (alacsony oddsnál kisebb küszöb is elég, mert
   kisebb a szórás), gyanúsan nagy EV kihagyása (`max_ev`, többnyire adathiba), meccsenként legfeljebb 1 fogadás,
   a kezdés előtt legalább 20 perccel.
4. **Tét:** negyed-Kelly a szimulált tőkéből, legfeljebb a tőke 3%-a, 50 Ft-ra kerekítve; a nyitott fogadások együtt
   legfeljebb a tőke 35%-a.
5. **CLV:** amíg a meccs nem kezdődik el, minden futás frissíti a Pinnacle fair árát. A kezdés előtti utolsó árhoz mért
   EV a CLV. Ez néhány hét alatt megmutatja, valódi-e az előny, akkor is, ha a profit még a szórás miatt ugrál.
6. **⚡ Steam (odds-mozgás):** a bot megjegyzi a Pinnacle fair árait. Ha egy kimenetel fair valószínűsége az elmúlt 3 órában
   legalább 4%-kal (relatívan) nőtt, az steam: valami hír (sérülés, kezdőcsapat) mozgatta a piacot, a Tippmix pedig gyakran
   késve követi. Ezek a fogadások ⚡ jelet kapnak az üzenetben és a dashboardon. A futás végén a bot meccsenként egy
   kéréssel újranézi a steam-es meccseket, mert a mozgás gyakran folytatódik.
7. **Végrehajthatóság:** az ajánlás után 3 perccel a bot újra lekéri a Tippmix-oddsot, mintha akkor nyitnád meg az appot.
   - Ha az odds nem esett: a fogadás marad.
   - Ha esett, de még megéri: az új, rosszabb oddsszal számol.
   - Ha már nem éri meg, vagy a piac lezárt: „nem volt megtehető”, kimarad az eredményből, de a statisztikában látszik
     (végrehajthatósági arány, átlagos csúszás).
8. **Elszámolás:** a kezdés után 2,5 órától az OddsPapi `settlements` végpontjából (nyert / vesztett / fél / visszajár).

Minden szabály a `config.yaml`-ban állítható.

## Üzenetek

- **🎯 Új ajánlások:** futásonként egy üzenet, ha van új szimulált fogadás. Tartalma: meccs, tipp, Tippmix- és fair odds,
  EV, tét; a ⚡ steam-es tételeknél a gyorsaság számít.
- **⌛ Nem volt megtehető:** ha egy ajánlásnál 3 perc múlva már esett az odds.
- **📊 Napi kimutatás** 23 órától: a nap lezárt fogadásai (✅/❌), találati arány a várthoz képest, napi profit és ROI,
  összesítés indulás óta, végrehajthatóság, CLV, steam vs. normál összevetés, bontás odds-sávonként, a dashboard linkje.

## Ingyenes üzemmód (alapbeállítás)

Az OddsPapi ingyenes csomagja havi 250 kérés. A bot ezt így osztja be (`api.mode: free`):

| Mire | Végpont | Költség |
|---|---|---|
| Tippmix- és Pinnacle-oddsok, steam, végrehajthatóság, CLV | `/v4/historical-odds` | **ingyenes** (5 mp várakozás kérésenként) |
| Napi meccslista | `/v4/fixtures` | sportáganként napi 1 kérés (2 sport ≈ 60/hó) |
| Elszámolás | `/v4/settlements` | szimulált fogadásonként 1 kérés (napi max. 5 ≈ 150/hó) |
| Piac- és sportlista | `/v4/markets`, `/v4/sports` | hetente/havonta 1 kérés |
| Keret lekérdezése | `/v4/account` | ingyenes |

Ez havonta kb. 220 kérés. A bot a `/v4/account` alapján figyeli a keretet, és ha fogyóban van, a régi meccslistával
dolgozik tovább, az elszámolást pedig elhalasztja.

- **Napi 5 szimulált fogadás** (`free.max_bets_per_day`): a nap legjobb EV-jű jelzései. Az elszámolás ennyit enged.
- **📡 Árnyék-jelzések:** minden value-jelzés CLV-jét méri, akkor is, ha nem lett belőle fogadás. Ez díjmentes, és
  naponta jóval több adatot ad, mint 5 fogadás. **Ez a fő jelző arra, hogy van-e valódi előny.**
- Futásonként legfeljebb 90 meccset néz meg. A 6 órán belül kezdődőket minden futásban, a távolabbiakat 90 percenként.

Ha később fizetős csomagra váltasz: `api.mode: paid`, és a `free.max_bets_per_day` korlát megszűnik.

## Beállítás (nyilvános repó)

1. Hozz létre egy **nyilvános** `tippmix_value_bot` repót, és töltsd fel ezeket a fájlokat. Nyilvános repóban
   a GitHub Actions percek ingyenesek, így a bot 20 percenként futhat.
2. **OddsPapi:** ingyenes regisztráció az oddspapi.io-n. A kulcs a repó *Settings → Secrets and variables → Actions*
   alatt legyen `ODDSPAPI_KEY` néven. A titkok nyilvános repóban is titkosak maradnak.
3. **Telegram:** `TELEGRAM_BOT_TOKEN` és `TELEGRAM_CHAT_ID` (átveheted a piaci botodtól).
4. **Dashboard:** nem kell hozzá külön repó vagy token. A bot a repó `gh-pages` ágára teszi ki.
   - Az első futás után: *Settings → Pages → Deploy from a branch → gh-pages / (root)*.
   - Címe: `https://ferzi3dprint.github.io/tippmix_value_bot/`
   - Ha megadsz egy `DASHBOARD_PASSWORD` titkot, az oldal jelszóval védett lesz. Nyilvános repóban viszont a
     `data/` mappa (a fogadási napló) amúgy is látható, ezért a jelszó elhagyható.
5. **Indítás:** *Actions → Tippmix value-bot → Run workflow*. Ezután 20 percenként magától fut, reggeltől éjfélig.

Megjegyzés: a GitHub 60 nap inaktivitás után leállítja az időzített futásokat, de a bot minden futásnál commitol,
így ez nem fordul elő.

## Helyi kipróbálás kulcs nélkül

```bash
pip install -r requirements.txt pytest
python -m pytest -q tests                  # tesztek
python demo.py 21                          # 21 nap gyorsított szimuláció kitalált adatokkal
DATA_DIR=demo_data python -m dashboard.build --plain demo_site   # demó dashboard: demo_site/index.html
MOCK_API=1 DRY_RUN=1 python bot.py         # egy futás szintetikus adattal, Telegram helyett kiírás
```

A demó számai semmit nem mondanak a valódi Tippmixről, csak a működést mutatják be.

## Fájlok

| Fájl | Feladat |
|---|---|
| `bot.py` | egy futás: elszámolás → odds → value-keresés → szimulált fogadás → értesítés → napi kimutatás |
| `valuefinder.py` | devig, EV, szűrők, Kelly-tét |
| `ledger.py` | fogadási napló (`data/bets.json`), tőke, összesítők |
| `oddsapi.py` / `mockapi.py` | OddsPapi v4 kliens / szintetikus utánzat a demóhoz és a tesztekhez |
| `freefeed.py` | a díjmentes historical-odds feldolgozása (aktuális odds, steam az előzményből) |
| `report.py`, `notify.py` | Telegram-szövegek és küldés |
| `dashboard/` | titkosított statikus oldal (a piaci dashboard stílusában) |
| `data/` | napló, gyorsítótár, kvóta, futásnapló (a workflow visszacommitolja) |

## Fontos tudnivalók

- Az éles API-mezőneveket (`sportName`, `request_limit`, piac- és kimenetel-azonosítók) és azt, hogy a historical-odds
  a még el nem kezdődött meccsekre is ad adatot és ingyenes, az OddsPapi dokumentációja alapján építettem be;
  éles kulccsal nem teszteltem. Az első futások naplóját (*Actions*) és a `data/runs.json`-t érdemes megnézni.
  Ha valami nem stimmel, a `bot.py` / `oddsapi.py` kis igazítást igényelhet.
- A Tippmix és a Pinnacle egyes piacain eltérhetnek a szabályok (pl. hosszabbítás, tenisz-feladás). A `max_ev` szűrő
  a legtöbb ilyen hamis „value”-t kiszűri, de érdemes időnként ránézni a nagy EV-jű tételekre.
- A szimuláció feltételezi, hogy a jelzett odds mellett a fogadás valóban megtehető lett volna. A valóságban az odds
  közben elmozdulhat, és a Tippmix limitálhatja a tétet.
