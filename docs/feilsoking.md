# Feilsøking

Oppslagsverket når noe ser rart ut. Tre deler: hvilket skript som svarer på
hvilket symptom, hva de faste helsesjekkene *skal* si, og de feilene som går
igjen.

Arkitekturen bak står i [arkitektur.md](arkitektur.md), kartlagene i
[kartlag.md](kartlag.md), og drift i [../drift/](../drift/).

---

## Verktøykassa

Alt kjøres fra prosjektroten. Står du i feil mappe, feiler uvicorn med
`Directory 'static' does not exist`, og probene med
`ModuleNotFoundError: No module named 'entur'`.

```powershell
python sjekk.py alle                   # faste helsesjekker, seks på en gang — start her
python sjnord.py                       # hvilke tog mangler posisjon og beregnes
python analyse.py                      # operatørrangering og rushtidsprofil, uten nett
python flaskehals.py                   # hvor togene mister tid, uten nett
python avvik.py                        # hva nyhetsstripa ville rullet akkurat nå
python jernbanenett.py                 # ruting mot spornettet, uten nett
python vedlikehold.py --status         # hva ligger i historikk.db, uten å endre noe
```

Selvtester uten nett, sekunder hver:

```powershell
python sporgeometri.py                 # selvtest av matematikken
python materiell.py                    # demo av oppslagene
python lagbaner.py --selvtest          # forenkling og OSM-tolkning
python lagjernbanenett.py --selvtest   # tolkning og kryssdeling av spornettet
python lagjernbanenett.py --sjekk      # rapporter om spornettet som ligger der
python lagstasjoner.py --selvtest      # geocoder-tolkning og validering
python vedlikehold.py --selvtest       # rullup og rotasjon mot en midlertidig base
python strupe.py --selvtest            # bøttematematikk og tillitskjede
python prober/test_geometri.py         # regresjonstest, interpolasjon
python prober/test_dobbeltsett.py      # regresjonstest, dobbeltsett
python prober/test_ferdige.py          # regresjonstest, ferdige tog og endestasjon
python prober/test_telling.py          # regresjonstest, ringdiagrammets telling
node prober/test_tooltip.mjs           # regresjonstest, tooltipen på berøring og mus
python prober/sjekk_flaskehals.py      # varmekartet: geometri og utvalg
python prober/sjekk_strupe.py --uten-server
python prober/sjekk_headere.py         # sikkerhetsheadere og CSP. Kjør også med TOGKART_MILJO=prod
python prober/sjekk_helse.py           # /api/health: pris, statuskode og alder
python prober/felles.py --selvtest     # at HTTP 200 med `errors` ikke slipper gjennom
```

Prober som henter fra Entur:

```powershell
python prober/smoketest.py             # svarer API-et? har togene posisjon og delay?
python prober/sjekk_geometri.py        # sporgeometri mot ekte Entur-data
python prober/sjekk_dato.py            # kjøredato i nøstet mot ytre spørring
python prober/sjekk_punktlighet.py     # Vehicle Positions mot Journey Planner
python prober/sjekk_avvik.py           # driftsmeldingene bak nyhetsstripa
python prober/sjekk_dekning.py         # hvilke tog Entur ikke har posisjon for
python prober/sjekk_posisjon.py        # stemmer posisjonene med rutetabellen?
python prober/sjekk_historikk.py       # låsing, vekst og arkiv i historikk.db
python prober/sjekk_duplikater.py      # tog som tegnes to ganger. Krever uvicorn
python prober/sjekk_retning.py         # peker pilene riktig vei. Krever uvicorn
python prober/sjekk_strupe.py          # ratebegrensningen. Krever uvicorn
```

Probene er egne prosesser. De importerer `sjnord` direkte og henter fra Entur
selv — de snakker aldri med uvicorn og leser aldri serverens cache. Derfor
spiller det ingen rolle om serveren kjører, eller i hvilken rekkefølge du
startet ting.

`prober/sjekk_duplikater.py` er unntaket: den spør `/api/trains` nettopp fordi
den skal se det kartet ser, etter at begge kildene er slått sammen.

---

## Hvilket skript svarer på hva

| Symptom | Kjør | Ser etter |
|---|---|---|
| Ingen tog i kartet | `prober/smoketest.py` | Svarer API-et? Har togene posisjon og `delay`? |
| Forsinkelsene ser rare ut | `sjekk.py diagnose` | Fordeling, verstinger, alder på posisjon, per operatør |
| Tognumre mangler eller gjentas | `sjekk.py tognummer` | Unikhet per operatør |
| Tog mangler fra en landsdel | `sjekk.py operatorer` | Hvem publiserer, i hvilken modus. Treg — hele feeden |
| Linjekoder forsvinner | `sjekk.py linjekoder` | `publicCode` utfylt for alle |
| Er tomme felt tomme som før? | `sjekk.py spokelser` | `speed`, `vehicleStatus`, `monitoredCall` |
| Et Entur-felt oppfører seg rart | `sjekk.py skjema <Type>` | Hva finnes faktisk i skjemaet |
| Alt på én gang | `sjekk.py alle` | Seks sjekker, OK / MERK / FEIL |
| Nordlige tog mangler eller står stille | `sjnord.py` | Hvilke tog beregnes, med posisjon, avvik og grunn |
| SJ-tog ligger utenfor sporet | `sporgeometri.py` | Dekoder, snapping, avstandsregning. Uten nett |
| Samme, mot ekte data | `prober/sjekk_geometri.py` | Skjema, dekning, snapping, hvor langt fiksen flytter |
| Ingen SJ-tog i kartet om natta | `prober/sjekk_dato.py` | Ytre mot nøstet avgangstid |
| Er `delay` til å stole på? | `prober/sjekk_punktlighet.py` | Vehicle Positions mot Journey Planner |
| Samme tog tegnes to ganger | `prober/sjekk_duplikater.py` | Målt mot beregnet. Krever uvicorn |
| Et tog mangler i kartet | `prober/sjekk_dekning.py` | Punkt 3: er det i Vehicle Positions? Punkt 4: klarte vi å regne det ut? |
| Samme tog med både prikk og ring | `/api/trains` → `meta.computedDropped` | Skal være 0. Er den over, matcher ikke tur-ID-ene — se `tognokkel()` |
| Beregnede tog ligger ved siden av sporet | `python sjnord.py` | Kolonnen `positionMethod`. Rett etter oppstart er mange `straight`; det retter seg i løpet av et par hentinger |
| Et tog står stille utenfor stasjonen og er «forsinket» | loggen: `Tok ut N tog som har fullført turen` | Står det ikke der, er turen ikke registrert som ferdig i Journey Planner |
| Et tog forsvant fra kartet uten grunn | `/api/trains` → `meta.ferdige` | Nyhetslinja under ringdiagrammet sier hvor mange som er tatt ut |
| For mange tog forsvinner midt på dagen | loggen: `… N ferdige siste time, M eldre` | Er M stor på dagtid, henger `journeyRef` igjen mens toget kjører noe annet |
| Ringen sier 100 % i rute, men et tog er forsinket | `/api/trains` → `meta.counts` | Summen av båndene pluss `stale` skal være lik `meta.count`. Er den ikke det, teller ringen en annen populasjon enn overskriften |
| Et tog står et sted det umulig kan være | `prober/sjekk_posisjon.py` | Måler målt posisjon mot rutetid og sporgeometri. Over 15 km for ett tog er en reell feil |
| Alle togene ser litt forskjøvet ut | `prober/sjekk_posisjon.py` | Går medianen over 3 km, har noe skjedd med sporgeometrien |
| SJ-tog mangler nord for Dovre | `prober/sjekk_dekning.py` | Punkt 5: står `SJN` i tabellen, og er «UTEN POS» null? |
| Ingen beregnede tog i det hele tatt | `python sjnord.py` | Fikk den knutepunkter? «Ingen knutepunkter å hente fra» betyr at `stasjoner.geojson` mangler eller er ødelagt |
| Nyhetsstripa er tom | `prober/sjekk_avvik.py` | Svarer kodrommene? Punkt 1. Er portene for stramme? Punkt 5 |
| Stripa ruller gammelt nytt | `prober/sjekk_avvik.py` | Punkt 5: aldersfordeling og hva portene forkastet |
| For få meldinger i stripa | `MAKS_ALDER_TIMER` i `avvik.py` | Punkt 5 viser hvor mange `forGammel` tok |
| Flere linjer sier nesten det samme | `MIN_GRUPPE` i `avvik.py` | Punkt 3: sammendrag som gjentar seg uten å bli gruppert |
| En gruppe ble slått for hardt sammen | `GRUPPE_MAKS_SAMMENDRAG` i `avvik.py` | Punkt 3 lister hva som står for flere avganger |
| Alt i stripa har samme farge | `prober/sjekk_avvik.py` | Punkt 2: hvilken regel avgjorde hver melding |
| Tooltipen svarer ikke på trykk på mobil | `node prober/test_tooltip.mjs` | Hele tooltipen hang på hover til 21. september, og en telefon har ingen. `HAR_HOVER` i `app.js` skiller de to verdenene; faller trykkveien bort, går testen rødt |
| Tooltipen blinker eller legger seg under fingeren | `node prober/test_tooltip.mjs` | De syntetiske museventene en telefon sender etter et trykk har sluppet gjennom. Musehåndtererne skal returnere med én gang når `HAR_HOVER.matches` er usann |
| Stripa gjentar seg selv | `prober/sjekk_avvik.py` | Punkt 3: ble noe slått sammen i det hele tatt |
| Klikk på en melding gjør ingenting | `prober/sjekk_avvik.py` | Punkt 4: har meldingen steder å fly til |
| Hvordan ser stripa ut nå? | `python avvik.py` | Meldingene i rekkefølge, uten server og nettleser |
| Varmekartet er tomt | `prober/sjekk_flaskehals.py` | Punkt 1: kom noe med i det hele tatt |
| En strekning ligger ved siden av sporet | `prober/sjekk_flaskehals.py` | Punkt 2: er den rutet, eller falt den på medianlinja |
| Mange strekninger bruker medianlinja | `KORRIDOR_M` og `SNAPP_M` i `jernbanenett.py` | Punkt 2 lister dem og sier hvilke tall som gjelder |
| To baner er blandet sammen | `MAKS_STASJONSAVSTAND_M` i `flaskehals.py` | Parallelle baner — se punkt 3 under «Flaskehalskartet» i `kartlag.md` |
| Ruter den på feil bane? | `python jernbanenett.py` | Selvtesten tvinger Bryn–Lillestrøm begge veier |
| Hvilke strekninger er verst nå? | `python flaskehals.py` | Samme tall som kartet, uten nettleser |
| Pilene mangler på kartet | `prober/sjekk_retning.py` | Punkt 1: dekning per operatør og kilde |
| En pil peker feil vei | `prober/sjekk_retning.py` | Punkt 2: krysspeiling mot faktisk bevegelse |
| Bare Flytoget har piler | `prober/sjekk_retning.py` | Er `bevegelse` null? Da fylles ikke minnet i app.py |
| Logges det til historikk.db i det hele tatt? | `sjekk.py historikk` | Radantall, ulike tog, siste time, operatørmerking. Ingen nett |
| En operatør mangler i rangeringen | `sjekk.py historikk` | `MERK: N linjer uten operatør` — har linja kjørt siden kolonnen kom? |
| Historikkpanelet står tomt eller rart | `python analyse.py` | Samme to tabeller som panelet, uten server og nettleser |
| Kartet er svart, konsollen sier `Refused to` | `prober/sjekk_headere.py` | CSP-en blokkerer noe. Sjekk 3 leser direktivene; `worker-src blob:` er den som gjør kartet svart hvis den mangler |
| La til et bibliotek, og det laster ikke | `prober/sjekk_headere.py` | `script-src 'self'` slipper bare inn det vi serverer selv. Legg fila i `static/vendor/` |
| `togkartet.no` svarer **Error 1033** | på verten: `pct exec 106 -- systemctl status cloudflared` | Cloudflare finner ingen tilkoblet connector. **Appen kan være helt frisk** — enten er `cloudflared` nede, eller så er containeren så minnetrengt at den ikke rekker å holde QUIC-forbindelsene i live |
| `togkartet.no` svarer **502** | på verten: `pct exec 106 -- systemctl status togkart` | Det motsatte av 1033: tunnelen står, men uvicorn svarer ikke på `127.0.0.1:8000`. `cloudflared` logger «Unable to reach the origin service» |
| Nede, og `pct exec` bare henger | på VERTEN: `cat /sys/fs/cgroup/lxc/106/memory.events` | Containeren er tom for minne og kan ikke starte en ny prosess, så alt som må *inn* i den henger. `oom_kill` over 0 er beviset. `grep -E '^(anon\|file) ' …/memory.stat` skiller ekte minne fra filcache, og `ps -eo pid,rss,pcpu,etime,cgname,args --sort=-rss \| grep lxc/106` sier hvem som holder det — LXC-prosesser er synlige fra verten, så du slipper inn i containeren |
| Er det appen eller huset? | `curl -sI https://stromkart.no/` | Stromkart står i LXC 105 med sin egen tunnel. Svarer den 200, er hjemmelinja, Proxmox-verten og Cloudflare friske, og feilen er togkarts alene |
| `vedlikehold.py --status` sier basen er tom | `HISTORIKK_DB=… python vedlikehold.py --status` | Verktøyene leste ikke `.env` fram til 21. september og traff `historikk.db` i prosjektroten. Rettet med `historikk.les_env()`; ser du det igjen, er `load_dotenv()` eller rebindingen av `DB_PATH` borte fra `__main__` |
| CSP-en i doket og i appen spriker | `prober/sjekk_headere.py` | Sjekk 5 sammenligner dem ordrett og sier hvilken som er hvilken |
| `No time zone found with key Europe/Oslo` | `pip install -r requirements.txt` | `tzdata` mangler — Windows har ingen tidssonetabeller selv |
| En bane mangler i kartet | konsollen i nettleseren | `hovedbaner: N baner lastet — <navn>` |
| En bane har hull i seg | `lagbaner.py --bane X --min-bit 0` | Krymper verste hull mye? Da kutter filteret hovedspor. **NB: skriver filen med bare den ene banen** |
| En bane ser for kort ut | `km` i `BANER` først | Er målestokken riktig? Se lærdom 13 i `erfaringer.md` før du mistenker OSM |
| En bane gir MERK for mye spor | `km` og `spor` i `BANER` | Måler `km` alle navnene i `osm`? Er banen dobbeltsporet? |
| En stasjon mangler i kartet | konsollen i nettleseren | `stasjoner: N lastet — …` |
| En bestemt stasjon er ikke med | `lagstasjoner.py` | Har den avganger fra VYG/FLT/GOA/SJN neste døgn? Uten det er den ikke aktiv |
| Ingen stasjoner før man zoomer langt inn | `STASJON_ZOOM` i `app.js` | `alle: 12` — senk den |
| Cyan tåke av stasjoner | `STASJON_ZOOM` i `app.js` | `alle: 12` — hev den |
| Zoom 4 står uten stasjoner | `lagstasjoner.py` | `ADVARSEL: oversiktsstasjoner mangler` — er NSR-ID-en byttet? |
| Alle stasjoner har like mange avganger | `lagstasjoner.py` | `ADVARSEL: N stasjoner traff taket` — hev `MAKS_AVGANGER` |
| Materiellinja mangler | `materiell.py` | Tolkes kjøretøy-ID-en? For Vy finnes det ingen — Entur har det ikke, og popupen viser trafikktype i stedet |
| Trafikktypen mangler også | `python materiell.py` | Kom `transportSubmode` med i spørringen? Den ligger på TUREN; på linja er den `unknown` for alle utenom Flytoget |
| «database is locked» i loggen | `prober/sjekk_historikk.py` | Punkt 1: står journalmodus på `wal`, og har leserne busy_timeout? |
| Et døgn mangler i arkivet | `python vedlikehold.py --status` | Står det under «bevisst utelatt», er det med vilje — begrunnelsen står ved siden av |
| Et utelatt døgn kom tilbake | `SELECT * FROM dogn_utelatt` | Er datoen borte fra tabellen, ruller `OVERLAPP_DOGN` den inn igjen |
| Varmekartet eller rangeringen ble plutselig tomt | `python vedlikehold.py --status` | Er alle døgn i vinduet utelatt? De leser rådata, og utelatelsen gjelder der også |
| En strekning inn mot en endestasjon ser mistenkelig grønn ut | `python flaskehals.py` | Et tog som parkerer nærmere plattformen enn det passerte, får sitt frosste avvik lest av som ankomst. Skal ikke skje etter 22. august — se lærdom 18 |
| historikk.db bare vokser | `python vedlikehold.py --status` | Kjører rotasjonen? `auto_vacuum` skal være `INCREMENTAL` |
| `?dager=90` gir færre døgn enn 90 | `python vedlikehold.py --status` | `HISTORIKK_BEHOLD_DAGER` lavere enn `MAKS_DAGER` i app.py. Appen advarer ved oppstart |
| Arkivet står stille | `prober/sjekk_historikk.py` | Punkt 3: hull i rullup-loggen, eller ferdige døgn som venter |
| Sletter den ikke gamle rader? | `prober/sjekk_historikk.py` | Punkt 4: sperren holder igjen til arkivet har tatt igjen. Det er meningen |
| Hvor mye plass tar dette om et år? | `python vedlikehold.py --status` | Regner ut både med og uten rotasjon, fra målt radstørrelse |
| Brukere får 429 av og til | `prober/sjekk_strupe.py` | Punkt 1: rammer strupen tre helt vanlige faner? Da er grensene for lave |
| Entur ratebegrenser oss | `/api/health` | `strupe.enturKvote.igjen` — er den lav, hamrer noen på søket eller så bommer søkecachen |
| Alle besøkende strupes samtidig | `/api/health` | `bakCloudflare: false` i prod betyr at alle telles som 127.0.0.1. Sett `TOGKART_BAK_CLOUDFLARE=1` |
| Er vernet i det hele tatt på? | `/api/health` | `strupe.aktiv`. En strupe som virker ser lik ut som en som er av |
| Er tallene i kartet ferske? | `/api/health` | `alderSekunder`. Over 300 betyr at siste hentingsforsøk feilet |
| Overvåkeren varsler ikke selv om appen er nede | `prober/sjekk_helse.py` | Sjekk 3: svarer endepunktet 503 og ikke 200? |
| Entur-kvoten spises uten besøkende | `prober/sjekk_helse.py` | Sjekk 1 og 6: henter helsesjekken selv om cachen er fersk? |
| En probe melder «MANGLER» om noe som finnes | `prober/felles.py` | GraphQL svarer 200 på en avvist spørring. Går kallet gjennom `sporr()`, kastes det i stedet for å bli et tomt svar |

## Faste helsesjekker

- `sjekk.py alle` skal si **OK** eller **MERK**, aldri **FEIL**.
- `sjnord.py` henter typisk **200–260 turer** og beregner posisjon for
  **40–50** av dem. Resten er turer som er ferdige eller ikke har startet, og
  det er normalt: vinduet bakover er tolv timer på langbanene. Falt tallet til
  under tjue turer hentet, se etter «Ingen knutepunkter å hente fra» eller at
  et knutepunkt har byttet NSR-ID.
- `Hoppet over N turer med målt posisjon eller uten sanntid` skal være
  **vesentlig større** enn antallet som beregnes — typisk 400 mot 250. Går den
  mot null, har Vehicle Positions sluttet å svare, og da beregner vi tog som
  allerede har en målt posisjon.
- `Beregnet N tog inn i kartet (ingen-gps=…, mangler-posisjon=…)` i loggen.
  `ingen-gps` er SJ og ligger stabilt på 12–20. `mangler-posisjon` er hullet
  hos operatører som ellers publiserer, og det er tallet å følge over tid.
  Blir det 0 mens `ingen-gps` står, har filteret sluttet å slippe noe gjennom.
- `sum(meta.counts.values()) + meta.stale` skal være **lik `meta.count`**.
  Går den ikke opp, teller ringdiagrammet en annen populasjon enn overskriften
  «N tog i trafikk» — og da mangler det tog i oppdelingen uten at noe sier fra.
  `prober/test_telling.py` vokter invarianten uten nett.
- `meta.countsComputed` er hvor mange av de talte togene som har beregnet
  posisjon. Om natta er den ofte halvparten eller mer, siden fjerntogene er de
  uten GPS. Avviket deres er like målt som de andres — det er posisjonen som er
  regnet ut.
- `prober/sjekk_posisjon.py` skal si **Posisjonene stemmer med rutetabellen**.
  Målt 21. august: median **0,96 km**, p90 3,98, verste 8,44, ingen over 10 km.
  Det er spredningen mellom to måter å bestemme en posisjon på, ikke en feil —
  GPS-en måler hvor toget er, rutetiden sier hvor det skulle vært.
  **Forsinkelsen må regnes inn**, ellers måler man den om igjen: samme måling
  uten korreksjon ga median 2,31 km og verste 68,58, og verstingene var
  nøyaktig de mest forsinkede togene.
- `prober/sjekk_dekning.py` punkt 5 skal ha **null** i kolonnen `UTEN POS`
  for hvert kodeområde. En tur som er underveis SKAL få en posisjon; at tallet
  er null er det eneste som skiller «ingen posisjon fordi toget ikke kjører»
  fra «ingen posisjon fordi noe er ødelagt». Det var nettopp den linja som
  manglet da SJ-dekningen var 12 av 16 — ingen målte den, så ingen så det.
- Samme punkt: **`SJN` skal stå i tabellen** midt på dagen, med 30–60 hentede
  turer. Er kodeområdet borte, ser ikke knutepunktlista de nordlige banene
  lenger. Om natta er det normalt at SJ nesten ikke er der.
- `Tok ut N tog som har fullført turen sin (nådetid 300 s): X ferdige siste
  time, Y eldre`. Om natta er N stor — over halve feeden — og det er riktig:
  togene står hensatt. **På dagtid skal Y være lav.** Et tog som har vært
  «ferdig» i mange timer og likevel sender, har en `journeyRef` som ikke er
  oppdatert, og da risikerer vi å skjule et tog som er i trafikk. Sjekk et av
  dem mot togkart.banenor.no før du konkluderer.
- `Journey Planner: N av M tog fikk forsinkelse (FERDIG=…, underveis=…)` fra
  `punktlighet.py`. `FERDIG` + `ikke startet` + `ingen tider` er togene som
  ikke får et JP-tall, og bare `FERDIG` fører til at toget tas ut av kartet.
- `meta.computedDropped` skal være **0**. Den teller tog som ble fanget av det
  siste nettet i `app.py` — samme (linje, tognummer) som et målt tog, men annen
  tur-ID. Er den over null, har `tognokkel()` sluttet å matche, og da er neste
  skritt å se om Entur har endret formen på tur-ID-ene.
- `%s ga %d av maks %d avganger. Nærmer seg taket` skal **aldri** stå i loggen.
  Entur kutter fra den eldste enden av vinduet, så et kuttet svar mangler
  nettopp de ferskeste turene. Kort ned vinduet for det knutepunktet.
- `Fant N turer uten posisjon på K kjøredato(er)` skal vise to datoer på
  kvelden, én midt på dagen, og **én — gårsdagens** etter midnatt. Ser du
  `faller tilbake til samlet spørring`, er problem 3 tilbake.
- `Sporgeometri: N av M turer følger sporet (X nye, Y snappet på nytt)` skal
  ha N nær M **etter noen minutter**. Rett etter oppstart er N lav og X står på
  60 — det er taket `GEOMETRI_PER_HENTING`, og det er meningen. Blir N stående
  lavt i timevis, se etter `Entur avviste pointsOnLink` rett over i loggen.
- `Hoppet over N turer med målt posisjon eller uten sanntid` skal vise `VYG`,
  `GOA` og `FLT` — **aldri `SJN`**. Står `SJN` der, har du filtrert bort hele
  grunnlaget for de nordlige togene: SJ har ingen GPS, så en SJ-tur kan bare
  hoppes over hvis noen har rørt `KODESPAKET_UTEN_GPS`.
- `Slo sammen N dobbeltsett` er normalt 0–3. Ser du `N km fra hverandre`, har
  `_train_number` tolket feil — eller så er det et sammenkoblet løp som bytter
  nummer underveis, og da skal `sjekk.py tognummer` si `OK` likevel. Sier den
  `GJENBRUKT`, er det den ekte feilen. Se «Full testrunde» i `undersokelser.md`.
- `Tog X Y finnes både målt og beregnet` skal **aldri** stå i loggen. Det er
  samme sak som `meta.computedDropped` over.
- `hovedbaner: N baner lastet` i nettleserkonsollen skal liste **seksten**
  navn. Listen skrives ut nettopp fordi et tall som er litt for lavt ser ut som
  et tall.
- `stasjoner: N lastet` i nettleserkonsollen skal si **335** og liste de fem
  oversiktsstasjonene ved navn: Bergen stasjon, Bodø stasjon, Oslo S,
  Stavanger stasjon, Trondheim S. Er lista over de fem tom, står kartet uten
  stasjoner til zoom 12.
- `python lagstasjoner.py` skal ikke skrive `ADVARSEL: oversiktsstasjoner
  mangler` og ikke `ADVARSEL: N stasjoner traff taket`. Den første betyr at en
  NSR-ID er byttet; den andre at `avganger` er sensurert og de travleste
  stasjonene ser like travle ut.
- `python prober/test_geometri.py` og `python prober/test_dobbeltsett.py` etter
  enhver endring i interpolasjonen, `to_geojson` eller `_train_number`. Uten
  nett, under et sekund hver.
- `python lagbaner.py --selvtest` etter endringer i forenklingen eller
  OSM-tolkningen. Den vokter nå også at `--min-bit 0` overlever som
  overstyring, at hver bane i lista har en `km` å måles mot, og at `spor`
  hever MERK-taket uten å flytte gulvet som utløser Entur-reserven.
- `MERK: OSM ga N km mot ventet ~M km` er ikke bare støy. Spriker de to mye,
  er det like ofte `km` som er feil som OSM — se lærdom 13 i `erfaringer.md` før du feilsøker
  hentingen. Sjekk i denne rekkefølgen: måler `km` alle navnene i `osm`, eller
  bare det første? Er tallet dagens trasé eller en historisk? Er banen
  dobbeltsporet, og mangler den i så fall `spor`? Alle tre har tatt feil før.
  **Etter 19. august gir ingen av de seksten banene MERK** — dukker en opp, er
  det noe nytt.
- `python lagstasjoner.py --selvtest` etter endringer i geocoder-tolkningen.
  Uten nett, under et sekund.

- `python prober/sjekk_flaskehals.py` skal si **Varmekartet ser riktig ut**, og
  særlig **verste punkt utenfor sporet: 0 m** i punkt 2. Den sjekken er verdt å
  forstå: hvert punkt i en rutet strek er plukket ut av spornettet, så det kan
  ikke ligge noe annet sted enn på en skinne. Måler den noe annet, er det ikke
  geometrien som er skjev — det er rutingen som ikke ble brukt.
- `python jernbanenett.py` skal si **Rutingen ser riktig ut**. Den viktigste
  linjen er `korridor`: klarer ikke observasjonene å tvinge Bryn–Lillestrøm om
  Hovedbanen i stedet for gjennom Romeriksporten, er hele straffemekanikken
  uvirksom, og strekene legger seg på korteste vei uansett hvor togene kjørte.
  Det var slik parallellbanefeilen ble funnet, og tallene så helt rimelige ut
  imens. Forkastet andel ligger normalt rundt **50 %** — det meste er hull i
  loggingen om natta.
- `python prober/sjekk_retning.py` skal si **Pilene peker riktig vei**, og
  `0 flyttet seg likevel uten å få retning` i punkt 4. Den siste er den
  viktigste linja i hele proben: et tog som beveger seg SKAL få en pil, og at
  tallet er null er det eneste som skiller «ingen pil fordi toget står stille»
  fra «ingen pil fordi noe er ødelagt». Dekningen ligger normalt på **80–88 %**;
  resten er spøkelsestog og tog som står stille.
- `Kjøreretning: N av M målte tog (bevegelse=…, rutetabell=…)` i loggen.
  Rett etter oppstart er `bevegelse` null — det er ingenting å måle mot ennå —
  og `rutetabell` bærer alt. Ti sekunder senere skal `bevegelse` være den
  største kolonnen. Blir den stående på null, fylles ikke `_retning_minne`.
  Er `vehicle-positions` mer enn en håndfull, har de andre kildene sviktet:
  den er siste utvei og bommer med 26° i median. Se «Kjøreretning» over.
- `python prober/sjekk_avvik.py` skal si **Alt ser riktig ut**. Tre ting den
  vokter som ikke synes på stripa selv: at `NSB` fortsatt svarer (Vy publiserer
  der, ikke under `VYG` — se `avvik.py`), at noe faktisk **slås sammen** (feeden
  gjentar seg kraftig, og 59 situasjoner ble til 15 meldinger 20. august), og at
  meldingene er **stedfestet** — 14 av 15 kunne klikkes fram i kartet. Faller
  den siste mot null, er hele kartkoblingen død uten at stripa ser annerledes ut.
- `Driftsmeldinger: N situasjoner -> M i stripa` i loggen. Blir M lik taket
  (15) hver gang, drukner de minst alvorlige — det er meningen, men sjekk at
  `rettet` fortsatt kommer med. Blir M lik 0 mens N er stort, har nivåreglene
  eller gyldighetsfilteret begynt å kaste alt.
- `Punktlighet påført: N av M målte tog fikk JP-tall` skal ha samme N som `Journey Planner: N av M` fra `punktlighet.py` når begge skrives i samme rebuild. Spriker de i samme rebuild, har en `journeyRef` falt mellom henting og påføring. At tallet vandrer mellom ulike rebuilds er derimot normalt — se lærdom 12 i `erfaringer.md`.  

## Vanlige feil

| Feil | Årsak |
|---|---|
| `Directory 'static' does not exist` | Du kjører uvicorn fra feil mappe |
| `ModuleNotFoundError: No module named 'entur'` | Samme — stå i prosjektroten |
| Endringer i `static/` vises ikke | Nettleseren cacher. Ctrl+Shift+R |
| Endringer i `.env` gjør ingenting | `--reload` våker ikke over `.env` |
| `error while attempting to bind` | `Get-Process python \| Stop-Process` |
| Tog i havet eller på fjellet | Lat og lon byttet om. Alt i `sporgeometri.py` er `(lon, lat)` |
| Bare én bane i kartet | Du kjørte `lagbaner.py --bane X`. Kjør uten flagget |
| Dobbelttegnet spor | To lag med ulik nøyaktighet i samme farge. Se `kartlag.md` |

---

## Når en endring ikke slår gjennom

| Du endret | Da må du |
|---|---|
| `.py` i roten | ingenting — `--reload` starter serveren på nytt selv |
| `static/*` | Ctrl+Shift+R i nettleseren, eller hak av «Disable cache» i DevTools |
| `.env` | full omstart. `--reload` våker ikke over `.env` |
| `lagbaner.py` | kjør `python lagbaner.py`, så Ctrl+Shift+R |
| `lagstasjoner.py` | kjør `python lagstasjoner.py`, så Ctrl+Shift+R |

Sitter det en gammel prosess på porten — `error while attempting to bind` —
frigjør du den med:

```powershell
Get-Process python | Stop-Process
```
