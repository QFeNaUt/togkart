# Endringslogg

Nyeste først. Hver dato er én arbeidsøkt.

Løpende oppgaver og feil ligger som issues, ikke her. Bakgrunnen for de store
avgjørelsene står i [docs/undersokelser.md](docs/undersokelser.md), og de
destillerte lærdommene i [docs/erfaringer.md](docs/erfaringer.md).

---

## 13. september 2026 — i produksjon, på et domene som heter noe annet

Kartet er live på **<https://togkartet.no>**, fra LXC 106 på Proxmox-verten.
Fra tom container til offentlig nettsted på én kveld. Det som tok tid var
ikke oppsettet — det var to antakelser som hadde stått i dokumentasjonen i
tre uker uten å bli prøvd.

**Domenet heter `togkartet.no`.** Ikke `togkart.no`, som `drift/`, `issues/`,
`docs/sikkerhet.md` og en kommentar i `app.py` hadde sagt siden 21. august.
Det domenet har aldri vært vårt. 28 forekomster i ti filer er rettet;
`CHANGELOG.md` og `docs/arkiv/` står urørt, fordi en changelog og et arkiv
som skrives om i ettertid er noe annet enn en changelog og et arkiv.
Registraren er **Uniweb** (GROUP.ONE NORWAY AS), ikke Domeneshop.

**DNSSec var i ferd med å bli slått på, og det ville vært dyrt.** Uniweb
hadde startet en aktivering samme dag. Hadde DS-posten rukket ut i
`.no`-sonen før navnetjenerne ble byttet, ville domenet blitt utilgjengelig,
ikke tregt: Cloudflare svarer usignert, og en validerende resolver nekter da
å levere svaret i det hele tatt. Målt før byttet at ingen DS-post var
publisert ennå, og slått av i tide. Det er nå det første punktet i
`drift/cloudflare.md`.

**Norid har to klokker.** Registerdatabasen tok imot navnetjenerbyttet innen
minutter — synlig i oppslaget på norid.no — men `.no`-sonen ble ikke
publisert før flere timer senere. Google serverte den gamle delegeringen hele
veien. Kommandoen som skiller de to, og den eneste som er verdt å polle:

    nslookup -norecurse -type=NS togkartet.no charm.norid.no

Den spør registeret direkte, uten mellomlager. En ferdig support-e-post til
Uniweb ble skrevet og aldri sendt, fordi endringen landet først.

**Tunnelen er fjernstyrt, ikke lokalt styrt.** `tunnel-og-tjeneste.md` punkt 1
beskrev `cloudflared tunnel login`, en `config.yml` med ingress-regler og
`tunnel route dns`. Det som ble brukt er en tunnel opprettet i dashbordet med
et token, der ingressen ligger hos Cloudflare og `cloudflared service install
eyJ...` er hele oppsettet på maskinen. De to oppskriftene skal ikke blandes,
og punktet er skrevet om.

**«Published application routes», ikke «Hostname routes».** Nabofanen i
dashbordet ser ut som det samme, men er privat tilgang gjennom WARP-klienten
og publiserer ingenting. Kjennetegnet: den spør ikke om tjeneste og URL.

**Prosjektet lå uten versjonskontroll.** Det holdt så lenge det bare kjørte
lokalt. `git init`, to innsjekkinger, og repoet ligger nå offentlig på
<https://github.com/QFeNaUt/togkart>. Utrulling er `git pull` og omstart —
`drift/oppdater.sh` gjør de tre stegene og venter til `/api/health` er 200
før den sier seg fornøyd. CI-en i `.github/workflows/selvtester.yml` hadde
aldri kjørt før i kveld.

**Målt etter utrulling:**

| | |
|---|---|
| Minne, uvicorn i LXC | **38 MB** — halvparten av de 72 MB målt på Windows |
| `/api/health` utenfra | 200, `ok:true`, `bakCloudflare:true` |
| Forsiden | 200, 12,8 kB, 145 ms |
| `/api/docs` | 404 — `TOGKART_MILJO=prod` virker |
| Sikkerhetsheadere | Alle fem overlever gjennom Cloudflare |
| `http://` | 301 til `https://`, ett hopp |
| Tunnel | Fire forbindelser, `osl02` og `arn07`, QUIC |

`"bakCloudflare": true` er linja som ikke kunne verifiseres før nå. Den sier
at `CF-Connecting-IP` når fram, og at strupen teller per besøkende i stedet
for hele internett i én bøtte — fordi `cloudflared` kjører i samme LXC som
appen og derfor kobler seg til fra loopback.

**Én feil i den nye systemd-unit'en, funnet av `systemd-analyze verify`:**
`[Unit]`-seksjonen forsvant under innliming, så `After=network-online.target`
ble ignorert i stillhet. Tjenesten kjørte likevel — men ville startet før
nettverket ved en omstart av containeren. Verktøyet som fant den er verdt å
huske: `systemd-analyze verify` skriver ingenting når alt er i orden.

**Docker-filene finnes, men er ikke veien som ble valgt.** `Dockerfile`,
`compose.yaml` og `.dockerignore` ble skrevet, bygget og testet før det ble
klart at verten er Proxmox og at «container» der betyr LXC. De står ved lag
som alternativ og som presis dokumentasjon av kjøremiljøet. To feil ble
funnet ved å faktisk bygge dem, og begge er den slags som ikke kan resonneres
fram:

- **Dockers ignoreringsmønstre er ikke rekursive.** `__pycache__/` traff bare
  roten, og `prober/__pycache__` fulgte med inn i imaget.
- **`drift/` kan ikke utelates.** `prober/sjekk_headere.py` punkt 5 leser
  `drift/cloudflare.md` for å sjekke at koden og dokumentasjonen sier det
  samme om CSP-en, og falt på `FileNotFoundError` inne i containeren.

**`requirements.txt` er låst med `==`.** `>=` holdt så lenge det bare var en
lokal `.venv`. Et miljø som bygges på nytt om tre måneder er noe annet: da er
det ikke lenger koden i git som avgjør hva som kjører.

**`docs/sikkerhet.md` hadde en rad som var blitt usann.** «Sikkerhetsheadere
og CSP — formulert, ikke satt opp» sto fortsatt under «Enklest å forbedre».
De har vært sendt av appen selv siden 23. august, og er nå verifisert
gjennom kanten.

---

## 14. september 2026 — historikken slutter å avhenge av at noen ser på

Issue 22, og den var større enn tittelen sa.

**Funnet som gjorde den prioritert.** Fordelingen over døgnet i sju døgns
data:

```
05 UTC     990   ##
06 UTC   4 929   ###########
07 UTC       0            <- ingenting
17 UTC  15 214   ###################################
20 UTC  17 374   ########################################
```

Togtrafikken varierer ikke slik mellom morgen og kveld. Dette var et mål på
når nettleseren sto åpen. Og det er verre enn hull i en logg: `rushtidsprofil()`
i `analyse.py` leser de rå radene direkte gjennom `_hent_rader()`, så
rushtidsprofilen ble regnet ut av et utvalg vektet etter surfevaner — med
morgenrushet som den dårligst dekkede delen av døgnet.

**Løsningen er ett kall.** En jobb i `livslop()` kaller `get_snapshot()` med
jevne mellomrom og gjør ingenting annet. Issuet fryktet at en poller ville
gjøre cachen til «noe to ting skriver til»; det unngås ved at de to kallerne
deler skriveren i stedet for å ha hver sin. Ingen duplisert logikk, ingen ny
feilhåndtering, samme lås.

**Intervallet er 60 sekunder, ikke `CACHE_TTL`.** Det er den eneste
avgjørelsen i saken som var vanskelig. `logg_snapshot` skriver når et tog har
flyttet seg over 50 m, og et tog i 100 km/t gjør det på under to sekunder — så
ved ethvert intervall over ti sekunder logges praktisk talt hvert tog ved hver
runde. Da er det intervallet, ikke trafikken, som bestemmer størrelsen:

```
hvert 10. sekund   ~864 000 rader/døgn    20 GB på 90 dager
hvert 60. sekund   ~144 000 rader/døgn   3,3 GB
```

Målt på en testkjøring: 56 rader i første runde (alle tog ukjente), deretter
20–37 per runde av 56 aktive tog. Anslaget holder.

60 er dessuten det som bevarer datatettheten: observasjonene fra august ligger
på én per tog hvert 49. sekund, og et raskere intervall ville brutt med
grunnlaget de historiske tallene er regnet ut fra uten å gi bedre tall.

**En lekkasje som ble innført og lukket i samme endring.** `historikk._siste`
husker siste posisjon per tog og ble aldri tømt. Det gikk bra så lenge
prosessen ble startet om stadig vekk under utvikling; med en jobb som kjører i
måneder er det ~1 200 nye tog-ID-er i døgnet som aldri slippes. Nå bærer hver
oppføring et `time.monotonic()`-stempel, og tog som ikke er sett på en time
glemmes. Stempelet oppdateres for hvert tog i snapshotet, ikke bare for dem
som logges — ellers ville et tog som står stille på en perrong blitt glemt
mens det fortsatt var på kartet.

**Jobben rapporterer seg selv** i `/api/health` under `poller`, av samme grunn
som `strupe` gjør det: en bakgrunnsjobb som har stoppet ser nøyaktig ut som en
som virker, helt til noen leter etter hull en uke senere.
`sisteOkSekunderSiden` vesentlig over `intervallSekunder` betyr at den står.

**`TOGKART_POLLER=av`** stopper den uten en utrulling.

**Testet før utrulling:** ny `historikk.py --selvtest` med tretten sjekker på
minnelogikken, lagt til i CI. Bekreftet at probene ikke starter jobben ved et
uhell — `TestClient(app)` uten `with` kjører ikke lifespan. Bekreftet ren
avslutning gjennom lifespan med `RuntimeWarning` som feil. Bekreftet at
nødbryteren virker.

**Driftstallene i `drift/README.md` er merket som anslag.** 5 MB i døgnet og
443 MB i likevekt ble målt på det samme skjeve utvalget. Anslaget nå er ~37 MB
og ~3,3 GB, men det skal måles det første døgnet i stedet for å stoles på.

**Sideeffekt:** cachen holdes alltid varm. En kald henting er blitt sjelden i
stedet for vanlig, så både `/api/trains` og `/api/health` svarer raskere.

---

## 13. september 2026, sent — kanten satt opp, og en megabyte funnet

Fortsettelse av utrullingen samme kveld. Alt her er i Cloudflare-dashbordet,
og alt er verifisert utenfra etterpå.

**TLS.** Full (Strict), og **Always Use HTTPS** — den siste var den som
manglet: `http://togkartet.no` svarte 200 uten omdirigering. Nå 301, ett hopp.
HSTS står igjen med vilje; den kan ikke rulles tilbake.

**Ratebegrensning: én regel, ikke tre.** `drift/cloudflare.md` foreskrev 3a,
3b og 3c med ett-minutts vinduer. Free-planen gir `0/1 rules`, og `Period` har
bare 10 sekunder. Det som står ute er `/api/` på 30 per 10 sekunder, Block i
10 s.

Terskelen er 30 og ikke 20, som delingen ville gitt. Et kort vindu er
strengere enn tallet ser ut til: 120/minutt tåler en klump på 40 og så
stillhet, 20/10 s gjør ikke. Ekte bruk er klumpete — fire API-kall ved
sidelasting, noen til fra søket, dobbelt med to faner.

Verifisert med 60 kall og 10 samtidige: 32 × 200, så 429. Og 429-en kom fra
riktig sted — `CF-RAY: ...-OSL`, `Server: cloudflare`, og **ingen
`content-security-policy`**. Hadde `strupe.py` svart, ville headeren vært der.
Den er borte, altså nådde forespørselen aldri M720Q-en. `avviste: 0` i
helsesjekken sier det samme fra andre siden.

**Og så funnet: `.geojson` ble ikke cachet.**

```
app.js                 121 kB   HIT       Cloudflare betalte
maplibre-gl.js         803 kB   HIT
stasjoner.geojson       82 kB   DYNAMIC   DU betalte
hovedbaner.geojson     185 kB   DYNAMIC
jernbanenett.geojson   856 kB   DYNAMIC
```

Cloudflare cacher etter filendelse, og `.geojson` står ikke på lista. Hver
sidelasting hentet 1,12 MB fra stua, opp hjemmelinja, mens JavaScripten kom
fra Oslo. En cacheregel med én måneds edge-TTL senere: **67 kB fra opphavet
per førstegangsbesøk i stedet for 1 196 kB.** Atten ganger mindre.

Det er verdt å se mot driftsdokumentets «det som vokser med trafikken er
statiske filer ut av din egen server». Det var sant, og det var den største
posten — uten at noen hadde målt hvilke filer det gjaldt.

**Kapasitet, regnet på 400/400 Mbit etter regelen:** hundre samtidige
førstegangsbesøk er 6 MB gjennom tunnelen, altså 0,12 sekunder. Vedvarende
polling fra hundre faner er 16 Mbit/s — 4 % av opplinja. Taket ligger ikke i
sidelastingen men i pollingen, rundt 2 500 samtidige seere.

**Målt underveis, ikke rettet:** Cloudflare komprimerer med zstd — `/api/trains`
går fra 32 kB til 4,7 kB over nettet — men komprimeringen skjer på kanten.
`app.py` har ingen `GZipMiddleware`, så uvicorn sender rått og tunnelen bærer
rått. Besøkendes nedlasting er liten; opplinja betaler fullpris. Lagt som egen
sak.

---

## 24. august 2026 — én ask(), og en probe som løy

De fem utgavene av «post GraphQL, sjekk `errors`» er samlet i
`prober/felles.py`. Det var ment som opprydding, og ble en feilretting.

- **`be_om()` i `sjekk_dato.py` sjekket ikke `errors` i det hele tatt.** Den ga
  hele nyttelasten videre, og førstekallet gjorde `(payload.get("data") or {})`
  på den. En avvist spørring ble dermed til et tomt felt-sett, og proben meldte
  **«date på EstimatedCall .... MANGLER»** — et målt svar på et spørsmål den
  aldri fikk stille. Reprodusert med en skrivefeil i skjemaspørringen: gammel
  vei sier «MANGLER» med `errors` på én melding i kroppen, ny vei kaster.
  Feilen satt i koden som skulle avdekke akkurat den slags feil.
- **Forskjellene mellom de fem var utilsiktede.** Én hadde `raise_for_status()`,
  fire hadde ikke. Én tok URL som parameter, tre hadde den innbakt. To støttet
  variabler. Det er det som skjer med fem kopier av samme funksjon, og det er
  argumentet for å ha én.
- **Tidsavbruddene er IKKE samlet.** 15, 25, 30, 30 og 25 sekunder er beholdt
  hos hver kaller, fordi forskjellene er reelle: `sjekk.py` stiller små
  spørringer mot Vehicle Positions, `sanntidsjekk.py` ber Journey Planner nøste
  seg gjennom en avgangstavle. Å gjøre dem like ville vært å skjule en
  forskjell, ikke fjerne den.
- **Feilmeldingene er beholdt ord for ord.** `EnturFeil` bærer `meldinger` som
  liste og ikke bare som ferdig streng, nettopp fordi `sjekk.py` setter én per
  linje med innrykk. En liste kan formateres; en sammenslått streng kan ikke
  tas fra hverandre igjen. Verifisert mot en ekte avvist spørring.
- **URL-ene hentes nå fra appen.** `entur.ENTUR_URL` og `sjnord.JOURNEY_URL` i
  stedet for fire kopier på tvers av probene. Geokoderen har ingen tilsvarende
  ett-sted — `app.py` og `lagbaner.py` har hver sin — og det står som en lapp i
  `felles.py` i stedet for å bli en femte kopi i det stille.
- **`prober/felles.py --selvtest`**, uten nett, i CI. Seks påstander om det ene
  modulen finnes for, blant dem at et *delvis* svar — `data` og `errors`
  samtidig, som er lovlig GraphQL — også kaster. Et halvt svar er ikke et svar
  man kan måle på.
- **`smoketest.py` er med vilje ikke lagt om.** Den skriver ut `HTTP 200` før
  den ser på `errors`, og det er hele poenget den underviser i. Går den gjennom
  `sporr()`, forsvinner statuskoden — og med den demonstrasjonen av hvorfor
  denne modulen finnes.

Kjørt etterpå, mot ekte Entur: `sjekk.py alle`, `smoketest.py`, `nordtog.py`,
`sanntidsjekk.py`, `sjekk_punktlighet.py` og `sjekk_dato.py`. Alle som før.

## 23. august 2026 — ett HTTP-bibliotek

`requests` sto i `requirements.txt` for `sjekk.py` og tre prober. To
biblioteker for samme jobb er to sett standardverdier å holde styr på.

- **Fem kallsteder byttet**, i `sjekk.py`, `prober/smoketest.py`,
  `prober/sanntidsjekk.py` og `prober/nordtog.py` — fire `post` og én `get`.
  Signaturene er ordrett de samme, så byttet var ett ord per sted.
- **Én forskjell er ikke kosmetisk: `requests` følger omdirigeringer av seg
  selv, `httpx` gjør det ikke.** Målt før byttet — Vehicle Positions, Journey
  Planner og geokoderen svarte alle 200 uten omdirigering — så det var rent
  her. Advarselen står nå i `requirements.txt` for den som legger til et kall
  mot noe annet.
- **`requests` er ute av `requirements.txt`.** Verifisert ved å blokkere
  importen med en `sitecustomize` som kaster `ImportError`, og så kjøre hele
  verktøykassa: `sjekk.py alle`, de fire probene, `analyse.py`, `avvik.py`,
  `flaskehals.py`, `vedlikehold.py --status` og importen av `app` selv. Alle
  grønne. Det er noe annet enn å lete etter `import requests` med grep — det
  fanger også en indirekte avhengighet.
- **Saken kalte `smoketest.py` arkivert.** Den er det motsatte: README sier
  «kjør denne FØRST», og `docs/feilsoking.md` fører den opp som svaret på
  «ingen tog i kartet». Beskrivelsen hadde eldet dårligere enn koden.

Det som gjensto av jobben var at byttet måtte gjøres fem steder, fordi
`ask()` finnes fem ganger — se `issues/12`.

## 23. august 2026 — helsesjekken ble noe en overvåker kan stole på

Saken het «ingen ser på `/api/health`». Det stemmer fortsatt — å peke
Cloudflare Health Checks mot den er et par klikk i et dashbord som venter på
en DNS-migrering. Men endepunktet var ikke klart til å bli sett på, og det
var tre ting galt med det.

- **Det svarte 200 uansett.** `"ok": false` lå i kroppen, og statuskoden var
  200 samme hvor galt det sto til. Det virker for en overvåker som er satt opp
  til å lese kroppen, og er blindt for alle andre — `curl -f`, en
  systemd-sjekk, en uptime-tjeneste på standardinnstillingene. Nå er det 503
  når `ok` er usann, så det samme kriteriet virker overalt.
- **Det utløste arbeidet det målte.** `CACHE_TTL` er ti sekunder, tilpasset en
  nettleser som poller hvert femtende. En monitor på ett minutt bommet derfor
  på cachen **hver eneste gang** — og en bom henter Vehicle Positions, spør
  Journey Planner om forsinkelser, henter rutedata for togene uten GPS og
  skriver en runde til historikk.db. Overvåkingen ville vært den tyngste
  trafikken appen hadde, og den ville tellet på Entur-kvoten.
  **Målt: 2,296 s kaldt mot 1,5 ms varmt.** Rundt femtenhundre ganger.
- **`TOGKART_HELSE_TTL`** (fem minutter) skiller nå helsesjekkens tålmodighet
  fra nettleserens. En monitor på ett minutt koster én henting per femte sjekk.
  Sjekken beholder tennene: går hentingen i stykker, blir cachen aldri fersk
  igjen, og da prøver den på ekte hver gang — nøyaktig når du vil det.
- **`alderSekunder` er nytt.** «96 tog» uten alder kan være fra nå eller fra i
  går. Alderen leses etter et eventuelt hentingsforsøk, så en mislykket
  henting rapporterer den ekte alderen på tallene vi fortsatt sitter med — ikke
  null. Målt over 40 sekunder: 14 → 26 → 38, uten en eneste ny henting.
- **`prober/sjekk_helse.py`**, uten nett og uten database, i CI. Cachen settes
  for hånd og `fetch_trains` byttes ut med en som nekter å svare, så alle fire
  tilstandene kan tvinges fram: fersk cache, gammel cache, feilet henting og
  tom cache. Sjekk 6 er invarianten som holder resten i live —
  `HELSE_TTL >= CACHE_TTL * 10`.
- **Sjekk 5 vokter runbooken.** `docs/feilsoking.md` sender folk til
  `/api/health` for tre spørsmål om strupen. Forsvinner feltene, blir
  runbooken en løgn, og proben sier fra.

Selve overvåkingen står igjen som `issues/10`, snevret inn til det den er: et
par klikk, etter at domenet er flyttet.

## 23. august 2026 — datafargene er klasser, og CSP-en har ingen unntak igjen

`style-src 'unsafe-inline'` sto i policyen fordi `app.js` bygde
`style="color:..."` mens markupen ble laget. Nå gjør den ikke det, og både
`script-src` og `style-src` er `'self'` uten unntak.

- **Det var sju, ikke seks.** `docs/sikkerhet.md` sa seks. Den sjuende kom med
  flaskehalspopupen 20. august, etter at avsnittet ble skrevet. Slik eldes en
  liste over kjente unntak: den teller det som fantes den dagen noen talte.
- **Én klassefamilie, utledet og ikke skrevet.** `fargeklasse("--c-low")` gir
  `.f-c-low`, og klassen setter bare `--f`. Komponenten i `app.css` bestemmer
  om variabelen blir tekstfarge (et tall) eller flatefarge (en fargeprøve, en
  strek). Ingen oversettelsestabell i JS som kan komme ut av takt med
  `theme.css`.
- **Reservene bærer mening.** `.hist-tall` er `var(--f, inherit)` fordi
  hovedtallet i operatørfanen er en andel, og en andel har ingen båndfarge —
  den fargen hører hjemme på medianen i detaljlinja. `.swatch` faller tilbake
  på `--c-unknown`: en fargeprøve uten farge skal se ut som «ukjent», ikke
  som ingenting.
- **Bredden på strekene kunne ikke bli en klasse** — den er kontinuerlig, én
  per rad. Den settes gjennom CSSOM etter at raden står. Det er ikke en
  omgåelse: `style-src` gjelder markup, ikke `element.style.width`. Et
  style-attributt bygget fra en streng er ikke til å skille fra et en angriper
  plantet; en CSSOM-tilordning er kode som allerede har kjørt.
- **`prober/sjekk_headere.py` håndhever nå begge direktivene.** Sjekken som
  bare så på `script-src` går i løkke over begge, så veien tilbake til
  `unsafe-inline` er stengt av CI.
- **Målt med policyen på:** null `securitypolicyviolation` gjennom
  fordelingslista, begge historikkfanene, tooltipen og begge popupene.
  Fargene lest med `getComputedStyle` er de samme som før — femfargeprøvene,
  medianene, strekene — og strekbreddene står på 262 / 243,7 / 162,4 px for
  andelene 1,00 / 0,93 / 0,62. MapLibre injiserer ingen `<style>`-elementer:
  dokumentet har null.

## 23. august 2026 — sikkerhetsheaderne sendes, av appen

Policyen hadde ligget ferdig formulert i `drift/cloudflare.md` siden 21.
august og kunne ikke settes opp: en Transform Rule trenger en sone å ligge i,
og `togkart.no` står fortsatt parkert hos registraren. Serveren sendte
ingenting.

- **Middleware i `app.py`, ikke en regel i et dashbord.** Samme mønster som
  `strupe.py`, og av samme grunn: kanten er det beste stedet, men appen er
  det stedet som finnes. De fem headerne følger nå appen — også lokalt, også
  hos andre som kjører prosjektet, også hvis den en dag står bak noe annet
  enn Cloudflare.
- **Registrert etter `Strupe`, altså ytterst.** Målt: en 429 fra strupen
  bærer alle fem. Et unntak i en sikkerhetspolicy er en ting man glemmer.
- **Unntaket for Swagger er tomt i produksjon.** `/api/docs` og `/api/redoc`
  henter sitt eget JavaScript fra jsdelivr og blir hvite sider under
  `script-src 'self'`, så de tre dokumentasjonsstiene står utenfor CSP-en —
  men bare når `TOGKART_MILJO` ikke sier prod. Stiene svarer 404 ute uansett,
  så lista *kunne* fått stå. Da må den som leser policyen holde to ting i
  hodet for å vite at den ikke har hull. Tømt i stedet, er setningen kort nok
  til å stemme: ute sendes CSP-en på hvert eneste svar.
- **`prober/sjekk_headere.py`**, uten nett og uten database, i CI — **to
  ganger**, siden sjekk 4 har to grener og bare den ene kjøres av gangen.
  Den leser hvert CSP-direktiv og krever at navnet finnes: `frame-ancestor`
  i stedet for `frame-ancestors` gir ingen advarsel noe sted, verken i
  nettleseren eller i koden.
- **Proben fant en feil på første kjøring.** `img-src` hadde fått
  `basemaps.cartocdn.com` ved siden av jokertegnet, fordi jeg gjenbrukte én
  konstant til to direktiver. Den bare domenet trengs i `connect-src` — det
  er dit `app.js` fetcher `style.json` — mens sprite og fliser kommer fra
  `tiles.basemaps.cartocdn.com`, som jokertegnet dekker. De to linjene ser ut
  som noe å rydde opp i, og er det ikke.
- **Sjekk 5 vokter mot drift:** CSP-en i `app.py` og CSP-en i
  `drift/cloudflare.md` sammenlignes ordrett. To steder som beskriver samme
  policy er ett for mye, og den dagen de spriker er det dokumentasjonen som
  lyver.
- **Cloudflare-regel 1 er strøket fra lista** over ting å gjøre i dashbordet,
  med begrunnelsen for hvorfor man ikke skal duplisere den — «Set static»
  overskriver origin, og da er det bare det utestede stedet som gjelder.
- **Målt i nettleseren med policyen på:** null `securitypolicyviolation`,
  kartet laster og tegner 125 flistrukne objekter, de tre API-stiene svarer
  200, og de seks inline-stilene appen bruker slår gjennom.

Saken er ikke lukket, bare snevret inn. `issues/08` heter nå «togkart.no er
ikke i Cloudflare, så kantvernet finnes ikke» — det som gjensto var aldri
headerne, det var en DNS-migrering.

## 23. august 2026 — MapLibre serveres av oss selv

`index.html` hentet MapLibre fra unpkg.com uten `integrity` og uten
`crossorigin`. Ble pakken eller CDN-et kompromittert, kjørte fremmed
JavaScript hos alle med kartet oppe — og en CSP som tillater verten stopper
ikke det, siden verten er den tillatte.

- **Filene ligger nå i `static/vendor/`.** Av de to utveiene i
  `docs/sikkerhet.md` punkt 4 er dette den grundigste. En SRI-hash stopper
  endret kode, men bare ved å nekte å laste den: er unpkg nede eller blokkert,
  er kartet borte uansett. Egne filer fjerner begge deler på én gang.
- **Sjekksummer, ikke bare filer.** `static/vendor/README.md` har versjon,
  hentedato, bytestørrelse og SHA-256 for begge filene, kommandoen som
  verifiserer dem, og oppskriften for å bytte versjon. Uten tabellen er
  ingenting vunnet — en fil man ikke kan etterprøve er like ukjent enten den
  ligger på unpkg eller hos oss.
- **`-text` i `.gitattributes`, og det er ikke pynt.** `* text=auto eol=lf`
  ville normalisert linjeskiftene ved commit, og da stemmer ikke sjekksummene
  med det utgiveren publiserte. 4.7.1 er ren LF og hadde overlevd det; neste
  versjon trenger ikke være det, og en hash som er stille feil er verre enn
  ingen hash.
- **Bundtet henter ingenting videre.** De eneste `http`-forekomstene i de 868 kB
  er SVG-navnerom i data-URI-er og lenkene i lisensbanneret. Hadde det pekt på
  et CDN for skrifter eller ikoner, ville avhengigheten kommet inn bakveien.
- **CSP-en er strammet.** `https://unpkg.com` er ute av både `script-src` og
  `style-src` i `drift/cloudflare.md`. `script-src` er nå `'self'` uten unntak.
- **Målt i nettleseren:** null forespørsler til unpkg, `getVersion()` svarer
  `4.7.1`, stilarket parser til 94 regler, og et kart bygget på bundtet laster
  93 lag og tegner 125 flistrukne objekter — så web workeren og flisrørledningen
  virker fra egen vert også.
- **Og med den nye CSP-en pålagt**, av en lokal proxy foran uvicorn, med siden
  lyttende på `securitypolicyviolation`: null brudd, kartet laster like fullt,
  og `/api/trains`, `/api/avvik` og `/api/statistikk/operatorer` svarer 200.
  `drift/cloudflare.md` har målingen.

## 23. august 2026 — klyngene talte spøkelsestog

`spokelser` ble regnet ut per klynge og aldri lest. En klynge på 20 så ut som
20 tog som rapporterer nå.

- **Fargen kom fra feil populasjon.** `avvik` talte alle tog med et avviksbånd,
  spøkelsene med, og delte på `point_count`. Det er den samme feilen
  ringdiagrammet hadde 22. august: teller og nevner fra hver sin populasjon. Et
  spøkelsestogs bånd er regnet av en `delay` som bare vokser — toget sluttet å
  sende, rutetida gikk videre — og resten av appen nekter å telle det. Nå
  holder både teller og nevner dem utenfor.
- **En klynge uten et eneste målt tog er grå.** Med den gamle brøken ble fem
  tapte tog og null målte til «null avvik av null», altså grønt. Grå er
  `--c-unknown`, samme farge som en prikk uten forsinkelsesdata.
- **Spøkelsene står som et grått tall ved siden av klyngen.** Tallet inne i
  klyngen er fortsatt `point_count`: så mange prikker klyngen står i stedet
  for, og zoomer du inn finner du like mange.
- **Hvorfor et tall og ikke demping.** En enslig spøkelsesprikk tegnes med
  opacity 0,3, og det er den etablerte måten å si det på. Men 3 av 20 er en
  dempning på under ti prosent — usynlig, og nettopp det tilfellet saken
  handlet om.
- **Panelteksten forklarer merket** der den allerede forklarer nedtoningen, så
  det grå tallet ikke er et symbol uten forklaring noe sted.
- **`addLayer` kaster ikke — det var nesten den andre feilen.** Merkelappens
  `text-offset` var et `step` med tallpar som utfall, og et tallpar inne i et
  uttrykk leses som et uttrykk. MapLibre sendte en error-hendelse og lot laget
  ligge: `map.addLayer(...)` returnerte som om alt gikk bra, og lappen fantes
  ikke. `["literal", [x, y]]` er rettelsen. Fanget ved å spørre
  `map.getLayer(id)` etter at klyngene var tegnet med testdata — ikke ved at
  noe feilet. Søsterfellen til `clusterProperties`-formen rett ved siden av,
  og begge står nå i [docs/kartlag.md](docs/kartlag.md).

## 23. august 2026 — mobilvisningen er et bunnsheet du kan dra i

Panelene lå som en fast stripe nederst på telefon: tallene alltid framme,
kartet alltid beskåret. Nå er det stromkart-mekanikken — tre stopp og et
gripefelt.

- **Tre stopp, ett gripefelt.** *Nede* er bare gripefeltet, og kartet har hele
  flaten. *Halvveis* er telleren og ringen over et halvt kart, og det er
  tilstanden appen møter deg i. *Oppe* er lesestilling. Du drar, trykker eller
  bruker piltastene; et kast som ikke rekker halvveis til neste stopp teller
  likevel som en retning.
- **Gripefeltet sier de to tallene som er verdt et blikk** — «79 tog · 94 % i
  rute» — så det å legge sheetet bort ikke er det samme som å miste dem.
  Andelen kommer fra `renderDonut`, ikke fra en egen utregning: stripa og
  ringen kan ikke si to forskjellige ting.
- **Høyden følger innholdet, med tak på 88dvh.** Et sheet med fast høyde sto
  med en tom flate over kartet hver gang panelene var sammenslått — og det er
  nettopp da man vil se kartet. En `ResizeObserver` måler stoppene på nytt når
  historikken slås ut.
- **`position: fixed` og ikke `absolute`.** Sheetet står nedenfor skjermkanten
  i to av tre tilstander. Som absolutt plassert boks ga det hele dokumentet en
  rullehøyde det ikke skulle ha: å fokusere søkefeltet skjøv kartet 395 piksler
  ut av bildet. Målt, ikke antatt. En fixed boks teller ikke med i rulleområdet
  i det hele tatt.
- **Innholdet er `inert` når sheetet ligger nede.** Det står fortsatt i DOM-en,
  utenfor skjermkanten, og uten dette kunne man tabbe seg inn i et søkefelt
  ingen ser. Motsatt vei: fokus som havner i innholdet mens sheetet står
  halvveis, drar sheetet opp.
- **Et valgt søketreff legger sheetet bort.** Kartet flyr til treffet, og et
  sheet som blir stående og dekke det er ikke til hjelp.
- **Mobilregelen navngir ikke paneler lenger.** `#sheet .panel` i stedet for
  `#info, #nyheter, #historikk`. Den listen var feilkilden sist: nyhetsstripa
  arvet plassen til en tegnforklaring som var tatt ut, og regelen sa fortsatt
  det gamle navnet.
- **Skrivebordsvisningen er uendret.** Målt mot forrige versjon i samme vindu:
  `#info`, `#historikk` og `#nyheter` har piksellike bokser før og etter.

## 22. august 2026 — trafikktype på tog uten materielldata

Vy legger tognummeret i `vehicleId` og publiserer ikke settnummer noe sted, så
rundt to tredeler av togene på kartet hadde ingenting å si om seg selv.

- **Ettersøkt, og Entur har det ikke.** Vehicle Positions har 26 felt og
  ingen om materiell. I Journey Planner var `notices` tomt for **0 av 60**
  turer, `publicCode` var `None`, og `privateCode` viste seg å være
  tognummeret om igjen. Konklusjonen er nå målt og skrevet inn i
  `materiell.py`, så ingen leter en gang til.
- **`transportSubmode` er utfylt for 381 av 381 turer.** Ikke materiell, men
  målt — og det eneste Entur har som beskriver toget for operatørene uten
  settnummer. `service_type()` oversetter til norsk, og feltet `serviceType`
  ligger nå på hvert tog: et Vy-tog som før viste ingenting sier «Lokaltog»,
  «Regiontog», «Fjerntog» eller «Nattog».
- **Feltet finnes to steder, og bare det ene er fylt ut.**
  `Line.transportSubmode` er `unknown` for **alle 24 Vy-linjer**, og for
  Go-Ahead og SJ. Bare Flytoget og de svenske operatørene har den på linja.
  Leser man den på linjenivå — billigere og mer opplagt, siden linjer er få og
  stabile — konkluderer man med at feltet er tomt.
- **Popupen faller tilbake** på trafikktypen når det ikke finnes materielldata,
  og formulerer seg deretter: den sier hva slags trafikk toget kjører, ikke
  hvilket sett det er.
- **Selvtest i `materiell.py`.** `unknown` skal ikke oversettes til noe som
  ser ut som en trafikktype, og Vys tognummer skal aldri tolkes som et sett.

Saken står igjen som en **avklaring**: skal Vy få en håndskrevet
typisk-materiell-tabell slik SJ har? Mekanikken finnes; det som mangler er
fasiten, og et prinsipielt valg om håndskrevne påstander.

## 22. august 2026 — ringdiagrammet talte feil populasjon

Observert i kartet kl. 03:48: overskriften sa **10 tog i trafikk**, ringen under
sa **100 % i rute**, og F6 405 lå på Dovrebanen ni minutter og femten sekunder
forsinket.

- **Teller og nevner kom fra hver sin populasjon.** `#count` leste `meta.count`
  — alt som tegnes, målt og beregnet. Ringen leste `meta.counts`, som bare
  talte de *målte* togene. Ringen kunne dermed aldri gå opp, og fire tog manglet
  i oppdelingen uten at noe sa fra.
- **Beregnede tog teller nå.** Begrunnelsen for å holde dem utenfor gjaldt
  POSISJONEN, som er interpolert. Men ringen handler om FORSINKELSEN, og den
  kommer fra Journey Planner for beregnede og målte tog likt, gjennom samme
  `_last_measured`. F6 405 hadde `delaySource: journey-planner` akkurat som
  F5 726, som ble talt. `analyse.py` har regnet slik hele tiden — beregnede
  turer teller i operatørrangeringen, med `andelBeregnet` som markør.
- **Tallet var systematisk for pent.** Fjerntogene er de som oftest er
  forsinket, og de er nettopp de uten GPS. Om natta, når kartet stort sett er
  SJ, betydde «100 % i rute» nesten ingenting.
- **Spøkelsestog holdes fortsatt utenfor**, og den grunnen er en annen og
  gyldig: avviket deres vokser mot en rutetid toget aldri innfrir.
- **`meta.countsComputed`** sier hvor mange av de talte togene som hviler på en
  beregnet posisjon, så ringen ikke ser like sikker ut uansett hvor mye av den
  som er interpolert.
- **`_tell_band()` skilt ut av `get_snapshot`** og vernet av
  `prober/test_telling.py`, fem tester uten nett. Invarianten som vaktes:
  `meta.count == sum(counts.values()) + stale`.

Etter endringen: 10 i trafikk, 9 i rute, 1 forsinket — og summen går opp.

## 22. august 2026 — posisjonsavviket mot Bane NOR

Saken «11 av 73 tog lå over 10 km fra Bane NORs posisjon» er **innsnevret, ikke
lukket**. Én av de to hypotesene er avlivet, og det finnes nå en målestokk.

- **Målestokk for hva to metoder normalt spriker.** Historikken har våre
  posisjoner fra måleøyeblikket, og Journey Planner har fortsatt rutedataene
  for 21. august, så hvert tog kunne plasseres på nytt etter rutetid og
  sporgeometri. 75 tog, samme minutt: **median 0,96 km, p90 3,98, verste 8,44
  — og null over 10 km.** Medianen på 2,1 km mot Bane NOR er altså omtrent
  det dobbelte av intern spredning, men **de elleve utliggerne er ikke normal
  spredning**. Det er dem saken handler om.
- **Forsinkelsen må regnes inn.** Journey Planner dropper sanntid for en dag
  som har vært, så uten korreksjon måler man forsinkelsen om igjen: samme
  måling ga da median 2,31 km og verste 68,58, og verstingene var nøyaktig de
  mest forsinkede togene (F5 706, +62 min → 68,58 km rå, 3,11 korrigert).
- **Hypotese 1 er avlivet i den formen den ble skrevet.** «Tog bytter nummer
  på Oslo S» — målt på alle 1051 turene 21. august bærer **8 (0,8 %) to
  tognumre, alle RE20 Oslo–Göteborg**, og byttet ligger på grensen mens Oslo S
  er første eller siste stopp. Unntaket er verdt å merke seg: RE20 sto på lista
  over de verste avvikene, så for den ene linja kan hypotesen holde.
- **Ny hypotese 1b:** ikke at Entur unnlater å bytte nummer, men at Enturs og
  Bane NORs tognummer er ulike identifikatorer. Entur bruker allerede to ID-rom
  for samme tog (`DatedServiceJourney` mot `ServiceJourney`), så en tredje
  kilde med en tredje nummerering er ikke fjernt.
- **`prober/sjekk_posisjon.py`** gjør målingen fast, uten Bane NOR: målt
  posisjon mot rutetid og sporgeometri, med FEIL hvis medianen går over 3 km
  eller et enkelt tog over 15 km. Den fanger ikke det saken handler om, men den
  fanger at våre egne posisjoner begynner å drive — og det er feilen som
  faktisk kan oppstå hos oss. Hadde den eksistert 17. august, ville den ropt da
  SJ-togene ble tegnet ute i Mjøsa.

Det som gjenstår krever et nytt uttrekk fra Bane NOR, med paringen på linje,
retning og endedestinasjon i stedet for tognummer. Saken er derfor **erstattet**
av en smalere issue som beskriver selve ommålingen — tittelen sier nå hva som
skal gjøres, ikke hva som ble observert. Bakgrunnen ligger i
[docs/undersokelser.md](docs/undersokelser.md).

## 22. august 2026 — SJ-dekningen verifisert

Saken «SJ-dekning: 12 av Bane NORs 16 SJN-tog» er **lukket**. Den ble løst av
dekningsjobben 21. august uten at noen målte det; dette er verifiseringen.

- **Alle fem manglende tog tegnes nå.** Journey Planner har fortsatt
  21. august, så begge konfigurasjonene kunne spørres med nøyaktig vinduet de
  ville brukt kl. 11:36 den dagen. Gammel: 37 SJN-turer, mistet alle fem. Ny:
  50 turer, fant alle fem, og mistet ingen den gamle hadde.
- **Hele kjeden kjørt til posisjon**, ikke bare oppdagelsen: alle fem havner
  på sporet (`positionMethod: track`) på steder som stemmer med rutetabellen —
  45 mellom Hamar og Lillehammer, 2381 to minutter fra Røros.
- **To grunner til at den gamle mistet dem:** Mo i Rana og Mosjøen fantes ikke
  i lista på seks, og Nordlandsbanen har ingen andre knutepunkter. Og tog 45
  passerte samtlige gamle knutepunkter i framtiden kl. 11:36, med et
  framovervindu på to timer.
- **Målingen er gjort fast** som punkt 5 i `prober/sjekk_dekning.py`: av alt
  `sjnord.py` hentet, hva ble tegnet, delt på kodeområde. Kolonnen `UTEN POS`
  skal være null. Koster ingen nye nettverkskall — turene og posisjonene
  ligger klare fra punkt 4. Punkt 4 måler mot proben sine egne åtte
  knutepunkter på Østlandet og ser derfor knapt SJ; det var hullet i vakten.

Verifisert mot Journey Planner, ikke mot Bane NOR. Se
[docs/undersokelser.md](docs/undersokelser.md).

## 22. august 2026 — natt: tog som har kjørt ferdig

Utløst av en observasjon i kartet: RE10 339 lå nord for Lillehammer, merket
6 min 12 s forsinket og på vei nordover — forbi endestasjonen sin. Toget var
ekte og posisjonen var ekte; turen var over. Kjøretøyet sto på hensetting på
Hovemoen og sendte fortsatt GPS.

- **Tog som har fullført turen tas ut av kartet.** `_uten_ferdige()` i
  `app.py` krever både status `FERDIG` fra Journey Planner og et sluttidspunkt
  eldre enn nådetiden (`FERDIG_NADETID_SEKUNDER`, standard 300), så en ankomst
  ikke blinker ut mens man ser på den. Målt natt til 22. august: **8 av 15 tog
  i feeden var ferdige med turen sin.**
- **Det er et annet filter enn spøkelsesdeteksjonen.** `STALE_AFTER_SECONDS`
  ser etter tog som har sluttet å *sende*; disse sendte helt fint, tre av dem
  var under ett minutt gamle. Ingen av de åtte ble fanget av det gamle filteret.
- **Filteret kjører før ringdiagrammet telles og før historikken skrives.**
  `analyse.py` og `flaskehals.py` filtrerer på `stale = 0`, som ikke fanger et
  tog som er ferdig men sender — så oppblåste avvik fra fullførte turer gikk
  rett inn i operatørrangeringen.
- **Endestasjonen måles nå på ankomst, ikke avgang.** Et endepunkt har ingen
  avgang, men Journey Planner fyller ut avgangsfeltene likevel. For RE10 339 ga
  de 372 s mot 145 s faktisk ankomstavvik — og 145 var det Vehicle Positions
  meldte. Her var VP riktig og JP feil, motsatt av problem 1. Feilen holdt
  dessuten turen «underveis» i fire minutter etter at toget sto stille.
  `_timeline()` i `sjnord.py`, med avgang som reserve.
- **`meta.ferdige`** i API-et og en egen linje under ringdiagrammet: et tog som
  forsvinner uten forklaring ser ut som en feil.
- **Loggen skiller på alder.** Flytogets sett 71-10 og 71-08 sto på Drammen med
  en `journeyRef` fra formiddagen dagen før — 21 timer gammel. Begge skal ut av
  kartet, men bare et tog som nettopp ankom er hensetting. Blir den gamle
  gruppen stor på dagtid, skjuler vi tog som er i trafikk, og da skal noen se
  på det.
- **Dekningsprobens vakt fikk et minste utvalg.** Kjørt 02:10 fant den to turer,
  begge uten posisjon, og meldte «100 % — FEIL». En andel uten nevner er ikke
  en måling. Se lærdom 16.
- **De tre første døgnene er fjernet fra døgnarkivet.** Rullupen kjørte
  21. august og bakte inn forurensningen i `dogn`, som aldri slettes. Målt
  over hele basen fikk **17 av 1475 turer** medianen flyttet over en
  båndgrense av halen, i **begge** retninger — verst `R13x:1690`, der 18 av 24
  rader var hale og medianen ble 1297 s (`mye`) mot 25 s (`i_rute`) uten den.
  Lite, men et arkiv man må huske forbehold om er et arkiv man ikke kan bruke.
  19.–21. august er derfor slettet fra `dogn`; arkivet starter rent 22. august.
- **Utelatelsene bor i basen, ikke i koden.** Ny tabell `dogn_utelatt (dato,
  grunn, lagt_inn)`. Første forsøk var en hardkodet liste i `vedlikehold.py`,
  og selvtesten falt over den umiddelbart: den bygger syntetiske døgn relativt
  til i dag, og et av dem traff en av datoene. Det var et varsel om at
  plasseringen var feil — utelatelsen er en egenskap ved *disse* dataene, ikke
  ved programmet, og et nytt oppsett skal ikke arve den. `--status` skriver ut
  datoene med begrunnelsen, fordi et hull i et evig arkiv uten forklaring blir
  til «her mangler det data, vet ikke hvorfor».
- **Rådataene er urørt.** De forsvinner av seg selv etter 90 dager, og til da
  er de fortsatt det beste grunnlaget hvis noen vil ettergå avgjørelsen.
- **Flaskehalskartet var også rammet, og på en egen måte.** `_passeringer`
  leser avviket der toget var **nærmest** stasjonen — og et tog som parkerer
  hundre meter fra plattformen etter endt tur er nærmere enn det var da det
  passerte. Da leses et frosset, oppblåst avvik av som om det var ankomsten,
  og strekningen inn mot endestasjonen får skylda. Målt A/B mot en renset
  base: **ingen strekning oppsto eller forsvant**, men 27 av 254 fikk endret
  median, verst 55 s på Egersund→Hellvik. **70 % av de endrede rører en
  endestasjon, mot 31 % blant alle** — mekanismen bekreftet.
  Vernet `MAKS_STASJONSAVSTAND_M = 500` fanget det ikke: de fleste
  hensettingsanlegg ligger nærmere stasjonen enn det (Voss 30 m, Arna 20 m,
  Skien 40 m, Drammen 110 m). Bare Hovemoen, på 4 km, falt utenfor.
- **Utelatte døgn gjelder nå overalt, ikke bare i arkivet.** `analyse.py` og
  `flaskehals.py` leser RÅdataene, ikke `dogn`, så å fjerne arkivrader gjorde
  ingenting med det operatørrangeringen og varmekartet faktisk viste — de
  ville stått med 19.–21. august i opptil nitti dager til. Leseren
  `historikk.utelatte_dogn()` er nå felles for rullupen, rangeringen og
  varmekartet, av samme grunn som `hent_rader_mellom` er delt: to steder som
  mener forskjellige ting om hvilke data som gjelder, gir tall som ikke kan
  sammenliknes.
- **`prober/test_ferdige.py`**, seks tester. Fem av dem sjekker hva filteret
  *ikke* skal gjøre: manglende JP-treff, `ingen tider`, `ikke startet`,
  `FERDIG` uten sluttidspunkt og tomt `journeyRef` skal alle la toget stå.
  Å skjule er en sterkere handling enn å tegne litt feil.

## 21. august 2026 — sen kveld: dekning

Kartet tegner tog det ikke tegnet før. **Vehicle Positions mangler posisjon for
en stor del av turene Journey Planner kjenner** — ikke bare SJ sine, som aldri
har GPS, men også mange av Vys egne. Det ble oppdaget da kartet ble målt mot
togkart.banenor.no om formiddagen: stikkprøven ga 14 treff på 24 tog, og hele
linjer lå ute.

- **Beregningen utvidet fra SJ til alle tog uten målt posisjon.** `sjnord.py`
  gjorde allerede jobben — rutetid pluss `pointsOnLink` gir en posisjon på
  sporet — men bare for operatører uten GPS i det hele tatt. Regelen er nå
  «har denne turen en posisjon akkurat nå», avgjort av snapshotet `app.py`
  nettopp bygget. **Kartet gikk fra rundt 105 til rundt 135 tog.**
- **Knutepunktene fra 6 til 36**, med navn slått opp i `stasjoner.geojson` i
  stedet for NSR-ID-er skrevet inn — samme felle `prober/sjekk_dekning.py` gikk
  i med sine egne. To vindusstørrelser: 90 minutter der trafikken er tett,
  tolv timer på langbanene.
- **`numberOfDepartures` kutter fra den ELDSTE enden.** Målt på Oslo S med
  tolv timers vindu: 200 avganger tilbake, hvorav to med sanntid. Et for stort
  vindu gir ikke for mye data, det gir feil data. Vakt lagt inn.
- **Sporgeometrien hentes nå én gang per tur** i stedet for én gang per
  henting — 4–44 kB mot 1–5 kB — med et tak på 60 nye per runde ved kaldstart.
  Turer som venter tegnes i luftlinje og snapper til sporet innen et par
  minutter. `snapp_stopp()` skilt ut av `bygg_trase()` for å gjøre det mulig.
- **Fem tog ble tegnet to ganger.** Vehicle Positions svarer for noen Vy-tog
  med en `DatedServiceJourney`-ID der Journey Planner gir en
  `ServiceJourney`-ID. Nøkkelen er nå «linje:tognummer» i tillegg til den rå
  ID-en, og `meta.computedDropped` gikk fra 5 til 0. Se
  [docs/erfaringer.md](docs/erfaringer.md), lærdom 14 — målingen som klarerte
  ID-ene talte treff og ikke bom.
- **`arrivalDeparture: both`** i oppdagelsesspørringen. Uten den var et tog som
  *ender* på et knutepunkt usynlig; med den gikk «ikke sett av knutepunktene»
  fra fem til null.
- **`computedReason`** på hver beregnet prikk: `ingen-gps` (SJ) eller
  `mangler-posisjon` (hullet). De to betyr forskjellige ting, og ett samletall
  ville skjult begge.
- **`prober/sjekk_dekning.py` måler nå også hva vi faktisk tegner**, med sine
  egne knutepunkter som fasit. Turer som ikke tegnes deles på årsak, og
  «underveis, men ingen posisjon» er den eneste kategorien som er vår feil.
  Den står på null.
- **Seks nye regresjonstester** i `prober/test_geometri.py`, blant dem et vern
  mot den daterte tur-ID-en.
- **`historikk.py` sluttet å slutte at beregnet betyr SJ.** Etterfyllingen har
  fått en datogrense, så regelen er sann for nøyaktig de radene den ble
  skrevet for.

Se [docs/undersokelser.md](docs/undersokelser.md) for målingene.

## 21. august 2026 — kveld: drift

Den første økta i dette prosjektet som ikke handler om hva kartet viser, men om
hvorvidt det står i morgen. To ting kom ut av den som ikke var synlige da
punktene ble skrevet: «ratebegrens i reverse-proxyen» lar seg ikke gjøre her —
det finnes ingen reverse proxy, appen skal stå bak en Cloudflare Tunnel, og en
tunnel ringer ut. Og skarpere: **en per-IP-grense kan ikke verne Entur-kvoten**,
fordi kvoten henger på klientnavnet vårt og ikke på den som ringer. Hundre
veloppdragne adresser summerer seg til én overskridelse.

- **Ratebegrensning, i to lag som verner mot forskjellige ting.**
  `strupe.py`: per klient som bakstopper, og — viktigere — et tak på
  UTGÅENDE kall til Entur, som ingen per-IP-regel kan uttrykke. Kvoten henger
  på `ET_CLIENT_NAME`, så hundre adresser som hver holder seg innenfor
  summerer seg til én overskridelse. WAF-reglene som hører til ligger i
  `drift/cloudflare.md`. Se [docs/sikkerhet.md](docs/sikkerhet.md), punkt 2.
- **WAL på historikk.db, og én dør inn.** `historikk.kobling()` setter
  journalmodus, busy_timeout og synchronous, og alle lesere og skrivere går
  gjennom den — tre av innstillingene er per tilkobling, ikke per fil. Målt
  begge veier: samme last gir «database is locked» i `delete` og null feil i
  WAL. Se [docs/sikkerhet.md](docs/sikkerhet.md), punkt 6.
- **Historikken har fått en levetid, og et minne som overlever den.**
  `vedlikehold.py` ruller hvert ferdig døgn til `dogn` (bevares for alltid,
  57 rader for to døgn) og sletter rådata etter 90 dager. 1,8 GB i året blir
  443 MB i likevekt. Sperren: ingenting slettes før arkivet har tatt igjen.
- **Topologien i dokumentasjonen var feil.** «Legg det i reverse-proxyen»
  sto tre steder. Det finnes ingen reverse proxy — appen skal stå bak en
  Cloudflare Tunnel, som ringer ut og ikke tar imot. `drift/`-mappa er
  skrevet for den topologien prosjektet faktisk har.
- **To nye prober.** `sjekk_historikk.py` (låsing, vekst, arkiv, og en ekte
  samtidighetstest som er verifisert å kunne feile) og `sjekk_strupe.py`
  (både at vernet tar i, og at det ikke rammer tre faner som bare bruker
  kartet).

## 21. august 2026

Kartkoblingen i nyhetsstripa lagt om. Markeringen fulgte rotasjonen og blinket
hvert sjette sekund på steder ingen hadde spurt om; nå viser hover, og klikk
låser og flytter kameraet. En melding som gjelder en hel strekning — «buss for
tog mellom Kristiansand og Gjerstad» — tegner banen mellom dem på ekte spor,
ikke to ringer. Endepunktene leses av teksten når `affects` er tomt, og det var
nettopp de to alvorligste meldingene i stripa som ikke hadde et eneste
stoppested.

- **Kartkoblingen i nyhetsstripa lagt om.** Markeringen følger ikke lenger
  rotasjonen — hover viser, klikk låser og flytter kameraet — og en melding
  som gjelder en hel strekning tegner banen på ekte spor i stedet for ringer
  rundt endestasjonene. `avvik.paafor_kart`, `jernbanenett.strekning`,
  `avvik-strek`-laget i app.js. Se «Ringene blinket av seg selv» i
  `docs/kartlag.md` — særlig hvorfor de to alvorligste meldingene i stripa var de
  eneste som ikke kunne klikkes fram.
- **Visuell prioritering av togene.** Klyngedannelse under zoom 7, større
  prikker, eksplisitt tegnerekkefølge etter punktlighet, dempet spor og
  bakgrunn delt i støy / geografi / stedsnavn. Se «Klyngedannelse og visuell
  prioritering» i `docs/kartlag.md` — særlig `clusterProperties`-fellen, som
  hadde gjort hver klynge grønn uten å feile.
- **Søket bygget om** til en egen uklynget kilde, fordi klyngedannelse skjer
  på kildenivå og et lagfilter på `id` ikke lenger har noe å treffe.


### Funnet på veien: «siste time» talte ikke siste time

`sjekk.py historikk` finnes for å svare på ett spørsmål: **har databasen fått
nye rader nylig, eller står den stille mens serveren tror den logger?** Den
svarte feil, og den svarte feil på den måten som gjør en helsesjekk verdiløs.

Spørringen sto som

```sql
WHERE tidspunkt > datetime('now', '-1 hour')
```

SQLite svarer `2026-08-21 13:24:18` — mellomrom, ingen sone. Vi lagrer
`2026-08-21T14:23:33.444835+00:00`. Sammenligningen er tekstlig, og `T`
(0x54) sorterer etter mellomrom (0x20). Dermed matcher **hver eneste rad fra
dagens dato**, uansett klokkeslett.

Målt da den ble funnet: sjekken meldte **30 624 rader «siste time»** mens det
riktige tallet var **4 856**. Den talte rader i dag.

Det for høye tallet er ikke det verste. Sjekken finnes for å oppdage at
loggingen har *stoppet* — og en logging som stanset klokka to om natta ville
fortsatt meldt et friskt firesifret tall helt til midnatt. Alarmen ville gått
av først når den ikke lenger trengtes.

Rettet ved å regne ut grensen i Python og binde den som parameter, slik alt
annet som spør om et tidsvindu i dette prosjektet gjør (`analyse.py`,
`flaskehals.py`, `vedlikehold.py`). Det er nok en variant av lærdommen om at
to formater som *ser* like ut ikke er det — samme familie som `clock()` i
app.js og kjøredatoen i problem 3.

Samme runde: `sjekk.py` manglet `sys.stdout.reconfigure(encoding="utf-8")`,
som alle probene i `prober/` har hatt hele tiden. Uten den faller den over med
`UnicodeEncodeError` i det øyeblikket noen rørlegger utskriften videre til en
fil eller en annen kommando — for da er stdout cp1252 på Windows, ikke
konsollen. Det var slik feilen over ble funnet.


## 20. august 2026 — kveld

Nyhetsstripa bygget på SIRI-SX og lagt der tegnforklaringen sto, kjøreretning
som pil på hver prikk, og et varmekart over flaskehalser som kan slås av og på.
Varmekartet er det første laget som er utledet av historikken i stedet for av
sanntid, og det fant Oslotunnelen uten å bli fortalt at den finnes. Kartet fikk
dermed en fjerde datakilde, og den viste seg å ha sine egne feller — Vy
publiserer avvik under `NSB` og ikke `VYG`, og alvorlighetsgraden i feeden er
«normal» for nesten alt.

- **Nyhetsstripe bygget.** `avvik.py`, `/api/avvik`, `#nyheter` i frontend og
  `prober/sjekk_avvik.py`. Se «Driftsmeldinger og avviksringene» under
  Kartlagene for hva som ble målt underveis.
- **Varmekart over flaskehalser bygget.** `flaskehals.py`,
  `/api/flaskehalser`, et lag som kan slås av og på, og
  `prober/sjekk_flaskehals.py`. Se «Flaskehalskartet» i `docs/kartlag.md` — særlig
  hvorfor strekningene ikke kunne komme fra `hovedbaner.geojson`, og hvordan
  tegningen avslørte en tallfeil.
- **`avstand_til_strekning()`** lagt til i `sporgeometri.py`.
- **Løse tråder ryddet.** `entur._meters_between` slettet (tredje haversine),
  `on_event("startup")` → `lifespan`, `/api/search` fikk cache og
  lengdegrense, `/api/docs` slås av med `TOGKART_MILJO=prod`. Og fem påstander
  i dokumentasjonen som ikke lenger var sanne.
- **Kjøreretning bygget.** Pil på hver togprikk, fire kilder,
  `prober/sjekk_retning.py`. `retning_grader()` og `retning_langs_spor()` i
  `sporgeometri.py`, `paafor_retning()` i `entur.py`, `_retning_na()` i
  `punktlighet.py`. Se «Kjøreretning» i `docs/kartlag.md` — særlig hvorfor
  Enturs eget `bearing`-felt endte som siste utvei og ikke som førstevalg.
- **Tegnforklaringen «Forsinkelse» tatt ut.** Ikke slettet — markupen og de tre
  tingene som må følge med står i issuet om tegnforklaringen.
- **`AVVIK_TTL_SECONDS`** lagt til i `.env.example`.

## 20. august 2026

- **Samtlige stasjoner i kartet.** 335, hentet fra Enturs stoppestedsregister
  av `lagstasjoner.py`, opp fra de fem som ble lagt inn dagen før. Delingen på
  `viktighet` og de fire tersklene i `STASJON_ZOOM` kom samtidig — 335 prikker
  ved zoom 4 er ulesbart. Se [docs/kartlag.md](docs/kartlag.md).
- **XSS gjennom oppstrømsdata funnet, bevist og fikset.** Felt fra Entur ble
  limt rett inn i `innerHTML`. Alt utenfra går nå gjennom `esc()` i `app.js`.
  Se [docs/sikkerhet.md](docs/sikkerhet.md).
- **`docs_url=None`, `redoc_url` og `openapi_url`** slås av med
  `TOGKART_MILJO=prod`. Øvre lengdegrense på `q` (60 tegn), og cache på
  `/api/search` (10 min, med tak på antall oppslag).
- **Full testrunde, og tre feil den fant** — blant dem at
  `python prober/smoketest.py` ikke kunne kjøres slik README sa, og at
  tognummeret på sammenkoblede løp forurenset historikken. Se
  [docs/undersokelser.md](docs/undersokelser.md).
- **`diagnose.py`, `tognummer.py` og `operatorer.py`** verifisert borte fra
  prosjektroten; alle tre bor som underkommandoer i `sjekk.py`. Punktet hadde
  stått som «gjenstår» lenge etter at det var gjort.

## 19. august 2026

Lang natteøkt: Journey Planner tatt i bruk som punktlighetskilde, kjøredato
verifisert i full størrelse, korridorlaget i kartet lagt om, og en gjennomgang
av samtlige kildefiler.

- **Problem 1 lukket ende til ende.** Steg 3 wiret i `app.py` og bekreftet i
  første driftskjøring kl. 12:50: JP-forsinkelsene hentes, `delay` byttes der de
  finnes, hvert målt tog merkes med `delaySource`, og ringdiagrammet telles på
  nytt etter byttet. Kveldskjøringen kl. 23:43 fylte `FERDIG`-raden: uenigheten
  mellom Vehicle Positions og Journey Planner følger operatøren, ikke om turen
  er over. Se [docs/undersokelser.md](docs/undersokelser.md).
- **Problem 3 verifisert i full størrelse** — kjøredato i den nøstede spørringen.
- **Logging til SQLite.** `historikk.py` → `historikk.db`, kalt fire-and-forget
  fra `get_snapshot()`. Se [docs/arkitektur.md](docs/arkitektur.md).
- **Ruteopptegning ved klikk.** `/api/route/{journey_id}` leser
  `sjnord.route_line()`, og `app.js` tegner traseen i laget `valgt-rute`.
- **Fire baner inn:** Randsfjordbanen (femtende var Rørosbanen, sekstende
  Raumabanen), og de fem første stasjonene tegnet i kartet.
- **Bratsbergbanen lukket** — den manglet aldri i OSM, den ble målt mot feil
  tall. Samme kveld ble Drammenbanen og Gardermobanen målt etter samme metode
  og rettet av samme grunn: `km` beskrev bare det ene av de to navnene vi
  henter. Ingen geometri endret seg; det var fasiten som var feil, for tredje
  gang. Se [docs/erfaringer.md](docs/erfaringer.md), lærdom 13.
- **Feltet `spor`** innført på baner med dobbeltspor — det hever bare
  MERK-taket, ikke gulvet som utløser Entur-reserven.
- **Doc-drift rettet.** `entur.py` viste til `smoketest.py` og `tognummer.py` på
  rotnivå; begge er flyttet eller slått sammen. `lagbaner.py` viste til
  `diagnose.py`. Fire probedocstrings oppga `python X.py` for filer som ligger i
  `prober/`. Alle rettet.
- **`sjekk.py` sine hjelpetekster** sa «(fra diagnose.py)» og beskrev dermed
  filer som skal slettes. Nå beskriver de hva kommandoen gjør.
- **README.md skrevet om.** Den beskrev et prosjekt med fire filer. Nå står
  `sjnord.py`, `sporgeometri.py`, `lagbaner.py`, `sjekk.py`, `materiell.py` og
  `prober/` der de skal, sammen med et forbehold om `delay` som ikke sto der før.
- **`requirements.txt` begrunnet.** Hver avhengighet har nå en linje om hvorfor
  den er der.

## 18. august 2026

- **Tog som tegnes to ganger: løst.** To ulike årsaker bak samme symptom —
  `sjnord.py` manglet operatørfilter og plukket opp GPS-tog, og noen tog har
  faktisk to sendere. Se [docs/undersokelser.md](docs/undersokelser.md).

## 17. august 2026

- Prosjektstart. FastAPI mot Entur Vehicle Positions, MapLibre-frontend,
  punktlighetsfarger.
