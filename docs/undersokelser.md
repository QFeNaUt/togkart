# Undersøkelser

Post mortems. Hver av disse var en feil som så ut som gyldige data, og hvert
avsnitt er hvordan den ble målt fram — ikke hvordan koden virker i dag.

Les dette når du skal etterprøve en konklusjon, eller når et lignende symptom
dukker opp. Skal du bare finne ut hvordan API-et virker, hører du hjemme i
[arkitektur.md](arkitektur.md). De destillerte lærdommene står i
[erfaringer.md](erfaringer.md).

Alle sakene her er **lukket**. Åpne saker ligger som issues.

---

## RE10 339 nord for Lillehammer — 22. august

**Løst.** Observert i kartet: et tog med rute Drammen–Oslo S–Lillehammer sto
**nord for** Lillehammer, merket 6 min 12 s forsinket, med pil mot nord. Bane
NOR tegnet det ikke.

### Tre hypoteser, og hvilken som holdt

| Hypotese | Utfall |
|---|---|
| Beregnet posisjon som ekstrapolerer forbi siste stopp | **Nei.** Toget lå i Vehicle Positions (`vehicleId 339-2026-08-21`), altså målt, ikke beregnet |
| Spøkelsestog med voksende `delay` | **Delvis.** Posisjonen var 594 s gammel, så `stale` var satt — men tre andre tog i samme tilstand var under ett minutt gamle |
| Kjøretøyet står hensatt etter endt tur | **Ja** |

### Målingene

`vehicleId` bar kjøredato **2026-08-21**, dagen før. Journey Planner ga hele
ruta med 16 stopp, og siste stopp:

```
Lillehammer stasjon   avg 01:30:00 -> 01:36:12   ank 01:32:25   modified
```

Turen var altså over kl. 01:32, en snau halvtime før observasjonen. Posisjonen
lå **4,01 km** fra Lillehammer stasjon og **97 m** fra nærmeste spor i
`jernbanenett.geojson` — som bare inneholder hovedspor. Det stemmer med
hensettingsanlegget på Hovemoen. Retningen «mot nord» var heller ikke feil:
den måles mellom to hentinger, og toget rygget faktisk inn på anlegget.

### Hvor utbredt

Målt kl. 01:56 på hele feeden:

```
8 av 15 målte tog har status FERDIG i Journey Planner
   FLY1 3705, 3701, 3816, 3818    R40 1838
   RE10 339    RE11 839, 837
```

Tre av dem sendte aktivt (alder 5–65 s), så spøkelsesfilteret på alder fanget
dem ikke. Andelen er høyest om natta, men fenomenet finnes hele døgnet.

### Det andre funnet: forsinkelsen var også gal

«6 min 12 s» kom fra avgangsfeltene på **endestasjonen**, der det ikke finnes
noen avgang:

```
aimedDepartureTime  01:30:00      expectedDepartureTime  01:36:12   ->  372 s
aimedArrivalTime    01:30:00      expectedArrivalTime    01:32:25   ->  145 s
```

Vehicle Positions meldte 145. **Her var VP riktig og Journey Planner feil**,
motsatt av problem 1. Målt på samtlige ferdige tog samtidig spriker de to bare
for dette ene toget — men når de spriker, er avgangsfeltet det gale.

Følgen var større enn tallet: `velg_instans` avgjør om turen er over ved å se
om nå ligger etter siste stopp, så det oppblåste tidspunktet holdt turen
«underveis» i fire minutter etter at toget sto stille.

### Sidefunn: tur-ID-er som henger igjen

Flytogets sett 71-10 og 71-08 sto på Drammen med `journeyRef` fra et løp som
var over for **21 timer** siden. De skal ut av kartet — de er hensatt — men de
er ikke tog som nettopp ankom. Loggen skiller derfor på alder. **Blir den gamle
gruppen stor på dagtid**, er det ikke hensetting vi ser, men tur-ID-er som
ikke oppdateres mens toget kjører noe annet, og da skjuler filteret tog som er
i trafikk. Det er den ene måten dette kan gå galt på, og den er tellbar.

### Hva forurensningen gjorde med statistikken

Radene ble ikke bare tegnet, de ble logget. `historikk.py` skriver når toget
flytter seg mer enn 50 m — og et tog som rygger til hensetting flytter seg — så
turen fortsatte å generere rader med frosset, oppblåst avvik. `stale` var 0, så
`analyse.py` og `flaskehals.py` slapp dem rett gjennom.

For RE10:339 var **hele turdagen** de ni radene:

```
2026-08-19T23:32:02   2,4 km fra Lillehammer   delay=141   i_rute      <- ekte tur
2026-08-21T23:38:22   2,0 km fra Lillehammer   delay=372   forsinket   <- turen er over
2026-08-21T23:43:27   4,0 km fra Lillehammer   delay=372   forsinket   <- ute på Hovemoen
```

(Tidsstemplene er UTC; de ni siste er 01:38–01:43 lokalt, altså 6–11 minutter
etter ankomst.) Vys RE10-tur den dagen gikk inn i operatørrangeringen som «ikke
i rute», målt på et tog som sto stille på et hensettingsanlegg.

Målt over hele basen, 1475 turer:

```
17 turer (1,2 %) fikk medianen flyttet over en båndgrense av halen
813 rader (1,3 %) har frosset avvik gjennom hele turen

verste:  R13x:1690  21.08   18 av 24 rader var hale
         median 1297 s (mye)  mot  25 s (i_rute) uten halen
```

To forbehold: tallet er et **overtall**, siden proxyen («siste sammenhengende
rekke med identisk avvik, innenfor 6 km») også treffer turer som lovlig
avslutter med et stabilt avvik. Og feilen er **ikke ensrettet** — 4 av 17 gikk
andre veien og fikk toget til å se bedre ut. Det er støy, ikke skjevhet, og det
gjør den vanskeligere å oppdage.

### Flaskehalskartet, som ble rammet på en egen måte

Varmekartet måler **endringen** i avvik over hver strekning, ikke avviket selv,
så en frosset hale burde gi delta null og være harmløs. Den er ikke det, og
grunnen ligger i hvordan avviket leses av.

`_passeringer` komprimerer en rekke observasjoner ved samme stasjon til den ENE
der toget var **nærmest**. Uten stoppetider er det så nær «toget var ved
stasjonen» man kommer. Men et tog som parkerer på hensetting etter endt tur står
ofte nærmere plattformen enn det var da det passerte:

```
Voss     30 m        Arna    20 m
Skien    40 m        Drammen 110 m       Hovemoen (Lillehammer)  4015 m
```

Bare Hovemoen faller utenfor `MAKS_STASJONSAVSTAND_M = 500`. For resten vinner
den parkerte observasjonen, og et frosset, oppblåst avvik leses av som om det
var ankomsten. Strekningen inn mot endestasjonen får skylda for tid som gikk
tapt etter at turen var over.

Målt A/B mot en base uten halen, sju døgn, terskel tre passeringer:

```
ingen strekning oppsto eller forsvant        254 begge veier
27 av 254 fikk endret median
verst:  Egersund→Hellvik  -12 s  ->  +43 s   (55 s)
        Porsgrunn→Skien   +18 s  ->  +67 s   (49 s)
        Sparbu→Steinkjer  -32 s  ->   +2 s   (34 s)
```

Retningen er overveiende at halen **skjulte** tapt tid: en parkert passering
med delta rundt null trekker medianen mot null. Egersund–Hellvik så ut til å
hente inn tid der den i virkeligheten tapte 43 sekunder.

At mekanismen er den beskrevne og ikke tilfeldig støy, ble testet mot en
uavhengig egenskap: **70 % av de endrede strekningene rører en endestasjon,
mot 31 % blant alle 254.** Endestasjon er utledet av dataene — en stasjon som
avslutter minst 20 % av turene den er innom — ikke skrevet ned.

Forbeholdet gjelder her også: halen er identifisert med samme proxy som over,
så 27 er et overtall.

### Hva som ble gjort

Tog med status `FERDIG` og et sluttidspunkt eldre enn nådetiden tas ut før
ringdiagrammet telles og før historikken skrives. Fem tilstander som *ikke* er
grunn nok står i [arkitektur.md](arkitektur.md) — alle handler om å ikke
konkludere fra fravær.

Døgnarkivet var allerede skrevet, og det slettes aldri. 19.–21. august er
derfor fjernet fra `dogn` og registrert i `dogn_utelatt` med begrunnelsen.

**Og utelatelsen måtte gjelde mer enn arkivet.** `analyse.py` og
`flaskehals.py` leser RÅdataene, ikke `dogn` — så å tømme arkivet gjorde
ingenting med det operatørrangeringen og varmekartet faktisk viste. De ville
stått med de tre døgnene i opptil nitti dager til, helt til rotasjonen tok dem.
`historikk.utelatte_dogn()` er derfor felles for rullupen, rangeringen og
varmekartet.

Rådataene er urørt — de kan ikke renses, siden vi ikke lagret om turen var i
gang da raden ble skrevet (se [erfaringer.md](erfaringer.md), lærdom 17), men
de er fortsatt det beste grunnlaget for å ettergå avgjørelsen, og de forsvinner
av seg selv etter 90 dager.

## Posisjonsavviket mot Bane NOR — 22. august

**Ikke løst, men innsnevret.** Én hypotese avlivet, og en målestokk etablert
som funnet manglet.

### Målestokken som manglet

«Medianavviket mot Bane NOR var 2,1 km» er ikke en måling før man vet hva to
metoder normalt spriker. Den kunne regnes ut i ettertid: historikken har våre
posisjoner fra måleøyeblikket, Journey Planner har fortsatt rutedataene for
21. august, og da kan hvert tog plasseres på nytt etter rutetid og
sporgeometri — samme regnestykke `sjnord.py` gjør for tog uten GPS.

75 tog, samme minutt som Bane NOR-målingen:

```
                          median    p90     verste   over 10 km
rå                          2,31    8,17     68,58    6 av 75
korrigert for forsinkelse   0,96    3,98      8,44    0 av 75
Bane NOR, samme øyeblikk    2,10       -         -   11 av 73
```

**Funnet faller dermed i to deler.** Medianen på 2,1 km er omtrent det dobbelte
av intern spredning og lar seg forklare av at Bane NOR rapporterer ved
sporfelter mens vi rapporterer GPS. **De elleve utliggerne gjør det ikke:**
internt er det null tog over 10 km.

### Fella i målingen, som er lærdom 5 om igjen

Ukorrigert ga målingen median 2,31 km og verste 68,58, og tallene så ut som et
posisjonsproblem. Journey Planner dropper sanntid for en dag som har vært, så
tidslinja var ren rutetabell — og avviket målte **forsinkelsen** om igjen:

```
F5 706   +62 min forsinket   68,58 km rå  ->  3,11 km korrigert
F4 607   +25 min             33,67        ->  8,44
F5 707   +19 min             28,84        ->  3,34
F5 705   +13 min             15,75        ->  0,18
```

Korreksjonen er ikke uavhengig — avviket er målt mot rutetabellen, og vi bruker
det til å flytte rutetabellen. Den sier «posisjonen er forenlig med et tog som
ligger så langt etter ruta», ikke «posisjonen er riktig».

### Hypotese 1 avlivet

> «Mange gjennomgående tog bytter nummer på Oslo S.»

Målt mot Entur alene, på alle 1051 turene 21. august: **8 turer (0,8 %) bærer
to tognumre, og alle åtte er RE20 Oslo–Göteborg.** Byttet ligger på grensen,
mellom det norske og det svenske nummeret, og Oslo S er første eller siste
stopp — ikke et sted underveis:

```
RE20  103 -> 383    Oslo S er stopp nr 1 av 11
RE20  382 -> 110    Oslo S er stopp nr 11 av 11
```

For alle andre linjer bytter Entur ikke nummer i det hele tatt. Unntaket er
verdt å merke seg: RE20 sto på lista over de verste avvikene, så for den ene
linja kan hypotesen fortsatt holde.

### Det som står igjen

- **Hypotese 2, uendret.** Bane NOR interpolerer mellom sporfelter. Krever
  Bane NOR for å testes.
- **Hypotese 1b, ny.** Ikke at Entur unnlater å bytte nummer, men at Enturs og
  Bane NORs tognummer er ulike identifikatorer for samme tog. Entur bruker
  allerede to ID-rom internt (`DatedServiceJourney` mot `ServiceJourney`), så
  en tredje kilde med en tredje nummerering er ikke fjernt.

Neste måling må pare på **linje, retning og endedestinasjon** i stedet for
tognummer, og krever et nytt uttrekk fra `TogkartTrainsLayer`. Den står som
egen sak — «Mål posisjonsavviket mot Bane NOR på nytt».

### Vakten som kom ut av det

`prober/sjekk_posisjon.py` gjør målingen fast, uten Bane NOR: målt posisjon mot
rutetid og sporgeometri, med FEIL hvis medianen går over 3 km eller et enkelt
tog over 15 km. Spøkelsestog holdes utenfor enkeltvakten — en posisjon som er
tjue minutter gammel SKAL ligge langt fra der ruta sier toget er nå.

Den fanger ikke det denne saken handler om. Den fanger at våre egne posisjoner
begynner å drive, og det er feilen som faktisk kan oppstå hos oss: hadde den
eksistert 17. august, ville den ropt da SJ-togene ble tegnet ute i Mjøsa.

## SJ-dekningen: 12 av 16 — 22. august

**Løst, og løst før den ble undersøkt.** Dekningsjobben 21. august tettet den
uten at noen målte det. Dette er verifiseringen.

### Utgangspunktet

Målt mot togkart.banenor.no 21. august kl. 11:36–11:39 tegnet kartet **12 av
Bane NORs 16 SJN-tog**. Disse fem manglet:

    45 (F6 Dovrebanen)   478 (F7 Nordlandsbanen)   1785 (R75 Saltenpendelen)
    2381, 2383 (R60 Rørosbanen)

### Testen

Journey Planner har fortsatt 21. august, så begge konfigurasjonene kunne
spørres med nøyaktig det vinduet de ville brukt kl. 11:36 den dagen:

```
GAMMEL   6 knutepunkter, 12t/2t, tak 30, kun avganger     fant 37 SJN-turer
NY      36 knutepunkter, to vindusstørrelser, tak 300,
        avganger og ankomster                             fant 50 SJN-turer

   tog    gammel    ny
    45    mistet    FUNNET
   478    mistet    FUNNET
  1785    mistet    FUNNET
  2381    mistet    FUNNET
  2383    mistet    FUNNET
```

Ingen tur den gamle fant gikk tapt i den nye.

### Men «funnet» er ikke «tegnet»

Å bli oppdaget av et knutepunkt er nødvendig, ikke tilstrekkelig: turen må også
være underveis og ha to brukbare stopp. Hele kjeden ble derfor kjørt fram til
posisjon, med klokka frosset til 11:36:

```
   tog  linje  stopp  tegnet  metode  strekning
    45  F6        20  JA      track   Hamar -> Lillehammer
   478  F7        18  JA      track   Grong -> Snåsa
  1785  R75        7  JA      track   Rognan -> Fauske
  2381  R60       19  JA      track   Os -> Røros
  2383  R60       19  JA      track   Evenstad -> Stai
```

Alle fem på sporet, og alle fem på steder som stemmer med rutetabellen: 45 lå
mellom Hamar (11:10) og Lillehammer (12:00), 2381 to minutter fra Røros
(11:38). Det siste er verdt å merke seg — 1785 var *underveis* mot Bodø
(ankomst 12:25), ikke ennå ikke startet, slik avgangstiden alene antydet.

### Hvorfor den gamle mistet dem

To grunner, og de gjaldt hver sine tog:

- **Knutepunktene så dem ikke.** Mo i Rana og Mosjøen fantes ikke i den gamle
  lista på seks, og Nordlandsbanen har ingen andre.
- **Vinduet nådde ikke.** Tog 45 passerte de gamle knutepunktene (Dombås 14:05,
  Oppdal 15:16, Trondheim 16:53) alle sammen i FRAMTIDEN kl. 11:36, og
  framovervinduet var to timer.

### Forbeholdet

Verifiseringen er gjort mot **Journey Planner**, ikke mot Bane NOR. Den finner
alt vi mister etter at Entur har fortalt oss om turen, og det var der denne
saken bodde. Skulle Bane NOR se SJ-tog Entur ikke kjenner i det hele tatt,
ville dette ikke fanget det — men det er Enturs dekning og ikke vår.

### Vakten

Målingen er gjort fast som punkt 5 i `prober/sjekk_dekning.py`: av alt
`sjnord.py` hentet, hvor mye ble tegnet, delt på kodeområde. Kolonnen
`UTEN POS` skal være null for hvert kodeområde, og `SJN` skal stå i tabellen
midt på dagen. Punktet koster ingen nye nettverkskall — turene og posisjonene
ligger klare fra punkt 4.

Det var nettopp den linja som manglet: dekningen var 12 av 16 i ukevis, og
ingenting sa fra.

## Tog som manglet i kartet — 21. august

**Løst.** Kartet gikk fra rundt 105 til rundt 135 tog. Dette er hva som ble
målt underveis, i den rekkefølgen det ble målt.

### Utgangspunktet

Sammenlikningen mot togkart.banenor.no 21. august kl. 11:36–11:39 ga
**14 treff på 24 tog** i stikkprøven, mens totalene lignet: Bane NOR 116–122,
vi 114. Hele linjer manglet — `L2` med sju av sju turer, `RE10` med fire.

Tre årsaker, og bare én av dem var vår:

1. **Gods kommer aldri i Entur.** CargoNet, Hector Rail og de andre publiserer
   ikke dit, og det gjør heller ikke Arctic Train eller Flåmsbana.
2. **SJ publiserer ingen GPS** — det kompenserte `sjnord.py` allerede for.
3. **Vy publiserer GPS for bare noen av sine egne turer.** Journey Planner
   kjente RE10 311 og 316 med `realtime: true` og sanntidsavvik; Vehicle
   Positions hadde ingen posisjon for dem.

Den tredje var den fikserbare, og maskineriet fantes: rutetid pluss
`pointsOnLink` gir en posisjon på sporet.

### Hva som ble målt før noe ble skrevet

| Spørsmål | Svar |
|---|---|
| Hvor mange turer kjenner Journey Planner med sanntid? | 172 fra 31 knutepunkter, på 0,4 s og 99 kB |
| Hvor mange mangler VP-posisjon? | 95 (55 %), hvorav 80 ikke er SJ |
| Hva veier rutedata per tur? | 6,7–43,9 kB **med** `pointsOnLink`, 1,3–5,4 kB uten |
| Hvor mange kjøredatoer er i spill? | Én midt på dagen, to på kvelden |

Vektforskjellen på geometrien avgjorde arkitekturen: polylinjen er den samme
hele dagen, så den hentes én gang per tur og gjenbrukes. Uten det ville den
brede dekningen kostet megabyte i minuttet.

### Fella i vinduet

Første utkast ga alle knutepunkter samme vindu — tolv timer, som langbanene
trenger. Målt på Oslo S med tak på 200 avganger: **200 kom tilbake, hvorav to
med sanntid.** Resten var formiddagen.

`numberOfDepartures` kutter fra den **eldste** enden. Et for stort vindu gir
altså ikke for mye data, det gir feil data — og lista er full, tallet ser stort
ut, og togene som mangler er nøyaktig de som kjører nå. Derfor to grupper
knutepunkter med hvert sitt vindu, og en vakt som sier fra når et knutepunkt
nærmer seg taket.

### Fella i ID-ene

Filteret skulle sammenlikne `serviceJourney.id` fra de to kildene. Målingen sa
79 av 79 eksakt treff, så de ble sammenliknet rått.

Fem tog ble tegnet to ganger. Vehicle Positions svarer for noen Vy-tog med en
`DatedServiceJourney`-ID:

    VP  VYG:DatedServiceJourney:1931_OSL-RST_26-08-21
    JP  VYG:ServiceJourney:1931_443485-R

Målingen talte hvor mange som matchet, ikke hvor mange som var samme tog uten
å matche. Se [erfaringer.md](erfaringer.md), lærdom 14. Nøkkelen er nå
«linje:tognummer» i tillegg til den rå ID-en, og `meta.computedDropped` — det
siste nettet i `app.py` — gikk fra 5 til **0**.

### Fella i avgangene

Etter at alt virket, sto det fortsatt fem turer i `prober/sjekk_dekning.py`
under «ikke sett av knutepunktene». Årsaken var at `QUERY_HVEM` spurte om
avganger alene, så et tog som **ender** på et knutepunkt var usynlig.
`arrivalDeparture: both` tok tallet til null.

### Resultatet

Målt med `python prober/sjekk_dekning.py`, som bruker sine egne åtte
knutepunkter og derfor ikke er en sirkelslutning:

    49 turer i hullet
    17 tegnes nå (35 %)
       ingen-gps 3, mangler-posisjon 14
       13 av 17 ligger på sporet
    32 tegnes ikke:
       26  turen er ferdig
        6  turen har ikke startet
        0  underveis, men ingen posisjon

Den siste linja er den som betyr noe. **Hver eneste tur som faktisk er
underveis får nå en posisjon.** Resten av hullet er turer som ble kjørt ferdig
for timer siden — vinduet bakover er tolv timer på langbanene — og de har ingen
posisjon å regne ut.

Det som ikke ble løst: gods kommer fortsatt aldri, og dekningen av SJ er en
egen sak.

## Problem 1 — Forsinkelser som ikke er forsinkelser

**Løst, wiret og bekreftet i drift 19. august.** Koblingen mellom Vehicle Positions og Journey Planner er verifisert i full skala, 
implementert i punktlighet.py, og steg 3 er nå på plass i app.py: JP-forsinkelsene hentes, 
delay byttes der de finnes, hvert målt tog merkes med delaySource, og ringdiagrammet telles på nytt etter byttet. 
Første driftskjøring kl. 12:50 bekreftet alle fire signalene på én gang — se «Bekreftet i drift» nedenfor. 
Resten av avsnittet er hvorfor, og hva som ble målt fram.

**Problemet.** `delay` fra Vehicle Positions er systematisk feil for en del tog,
og Vehicle Positions **kan ikke forklare sin egen `delay`**. `monitoredCall` er
en blindvei: typen `MonitoredCall` har tre felt — `order`, `stopPointRef`,
`vehicleAtStop` — og ingen er en tid. For tog er hele objektet tomt:

| Modus | Kjøretøy | Med `monitoredCall` |
|---|---|---|
| BUS | 6 810 | 6 696 |
| FERRY | 358 | 358 |
| TRAM | 6 | 6 |
| **RAIL** | **104** | **0** |

Sammen med tomme `speed`, `vehicleStatus`, `destinationRef` og `destinationName`
finnes det ikke noe i svaret å holde tallet opp mot. Derfor sto problem 1 fast
så lenge dette var den eneste kilden.

**Journey Planner kan.** Koblingen `serviceJourney.id` → `estimatedCalls` virker
for alle tre GPS-operatørene, målt i full skala 19. august: **83 av 96 tog fikk
et forsinkelsestall fra Journey Planner** (59 underveis, 24 av 26 ferdige). De
13 øvrige falt tilbake på `delay` av rene, forklarte grunner — 7 ikke startet
(står på origo, VP 0,0), 4 «ingen tider», 2 ferdige uten måling. Ti Journey
Planner-kall i alt. Der begge måler, er de stort sett enige på tidelen: F4 61
40,2 mot 40,2, RE10 311 7,3 mot 7,3, RE20 385 0,0 mot 0,0 — som bekrefter at
koblingen treffer riktig tur.

**Retningen på uenigheten var feil i en tidligere utgave av dette avsnittet, og
er nå rettet.** Den
gamle teksten slo fast at når kildene spriker, sier Journey Planner 0,0 og
Vehicle Positions 19–31 minutter — «aldri motsatt i den størrelsesorden».
Kjøringen 19. august viser det omvendte som det dominerende. Av togene som
spriker mer enn to minutter er fem av seks *Journey Planner høyere* — altså
Vehicle Positions som melder toget mer i rute enn det er:

| Linje/tog | VP | JP | Status |
|---|---|---|---|
| F5 707 | 0,0 | 30,0 | underveis |
| R22 1909 | −1,1 | 5,6 | ferdig |
| F5 90704 | 0,0 | 4,5 | underveis |
| RE10 308 | 3,0 | 7,0 | ferdig |
| L5 3023 | 0,0 | 2,7 | underveis |
| R14 1012 | 7,1 | 4,5 | underveis |

Bare den siste peker andre vei. Holdt opp mot den gamle verstingen `R13 1639`
(VP 30,7, JP 0,0) betyr det at **Vehicle Positions er upålitelig i begge
retninger** — den både blåser opp og underrapporterer. «VP blåser opp, JP sier
null» dekker altså ikke lenger. Konklusjonen står likevel: der kildene er
uenige, er Journey Planner den vi kan forsvare — den måler planlagt mot
forventet på et navngitt stopp, mens `delay` er et tall uten oppgitt referanse.

**F5 707 er toget å slå opp hos Bane NOR.** VP 0,0 mot JP 30,0 — og nøyaktig
samme 30 minutter i forrige probe-kjøring. To uavhengige kjøringer, samme tog,
samme tall: en vedvarende lesing, ikke støy. Det er kandidaten til veikartets
«se et ekte tog stå på riktig sted» — et tog kartet i dag tegner grønt mens det
er en halvtime forsinket.

**Spøkelseshypotesen holder fortsatt ikke.** 26 ferdige tog i kjøringen, og
ingen av dem er blåst opp mot Journey Planner — der de ferdige spriker (RE10
308, R22 1909), er det JP som er høyest, ikke VP. Et tog som er framme og var
forsinket er ikke et spøkelse. Merk at stale-togene ikke er isolert i denne
tabellen; en egen måling på bare spøkelseskandidater gjenstår, men
live-uenigheten gir hypotesen ingen støtte.

**Kveldskjøringen 19. august 23:43 — `FERDIG`-raden er fylt, og den peker et
annet sted enn ventet.** 68 tog i feeden, 20 valgt (10 verste + 10 tilfeldige),
18 sammenlignbare. Krysstabellen som manglet:

| status | enige | uenige |
|---|---|---|
| underveis | 7 | 4 |
| FERDIG | 4 | 3 |

Uenigheten følger altså **ikke** om turen er over — 36 % mot 43 % er samme
størrelsesorden. Det var hypotesen bak hele kveldskjøringen, og den faller.

**Den følger operatøren i stedet:**

| kodespak | uenige | verst |
|---|---|---|
| VYG | 7 av 16 | 5,4 min |
| FLT | 0 av 2 | — |

Flytoget er enig med Journey Planner på hvert eneste tog. Hele spriket ligger
hos Vy. To Flytoget-tog er et tynt grunnlag, så dette er en retning å måle
videre på og ikke en konklusjon — men det er første gang uenigheten lar seg
knytte til noe annet enn tilfeldighet, og proben sier selv at det er den
observasjonen som skal hit.

To forbehold verdt å ta med:

- **Utslagene er små denne kvelden.** Verste avvik er 5,4 minutter (R12 536),
  mot de 19–31 minuttene README og eldre målinger beskriver. Ingen `F5 707` i
  utvalget. Enten er kvelden rolig, eller så bor de store utslagene et annet
  sted enn i et tilfeldig kveldsutvalg.
- **`2 av 7 ferdige tog er blåst opp`** — VP høyere enn JP, altså den gamle
  spøkelsesretningen. Det er RE20 398 (VP 0,0 mot JP −3,7) og R13 1671
  (VP 2,6 mot JP 1,4). Begge er små, og 2 av 7 på ett utvalg omgjør ikke
  avsnittet over. Men det er første observerte tilfelle av oppblåste ferdige
  tog, og motsier ordrett «ingen av dem er blåst opp» fra måling i full skala.
  Neste kveldskjøring bør se etter om det gjentar seg.

Go-Ahead hadde 4 tog i feeden, men ingen kom med i utvalget på 20. GOA er
dermed fortsatt umålt i denne sammenligningen.

**Implementasjonen.** `punktlighet.py` gjør for Vy, Flytoget og Go-Ahead det
`sjnord.py` gjør for SJ: henter begge kjøredatoer, velger instansen som
omslutter nå (`velg_instans`, nå løftet inn i `sjnord.py` og delt med proben),
og leser forsinkelsen bakover fra siste passerte stopp (`_last_measured`). Alt
tungt gjenbrukes, så det finnes én utgave av spørringen. 
Budsjett: tog i grupper på tjue × 2 datoer. Målt i drift 12:50: tolv 
Journey Planner-kall for 103 tog (seks grupper på inntil tjue, ganger to datoer) — 
litt over anslaget på ~10 fordi flåten var større enn de 96 anslaget bygde på. `app.py`
bytter `delay` til JP-tallet der det finnes, faller tilbake på VP ellers, og
merker hvert tog med `delaySource: journey-planner | vehicle-positions` — samme
tanke som `positionMethod`. Ringdiagrammet telles på nytt etter byttet, ellers
viser kartet JP-tallet mens tellingen står på det gamle.

**Bekreftet i drift 19. august 12:50.** 
Første snapshot etter wiringen ga to logglinjer rett etter hverandre:
punktlighet: Journey Planner: 79 av 103 tog fikk forsinkelse 
(FERDIG=25, ikke startet=9, ingen tider=12, underveis=57)
togkart:     Punktlighet påført: 79 av 103 målte tog fikk JP-tall (resten VP-delay)

**Fellen i proben (permanent lærdom).** Nattkjøringen 02:09 meldte «251 av 251
scheduled — ingen sanntid» og konkluderte at veien var stengt. Feil: togene i
feeden klokka to gikk før midnatt og tilhører gårsdagens kjøredato, men proben
spurte om i dag og fikk morgendagens instans, som naturligvis har rutetid på
hvert stopp. En fullstendig omvending til 100 % `scheduled` er signaturen til
feil instans, ikke til en stille feed. Både proben og `punktlighet.py` henter
derfor begge kjøredatoer og velger etter hvilken som omslutter nå. Se lærdom 8 i [erfaringer.md](erfaringer.md).

**Kveldskjøringen er ikke lenger sperre, men fortsatt nyttig.** Den er kjørt
19. august 23:43, og `FERDIG`-raden er fylt — se avsnittet over. Det som
gjenstår er spøkelseshypotesen med mange parkerte tog samtidig, og å måle
Flytoget-observasjonen på et større utvalg enn to tog.

**Løse tråder:**

- Fire tog fikk `serviceJourney = null` på begge datoer (ukjent ID) og fire
  «ingen tider». Begge faller tilbake på VP-delay, som er riktig oppførsel.
- **Observert 19. august:** `publicCode` forsvant for 90 av 101 tog i noen
  minutter og kom tilbake av seg selv. `sjekk.py linjekoder` vokter det nå, og
  `entur._line_code` faller tilbake på `lineRef` med kolonklipp — så det står
  `R13` i popup-en og ikke `VYG:Line:R13`.

## Problem 2 — Interpolasjonen går i rett linje

**Løst og verifisert.** Gjenstår bare én visuell sammenligning mot Bane NOR.

SJ-tog ble plassert på rett linje mellom to stasjoner. Over Saltfjellet og
Dovre ligger stasjonene 20–30 km fra hverandre, så toget skar over terrenget.
F6 (tog 42) ble tegnet nordvest for Eidsvoll, ute i Mjøsa, mens Bane NOR viste
toget rett sør for Stange. En modell av strekningen ga **opptil 11,9 km avvik**.

**Løsning:** `serviceJourney { pointsOnLink { points } }` gir traseen som en
Google-kodet polylinje. To detaljer som ikke var åpenbare:

- Feltet dekker **hele turen**, ikke ett strekk om gangen. Hvert stoppested må
  snappes til linjen først. Det er derfor `sporgeometri.py` ble mer enn en
  dekoder.
- Feltet ligger i den **nøstede** spørringen. Kjenner ikke Entur feltet, feiler
  hele spørringen — og da forsvinner samtlige SJ-tog. `sjnord.py` slår derfor
  `pointsOnLink` av og prøver på nytt hvis Entur klager.

Hver posisjon merkes `positionMethod: track` eller `straight`. Mangler
geometrien, eller ligger et stoppested mer enn en kilometer fra traseen, brukes
rett linje — en ærlig luftlinje er bedre enn en presis posisjon på feil bane.

Verifisert med `prober/sjekk_geometri.py`, sist 19. august 02:00:

| Sjekk | Resultat |
|---|---|
| Turer med geometri | 47 av 47, ingen tomme strenger |
| Punkter per trasé | 1 646 – 18 651 (median 4 569) |
| Traséer godkjent av `bygg_trase` | 47 av 47 |
| Verste stopp-avvik per tur | median 5 m, verst 9 m |

Enturs `length` er **antall punkter, ikke meter**, og stemte eksakt med
dekoderens tall i alle stikkprøver. Det er en sterkere test enn at kartet ser
riktig ut: en dekoder som mister ett punkt ville slått ut umiddelbart.

**Halvveis verifisert visuelt:** F6 45 lå på sporet ved Sjoa. Det beviser at
toget er på skinner, ikke at det er på riktig punkt *langs* skinnene.

**Punktet langs skinnene: trolig for langt fremme.** 19. august, to avlesninger
mot Bane NORs kart for F6 42 (Hamar → Oslo lufthavn):

| Tid | Bane NOR (observert) | Systemet (beregnet) | Avvik |
|---|---|---|---|
| 14:03 | like sør for Tangen stasjon (60,616°N) | 60,505°N (14:04) | ~11–13 km for langt sør |
| 14:07 | kommunegrensa sør for Strandlykkja/Morskogen (~60,47–60,49°N) | 60,440°N | ~2–5 km for langt sør |

Samme retning begge ganger, ikke tilfeldig støy: **systemet plasserer toget
lenger fremme på ruta enn Bane NOR viser det**, mens sporgeometrien treffer
riktig bane (bekreftet av `sjekk_geometri.py`, se over). Feilen ser dermed ut
til å ligge i *hvor langt* langs traseen interpolasjonen regner at toget har
kommet, ikke i traseen selv. Tallene er fra manuelle avlesninger og for grove
til å fastslå eksakt størrelse — verdt å måle presist når SQLite-loggingen
(problem 4) finnes, med et fast landemerke og eksakt klokkeslett i stedet for
stasjonsnavn anslått fra et kart.

## Problem 3 — Feil kjøredato i den nøstede spørringen

**Løst, og 19. august verifisert i full størrelse.**

`ServiceJourney.estimatedCalls` tar en `date`-parameter som ikke ble sendt.
Følgen var større enn overskriften antydet: **kartet var tomt for SJ-tog fra
midnatt til første avgang neste morgen.** På dagtid skjuler feilen seg, fordi
avgangene i vinduet uansett tilhører i dag.

`sjnord.py` henter i to steg. `QUERY_HVEM` spør hvem som går og hvilken
kjøredato de tilhører — slankt svar med vilje. Turene grupperes etter dato, og
detaljene hentes med datoen eksplisitt, tjue om gangen via GraphQL-aliaser
(`t0: serviceJourney(id: $id0)`).

Godtar ikke Entur `date`, faller `sjnord.py` tilbake til `QUERY_SAMLET`. Da er
feilen tilbake, men togene er der, og loggen sier fra.

**Verifisert 19. august 02:09** — nøyaktig det tidspunktet som ga null tog før:

```
Fant 47 SJ-turer på 1 kjøredato(er): 2026-08-18 (47)
Beregnet posisjon for 2 SJ-tog (2 med sanntid, 0 kun rutetabell;
                                2 langs spor, 0 i luftlinje)
```

Samtidig målte `prober/sjekk_dato.py` kilden uten `date`: 0 riktige, 42 feil,
alle nøyaktig **+24,0 t**. Feilen i full skala, målt på nytt, mot en pipeline
som nå håndterer den.

Proben spør fortsatt *uten* `date`, med vilje: den måler kilden, ikke fiksen.
Kjør den om natta hvis du vil se feilen i full størrelse.

**Merk for helsesjekken:** etter midnatt skal `Fant N SJ-turer på K
kjøredato(er)` vise **én** dato, og den skal være gårsdagens. Ser du dagens
dato alene klokka to om natta, er noe galt.

**Bivirkning som holder:** gråtogene ser ut til å være borte. Tre målinger på
rad med `0 kun rutetabell` og `Ukjent 0` i ringdiagrammet. Kommentaren i
`positions()` om at «rundt halvparten aldri får sanntid» bør skrives om — det
var sannsynligvis samme feil hele veien.

## Problem 6 — Tog som tegnes to ganger

**Løst 18. august.** To ulike årsaker bak samme symptom.

**Rørledningene overlappet.** `sjnord.py` hadde ikke noe operatørfilter i det
hele tatt — alt som gikk på skinner forbi de seks knutepunktene ble plukket
opp, også Vy og Go-Ahead, som har GPS. Fiksen er en hvitliste på kodespaket:

```python
KODESPAKET_UTEN_GPS = {"SJN"}
```

Filteret ligger i **steg 1**, før detaljhentingen, så forkastede turer slipper
å dra med seg en polylinje på titusen punkter. Hvitliste og ikke svarteliste,
fordi feilmodusen er ærligere: endrer Entur kodespaket, forsvinner de nordlige
togene med en gang og loggen sier hvem som ble hoppet over.

I tillegg dropper `_uten_duplikater()` i `app.py` en beregnet prikk hvis samme
(linje, tognummer) allerede finnes som målt. Det er et sikkerhetsnett, ikke
fiksen — slår det til, står det `warning` i loggen.

**Dobbeltsett rapporterte hver for seg.** F5 tog 706 lå som to prikker null
meter fra hverandre: to BM73-sett koblet sammen, hvert med sin egen sender.
Begge posisjonene ekte. Følgen var ikke kosmetisk — **et dobbeltsett telte to
ganger i ringdiagrammet og i all punktlighetsstatistikk**, en systematisk
skjevhet som traff nettopp de lengste togene.

`_merge_coupled()` i `entur.py` grupperer på (linje, tognummer). Fire valg som
er verdt å kjenne:

- **Sammenslåingen skjer før opptellingen.** Ellers har vi bare flyttet feilen
  dit den er vanskeligere å se.
- **Avstandskrav på én kilometer.** Deler to kjøretøy tognummer *og* står langt
  fra hverandre, har `_train_number` tolket feil. Da beholdes begge, og loggen
  ber deg kjøre `python sjekk.py tognummer`.
- **Posisjonen fra det ferskeste kjøretøyet, ikke et gjennomsnitt.** Prikken
  skal være en måling noen faktisk sendte.
- **ID-ene sorteres alfabetisk og skjøtes med kolon:** `73-04:73-16`. Sortert,
  slik at nøkkelen er den samme hver eneste henting.

Merk at `stock`-ordboken **kopieres** før den endres. `rolling_stock()` deler
oppslagstabellen mellom alle tog av samme type; skriver du rett i den, står det
«4 + 16» på hvert eneste BM73 i kartet.

**Gjenstår:** `static/app.js` viser `stock.unit` som før. Det virker, men det er
ingen egen merking av at toget er et dobbeltsett. `coupled` og `vehicleIds`
ligger klare.

## Problem 7 — Geometrihull i hovedbanene

**Randsfjordbanen: løst 19. august.** Hokksund–Hønefoss manglet, og uten den
stoppet Bergensbanen i Hokksund og startet igjen på Hønefoss. På zoom 6 så
banen ut til å begynne midt inne i landet.

Hullet var usynlig over zoom 8, fordi flisene tegner sporet uansett. Verdt å
merke seg som mønster: **et lag som overlapper et annet, skjuler det andres
hull.**

Lagt inn med `rang: 6`, samme som Bergensbanen — ikke ny bunnplass. Rang er
prioriteringslista, og gir du senere de viktigste banene egen bredde eller
farge, må denne følge med, ellers går korridoren i stykker på nytt. OSM ga
**48,3 km mot ventet 54**, altså 89 %.

**Raumabanen: lagt inn 19. august.** Dombås–Bjorli–Åndalsnes, ned Romsdalen
til fjorden. Uten den stoppet jernbanen i kartet ved Dombås, og hele
Møre-siden så ut til å være uten spor. OSM har banen komplett — 114,8 km målt
mot 114 ventet — så Entur-reserven ble aldri aktuell.

Samme `min_bit: 0` som Bratsbergbanen, og av samme målte grunn: OSM deler
banen i 414 biter, og ved 300 m forsvinner 17 % mens verste hull vokser fra
91 m til 832 m. Romsdalen er bru og tunnel på rekke, og det er nettopp der
oppdelingen blir tett. Lagt inn med `rang: 15`.

**Rørosbanen: lagt inn 19. august.** Hamar–Elverum–Røros–Støren, 384 km, der
den møter Dovrebanen og følger den inn til Trondheim. Uten den var hele
Østerdalen og Nord-Østerdal tomt for jernbane, og kartet så ut som om det bare
går ett spor nordover. Lagt inn med `rang: 14`, som er ankomstrekkefølge og
ikke viktighet — banen har daglige persontog og bærer mer trafikk enn
Bratsbergbanen på 13. Skal rang en dag styre bredde eller farge, hører den
over. Å omnummerere tretten baner for å si det, ville bare flyttet
uoverensstemmelsen til dokumentasjonen.

**Bratsbergbanen: løst 19. august, og svaret var et annet enn begge
hypotesene.** Banen manglet ikke i OSM. Målestokken var feil.

Målt uten å skrive fil: OSM-relasjonen (`1946815`, 177 medlemmer) dekker
Porsgrunn–Skien–Nordagutu **sammenhengende** — verste hull 10 meter — og
summerer til 47,4 km. Enturs egen ruting av samme strekning sier 44,0 km, med
nesten identisk bbox. To uavhengige kilder på ~45 km, mot en `km`-verdi på 74.

Det er 74-tallet som var galt. Og fordi terskelen for å falle tilbake til
Entur er 50 % av `km`, klarte en **komplett** henting så vidt 37 km-grensen og
så ut som en halvfeilet en. `km` er nå 47, og hentingen ligger på 96 % der den
hører hjemme.

**Filteret kuttet i tillegg hovedspor, ikke sidespor.** Målt på samme data:

| `min-bit` | biter | km | verste hull |
|---|---|---|---|
| 0 m | 177 | 47,4 | 10 m |
| 100 m | 94 | 45,0 | 493 m |
| 300 m | 54 | 38,4 | 946 m |

At hullene *vokser med terskelen* er hele beviset: var de korte bitene
sidespor, ville det ikke revnet noe av å fjerne dem. OSM deler banen ved hver
bru og hver tunnel, og de bitene er ledd i kjeden. Bratsbergbanen har derfor
`min_bit: 0`.

**Entur-reserven pekte dessuten feil vei.** Den sto på Skien–Notodden, men
Notodden ligger på den andre siden av Nordagutu og er ikke Bratsbergbanen —
reserven ville tegnet 54 km av noe annet enn banen den het. Nå går den
Porsgrunn–Nordagutu, som er banen.

**Drammenbanen og Gardermobanen: målt 19. august, samme feil to ganger til.**
De sto på 174 % og 278 % av `km`. Ingen av dem hadde for mye data — begge
`km`-verdiene beskrev bare det *ene* av de *to* navnene vi henter.

| Bane | `osm` henter | `km` sto på | er egentlig |
|---|---|---|---|
| Drammenbanen | Drammenbanen + Askerbanen | 53 | 42 + 17 = **59** |
| Gardermobanen | Gardermobanen + Hovedbanen | 64 | 64 + 68 = **132** |

Drammenbanens 53 var i tillegg utdatert på egen hånd: det er traseen om
Spikkestad, slik den var før Lieråsen-tunnelen åpnet i 1973. Jernbanedirektoratet
oppgir 41,66 km for Oslo S–Drammen i dag. Tallet var altså feil av to grunner
samtidig, og begge pekte samme vei.

Gardermobanen og Hovedbanen er to selvstendige baner mellom Oslo og Eidsvoll —
den ene om Romeriksporten, den andre om Strømmen og Jessheim. At vi henter
begge er med vilje; at vi målte dem mot den enes lengde var det ikke.

Hvert navn hentet for seg, som var det som avgjorde saken:

| OSM-navn | spor-km | korridor-km | spor | offisiell rute |
|---|---|---|---|---|
| Drammenbanen | 78,2 | 42,5 | 1,7 | 42 (Oslo S–Drammen) |
| Askerbanen | 32,3 | 18,2 | 1,8 | 17 (Lysaker–Asker) |
| Gardermobanen | 104,8 | 55,0 | 1,9 | 51 (kun Lillestrøm–Eidsvoll) |
| Hovedbanen | 100,4 | 71,6 | 1,4 | 68 (Oslo S–Eidsvoll) |

Alle fire treffer sin egen offisielle lengde. Ingenting fremmed hadde sneket
seg inn, og ingenting manglet — bortsett fra én ting som er verdt å vite:

**OSM navngir ikke Romeriksporten.** «Gardermobanen» i OSM begynner på
Lillestrøm, ikke Oslo S; bboxen starter på 11,04 °Ø. De 13 kilometerne inn til
byen bærer ikke banenavnet. Det gir ingen hull i kartet, fordi Hovedbanen
dekker Oslo–Lillestrøm i dagen rett ved siden av, men det forklarer hvorfor de
to til sammen gir 119 km korridor og ikke 132.

Merk at `km` likevel står på 132, altså på banenes offisielle lengder og ikke
på de 119 vi får. En fasit som justeres ned for å passe målingen, kan ikke
lenger dømme den. Det er hele poenget med å ha en.

**Resten er dobbeltspor, og det er ikke en feil.** OSM tagger hvert spor som
sitt eget way, så en dobbeltsporet bane gir omtrent dobbelt så mange
spor-kilometer som rute-kilometer. Målt med et 60-meters rutenett over
geometrien:

| Bane | spor-km | korridor-km | spor per korridor |
|---|---|---|---|
| Drammenbanen | 92,0 | 57,7 | **1,59** |
| Gardermobanen | 177,9 | 116,9 | **1,52** |
| Rørosbanen | 361,4 | 432,5 | 0,84 |
| Nordlandsbanen | 682,1 | 770,0 | 0,89 |
| Sørlandsbanen | 489,5 | 544,1 | 0,90 |

De tolv enkeltsporede banene ligger alle mellom 0,83 og 0,91 — det er
rutenettets egen skjevhet, og dermed nullpunktet. De to som skiller seg ut,
gjør det fordi de faktisk har to spor. Korrigert for nullpunktet: 1,8 spor på
Drammenbanen, 1,7 på Gardermobanen. Det stemmer med virkeligheten —
Drammenbanen er dobbeltsporet hele veien og firesporet Lysaker–Asker når
Askerbanen regnes med.

Korridorlengden er fasiten å sammenligne med, og den treffer: 57,7 mot 59
ventet, og 116,9 mot 132.

**Sjekken sammenlignet to ulike størrelser.** Med riktig `km` falt Gardermobanen
til 135 %, men Drammenbanen ble liggende på 156 % og ga fortsatt MERK ved hver
bygging. Det var ikke en rest av feilen — det var taket som målte spor-km mot
rute-km. På en dobbeltsporet bane *skal* de forholde seg som to til én.

Derfor er `spor` innført, etter samme mønster som `min_bit`: et valgfritt tall
per bane som sier hvor mange parallelle spor korridoren har. Det **hever bare
taket**. Gulvet på 50 %, det som utløser Entur-reserven, måles fortsatt mot ren
rutelengde — ellers måtte en dobbeltsporet bane mangle 75 % før reserven slo
inn, og vi hadde byttet et støyende varsel mot et stumt. `_test_maalestokk`
vokter nettopp den grensen.

Begge banene har `spor: 2`. Etter rettingen gir ingen av de seksten banene
lenger MERK eller reserve.

**`min_bit` var derimot uskyldig her.** Hypotesen fra Bratsbergbanen holdt ikke:

| Bane | verste hull ved `min-bit 0` | ved `min-bit 300` |
|---|---|---|
| Drammenbanen | 1557 m | 1557 m |
| Askerbanen | 7 m | 67 m |

Hullet på Drammenbanen står helt stille når filteret skrus av, altså er det
ikke filteret som lager det. Det ligger inn mot Oslo S, der sporet ikke bærer
navnet «Drammenbanen» i OSM, og det er dekket av både `rail-network` og de
andre banene inn mot samme stasjon. Ingen av de to har fått `min_bit: 0` — den
samme målingen som dømte filteret på Bratsbergbanen frikjenner det her.

**Lærdommen er generell:** en målestokk som er feil, gjør riktige data
mistenkelige. `km` må være banens faktiske lengde, ikke et tall fra en
overskrift — og henter `osm` flere navn, må `km` være summen av dem.

**Felle å kjenne:** `python lagbaner.py --bane X` skriver
`hovedbaner.geojson` med **bare** den ene banen. Test med `--bane`, bygg så alt
med `python lagbaner.py` før du laster kartet.

## Full testrunde 20. august, og tre feil den fant

Hele testparken kjørt etter økta med stasjoner og tooltip. Alle grønne til
slutt, men runden var ikke uten funn — tre reelle feil, og den ene hadde
ligget der siden før denne økta.

*`prober/smoketest.py` kunne ikke kjøres slik README sa.* Python legger
skriptets egen mappe på `sys.path`, ikke den du står i, så
`python prober/smoketest.py` feilet med `No module named entur`. Fem av de
ni probene hadde allerede shimmen som retter det; smoketesten — den README
kaller «kjør denne FØRST» — hadde den ikke. Én linje.

*Cachen på `/api/statistikk/*` ignorerte `dager`.* Nøkkelen var bare
`"operatorer"`, uten argumentene. `?dager=90` fikk derfor det `?dager=1`
hadde lagt igjen, i fem minutter av gangen. Det er ikke en treg cache, det er
feil svar — hvem som spurte først avgjorde hvilket tidsvindu alle andre fikk.
Verifisert som feil (`dager=90` → `dager: 1`), rettet, verifisert som riktig.
Egen feil, innført dagen før.

*Tognummeret på sammenkoblede løp var feil, og forurenset historikken.*
`sjekk.py alle` ga **FEIL** på unikhetstesten: `VYG 838`. Ikke støy — to Vy-tog
32 km fra hverandre delte tognummer. Årsaken viste seg å være et løp som bytter
nummer underveis, der Entur gir begge halvdelene samme ServiceJourney:

```
VYG:ServiceJourney:838-341_515579-R
  vehicleId 838-2026-08-19   -> er tog 838
  vehicleId 341-2026-08-20   -> er tog 341
```

Regelen «nummeret står først i journey-ID-en» ga 838 til begge. Konsekvensen
var verre enn en feil etikett: `_tog_id` i `historikk.py` nøkler på
(linje, tognummer), så de to togene smeltet sammen til én rad-serie i
databasen — og medianen i `analyse.py` ble et blandingstall av to forskjellige
tog. `sjekk.py historikk` viser `RE11:838` som mest loggede tog med 246 rader;
de radene er to tog i én bunke.

Vy legger sitt eget tognummer i `vehicleId` foran kjøredatoen
(`838-2026-08-19`), og det mønsteret — siffer, bindestrek, ISO-dato — treffer
ingen av settnumrene til Flytoget (`71`) eller Go-Ahead (`73-08`). Målt mot
feeden før endringen: 22 av 22 Vy-kjøretøy hadde formen, ingen FLT eller GOA
hadde den, og 21 av 22 ga samme svar som journey-ID-en. Det tjueandre var
feilen. Regelen står derfor først i `_train_number()`, med settnummer-testen
som regresjonsvern i `prober/test_dobbeltsett.py`.

**Radene som allerede er skrevet blir ikke rene av dette.** `vehicleId` lagres
ikke i `historikk.db`, så de sammenblandede RE11:838-radene kan ikke skilles i
ettertid — de må enten slettes eller leves med. Nye rader er riktige.

*Og en fjerde, funnet ved å prøve i stedet for å lese:* XSS-en i punkt 1 i [sikkerhet.md](sikkerhet.md).
Den ble ikke funnet av testparken — ingen av probene ser på
frontend. Det er verdt å merke seg: ni prober, seks helsesjekker og to
regresjonstester dekker dataene grundig og nettleseren ikke i det hele tatt.

*Testparken slik den står nå* — alle kjørt 20. august, alle grønne:

| Kjørt | Utfall |
|---|---|
| `sjekk.py alle` | Alt grønt (var FEIL på tognummer før fiksen) |
| `sjekk.py tognummer` / `linjekoder` / `historikk` / `diagnose` / `spokelser` / `operatorer` | OK |
| `prober/smoketest.py` | OK — 30 tog, alle med posisjon, delay og linjekode |
| `prober/test_geometri.py` | Alt grønt, ti sjekker |
| `prober/test_dobbeltsett.py` | Alt grønt, åtte sjekker (to nye) |
| `prober/sjekk_geometri.py` | 1 av 1 tog langs spor, median flytting 4,1 km |
| `prober/sjekk_dato.py` | Problem 3 fortsatt observerbart, som ventet |
| `prober/sjekk_punktlighet.py` | GOA 3 av 3 uenige, VYG 4 av 12 — problem 1 lever |
| `prober/sjekk_duplikater.py` | 0 duplikater |
| `sporgeometri.py` / `materiell.py` / `lagbaner.py --selvtest` / `lagstasjoner.py --selvtest` | OK |
| `sjnord.py` | 48 turer, 48 av 48 følger sporet |
| `analyse.py` | Begge tabellene skrives |

`prober/sanntidsjekk.py` avslutter med 1 og «Ingen tog underveis fra
Trondheim S akkurat nå» når den kjøres om natta. Det er en tilstand, ikke en
feil — den trenger tog i trafikk for å ha noe å se på.
