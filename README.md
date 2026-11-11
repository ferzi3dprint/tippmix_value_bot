# Tippmix value-bot (szimuláció)

Félóránként összeveti a **Tippmixpro** oddsait a **Pinnacle** árrés nélküli (fair) áraival. Ha a Tippmix jobb oddsot ad, mint
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

## Beállítás

1. **Privát repó:** hozd létre a `tippmix_value_bot` repót, és töltsd fel ezeket a fájlokat.
2. **OddsPapi:** regisztrálj az oddspapi.io-n. Az API-kulcs a repó *Settings → Secrets and variables → Actions* alatt
   legyen `ODDSPAPI_KEY` néven.
3. **Telegram:** használhatod a piaci bot titkait (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`), vagy csinálhatsz neki
   külön botot a @BotFathernél.
4. **Dashboard:**
   - Hozz létre egy **nyilvános** `tippmix-dashboard` repót, és kapcsold be rajta a GitHub Pages-t (*Deploy from branch → main / root*).
   - Csinálj egy fine-grained tokent, amely csak erre a repóra kap *Contents: Read and write* jogot. Ez legyen `DASHBOARD_TOKEN`.
   - Adj meg egy jelszót `DASHBOARD_PASSWORD` néven.
   - Az oldalra csak titkosított adat kerül, ugyanúgy, mint a piaci dashboardnál.
5. **Indítás:** *Actions → Tippmix value-bot → Run workflow*. Ezután félóránként magától fut 7 és 23 óra között.

## API-kvóta

Egy futás kb. 2 kérés minden 10 bajnokságra (Tippmix + Pinnacle). Ehhez jön 6 óránként sportáganként 1 kérés
a bajnokságlista frissítésére, és meccsenként 1 kérés elszámoláskor. 30 bajnokság és félórás futás mellett ez kb. 200–250
kérés naponta, vagyis havonta több ezer. **Az ingyenes csomag ehhez kevés.** A bot a `/v4/account` alapján a havi keretet
egyenletesen osztja el a hónap napjaira, és ha a napi rész elfogy, kihagyja a keresést (az elszámolás tovább megy).
Kevesebb kérést a `scan.sports` szűkítésével vagy ritkább cronnal (`.github/workflows/bot.yml`) érhetsz el.

## GitHub Actions percek

Ha egy futásban van új ajánlás vagy steam, a futás 3 percet vár a végrehajthatóság-ellenőrzésig. Privát repónál a GitHub
havi ingyenes kerete 2000 perc. Félórás futással a bot nagyjából 1000–2000 percet használ havonta, ami szoros lehet. Három
lehetőség: a repó nyilvános (a fogadási napló nem érzékeny, a dashboard adata titkosított, és nyilvános repónál nincs
percdíj), vagy a `confirm_after_minutes` 2-re csökkentése, vagy ritkább cron.

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
| `report.py`, `notify.py` | Telegram-szövegek és küldés |
| `dashboard/` | titkosított statikus oldal (a piaci dashboard stílusában) |
| `data/` | napló, gyorsítótár, kvóta, futásnapló (a workflow visszacommitolja) |

## Fontos tudnivalók

- Az éles API-mezőneveket (`sportName`, `request_limit`, piac- és kimenetel-azonosítók) az OddsPapi dokumentációja alapján
  írtam meg, éles kulccsal nem teszteltem. Az első futások naplóját (*Actions*) és a `data/runs.json`-t érdemes megnézni.
  Ha valami nem stimmel, a `bot.py` / `oddsapi.py` kis igazítást igényelhet.
- A Tippmix és a Pinnacle egyes piacain eltérhetnek a szabályok (pl. hosszabbítás, tenisz-feladás). A `max_ev` szűrő
  a legtöbb ilyen hamis „value”-t kiszűri, de érdemes időnként ránézni a nagy EV-jű tételekre.
- A szimuláció feltételezi, hogy a jelzett odds mellett a fogadás valóban megtehető lett volna. A valóságban az odds
  közben elmozdulhat, és a Tippmix limitálhatja a tétet.
