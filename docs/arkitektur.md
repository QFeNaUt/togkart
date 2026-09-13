# Arkitektur

Hvordan TogKart henger sammen: dataflyt, cachestrategi, hvilken fil som har
hvilket ansvar, og hva tallene faktisk måler.

Kartlagene og alt som tegnes i nettleseren står i [kartlag.md](kartlag.md).
Sikkerhetsgjennomgangen står i [sikkerhet.md](sikkerhet.md).

---

## Dataflyt

```
Nettleser  ──15s──>  /api/trains  ──10s──>  Vehicle Positions API
                          │        ──60s──>  Journey Planner v3
                          │                  (rutedata for tog uten posisjon,
                          │                   og forsinkelse for dem som har)
                          │
                     /api/search  ────────>  Geocoder v3

Nettleser  ──60s──>  /api/avvik   ──60s──>  Journey Planner v3 (situations)
                                                  = SIRI-SX

Nettleser  ──5min──> /api/statistikk/*  ──>  analyse.py  ──>  historikk.db
                                                  (ingen nettverkskall)

static/hovedbaner.geojson  <──  lagbaner.py  <──  Overpass / Entur
        (statisk fil, bygget når du vil, ingen API-kall i drift)
```

Fire cacher med ulik levetid. Kjøretøyposisjoner er ferskvare og hentes hvert
tiende sekund. **Rutedataene** for turer uten målt posisjon endrer seg langsomt
og hentes hvert minutt (`SJ_TTL_SECONDS`, målt til 2–3 sekunder for rundt 250
turer) — men **posisjonene** regnes ut på nytt ved hver forespørsel, siden
interpolasjon er ren matematikk.

De to taktene er grunnen til at filteret ligger **to steder**. Ved hentingen
sparer det båndbredde; i `positions(hopp_over=...)` kjører det på nytt med
ferske ID-er ved hver forespørsel. Uten det siste ville et tog som begynte å
sende GPS blitt tegnet to ganger til rutedatacachen løp ut — i opptil et minutt.

Sporgeometrien følger samme deling: polylinjen dekodes og stoppene snappes én
gang per tur, mens selve oppslaget langs linjen skjer per forespørsel.

Historikkanalysene er den tredje: de leser SQLite og regner medianer, uten å
røre nettet, og svaret endrer seg i timesskala. Fem minutters cache
(`STATISTIKK_TTL_SECONDS`), og selve utregningen går via `asyncio.to_thread`
— den er synkron, og skal ikke stoppe `/api/trains` mens den holder på.

Driftsmeldingene er den fjerde (`AVVIK_TTL_SECONDS`, standard 60). Svaret fra
Entur er stort — rundt 370 kB, fordi én melding om planlagt vedlikehold lister
opp hver eneste avgang den rammer, og det ble over 2000 for Sørlandsbanen. Vi
koker det ned til under 7 kB før det går ut på nettet. Cachen har sin egen lås
av samme grunn som `/api/trains` har det: uten den ville tre faner som lastes
samtidig fyrt av tre 370 kB-kall i samme sekund.

## Datakildene

Dekningen er delt i to:

| | Kilde | Posisjon | Antall (typisk) |
|---|---|---|---|
| Vy, Flytoget, Go-Ahead | Vehicle Positions API | GPS, målt | 70–85 på dagtid, 9–16 om natta |
| SJ Norge | Journey Planner v3 | Beregnet fra stoppetider | 12–16 på dagtid, 2 om natta |

SJ publiserer ingen togposisjoner til Entur. Det er verifisert, ikke antatt: av
4688 kjøretøy i hele feeden har kodespaket `SJN` tre busser og null tog.

Nordlige tog plasseres derfor langs sporet ut fra hvor mye av reisetiden som er
gått — traseen kommer fra `pointsOnLink`, så toget følger skinnene og ikke
luftlinjen. Hver posisjon merkes `positionMethod: track` eller `straight`, slik
at du kan se på dataene hvordan de ble til.

Ikke i Entur i det hele tatt: Arctic Train, Flåmsbana og all godstrafikk.

### Vy publiserer GPS for bare noen av sine egne turer

Delingen over er ikke hele bildet, og det tok en måling mot togkart.banenor.no
å oppdage. **Vehicle Positions mangler posisjon for en stor del av turene
Journey Planner kjenner** — ikke bare SJ sine. Målt 21. august lå hele linjer
ute: L2 med sju av sju turer, RE10 med fire, mens Journey Planner samtidig
visste at togene rullet og hvor forsinket de var.

Regelen er derfor ikke «hvilket selskap er dette», men **«har denne turen en
posisjon akkurat nå»**:

```
Vehicle Positions  ──>  turer MED posisjon   ──>  tegnes som målt (fylt prikk)
                                │
Journey Planner    ──>  alt annet med sanntid ──> tegnes som beregnet (ring)
```

Filteret er en observasjon per henting, ikke en liste noen må vedlikeholde —
samme tanke som stasjonsutvalget i `lagstasjoner.py`. `app.py` sender inn
tur-ID-ene fra snapshotet den nettopp bygget, og `sjnord.py` regner ut resten.

**Nøkkelen har to former, og begge trengs.** Tur-ID-en er den presise, men den
holder ikke alene: for noen Vy-tog svarer Vehicle Positions med en
`DatedServiceJourney`-ID der Journey Planner gir en `ServiceJourney`-ID.

```
VP  VYG:DatedServiceJourney:1931_OSL-RST_26-08-21
JP  VYG:ServiceJourney:1931_443485-R
```

Samme tog, to ID-rom. Sammenlikner man bare rå ID-er, ser fem av nitti tog ut
som et hull og blir tegnet en gang til — ved siden av prikken som allerede står
der. `tognokkel()` bygger derfor «linje:tognummer», som begge kildene kan
uttrykke likt, og settet inneholder begge formene.

**To unntak, begge med grunn:**

- **SJ tas med uansett sanntid.** Rundt halvparten av SJ-avgangene får aldri en
  sanntidsoppdatering. Uten unntaket ville de forsvunnet helt i stedet for å
  stå grå. For alle andre kreves `realtime` — uten det vet vi bare hva
  rutetabellen lover, og en prikk tegnet fra rutetabellen alene er ikke et tog
  vi har observert.
- **Et tomt VP-svar betyr ikke «ingen har GPS».** Det betyr at feeden er nede.
  Da faller filteret tilbake til den gamle regelen — bare operatører uten GPS —
  så vi heller tegner for få tog enn to prikker på samme tog.

**Beregnede tog teller i ringdiagrammet.** De gjorde det ikke før 22. august,
og begrunnelsen for å holde dem utenfor gjaldt posisjonen — ringen handler om
forsinkelsen, og den kommer fra Journey Planner for beregnede og målte tog
likt. Invarianten `meta.count == sum(counts.values()) + stale` vokter at
overskriften og oppdelingen teller samme populasjon; `meta.countsComputed` sier
hvor stor del av tellingen som hviler på en beregnet posisjon. Spøkelsestog
holdes fortsatt utenfor, og den grunnen er gyldig: avviket deres vokser mot en
rutetid toget aldri innfrir.

`computedReason` står på hver beregnet prikk og skiller de to grunnene:
`ingen-gps` (SJ) og `mangler-posisjon` (hullet). Den første er en konstant, den
andre er tallet som sier om Enturs dekning blir bedre eller verre. Ett samletall
ville skjult begge.

### Tog som har kjørt ferdig

Et kjøretøy slutter ikke å sende når det ankommer endestasjonen. Det rygger inn
på hensettingsanlegget, og GPS-en følger med. Målt natt til 22. august var
**8 av 15 tog i feeden ferdige med turen sin** — RE10 339 sto fire kilometer
nord for Lillehammer, på Hovemoen, merket «6 min 12 s forsinket». Bane NOR
tegner dem ikke, og det er riktig: de er ikke tog i trafikk.

`_uten_ferdige()` i `app.py` tar dem ut, og krever **to** ting:

- Journey Planner sier `FERDIG` for turen, og
- sluttidspunktet ligger mer enn `FERDIG_NADETID_SEKUNDER` (standard 300) tilbake.

Nådetiden er der for at en ankomst ikke skal blinke ut mens man ser på den, og
for at klokkeslett som spriker litt mellom kildene ikke skal avgjøre saken.

**Dette er et annet filter enn spøkelsesdeteksjonen**, og de svarer på hver sin
ting. `STALE_AFTER_SECONDS` i `entur.py` ser etter tog som har sluttet å
*sende*; disse sender helt fint — tre av de åtte var under ett minutt gamle.

Filteret kjører **før** ringdiagrammet telles og før historikken skrives.
Rekkefølgen er ikke likegyldig: gjør man det etterpå, teller togene fortsatt i
statistikken, og da er feilen bare flyttet dit den er vanskeligere å se.
`analyse.py` og `flaskehals.py` filtrerer på `stale = 0`, som ikke fanger et
tog som er ferdig men sender.

**Det som ikke er grunn nok til å skjule et tog**, og hvorfor:

| Tilstand | Hvorfor vi likevel tegner |
|---|---|
| Ingen JP-treff | Vi vet ingenting om turen. Fravær av data er ikke en fullført tur — se lærdom 8 |
| `ingen tider` | Turen finnes, men uten stoppetider har vi ikke *målt* at den er over |
| `ikke startet` | Ser ut som et tog før avgang, men det er også slik feil kjøredato ser ut (problem 3). Å skjule på den ville gjort en datofeil usynlig |
| `FERDIG` uten sluttidspunkt | Vi vet at turen er over, men ikke når, og da kan ikke nådetiden måles |

Antallet står i `meta.ferdige` og i nyhetslinja under ringdiagrammet, fordi et
tog som forsvinner uten forklaring ser ut som en feil.

**Loggen skiller på alder.** Et tog som ble ferdig for en halvtime siden står
hensatt etter nettopp den turen. Et som ble ferdig for tjue timer siden har en
`journeyRef` som ikke er oppdatert siden forrige gang settet kjørte — målt på
Flytogets sett 71-10 og 71-08, som natt til 22. august sto på Drammen med en
tur fra formiddagen dagen før. Begge skal ut av kartet, men bare den første er
et tog som nettopp ankom. **Blir den gamle gruppen stor midt på dagen, er det
et funn:** da er det ikke hensetting vi ser, men tur-ID-er som henger igjen
mens toget kjører noe annet — og da skjuler vi tog som er i trafikk.

### Endestasjonen måles på ankomst

`_timeline()` i `sjnord.py` bruker avgangstidene for alle stopp unntatt det
siste. Et endepunkt har ingen avgang, men Journey Planner fyller likevel ut
avgangsfeltene, og tallet der måler ingenting. Målt på RE10 339:

```
aimedDepartureTime  01:30:00      expectedDepartureTime  01:36:12
aimedArrivalTime    01:30:00      expectedArrivalTime    01:32:25
```

Toget ankom 2 min 25 s for sent — nøyaktig det Vehicle Positions meldte — mens
avgangsfeltene ga 6 min 12 s for en avgang som aldri skjer. **Her er VP riktig
og JP feil, motsatt av problem 1.**

Følgen var ikke bare et pent tall: `velg_instans` avgjør om turen er over ved å
se om nå ligger etter siste stopp, så det oppblåste tidspunktet holdt turen
«underveis» i fire minutter etter at toget sto stille.

Mangler ankomsttidene, faller `_timeline` tilbake på avgang — bedre enn å miste
stoppet.

### Knutepunktene

Turer oppdages ved å spørre 36 stasjoner om hvem som er innom. Hele ruta til
hvert tog hentes, ikke bare stoppet der, så ett knutepunkt per bane er nok.

Navnene slås opp i `static/stasjoner.geojson` i stedet for å ha NSR-ID-ene
skrevet inn — et knutepunkt som peker feil er en **stille** feil, siden
`stopPlace` svarer villig for enhver gyldig ID og rapporterer null tog.

To vindusstørrelser, fordi de svarer på ulike spørsmål:

| Gruppe | Vindu | Hvorfor |
|---|---|---|
| Lokalt (Oslo S, Lillestrøm, Asker, Drammen, Ski, Moss) | 90 min tilbake, 30 fram | Et lokaltog bruker under to timer fra ende til ende |
| Fjernt (30 stasjoner) | 12 t tilbake, 2 fram | Nordlandsbanen bruker rundt ti timer |

**Vinduet må være kort der trafikken er tett.** `numberOfDepartures` kutter fra
den *eldste* enden. Målt på Oslo S med tolv timers vindu og tak på 200: 200
avganger kom tilbake, hvorav **to** med sanntid — resten var formiddagen. Et
for stort vindu gir ikke for mye data, det gir feil data, og det ser like fullt
ut som et svar. `_hvem_gar` sier fra når et knutepunkt nærmer seg taket.

Spørringen ber om `arrivalDeparture: both`. Uten det er et tog som *ender* på
et knutepunkt usynlig; med det gikk «ikke sett av knutepunktene» fra fem til
null i `prober/sjekk_dekning.py`.

### Sporgeometrien hentes én gang per tur

`pointsOnLink` er den tunge delen — målt 4–44 kB per tur mot 1–5 kB uten — og
den er den samme hele dagen. Den hentes derfor bare for turer som ikke allerede
har en trasé i `_FORBEREDT`, og stoppene snappes på nytt mot den lagrede trasen
når rutetidene endrer seg.

Ved kaldstart er det rundt 250 turer på én gang. `GEOMETRI_PER_HENTING = 60`
sprer det over noen hentinger; togene som venter tegnes med
`positionMethod: straight` i mellomtiden og snapper til sporet innen et par
minutter. Det er en synlig og selvhelbredende degradering, ikke et stille tap.

## Filer og ansvar

| Fil | Ansvar |
|---|---|
| `app.py` | FastAPI: cacher, `/api/trains`, `/api/search`, `/api/statistikk/*`, `/api/avvik`, `/api/flaskehalser`, serverer frontend |
| `entur.py` | Vehicle Positions: GraphQL → GeoJSON, punktlighetsbånd, dobbeltsett |
| `avvik.py` | SIRI-SX: driftsmeldinger → sorterte en-linjere til nyhetsstripa |
| `sjnord.py` | Journey Planner: henter rutedata for turer uten målt posisjon og interpolerer dem langs sporet. Filnavnet er historisk — modulen dekket SJ Nord alene fram til 21. august |
| `punktlighet.py` | Journey Planner: henter forsinkelse for GPS-operatørene (Vy, Flytoget, Go-Ahead). Gjenbruker hentelogikken i `sjnord.py`, men beregner ingen posisjon |
| `sporgeometri.py` | Polylinjedekoding, avstand langs trasé, snapping av stopp, kompassretning |
| `materiell.py` | Kjøretøy-ID → togsett, linjekode → typisk materiell for SJ, og `transportSubmode` → trafikktype for alle. Entur har ingen materielldata for Vy — målt 22. august, se docstringen |
| `lagbaner.py` | Byggeskript: OSM/Entur → `static/hovedbaner.geojson` (visningslag) |
| `lagjernbanenett.py` | Byggeskript: OSM → `static/jernbanenett.geojson` (rutingsnett) |
| `lagstasjoner.py` | Byggeskript: Journey Planner → `static/stasjoner.geojson` |
| `historikk.py` | Logger endrede tog til `historikk.db` (SQLite), migrering og etterfylling. `kobling()` her er den ENE døra inn til databasen — WAL, busy_timeout og lesemodus settes der |
| `vedlikehold.py` | Døgnrullup til `dogn` (bevares for alltid) og rotasjon av rådata etter 90 dager. Ingen nett |
| `strupe.py` | Ratebegrensning: per klient som bakstopper, og taket på utgående Entur-kall som ingen proxy kan sette |
| `analyse.py` | Leser `historikk.db`: operatørrangering og rushtidsprofil. Ingen nett |
| `flaskehals.py` | Leser `historikk.db`: hvor togene mister tid, som GeoJSON. Ingen nett |
| `jernbanenett.py` | Ruter en strekning gjennom spornettet, styrt av observasjonene. Ingen nett |
| `sjekk.py` | Faste helsesjekker, åtte underkommandoer |
| `prober/felles.py` | Entur-kallet verktøyene deler: GraphQL, `errors`-sjekk, `EnturFeil` |
| `prober/` | Regresjonstester og engangsprober. Se `feilsoking.md` |
| `static/theme.css` | Alle farger. Samme variabelnavn som stromkart |
| `static/app.css` | Layout og komponenter |
| `static/app.js` | Kart, lag, søk, popup, historikkpanel, nyhetsstripe, bunnsheet, oppdateringsløkke |
| `static/index.html` | Struktur |
| `static/hovedbaner.geojson` | Bygget av `lagbaner.py`. Sjekkes inn i git |
| `static/jernbanenett.geojson` | Bygget av `lagjernbanenett.py`. Sjekkes inn i git |
| `static/stasjoner.geojson` | Bygget av `lagstasjoner.py`. Sjekkes inn i git |
| `drift/cloudflare.md` | Struping, sikkerhetsheadere og CSP som skal settes opp i Cloudflare |
| `drift/tunnel-og-tjeneste.md` | Tunnel, systemd-unit, `.env` i prod, backup av databasen |

Underkommandoer i `sjekk.py`: `diagnose`, `tognummer`, `operatorer`, `skjema`,
`spokelser`, `linjekoder`, `historikk`, `alle`.

Geometrisjekken ligger bevisst **ikke** i `sjekk.py`. Den trenger `sjnord` sin
hentelogikk og cache, som er noe annet enn de rene feedsjekkene — den bor i
`prober/sjekk_geometri.py`.

---

## I nettleseren

Nettleseren spør `/api/trains` hvert 15. sekund. Backend spør Vehicle Positions
maks én gang per 10 sekunder og rutedataene for tog uten posisjon én gang per
minutt, og deler svarene mellom alle faner. Uten cachene ville tre åpne faner
gitt tre kall i sekundet mot Entur.

Rutedataene caches lenge, men **posisjonene regnes ut på nytt ved hver
forespørsel** — interpolasjon er ren matematikk og koster ingenting. Det gir
ferske posisjoner med få nettverkskall.

Går Entur ned, serverer backend siste kjente posisjoner med `stale: true`, og
frontend viser en varsellinje i stedet for blank skjerm.

Kartet bygges én gang. Hver oppdatering bytter bare ut dataene i GeoJSON-kilden
med `setData()`, så prikkene flytter seg uten at kartet blinker.

Ringdiagrammet og fordelingslista under det svarer på hover med definisjonen av
båndet man peker på: hvilke minutter det dekker, og hva ordet betyr. Grensene
kommer fra `BANDS` i `app.js`, som også er kilden til fargegradienten på
prikkene (`STOPS`) og til fargene i historikkpanelet (`bandFarge()`).

To kopier av de samme tallene står igjen, begge med vilje: `delay_band()` i
`entur.py`, siden backend fargelegger `band` selv, og tallene på aksen under
`.legend-bar` i `index.html`, som er statisk markup. Endrer du tersklene, må
den ene linja rettes for hånd.

## Hva tallene betyr

`delay` fra Vehicle Positions er ikke til å stole på for alle tog. Målt mot
Journey Planner spriker de to kildene med opptil 19–31 minutter for enkelte
tog — og **i begge retninger**: VP både blåser opp og melder tog mer i rute enn
de er. Kilden er derfor allerede byttet: `punktlighet.py` henter
forsinkelsestall fra Journey Planner for Vy, Flytoget og Go-Ahead, og hvert tog
merkes med `delaySource` slik at du kan se hvilket tall det fikk. `delay` er
reserven, ikke hovedkilden.

Kveldsmålingen 19. august antyder at spriket i hovedsak ligger hos én operatør
(Vy 7 av 16 uenige, Flytoget 0 av 2), men utvalget er for lite til å slå det
fast. Se [undersokelser.md](undersokelser.md), problem 1, før du bygger noe
oppå tallene.

## Historikk-loggingen

`historikk.py` skriver hvert tog til `historikk.db` (SQLite, tabellen
`observasjoner`), kalt fra `get_snapshot()` i `app.py` som en fire-and-forget
bakgrunnsoppgave (`asyncio.create_task`) rett etter at snapshotet er bygget —
selve loggingen skal aldri gjøre `/api/trains` tregere. Skrivingen går via
`asyncio.to_thread`, så SQLite-filens I/O aldri blokkerer event-loopen.

Et tog logges bare når posisjonen har flyttet seg mer enn **50 m** (gjenbruker
`sporgeometri.avstand_m`, ikke en fjerde haversine-utgave) eller forsinkelsen
har endret seg mer enn **30 sekunder** siden forrige logging av akkurat det
toget. Uten sperren ville hver av de beregnede SJ-posisjonene fått en ny rad
ved hver eneste forespørsel, siden de regnes ut på nytt fra klokka — nettopp
de 700 000 radene i døgnet spesifikasjonen advarte mot.

**Hvem som utløser skrivingen, endret seg 14. september.** Til da var svaret
«den som ser på kartet»: `get_snapshot()` kalles fra `/api/trains`, og sto
nettleseren lukket, ble ingenting logget. Målt på sju døgn ga det en database
som var et bilde av utviklerens surfevaner — klokka ni om morgenen hadde null
observasjoner, mens kveldstimene hadde tolv ganger så mange som morgenrushet.
Og `rushtidsprofil()` i `analyse.py` leser nettopp de rå radene.

Nå kaller en bakgrunnsjobb i `livslop()` `get_snapshot()` hvert 60. sekund,
uansett om noen ser på. **Jobben kaller ikke noe annet**, og det er poenget:
det er fortsatt én vei inn til cachen og til historikken, bak samme lås. To
kallere som deler én skriver, ikke to skrivere.

Intervallet er ikke `CACHE_TTL`, og forskjellen er ikke akademisk. Et tog i
100 km/t flytter seg 50 m på under to sekunder, så ved ethvert intervall over
ti sekunder logges praktisk talt hvert tog ved hver runde — og da er det
intervallet, ikke trafikken, som bestemmer databasens størrelse: 10 sekunder
gir rundt 864 000 rader i døgnet mot 60 sekunders 144 000. Observasjonene fra
august ligger på én per tog hvert 49. sekund, så 60 er også det som bevarer
datatettheten analysene er innstilt på.

En sideeffekt verdt å kjenne: cachen holdes nå alltid varm. `/api/trains` og
`/api/health` treffer ferske tall i stedet for å utløse en henting, så en
kald henting er blitt sjelden i stedet for vanlig.

Siste loggede verdi per tog holdes i minnet (`historikk._siste`), ikke slått
opp i databasen. Den nullstilles ved omstart, med vilje.

**Døgnarkivet kan ha hull, og de er dokumentert i basen.** `vedlikehold.py`
ruller hvert ferdige døgn til `dogn`, som aldri slettes — så et døgn med kjent
forurensning må kunne holdes utenfor. Tabellen `dogn_utelatt (dato, grunn,
lagt_inn)` gjør det, og `--status` skriver ut datoene med begrunnelsen.

Lista bor i basen og ikke i koden, fordi utelatelsen er en egenskap ved *disse*
dataene: et nytt oppsett skal ikke arve den. 19.–21. august står der, fordi de
ble logget før kartet sluttet å tegne tog som hadde fullført turen sin.

**Utelatelsen gjelder alt som leser historikken**, ikke bare rullupen.
`analyse.py` og `flaskehals.py` leser rådataene direkte, så uten dette ville et
forkastet døgn fortsatt farget operatørrangeringen og varmekartet i nitti dager
— helt til rotasjonen tok det. `historikk.utelatte_dogn()` er derfor felles,
av samme grunn som `analyse.hent_rader_mellom` er delt med rullupen: to steder
som mener forskjellige ting om hvilke data som gjelder, gir tall som ikke kan
sammenliknes.

Datoen som sammenliknes er den **lokale** driftsdagen, samme definisjon som
`dogn` bruker. Å klippe de ti første tegnene ut av ISO-strengen ville vært UTC
og truffet feil rader rundt midnatt.

`historikk.db` ligger i `.gitignore`. Filbanen kan overstyres med
miljøvariabelen `HISTORIKK_DB`, og **skal** overstyres i produksjon — se
[../drift/README.md](../drift/README.md).

Tre ting står oppå databasen: operatørsammenligning og rushtidsprofil
(`analyse.py`), og flaskehalskartet (`flaskehals.py`). De to første grupperer
på tog og operatør, den tredje på **sted** — en akse som ikke fantes i
skjemaet og måtte utledes av `lat`/`lon` mot stasjonsregisteret.

## Historikkpanelets tre valg

Øverst til høyre står to rangeringer, begge regnet ut av `analyse.py` fra
`historikk.db`:

- **Operatører** — hvem kjører flest togturer i rute.
- **Rushtid** — hvilke strekninger som taper mest tid mellom 06 og 10, og
  mellom 14 og 18, på hverdager.

Tre valg avgjør hva tallene betyr, og de står i modul-docstringen i
`analyse.py`:

**Én terskel for alle.** Bransjen bruker to — 3:59 for lokaltog, 5:59 for
fjerntog. Den delingen er riktig når et selskap måles mot sin egen kontrakt og
feil når selskaper rangeres mot hverandre: SJ kjører nesten bare fjerntog og
ville fått den milde terskelen på hele porteføljen. Rangeringen bruker derfor
de samme 240 sekundene som fargene på kartet, og skriver grensen i panelet.

**En togtur, ikke en observasjon.** Databasen skriver bare når toget flytter
seg eller avviket endrer seg, så antall rader per tog sier mest om hvor langt
toget kjørte. Alle observasjoner av samme tog samme dag slås sammen til én
togtur med medianen av avvikene underveis. Uten det ville Bergensbanen veid
tyngre enn et lokaltog fordi den er lengre.

**Underveis, ikke ved endestasjonen.** Offisiell punktlighet måles ved ankomst
siste stasjon. Dette måler det typiske avviket gjennom hele turen — en annen
størrelse, og strengere mot tog som henter inn tid på slutten. Ikke sett tallet
opp mot Bane NOR sine uten å nevne det.

Streken under hver rad har to dimensjoner: **lengden** er hvor ofte toget er i
rute, **fargen** hvor mye det bommer når det bommer. En kort grønn strek er et
selskap som er sent støtt og stadig, men bare litt; en lang rød er et som stort
sett går presist og så plutselig ikke.

Rushtidsprofilen måler avvik og trafikkmengde, ikke passasjerbelegg — Entur
publiserer ingen passasjertall i de åpne feedene. Et fullt tog som går presist
havner derfor ikke på lista.

Uten server: `python analyse.py` skriver de samme to tabellene i terminalen.

### Operatøren står ikke i feeden

Kodeområdet foran NeTEx-ID-ene (`VYG:ServiceJourney:…`) er den eneste
operatørmerkingen som følger med helt fram, og `historikk.py` plukker den ut
ved skriving. Rader skrevet før kolonnen fantes etterfylles ved oppstart, uten
en håndskrevet tabell over hvem som kjører hva: `computed = 1` kan bare være
SJ, og for resten lærer databasen av seg selv — har en linje fått operatør på
en nyere rad, gjelder den for de eldre radene på samme linje.

En linje som ikke har kjørt siden kolonnen kom står derfor fortsatt umerket, og
`python sjekk.py historikk` sier hvilke.

## Forholdet til stromkart

`static/theme.css` bruker nøyaktig samme variabelnavn som stromkart sin
`:root`, så oppdaterte farger kan limes rett inn. `.panel`, `.subtitle`,
`.pulse`, `.legend-bar` og `.info-badge` er tatt derfra uendret. Det samme er
mobilvisningen: bunnsheetet med tre stopp og et gripefelt du drar i er
mekanikken fra stromkarts `ui/sheet.js`, skrevet om fra moduler til det ene
skriptet denne appen har.

Tre bevisste avvik:

- **Bakgrunnskart.** Stromkart har ingen tile-kilde — prissoner tegnes som
  polygoner på mørk flate. En togprikk uten kystlinje rundt seg er uleselig, så
  her brukes et svært nedtonet mørkt kart. Bytt `CONFIG.mapStyle` i `app.js`.
- **`--c-unknown`.** Et prisområde har alltid en pris; et tog kan mangle
  forsinkelsesdata. Grå er nødvendig for ikke å vise ukjent som «i rute».
- **`--rail`.** Cyan, valgt fordi den ligger langt unna hele
  punktlighetsgradienten i fargesirkelen. Grønt, gult, oransje og rødt kan
  aldri forveksles med sporet.

Stromkart er en React/Vite-app. Denne er vanilla JS — utseendet er felles,
koden er det ikke.
