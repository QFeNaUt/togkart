# Sikkerhet

Full gjennomgang 20.–21. august, testet mot en kjørende server. Dette er
funnene og målingene. Det som skal settes opp i Cloudflare står i
[../drift/cloudflare.md](../drift/cloudflare.md).

Åpne sikkerhetsoppgaver ligger som issues med etiketten `sikkerhet`.

---

Konklusjonen først: **angrepsflaten er liten fordi appen ikke har noe å stjele.**
Ingen innlogging, ingen brukerkontoer, ingen personopplysninger, ingen
informasjonskapsler, og ingenting en besøkende kan skrive til. Hele klassen av
sårbarheter som handler om å bli en annen bruker — sesjonskapring, IDOR,
rettighetseskalering — finnes rett og slett ikke her. Det som gjenstår er
tilgjengelighet, forsyningskjede og det ene stedet der data ble behandlet som
kode.

## Det som holder

| | Testet slik |
|---|---|
| **Ingen SQL-injeksjon.** Alle verdier er parameterbundet. Den eneste f-strengen i SQL er `ALTER TABLE ... ADD COLUMN {navn}`, og `navn` kommer fra `_TILLEGGSKOLONNER` — en modulglobal dict, ikke fra nettet. | Lest gjennom, og `'; DROP TABLE observasjoner; --` sendt gjennom `/api/search` (200, tomt resultat — strengen når aldri SQL) |
| **Path traversal blokkert.** Starlette sin `StaticFiles` løser opp stien og nekter å gå ut av mappa. | Sju varianter mot serveren: `/../app.py`, `/..%2fapp.py`, `/%2e%2e/%2e%2e/.env`, `/static/../app.py`, `/.env`, `/historikk.db`, `/app.py` — alle 404 |
| **CORS er default-deny.** Ingen `CORSMiddleware` er lagt inn, så nettleseren nekter andre nettsteder å lese API-et på en besøkendes vegne. | Lest: ingen middleware registrert |
| **Inndata valideres.** `dager` klemmes til 1–90, `limit` til 1–20, og FastAPI avviser ikke-tall selv. | `dager=0`, `-5`, `99999` → 200 med klemt verdi; `dager=abc` → 422 |
| **Ingen hemmeligheter i koden eller i git.** `.env` og `*.db` står i `.gitignore`. `ET_CLIENT_NAME` er en identifikator, ikke en nøkkel — det står i `.env.example` også. | Lest `.gitignore` |
| **Feilmeldinger lekker ikke internt.** Endepunktene fanger bredt og svarer med en setning på norsk, ikke med stacktrace. | `/api/route/tull` → 404 med melding; statistikk-feil → 503 med generisk tekst |
| **Backend snakker med Entur, ikke nettleseren.** Ett sted å cache og strupe, og besøkendes IP-adresser når aldri Entur. | Arkitektur |

## Det som ikke holder

Sortert etter alvorlighet.

**1. XSS gjennom oppstrømsdata. Funnet, bevist og fikset 20. august.**

`showPopup()`, `showResults()` og `histRad()` limte felt fra Entur rett inn i
`innerHTML` og `setHTML()`. Et linjenavn på formen

```
<img src=x onerror="...">
```

kjørte da vilkårlig JavaScript i nettleseren til alle som hadde kartet oppe.
Bevist i nettleseren: `window.__xss` gikk fra 0 til 1 gjennom `lineName`,
gjennom et søketreff og gjennom `strekning` i rushlista.

Entur er en kilde vi stoler på, og dette har aldri skjedd. Men tilliten var
ikke poenget — feilen var at et API-svar ble behandlet som markup. Prosjektet
sier selv, om og om igjen, at data ikke er det samme som sannhet; her ble data
heller ikke skilt fra kode.

Rettet med `esc()` i `app.js`, påført hvert eneste sted der noe utenfra går inn
i HTML. Verifisert på nytt etterpå: nyttelasten står nå som synlig tekst,
ingen `<img>` opprettes, og `window.__xss` blir stående på 0.

**2. ~~Ingen ratebegrensning.~~ Løst 21. august — men ikke der dette avsnittet
sa den skulle løses.**

Punktet sto her med to utslag og én anvisning: «riktig sted å løse det er i
reverse-proxyen foran, ikke i appen — `limit_req` i nginx eller `rate_limit`
i Caddy». Anvisningen var feil, og den var feil om noe som ikke sto skrevet
noe sted i dette dokumentet: **det finnes ingen reverse proxy.** Appen skal
stå der stromkart.no står, og der går trafikken

```
nettleser -> Cloudflare (kant) -> Cloudflare Tunnel -> cloudflared -> uvicorn
```

`cloudflared` er en utgående tunnel. Den har ingen `limit_req`, ingen
headerkonfigurasjon og ingen cache. Hele resonnementet om reverse-proxyen —
her, under «Enklest å forbedre», og i punkt 5 og 6 av «Hva som skal til» —
beskrev en topologi prosjektet ikke har.

Løsningen ble derfor delt i to, og delingen er verdt å forstå fordi de to
halvdelene verner mot forskjellige ting:

**Per klient — rettferdighet. Ligger i Cloudflare.** Tre WAF Rate Limiting
Rules, ferdig formulert i `drift/cloudflare.md`: 20/min på `/api/search`,
30/min på statistikken, 120/min på resten. Struping på kanten koster deg
verken båndbredde eller CPU, og Cloudflare ser den ekte IP-adressen uten at
noen må tro på en header. `strupe.py` har de samme grensene som bakstopper —
en regel i et dashbord er ikke en garanti i koden.

**Globalt mot Entur — kvotevern. Kan ikke ligge i Cloudflare.** Dette er
innsikten som kom ut av arbeidet, og den var ikke synlig da punktet ble
skrevet:

> Kvoten hos Entur henger på `ET_CLIENT_NAME`, ikke på den besøkendes IP.

En per-IP-regel sier «du får 20 søk i minuttet». Hundre adresser som hver
holder seg pent innenfor gir 2000 søk i minuttet ut av `/api/search` — alle
signert med ditt klientnavn, alle helt innenfor regelen, ingen av dem
blokkert av noe som teller per IP. Da er det du som blir ratebegrenset av
Entur, og søket slutter å virke for alle. Et botnett trenger ikke være
ondsinnet for å få det til; det holder at det er distribuert.

En per-IP-grense kan ikke uttrykke «til sammen». Derfor ligger det en bøtte
i `strupe.py` som ikke teller besøkende i det hele tatt, bare utgående kall
til geocoderen: 60 i minuttet for hele appen. Går den tom, svarer
`/api/search` 503 og Entur får være i fred.

Målt med `prober/sjekk_strupe.py`, sjekk 4 — hundre adresser med fem søk
hver:

| | Slapp gjennom |
|---|---|
| Per IP (Cloudflare eller `GRENSER`) | **500 av 500** — alle er innenfor |
| Mot Entur (`ENTUR_GEOKODER`) | **30** |

Første rad er hva et per-IP-tak alene ser. Andre rad er hva Entur faktisk
får.

Det andre utslaget i det opprinnelige punktet — at `/api/statistikk/*` er
billig å be om og dyr å svare på — er dekket av `tung`-grensen (30/min,
klump 10) og av at databasen nå har et tak på størrelsen (punkt 6).

Og motprøven, som er den viktigste: **tre faner som laster kartet og poller
i to minutter møter null 429.** 60 forespørsler, alle 200. En strupe som
rammer vanlige folk er en feil selv om den «virker» — se sjekk 1 i proben.

**3. ~~Ingen sikkerhetsheadere.~~ Løst 23. august — i appen, ikke i proxyen.**

Punktet målte svaret fra `/` og fant bare `server`, `content-type`, `etag` og
`last-modified`. Ingen `Content-Security-Policy`, ingen
`X-Content-Type-Options`, ingen `Referrer-Policy`, ingen `frame-ancestors`.

Avsnittet sa at det «koster ingenting å legge på i proxyen», og pekte på
Cloudflare. Det er fortsatt riktig — men det stedet finnes ikke ennå:
`togkart.no` står parkert hos registraren, og en Transform Rule trenger en
sone å ligge i. En policy som venter på en DNS-migrering er ingen policy.

Headerne sendes derfor av `app.py`, som middleware ytterst i stakken. Samme
mønster som `strupe.py`, og av samme grunn: kanten er det beste stedet, men
appen er det stedet som finnes. Tre ting følger med på kjøpet:

- **De følger appen.** En policy i et dashbord gjelder ett domene bak én
  konfigurasjon. Denne gjelder også lokalt, hos andre som kjører prosjektet,
  og hvis appen en dag står bak noe annet enn Cloudflare.
- **De er i git.** Endres de, står det i en diff med en begrunnelse ved siden
  av. Endres en Transform Rule, står det i en logg ingen leser.
- **De kan testes.** `prober/sjekk_headere.py` kjører uten nett og uten
  database, og er med i CI — to ganger, siden unntaket for Swagger-sidene
  bare finnes i utvikling.

Middlewaren er registrert ETTER `Strupe`, altså ytterst, slik at også svar
Strupe avviser før ruting får headerne. Målt: en 429 fra strupen bærer alle
fem.

Forbeholdet fra før sto i under et døgn. `app.js` bygde sju
`style="..."`-attributter for datafarger, så policyen måtte ha `style-src
'unsafe-inline'`. De er klasser nå, og **begge** direktivene står på `'self'`
uten unntak. Se punktet under om inline-stilene.

Målt i nettleseren med policyen på: null `securitypolicyviolation`, kartet
laster og tegner 125 flistrukne objekter, og `/api/trains`, `/api/avvik` og
`/api/statistikk/operatorer` svarer alle 200.

Det som gjenstår er kanten, og det er en annen sak: se
[../drift/cloudflare.md](../drift/cloudflare.md).

**4. ~~Tredjeparts-CDN uten integritetssjekk.~~ Løst 23. august.**

Punktet beskrev to filer hentet fra unpkg.com uten `integrity` og uten
`crossorigin`: ble unpkg kompromittert eller DNS-en kapret, kjørte fremmed kode
på siden med full tilgang til alt. Versjonen var i det minste pinnet —
`@4.7.1` og ikke `@latest` — så det var ikke *helt* åpent.

Av de to utveiene ble den grundigste valgt. **Filene ligger nå i
`static/vendor/` og serveres av oss selv.** En SRI-hash ville stoppet endret
kode, men bare ved å nekte å laste den: er unpkg nede eller blokkert, er kartet
borte uansett. Egne filer fjerner begge deler på én gang, og angrepsflaten
krymper fra «unpkg og DNS-en foran den» til «hvem som kan gjøre en commit her».

Sjekksummene fra hentetidspunktet står i
[../static/vendor/README.md](../static/vendor/README.md), sammen med
kommandoen som verifiserer at filene er uendret og oppskriften for å bytte
versjon. Uten den tabellen er ingenting vunnet: en fil man ikke kan
etterprøve, er like ukjent enten den ligger på unpkg eller hos oss.

To ting ble sjekket i tillegg til at kartet tegner:

- **Filene henter ingenting videre utenfra.** De eneste `http`-forekomstene i
  de 868 kB er SVG-navnerom i data-URI-er og lenkene i lisensbanneret. Hadde
  bundtet pekt på et CDN for skrifter eller ikoner, ville avhengigheten vært
  tilbake bakveien.
- **`-text` i `.gitattributes`.** Regelen `* text=auto eol=lf` ville normalisert
  linjeskiftene ved commit, og da stemmer ikke sjekksummene med det utgiveren
  publiserte. 4.7.1 er ren LF og hadde overlevd det; neste versjon trenger ikke
  være det, og en hash som er stille feil er verre enn ingen hash.

Målt i nettleseren etterpå: null forespørsler til unpkg, `maplibregl.getVersion()`
svarer `4.7.1`, stilarket parser til 94 regler, og et kart bygget på bundtet
laster 93 lag og tegner 125 flistrukne objekter — altså kom vektorflisene
gjennom web workeren også.

Følgen for CSP-en: `https://unpkg.com` er ute av både `script-src` og
`style-src` i [../drift/cloudflare.md](../drift/cloudflare.md).

**5. `/api/docs` og `/openapi.json` er offentlige.** Begge svarer 200. API-et er
offentlig uansett, så dette er ikke en lekkasje av data — men det er en ferdig
oversikt over angrepsflaten, servert gratis. `docs_url=None` i produksjon.

**6. ~~`historikk.db` vokser uten tak, og uten WAL.~~ Løst 21. august.**

Punktet beskrev to feil i samme fil, og den ene var verre enn den andre.

**Låsingen først, fordi den ville slått til lenge før disken tok slutt.**
`journal_mode` sto på `delete`, som er standarden. I den modusen sperrer en
skriver alle lesere mens den holder på — og en leser som møter låsen feiler
**umiddelbart**, for `busy_timeout` er null som standard. Det var ikke lasten
som gjorde «database is locked» uunngåelig; det var den innstillingen.

Databasen kjører nå i WAL, med `busy_timeout` på fem sekunder og
`synchronous=NORMAL`. Alle fire innstillingene settes i `historikk.kobling()`,
og alle som åpner filen går gjennom den — `analyse.py`, `flaskehals.py`,
`vedlikehold.py` og skrivetråden. Det er ikke en ryddesak: tre av de fire er
per TILKOBLING og ikke per fil, så en leser som åpner databasen selv får
standardverdiene igjen uansett hva noen andre satte.

Målt begge veier 21. august, samme last — fyrti skrivinger og tre lesetråder
samtidig, på en kopi av den ekte databasen:

| | Utfall |
|---|---|
| `journal_mode=delete`, `busy_timeout=0` | **«database is locked»** |
| `journal_mode=wal`, `busy_timeout=5s` | 40 rader på 0,6 s, ingen feil |

Første rad er grunnen til at sjekk 5 i `prober/sjekk_historikk.py` er verdt å
ha. Uten den ville den vært et grønt merke som ikke kunne bli rødt.

**Så veksten.** Målt på nytt 21. august: **20 733 rader i døgnet, 249 byte per
rad, 1,8 GB i året.** (Tallene fra 20. august sa 28 777 rader og 2,5 GB; det
lavere tallet nå er tre døgn med data mot ett, ikke en endring i appen.)

Løsningen er ikke sletting alene. Sletting er billig å skrive og dyrt å
angre: den dagen noen spør «var Vy mer punktlig i august enn i november», er
svaret borte, og det er ikke noe man regner seg fram til i ettertid.

`vedlikehold.py` gjør derfor to ting, i denne rekkefølgen:

1. **Rullup.** Hvert ferdig døgn regnes ut én gang og skrives til tabellen
   `dogn` — én rad per (dato, operatør, linje), med turer, turer i rute,
   median, p90, verste, og fordelingen på morgen- og ettermiddagsrush.
   **Den tabellen slettes aldri.**
2. **Rotasjon.** Råobservasjoner eldre enn 90 dager slettes.

Kompresjonen er hele poenget: to døgn med 40 000 råobservasjoner ble **57
rader** i arkivet, som dekker 595 togturer. Det er noen få MB i året, for
alltid.

| | Før | Etter |
|---|---|---|
| Størrelse etter ett år | 1,8 GB, voksende | **443 MB i likevekt** |
| Punktlighet i fjor sommer | borte | i `dogn` |
| Flaskehalskartet | 90 dager | 90 dager (uendret) |

Siste rad er forbeholdet, og det er bevisst: `flaskehals.py` leser lat og lon
per observasjon, og den aksen kan ikke aggregeres til en rad per døgn uten å
bli noe annet. Varmekartet er bundet til råvinduet. Det er riktig — det spør
«hvor er det trangt nå», ikke «hvor var det trangt i fjor».

**Sperren er den delen som skiller en rotasjonsjobb fra et datatap.**
Ingenting slettes med mindre rullupen har tatt igjen: `roter()` krever at
loggen `dogn_rullet` inneholder et døgn nyere enn eller lik grensen. Feiler
rullupen — en ødelagt rad, full disk, en exception — står slettingen stille
i stedet for å kaste data ingen har arkivert. Det er også derfor rullupen
kjøres før rotasjonen og ikke etter.

To ting til, som ikke sto i det opprinnelige punktet men som hører hjemme her:

- **`auto_vacuum` er satt til `INCREMENTAL`.** Med standarden `NONE` frigjør
  sletting sider inne i filen, men filen krymper aldri, og eneste vei tilbake
  er en full `VACUUM` som skriver hele databasen på nytt. Konverteringen
  krever selv én `VACUUM`, og den kjøres derfor ved **første** vedlikehold og
  ikke ved første sletting — nå koster den et blunk på 15 MB, om et år ville
  den kostet en omskriving av 443 MB.
- **Backup må gjøres med `sqlite3 .backup`, ikke `cp`.** I WAL-modus ligger
  de nyeste transaksjonene i `-wal`-filen, og en `cp` av bare hovedfilen gir
  deg en database som mangler dem. Se `drift/tunnel-og-tjeneste.md`.

Jobben kjøres av seg selv klokka fire om natta fra `app.py`, og ett minutt
etter oppstart — en server som startes klokka ni skal ikke vente nitten timer
på sin første rullup. For hånd: `python vedlikehold.py`, `--status`,
`--torrkjor`, `--selvtest`.

**7. `server: uvicorn` i svarhodet.** Gratis fingeravtrykk. `--no-server-header`.

**8. `q` i `/api/search` har nedre, men ingen øvre lengdegrense.** En streng på
3000 tegn går videre til Entur. Ufarlig i dag fordi Entur avviser den, men det
er vår grense som mangler, ikke deres.

## Enklest å forbedre

Alt her er minutter, ikke dager:

| Tiltak | Innsats |
|---|---|
| ~~XSS-escaping~~ | **Gjort 20. august** |
| ~~`docs_url=None` når `TOGKART_MILJO=prod`~~ | **Gjort 20. august** — også `redoc_url` og `openapi_url` |
| ~~Øvre lengdegrense på `q`~~ | **Gjort 20. august** — 60 tegn |
| ~~Cache på `/api/search`~~ | **Gjort 20. august** — 10 min, med tak på antall oppslag |
| ~~Ratebegrensning~~ | **Gjort 21. august** — `strupe.py` i appen, WAF-regler i `drift/cloudflare.md`. Ikke i en reverse proxy; det finnes ingen. Se punkt 2. |
| ~~`--no-server-header` på uvicorn~~ | **Gjort 21. august** — står i systemd-unitet i `drift/tunnel-og-tjeneste.md` |
| Sikkerhetsheadere og CSP | **Formulert 21. august**, ikke satt opp — ferdig policy å lime inn i `drift/cloudflare.md` punkt 1. Fem minutter i dashbordet. |
| SRI-hash på MapLibre, eller last ned filene til `static/` | to attributter, eller én kopiering |

Merk hva som *ikke* er streket over i den nest siste raden. Policyen er
skrevet og testet lokalt — alle CARTO-kall svarer 200, og konsollen gir
nøyaktig de samme meldingene med og uten CSP — men den er ikke lagt inn hos
Cloudflare. Til det er gjort, sender serveren fortsatt ingen headere.

Det arbeidet ga forresten et funn som er verdt å ta med seg: **flisvertene
står ikke i koden.** `app.js` peker på `basemaps.cartocdn.com`, den peker
videre på en `tiles.json`, og den oppgir fire helt andre verter
(`tiles-a` til `tiles-d`). En CSP skrevet ut fra `index.html` alene blir
riktig for alt unntatt det kartet faktisk tegner med. Samme lærdom som
resten av dette dokumentet er full av: følg kjeden helt ut, og se hva som
går over nettet i stedet for hva koden ser ut til å be om.

## Krever mer innsats

- ~~**En CSP som faktisk holder.**~~ **Gjort 23. august.** Punktet sa «de
  seks inline-stilene». Det var sju — den siste kom med flaskehalspopupen 20.
  august, etter at dette avsnittet ble skrevet, og det er nettopp slik en
  liste over kjente unntak eldes: den teller det som fantes den dagen noen
  talte.

  Alle sju satte en **datafarge**: hvilket punktlighetsbånd toget er i, hvor
  mye tid en strekning taper. De er nå klasser som setter én variabel, `--f`,
  og komponenten i `app.css` bestemmer om den blir tekstfarge eller
  flatefarge. Klassenavnet utledes av variabelnavnet i `fargeklasse()`, så det
  finnes ingen oversettelsestabell som kan komme ut av takt med `theme.css`.

  Den ene verdien som ikke kunne bli en klasse er bredden på strekene i
  historikkpanelet — den er kontinuerlig, én per rad. Den settes gjennom
  **CSSOM** (`element.style.width`), og det er verdt å vite hvorfor det ikke
  er juks: `style-src` gjelder `<style>`-blokker og `style`-attributter i
  markup. Et style-attributt bygget fra en streng er, sett fra nettleseren,
  ikke til å skille fra et en angriper fikk plantet der. En CSSOM-tilordning
  er kode som allerede har kjørt, og den rammes ikke av direktivet.

  Målt i nettleseren med `style-src 'self'` pålagt: null
  `securitypolicyviolation` gjennom fordelingslista, begge historikkfanene,
  tooltipen og begge popupene — og fargene er de samme som før, lest av
  `getComputedStyle`. MapLibre injiserer forresten ingen `<style>`-elementer:
  dokumentet har null.
- ~~**Levetid på historikken.**~~ **Gjort 21. august.** Spørsmålet punktet
  pekte på — «hva vil du kunne svare på om et år» — ble besvart med *begge
  deler*: 90 dager rådata for det som trenger posisjoner, og et døgnarkiv
  som aldri slettes for det som trenger tid. Se punkt 6.
- **Overvåking.** At `/api/health` finnes hjelper ikke hvis ingen ser på den,
  og det står fortsatt igjen å peke en overvåker mot den — Cloudflare Health
  Checks, se `drift/tunnel-og-tjeneste.md` punkt 6. Men **endepunktet er gjort
  overvåkbart 23. august**, og det var ikke det før:

  Det svarte **200 uansett** hvor galt det sto til, med `"ok": false` gjemt i
  kroppen. En overvåker på standardinnstillingene ville meldt at alt var i
  orden mens kartet sto tomt. Nå er det 503 når `ok` er usann.

  Og det **utløste arbeidet det målte**. `CACHE_TTL` er ti sekunder, så en
  monitor på ett minutt bommet på cachen hver gang — og en bom henter Vehicle
  Positions, spør Journey Planner, henter rutedata for togene uten GPS og
  skriver til historikk.db. Målt: 2,3 sekunder kaldt mot 1,5 millisekunder
  varmt. Overvåkingen ville vært den tyngste trafikken appen hadde, og den
  ville tellet på Entur-kvoten. `TOGKART_HELSE_TTL` (fem minutter) skiller nå
  helsesjekkens tålmodighet fra nettleserens.

  `alderSekunder` er nytt og sier hvor gamle tallene er — et tall uten alder er
  den slags data resten av dette prosjektet bruker mye krefter på å merke.
  `prober/sjekk_helse.py` vokter alle tre uten nett, i CI.
- **Bort fra SQLite hvis trafikken vokser.** SQLite tåler dette fint i dag med
  én skriver, og tåler det bedre etter at WAL kom på. Den dagen du kjører
  flere prosesser, er det Postgres som er svaret — men det er et problem du
  ikke har.
