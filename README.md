# TogKart

Alle persontog i Norge i sanntid, fargelagt etter punktlighet, tegnet på et
mørkt kart. Python-backend mot Entur, MapLibre-frontend, ingen rammeverk i
nettleseren.

Søk finner tog og stasjoner. Popup viser linje, tognummer, forsinkelse og — der
det finnes — hvilket togsett som kjører. Nederst til venstre ruller
driftsmeldingene fra Vy og Go-Ahead; hovrer du over en, markeres det den
gjelder i kartet, og klikker du, flytter kartet seg dit.

## Kom i gang

```powershell
cd C:\Users\Vegard\Documents\Kodeprosjekter\togkart
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env             # fyll inn ET_CLIENT_NAME
python prober\smoketest.py         # <- kjor denne FORST
uvicorn app:app --reload
```




Åpne <http://127.0.0.1:8000>.

**Alt kjøres fra prosjektroten.** Står du i feil mappe, feiler uvicorn med
`Directory 'static' does not exist`, og probene med
`ModuleNotFoundError: No module named 'entur'`.

`.env` må inneholde `ET_CLIENT_NAME`. Uten den svarer Entur, men uidentifisert.

`smoketest.py` svarer på de to spørsmålene appen hviler på: finnes det tog i
Entur-feeden, og er `delay` utfylt? Får du «Cannot query field», er det et
feltnavn i `QUERY` i `entur.py` som må fjernes — utforsk skjemaet i
<https://api.entur.io/graphql-explorer/vehicles-v2> eller kjør
`python sjekk.py skjema VehicleUpdate`.

## Kommandoer

```powershell
python sjekk.py alle          # faste helsesjekker — start her når noe ser rart ut
python sjnord.py              # hvilke tog mangler posisjon og beregnes
python avvik.py               # hva nyhetsstripa ville rullet akkurat nå
python analyse.py             # operatørrangering og rushtidsprofil, uten nett
python flaskehals.py          # hvor togene mister tid, uten nett
python vedlikehold.py --status  # hva ligger i historikk.db
python prober/sjekk_posisjon.py  # stemmer posisjonene med rutetabellen?
python prober/sjekk_headere.py   # sikkerhetsheadere og CSP, uten nett
python prober/sjekk_helse.py     # /api/health som overvåkingsmål, uten nett
```

Hele katalogen — prober, selvtester og hvilket skript som svarer på hvilket
symptom — står i [docs/feilsoking.md](docs/feilsoking.md).

## Prosjektstruktur

```
togkart/
├── app.py                  FastAPI: cacher, /api/trains, /api/search, /api/statistikk
├── entur.py                Vehicle Positions: GraphQL → GeoJSON, dobbeltsett
├── sjnord.py               Journey Planner: rutedata → beregnede posisjoner
├── punktlighet.py          Journey Planner: forsinkelse for GPS-operatørene
├── avvik.py                SIRI-SX: driftsmeldinger → nyhetsstripa
├── sporgeometri.py         Polylinjer, avstand langs trasé, snapping
├── materiell.py            Kjøretøy-ID → togsett
├── historikk.py            Logger endrede tog til historikk.db (SQLite)
├── vedlikehold.py          Døgnrullup og rotasjon av historikken
├── strupe.py               Ratebegrensning, per klient og mot Entur-kvoten
├── analyse.py              Leser historikk.db: operatørrangering, rushtidsprofil
├── flaskehals.py           Leser historikk.db: hvor togene mister tid
├── jernbanenett.py         Ruter en strekning gjennom spornettet
├── lagbaner.py             Byggeskript: OSM/Entur → hovedbaner.geojson
├── lagjernbanenett.py      Byggeskript: OSM → jernbanenett.geojson
├── lagstasjoner.py         Byggeskript: Entur → stasjoner.geojson
├── sjekk.py                Faste helsesjekker, åtte underkommandoer
├── prober/                 Regresjonstester og engangsprober; `felles.py` er Entur-kallet de deler
├── static/                 index.html, app.js, app.css, theme.css og tre GeoJSON-filer
│   └── vendor/             MapLibre, hentet uendret og servert av oss selv
├── docs/                   Dokumentasjon — se under
└── drift/                  Cloudflare, tunnel og systemd
```

De tre GeoJSON-filene i `static/` ligger i git, så du trenger ikke kjøre
byggeskriptene for å komme i gang. Kjør dem når du vil oppdatere banegeometrien,
spornettet eller stasjonslista — og **ikke** i produksjon.

De to OSM-filene er ikke to versjoner av det samme. `hovedbaner.geojson` er et
**visningslag**: seksten navngitte baner, forenklet til 150 m, som skal se
riktige ut på zoom 6. `jernbanenett.geojson` er et **rutingsnett**: alt kjørbart
hovedspor i Norge, forenklet til 5 m og delt ved sporvekslene, slik at det kan
gås gjennom som en graf. Visningslaget mangler blant annet Hovedbanen,
Askerbanen, Follobanen og Østfoldbanens østre linje, og kan derfor ikke brukes
til ruting.

## Dokumentasjon

| Fil | Når du trenger den |
|---|---|
| [docs/arkitektur.md](docs/arkitektur.md) | Dataflyt, cachestrategi, hvilken fil som har hvilket ansvar, hva tallene måler |
| [docs/kartlag.md](docs/kartlag.md) | Kartlagene: rekkefølge, zoomgrenser, stasjoner, klynger, varmekart |
| [docs/feilsoking.md](docs/feilsoking.md) | Runbook: hvilket skript svarer på hva, og hva helsesjekkene skal si |
| [docs/sikkerhet.md](docs/sikkerhet.md) | Sikkerhetsgjennomgangen, med målinger |
| [docs/undersokelser.md](docs/undersokelser.md) | Post mortems: hvordan hver feil ble målt fram |
| [docs/erfaringer.md](docs/erfaringer.md) | Tjueen lærdommer om datakvalitet |
| [drift/](drift/) | Cloudflare Tunnel, systemd, backup |
| [CHANGELOG.md](CHANGELOG.md) | Hva som er gjort, når |

Åpne oppgaver og feil ligger som **issues**, ikke i markdown.

Den gamle `STATUS.md` — som var alt dette på én gang — ligger uendret i
[docs/arkiv/](docs/arkiv/) for den som skal etterprøve en henvisning.

## Målte og beregnede posisjoner

Ikke alle tog i kartet har en GPS-posisjon bak seg. **Vehicle Positions mangler
posisjon for en stor del av turene Journey Planner kjenner** — ikke bare SJ
sine, som aldri publiserer GPS, men også mange av Vys egne. Målt 21. august lå
hele linjer ute: L2 med sju av sju turer, RE10 med fire.

De togene regnes derfor ut fra rutetid og sporgeometri, på samme måte som
SJ-togene alltid har blitt, og tegnes som **ring i stedet for fylt prikk**.
Kartet gikk fra rundt 105 til rundt 135 tog da det ble slått på.

En beregnet posisjon er ikke en måling. Den bærer `computed: true`,
`positionMethod` (`track` eller `straight`) og `computedReason` — `ingen-gps`
for SJ, `mangler-posisjon` for resten — og holdes utenfor punktlighets-
statistikken. Du skal kunne se på dataene hvor sikre de er. Detaljene står i
[docs/arkitektur.md](docs/arkitektur.md).

## Ett forbehold om tallene

`delay` fra Vehicle Positions er ikke til å stole på for alle tog. Målt mot
Journey Planner spriker de to kildene med opptil 19–31 minutter for enkelte tog,
og **i begge retninger**. Kilden er derfor allerede byttet: `punktlighet.py`
henter forsinkelsestall fra Journey Planner for Vy, Flytoget og Go-Ahead, og
hvert tog merkes med `delaySource`. `delay` er reserven, ikke hovedkilden.

Punktligheten i historikkpanelet måles **underveis** og ikke ved endestasjon,
med **én** terskel på 240 sekunder og ikke bransjens to. Ikke sett tallene opp
mot Bane NORs uten å nevne det. Begrunnelsen står i
[docs/arkitektur.md](docs/arkitektur.md).

## Lisens

Koden er MIT — se [LICENSE](LICENSE).

MapLibre GL JS i `static/vendor/` er ikke vår kode. Den er 3-Clause BSD, hentet
uendret fra utgiveren og sjekket inn med sjekksummer — se
[static/vendor/README.md](static/vendor/README.md).

Dataene er ikke våre: Entur-data er åpne under NLOD. Bakgrunnskartet krever
attribusjon til OpenStreetMap og CARTO, og den vises automatisk nede til høyre.
Banegeometrien i `hovedbaner.geojson` kommer fra OpenStreetMap (ODbL) med Entur
som reserve; hver bane er merket med `kilde`. Spornettet i
`jernbanenett.geojson` kommer fra OpenStreetMap (ODbL) alene.
