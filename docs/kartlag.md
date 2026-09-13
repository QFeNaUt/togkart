# Kartlagene

Hvordan kartet tegnes: hvilke lag som finnes, i hvilken rekkefølge, og hvorfor
hvert tall er som det er. Backend og dataflyt står i [arkitektur.md](arkitektur.md);
runbooken står i [feilsoking.md](feilsoking.md).

---

Fire lag tegner jernbane, og de har helt ulik nøyaktighet. Å ikke vite hvilket
man ser på var kilden til en dobbelttegnet strek som sto i kartet i dagevis.

| Lag | Kilde | Geometri | Zoom |
|---|---|---|---|
| `hovedbane-glow` | `hovedbaner.geojson` | RDP 150 m | 4–13 |
| `hovedbane` | `hovedbaner.geojson` | RDP 150 m | 4–10,2 |
| `rail-glow` | carto sine vektorfliser | uforenklet | 8+ |
| `rail-network` | carto sine vektorfliser | uforenklet | 8+ |
| `stasjon` | `stasjoner.geojson` | punkt | 4+ |
| `stasjon-navn` | `stasjoner.geojson` | punkt | 7+ |

Cartos **egne** jernbanelag (`rail`, `rail_dash`, `tunnel_rail`,
`tunnel_rail_dash`) er `#1a1a1a` og tegnes først fra zoom 13. De er usynlige og
ikke en del av bildet — verdt å vite, siden det var den første hypotesen da
doblingen ble oppdaget, og den var feil.

## Hvorfor korridorlaget skifter rolle

Målt i konsollen: flisene har **null** jernbanesegmenter under zoom 8, og fra
zoom 8,0 kommer de. Under 8 er korridorlaget alene om å tegne noe som helst.
Over 8 tegner begge det samme sporet, med ulik nøyaktighet.

RDP-toleransen i `lagbaner.py` er 150 meter. På 59,4°N blir det:

| Zoom | m per piksel | 150 m blir |
|---|---|---|
| 8 | 156 | 0,96 px — usynlig |
| 9 | 78 | 1,9 px — så vidt |
| 10 | 39 | 3,85 px — synlig som dobbel strek |
| 11 | 19,5 | 7,7 px — åpenbart feil |
| 12 | 9,7 | 15,4 px |

Derfor tre soner:

- **Strek, z4–8,5.** Full styrke. Vi er alene om å tegne.
- **Overgang, z8,5–10,2.** Den skarpe streken tones ut mens avviket ennå er
  under fire piksler. `rail-network` overtar som sporlinje.
- **Glød, z10–13.** Igjen står en bred, uskarp linje som merker hvilken
  korridor sporet tilhører — noe flisene ikke kan, siden de har `class` og
  `subclass`, men ikke banenavn.

Uskarpheten er ikke pynt. En diffus linje har ingen kant å sammenligne mot, så
de 150 meterne leses som bredde i stedet for som feil. Halv bredde pluss
`line-blur` må dekke avviket: 14,5 px mot 7,7 ved zoom 11, 18 mot 15,4 ved 12.
Ved 13 tar avviket igjen oss, og der er gløden allerede borte.

**Endrer du `TOLERANSE_M`, er tabellen over det eneste som må regnes om.**
Zoomstoppene i `app.js` henger på den, ingenting annet gjør det.

## Gløden stables der korridorer møtes

Korridorer som deler spor overlapper med vilje i geometrien, og MapLibre
blander dem: N lag med opasitet *a* gir 1−(1−a)^N. Rundt Oslo S møtes samtlige,
og opasitet 0,20 ble til 0,67 — en cyan tåke. Verre enn stygt: gløden sier
«dette er en hovedkorridor», og der alle er det, sier den ingenting.

Opasiteten er derfor 0,12–0,15, som gir 0,47 på det samme stedet og er nesten
umerkelig svakere ute på åpne strekninger. **Det er det ene tallet å skru på**
hvis knutepunktene fortsatt er for varme.

Et alternativ som ikke er prøvd: la opasiteten følge `rang`, slik at rang 2 og
3 bidrar halvt der de stables oppå rang 1.

## Tegnerekkefølgen

MapLibre tegner lagene i den rekkefølgen `addLayer()` kalles. Rekkefølgen i
`map.on("load")` er derfor ikke stil, den er innhold — og hvert hopp i den
har en grunn:

| # | Lag | Hvorfor akkurat her |
|---|---|---|
| 1 | `hovedbane*` | Forenklet geometri nederst. Over `rail-network` ville gløden dekket sporet den skal merke |
| 2 | `rail-network*` | Nøyaktig sporgeometri fra flisene, oppå den forenklede |
| 3 | `flaskehals-*` | Over sporet fordi det er strekningene det beskriver; under stasjonene fordi strekene er 20 px brede på full zoom |
| 4 | `stasjon*` | Punkter på sporet, skal ligge oppå det — ellers forsvinner ringen der sporet er bredest |
| 5 | `valgt-rute-*` | Over skinnene, under togene |
| 6 | `avvik-*` | Ringene rundt driftsmeldinger. Under togene: et tog som står i en stengt strekning skal ikke gjemmes av ringen som forklarer hvorfor |
| 7 | `trains-cluster*` | Under enkelttogene, så en enslig prikk inntil en klynge tegnes over den |
| 8 | `trains-glow`, `trains-dot`, `trains-arrow` | Togene |
| 9 | `sokte-tog-*` | Søketreff over alt annet — de er svaret på et spørsmål brukeren nettopp stilte |
| 10 | `trains-label` | Linjekodene helt øverst, så de aldri havner under en prikk |

**Rekkefølgen mellom togprikkene innbyrdes er en egen sak.** Innenfor ett lag
avgjorde tidligere rekkefølgen i GeoJSON-arrayet hvem som lå øverst — og den
er tilfeldig: den følger hvilken rekkefølge Entur nevnte kjøretøyene i. Rundt
Oslo S ligger det til enhver tid ti–tjue prikker delvis oppå hverandre, så et
tog som var tjue minutter forsinket kunne bli liggende under et som var i
rute. Nettopp avviket, det ene man skal se, forsvant.

`circle-sort-key` på `trains-dot` sorterer nå eksplisitt: spøkelsestog (−1),
ukjent (0), i rute (1), på grensen (2), forsinket (3), mye forsinket (4).
Høyest nøkkel tegnes sist, altså øverst. Nøkkelen leser `band` og ikke `delay`
direkte — `delay` kan være null, og null i en sorteringsnøkkel gir udefinert
rekkefølge.

## Stasjonene

Lagt inn 19. august med fem stasjoner, utvidet til **335** 20. august.
`lagstasjoner.py` henter dem fra Enturs stoppestedsregister og skriver
`static/stasjoner.geojson`.

**Hvorfor egne prikker når bakgrunnskartet allerede skriver bynavn.** Det er to
forskjellige punkter. En by-etikett står der rådhuset ligger, en stasjon står
der sporet går, og på Bergensbanen er det halvannen kilometer mellom dem. Skal
du lese hvor et tog er i forhold til stasjonen det kjører mot, må det være
stasjonen som er tegnet.

**Symbolet er Bane NORs, oversatt til mørkt tema.** Standardsymbolet på
togkart.banenor.no er en fylt skive i sporfargen med et hull i kartbakgrunnens
farge. Målt i `docs/bilder/ikon_stasjon.png`: 13 piksler ytre diameter, 5 pikslers hull —
en ring omtrent halvannen gang så tykk som hullet er bredt.

Oversettelsen er derfor ikke «blå ring», men «ring i sporfargen, hull i
bakgrunnsfargen»: `--rail` utenpå, `--bg` inni. Det er ett `circle`-lag, ikke
to stablede — `circle-radius` er hullet, `circle-stroke-width` er godset rundt.
Hadde hullet vært hardkodet hvitt, ville stasjonene lyst som hull i kartet i
stedet for å ligge på sporet, og et temabytte ville brutt dem.

**Zoomnivåene, og hvorfor det er to sett.** `viktighet` var lagt inn på hver
stasjon fra første dag nettopp for denne dagen, og utløpsdatoen kom 20. august:
335 prikker ved zoom 4 er den cyan tåka gløden ble rundt Oslo S. Delingen går
nå på `viktighet` — 1 er de fem landsdelshovedstedene, 2 er de andre 330 — og
er to par lag med motsatte filtre, ikke ett lag med et zoom-uttrykk. Filtrene
er hverandres negasjon (`== 1` mot `!= 1`), så ingen stasjon tegnes to ganger;
gjorde de det, ville de fem fått dobbelt strek og en etikett oppå seg selv.

Alle fire terskler står i `STASJON_ZOOM` øverst i stasjonsavsnittet i
`app.js`. Det er ett sted å skru, og `alle: 12` er den å skru på:

| | zoom | hva |
|---|---|---|
| `viktige` | 4 | prikkene til de fem |
| `viktigeNavn` | 7 | navnene til de fem |
| `alle` | 12 | prikkene til de 330 |
| `alleNavn` | 12 | navnene til de 330 |

`alleNavn` skal aldri være lavere enn `alle` — da står etiketter uten prikk
under seg. Etikettkollisjonene i Oslo ordner MapLibre selv, men
oversiktsstasjonene har `symbol-sort-key: 0` slik at «Oslo S» ikke taper mot
«Grorud stasjon» i klyngen. Uten den er det tilfeldig hvilket navn som
overlever.

Radius- og strekrampene går nå til zoom 16 og ikke til 12. Stoppet de der,
ville de 330 stått med sin minste størrelse gjennom hele det zoomområdet der
de er det eneste man ser på.

**Hvordan «samtlige stasjoner» ble avgrenset.** `stopPlacesByBbox` med
`filterByInUse: true` gir 50 238 stoppesteder i bruk innenfor Norge-boksen,
hvorav 446 har `rail`. Boksen er et rektangel og drar med seg halve Sverige —
Bastuträsk og Abisko kom inn sammen med Alna og Alvdal.

Fire filtre ble prøvd før ett virket:

1. **`authority`-argumentet på `stopPlacesByBbox`.** Ga null treff for alle de
   norske myndighets-ID-ene. Ubrukelig.
2. **Geocoderens `countryCode`.** Finnes og er riktig (`no` mot `se`), men bare
   per navneoppslag — 446 kall med navnematching, altså den samme
   autocomplete-fella lista skulle bort fra.
3. **NSR-API-et** (`/stop-places/v1/graphql`), som har topografisk sted. Svarer
   403 uten egen tilgang.
4. **`estimatedCalls` per stoppested**, med `authority` på linja. Virket, og er
   det som står nå.

Regelen ble derfor: **en stasjon er med hvis ett av de fire togselskapene
kartet viser tog for har en avgang derfra det neste døgnet.** Ikke «ligger i
Norge» og ikke «finnes i registeret» — begge er påstander om geografi som noen
må vedlikeholde, mens denne er en observasjon appen allerede gjør.

446 ble til 335. Det som falt fra:

- **Museumsbaner.** Grovane (Setesdalsbanen), Krøderen (Krøderbanen) og Løkken
  (Thamshavnbanen) står i registeret uten avganger.
- **Rene svenske strekninger.** Bastuträsk og Abisko har bare `SJV` (SJ AB).

Det som ble med, og som er verdt å vite om: **svenske stasjoner på norske
ruter.** Charlottenberg, Karlstad, Örebro og Stockholm C ligger på F1, som
Vy Tåg kjører, og kartets egne tog kan stoppe der. `operatorer` skrives på hver
stasjon, så du kan se hvorfor den kom med.

**Kodeområde, ikke myndighet.** Bergensbanen ligger under
`VYG:Authority:VYT` og Oslo–Stockholm under `VYG:Authority:TAG`, mens resten av
Vy ligger under `VYG:Authority:VY`. Filtreres det på myndighet i stedet for på
kodeområdet foran kolonet, forsvinner Bergen, Voss, Finse, Geilo og Hønefoss ut
av kartet. Det er 56 stasjoner, og de ser ikke ut som en feil før noen leter
etter Voss.

**`avganger` er målt, og taket bet første gang.** Veikartet ba om «antall
avganger per døgn» som den ærligste kilden til `viktighet`, og feltet ligger nå
på hver stasjon. Første kjøring sto med `numberOfDepartures: 400`, og fem
Oslo-stasjoner lå akkurat der — et sensurert tall som får de travleste
stasjonene til å se like travle ut. Taket er nå 1200, og byggingen sier fra
hvis noen treffer det. Oslo S 851, Nationaltheatret 680, Skøyen 657.

`viktighet` er fortsatt redaksjonell og ikke utledet av `avganger`. Bodø har få
avganger og hører like fullt hjemme på et oversiktskart — de fem forteller hvor
jernbanenettet ender, ikke hvor det er travelt.

**Fellen i geocoderen — historikk.** Den gamle utgaven hentet hver stasjon fra
`/geocoder/v3/autocomplete`, som svarer med sitt beste gjett og ikke med «fant
ingenting». Tre ting holdt den ærlig: `stopPlaceTypes=railStation`, eksplisitte
søkestrenger («Oslo» og «Oslo S» er ikke samme spørsmål), og en nedskrevet
NSR-ID å sammenligne mot. Journey Planner har ikke det problemet — den svarer
på hva som finnes, ikke på hva som ligner. `/api/search` bruker fortsatt
geocoderen, og der gjelder forbeholdet.

## Klyngedannelse og visuell prioritering

Justert 21. august, etter en gjennomgang av hva som faktisk fanget øyet på
oversiktskartet. Fem endringer som alle peker samme vei: **togene er
innholdet, resten er kontekst.**

**Klynger under zoom 7.** Tette grupper slås sammen til én prikk med antallet
i. `KLYNGE_MAKS_ZOOM = 7` er valgt fordi linjeetikettene kommer på 7,5 — alt
er individuelt i det øyeblikket etikettene begynner å bety noe. Klikk på en
klynge zoomer til den løses opp.

Klyngen er farget etter **andelen tog i den som ikke er i rute**, ikke etter
verstemann. Det var et bevisst valg og verdt å begrunne: en klynge over Oslo
har tjue tog, og det er nesten alltid ett av dem som er kraftig forsinket.
Farget etter verste tog ville hver eneste klynge i landet vært rød hele tida —
maksimal alarm, null informasjon. Andelen skiller derimot en normal ettermiddag
fra en der halve Østlandet står.

**Spøkelsestogene i klyngen telles for seg, og vises.** Fram til 23. august
gjorde de verken det ene eller det andre: `spokelser` ble akkumulert per klynge
og aldri lest, og `avvik` delte på `point_count` — alle togene på stedet,
spøkelsene med. En klynge på 20 så ut som 20 tog som rapporterer nå, og tre av
dem kunne ha sluttet å sende for en halvtime siden.

To ting rettet det. **Fargen regnes nå over de målte togene alene:** både
teller og nevner holder spøkelsene utenfor, akkurat som ringdiagrammet gjør.
Det er den samme feilen som ble funnet i ringen 22. august — teller og nevner
fra hver sin populasjon — og den satt her også. Er alt i klyngen spøkelser,
finnes det ingen andel å vise, og klyngen blir grå: `--c-unknown`, samme farge
som en prikk uten forsinkelsesdata. Uten det unntaket ville fem tapte tog og
null målte gitt «null avvik av null», altså grønt.

**Antallet står som et grått tall på skulderen av klyngen.** Tallet inne i
klyngen er fortsatt `point_count` — så mange prikker klyngen står i stedet for,
og zoomer du inn finner du like mange. Grunnen til at det ble et tall og ikke
en demping av hele klyngen, slik en enslig spøkelsesprikk dempes til opacity
0,3: 3 av 20 er en dempning på under ti prosent. Usynlig, og nettopp det
tilfellet saken handlet om. Et tall er lesbart uansett hvor liten andelen er.

**`clusterProperties` har to ledd, ikke tre.** MapLibre leser
`const [operator, mapExpression] = clusterProperties[key]` og bygger selv
reduksjonen `[operator, ["accumulated"], ["get", key]]`. Skriver man den lange
formen selv — `["+", ["accumulated"], <uttrykk>]` — blir `["accumulated"]`
tolket som kartleggingsuttrykket og `<uttrykk>` faller på gulvet. Da teller
hver klynge null avvik og alt blir grønt, **uten at noe feiler**. Feilen ble
funnet ved å lese MapLibre-kilden, ikke ved å se på kartet, og den ville vært
usynlig i drift.

**Og en søsterfelle, funnet 23. august: `addLayer` kaster ikke.** Merkelappen
med spøkelsestallet fikk `text-offset` som et `step`-uttrykk med tallpar som
utfall — `[1.4, -1.4]` og ikke `["literal", [1.4, -1.4]]`. Et tallpar inne i et
uttrykk leses som et uttrykk, og `1.4` er ikke navnet på noen operator. Da
sender MapLibre en **error-hendelse** og lar laget ligge. Ingen unntak, ingen
rød konsoll om man ikke lytter, og `map.addLayer(...)` returnerer som om alt
gikk bra. Laget var rett og slett ikke i stilen.

To vaner følger av det: **lytt på `map.on('error')` når du legger til lag**, og
**sjekk `map.getLayer(id)` etterpå** hvis du vil vite om det gikk. En prøve som
bare ser om `addLayer` kastet, svarer alltid ja.

**Søket måtte bygges om.** Klyngedannelse skjer på *kildenivå*, før noe lag
ser dataene: under zoom 7 er et tog som ligger i en klynge ikke lenger et eget
punkt. Det gamle lagfilteret på `id` hadde derfor ingenting å treffe, og søk
på oversiktskartet ville vist tom skjerm. Løsningen er en egen, uklynget kilde
(`sokte-tog`) som treffene tegnes fra, og at de vanlige toglagene skjules mens
et søk er aktivt. Klyngene måtte uansett bort under søk: de teller alle togene
på stedet, så «12» ved siden av ett søketreff ville vært en direkte usannhet.

**Prikkene er større** — radius 3,2 → 4,4 på zoom 4. Tre piksler holder til å
se *at* det er et tog, ikke til å se om det er grønt eller gult, og fargen er
hele budskapet.

**Sporet er dempet.** `rail-network` fra 0,95/0,75 til 0,55/0,42, `hovedbane`
fra 0,95 til 0,6. Cyan er den eneste mettede fargen på kartet utenom togene,
og på zoom 10+ er sporet overalt der togene er — det er jo derfor de er der.
Full styrke gjorde at nettet vant på ren flate. Nedre grense er ikke smak:
under rundt 0,35 forsvinner en 1,1-pikslers linje i pikselrasteret, og da er
laget borte i praksis.

**Bakgrunnen er delt i tre i stedet for én.** Den gamle `dimBasemap()` dempet
alt som traff ett regexp — motorveier og landegrenser — til samme 0,22.
Landegrensa mot Sverige er ikke støy; på zoom 4 er den nesten det eneste som
sier hvor du er, og den var like svak som en tunnelstrek i Bergen. Nå:

| Gruppe | Behandling | Hvorfor |
|---|---|---|
| Veier, tunneler, broer | 0,16 | Ren støy, og de er mange |
| Grenser (`boundary_*`) | 0,5 | Grå streker uten metning — konkurrerer ikke med fargede prikker |
| Stedsnavn (`place_*`) | 0,92 + sterkere glorie | Bakgrunnsstilen ga dem glorie på `#222` mot en bakgrunn på `#0d1117` |

Glorien gjør mer for lesbarheten enn tekstfargen: den skiller bokstavene fra
det som ligger under, og under her ligger både cyan spor og fargede prikker.

## Flaskehalskartet

Bygget 20. august. Et lag som slås av og på med bryteren nederst i `#info`, og
som tegner strekninger mellom nabostasjoner farget etter hvor mye tid togene
taper på å kjøre dem. Av som standard; valget huskes i `localStorage`.

**Det måler endring, ikke nivå — og det er hele poenget.** For hver passering
av en strekning: *avviket ved B minus avviket ved A*. Et tog som ruller inn til
Oslo S tjue minutter for sent ville gjort Oslo S rødt på et gjennomsnittskart,
men forsinkelsen oppsto kanskje på Kongsvingerbanen to timer tidligere. Skal
kartet peke på flaskehalsen og ikke på stedet der den blir *synlig*, må det
måle endringen over hver strekning. Det er også slik jernbanen selv måler
seksjonstid.

Skalaen er derfor **divergerende** og ikke ensrettet som prikkenes: forsinkelse
har et gulv på null, endring i forsinkelse har det ikke. Blått der togene tar
inn tid — som oftest betyr at rutetabellen har slakk innebygd der — og ikke
grønt, fordi grønt allerede betyr «i rute» på prikkene i samme kart.

**Hvilke strekninger som finnes, bygges av togene selv.** Hver observasjon får
sin nærmeste stasjon, og sekvensen av nærmeste stasjoner langs en tur sier
hvilke strekninger som finnes. Topologien utledes av trafikken i stedet for å
skrives ned. Det gir to ting gratis: ingen liste å vedlikeholde, og bare
strekninger der det faktisk går tog.

**Hvor streken tegnes er et eget spørsmål, og det ble besvart to ganger.** Se
«Flaskehalsstrekene lå ikke på sporet» under.

**Tre funn fra første kjøring, og to av dem var feil i metoden.**

1. **Kartet finner Oslotunnelen selv.** Verste strekning er `Oslo S–Skøyen`
   med 51 passeringer. Det er den mest omtalte flaskehalsen på norsk jernbane,
   og den kom ut av tallene uten at noe sted i koden nevner den. Retningene
   er dessuten skjeve: **+1,2 min vestover mot +0,5 min østover.**
2. **Hull i loggingen så ut som kjøringer.** De fire verste «flaskehalsene» i
   første kjøring var `Marnardal–Stavanger`, `Marnardal–Oslo S`,
   `Gulskogen–Nyland` og `Kløfta–Strømmen` — stasjonspar hundrevis av
   kilometer fra hverandre, som ikke er naboer i det hele tatt. Alle fire kom
   av at nettleseren hadde vært lukket mens toget kjørte: de to observasjonene
   som ble igjen så ut som én sammenhengende passering. Fikset med et tak på
   hull mellom observasjoner (6 min), på reisetid og på strekningslengde.
3. **Parallelle baner ble blandet sammen.** Gardermobanen går to–tre km fra
   Hovedbanen forbi Jessheim og Nordby; Askerbanen like ved Drammenbanen. Med
   en romslig grense ble et Flytog på Gardermobanen regnet som en passering av
   Hovedbanens stasjoner, og tapte minutter havnet på feil bane.

   Slik det ble synlig: **strekningens tegnede geometri bøyde seg lenger vekk
   fra luftlinja enn strekningen selv var lang.** `Jessheim–Nordby` bøyde 2,3
   km på en strekning på 1,5 km, noe som er geometrisk umulig. Det er verdt å
   merke seg at det var *tegningen* som avslørte tallfeilen — tallene alene så
   helt rimelige ut.

**Avstanden måles til togets BANE, ikke til nærmeste måling.** Denne
forskjellen er verdt å forstå, for den gjelder flere steder enn her. Måler man
avstanden fra stasjonen til nærmeste observasjon, straffer man fart: et tog i
130 km/t med 29 sekunder mellom målingene flytter seg over en kilometer mellom
hver, så nærmeste måling kan ligge 500 m unna en stasjon toget kjørte rett
gjennom. Grensen ville luket bort ekspresstogene og beholdt lokaltogene — en
skjevhet rett inn i tallene, siden det er ekspresstogene som taper mest tid.

Målt mot linjestykkene mellom observasjonene i stedet, ga samme grense på 500 m
**130 strekninger i stedet for 106**, og null umulige geometrier. Strengere
grense og mer data samtidig.

**Det kartet ikke er.** Strekningene er ikke en fasit over jernbanenettet, men
et avtrykk av hva som ble logget. En strekning uten trafikk finnes ikke i
kartet, og et døgn uten logging finnes ikke i tallene — se hullene
`historikk.db` har om natta, og «Datainnsamling som egen bakgrunnsjobb» i
Veikartet. Strekninger med under fem passeringer vises ikke i det hele tatt.

## Flaskehalsstrekene lå ikke på sporet

Ryddet 21. august. `jernbanenett.py`, `lagjernbanenett.py`,
`static/jernbanenett.geojson`.

Den første geometrien var **medianlinja**: observasjonene ble projisert på
korden mellom de to stasjonene, sortert i tolv bøtter, og hver bøtte bidro med
medianen sin. Ingen ekstra datakilde, og streken bøyde seg der togene bøyde
seg. Den holdt til å avsløre at parallelle baner ble blandet sammen (funn 3
over), men den holdt ikke som geometri — og den holdt dårligst nettopp i Oslo,
Akershus og Østfold, der linjene ligger tettest.

**Tre feil, som alle blir verre jo tettere nettet er.**

1. **Medianen av to parallelle spor er ingen av dem.** Går det både
   Askerbane-tog og Drammenbane-tog mellom to stasjoner, legger medianen
   streken midt imellom traseene — ute på jordet.
2. **Bøttene tømmes.** Bare punkter som projiserer til mellom 0 og 1 på korden
   teller. En trasé som svinger — Romeriksporten, Follobanen, Østre linje —
   mister punktene sine i endene. `Bryn–Lillestrøm` ble tegnet med **fem
   punkter over 13,6 km**: en luftlinje med et kink på.
3. **Median av lengdegrad og median av breddegrad hver for seg er ikke et
   punkt på sporet.** I en sving trekker de to medianene hver sin vei, og
   resultatet legger seg på innsiden av kurven.

Målt mot `hovedbaner.geojson` bøyde `Nationaltheatret–Oslo S` seg **966 m** vekk
fra nærmeste skinne på en strekning på 1,3 km.

**Hvorfor `hovedbaner.geojson` ikke kunne brukes, og hva som kom i stedet.**
Den gamle innvendingen var at fila er seksten MultiLineStrings med opptil 600
løsrevne biter i vilkårlig rekkefølge. Det er sant, men det er ikke den
avgjørende innvendingen. Den avgjørende er at fila **mangler Hovedbanen,
Askerbanen, Follobanen, Spikkestadbanen, Roa–Hønefossbanen og Østfoldbanens
østre linje** — altså nøyaktig de linjene som gjør Oslo-området vanskelig. Et
rutesøk i den ville tegnet Ski–Mysen tvers over åkeren.

`lagjernbanenett.py` henter i stedet **alt kjørbart hovedspor i Norge** fra
Overpass, deler det ved sporvekslene og forenkler til 5 m:

| | `hovedbaner.geojson` | `jernbanenett.geojson` |
|---|---|---|
| Formål | visningslag på zoom 6 | rutingsnett |
| Innhold | 16 navngitte baner | alt `railway=rail` uten `service` |
| Toleranse | 150 m | 5 m |
| Størrelse | 185 kB | 856 kB, 5 258 km spor i 10 052 biter |
| Sammenheng | irrelevant | 96 % i én komponent, alle 335 stasjoner koblet |

**Rutingen er ikke korteste vei.** Korteste vei fra Bryn til Lillestrøm går
gjennom Romeriksporten enten toget kjørte der eller tok Hovedbanen om Strømmen.
Derfor koster hver sporbit lengden sin **ganget med 1 hvis toget ble observert
langs den og med 8 hvis ikke**. Observasjonene brukes altså ikke lenger som
geometri, men som pekepinn på hvilken trasé søket får velge. Straffen er en
vekt og ikke et forbud: blir det hull i loggingen, finner søket fortsatt fram.

**Fire ting som gikk galt underveis, og som hver har en konstant nå.**

1. **Dobbeltspor er to linjer i OSM.** De møtes bare i sporvekslene, og
   vekslene kan ligge kilometer unna. Festet hver stasjon seg til sitt ene
   nærmeste spor, kunne Sagdalen havne på det ene og Strømmen på det andre:
   **1,1 km luftlinje ble rutet 4,1 km**, med alle punkter pent på skinner
   hele veien. `MAKS_FESTER = 12` lar søket velge festepunkt selv.
2. **Festet kunne kjøpes.** Uten `FESTE_VEKT` lønte det seg å feste
   stasjonene lenger fra sporet for å korte ned ruten:
   `Nationaltheatret–Skøyen` kom ut på **2,4 km der luftlinja er 3,1**, som
   ingen jernbane kan være.
3. **Vetoet målte feil vei.** Første versjon spurte «ligger hvert rutepunkt
   nær en observasjon», og straffet dermed hull i loggingen i stedet for feil
   trasé. `Lysaker–Sandvika` er ekspresstog som bare måles ved hver ende — fem
   kilometer uten en eneste observasjon — og ble vraket selv om hver eneste
   observasjon lå på skinnene ruten fant. Nå måles det fra hver **observasjon
   til ruten**.
4. **Prøvesteinen må faktisk skille.** Selvtesten sjekker at korridoren kan
   tvinge Bryn–Lillestrøm om Hovedbanen i stedet for gjennom tunnelen. Første
   forsøk brukte Strømmen som bevis — men Strømmen ligger nesten oppå
   luftlinja Bryn–Lillestrøm og er 50 m fra **begge** rutene. Grorud ligger
   nord for korden og over en kilometer fra tunnelen, og skiller dem.

**Resultatet, målt 21. august:** 148 av 149 strekninger rutet gjennom
spornettet, og **verste tegnede punkt ligger 0 m fra nærmeste skinne** — RDP
plukker ut punkter, den finner ikke opp nye, så et rutet punkt *er* et
sporpunkt. Median omvei mot luftlinje er 1,05. Hele kartet regnes ut på 1,2 s
mot 0,9 s før.

**Når rutingen ikke svarer** avvises den av to vetoer — ruten kan ikke være mye
lengre enn luftlinja, og togene må ha blitt målt der den går — og
medianlinja tegnes i stedet. `properties.geometri` er `"spor"` eller
`"median"`, og popupen sier fra i det siste tilfellet. Samme merkevane som
`kilde` i hovedbaner.geojson og `positionMethod` på hvert tog: en reserve som
ikke kan skilles fra hovedveien blir usynlig når den slår inn oftere enn den
skal.

Reserven slo inn på **én strekning av 147** da nettet ble tatt i bruk:
`Hvalstad–Slependen`, der togene er ekspresstog i Askerbanens tunnel mens
stasjonsparet ligger på den gamle Drammenbanen. Der er det ikke rutingen som
tar feil — det er strekningen selv som er tvetydig.

**`properties.km` betyr noe nytt.** Den var luftlinje mellom stasjonene og er
nå målt langs streken, altså kjørelengde for rutede strekninger. Det er tallet
som betyr noe når man skal se om ett minutts tap er mye.

**`forenkle` flyttet fra `lagbaner.py` til `sporgeometri.py`.**
Ramer–Douglas–Peucker trengtes i drift, og `lagbaner.py` er et byggeskript som
drar inn `httpx` og `dotenv`. Et API-svar skal ikke ha en Overpass-klient i
importkjeden for å kunne forenkle en strek.

## Kjøreretning — pila på prikken

Bygget 20. august. En liten trekant på kanten av hver togprikk, som peker dit
toget kjører. Samme farge som prikken, og av samme grunn: det er ett ikon, ikke
to. Vises fra zoom 6 — under det er prikken tre piksler bred, og en trekant ved
siden av den er støy.

**Retningen har fire kilder.** Rekkefølgen mellom dem er målt, ikke antatt, og
den er motsatt av den opplagte:

| Kilde | Hvor | Dekning | Nøyaktighet |
|---|---|---|---|
| `bevegelse` | mellom to hentinger | alle med GPS, i fart | følger sporet |
| `sporgeometri` | tangenten til traseen | alle beregnede tog | 12° median |
| `rutetabell` | forrige → neste stopp | alle JP kjenner | grov, men lokal |
| `vehicle-positions` | Enturs `bearing` | bare Flytoget | 26° median |

**`bearing` er nesten ubrukelig, og det tok to målinger å se.** Feltet finnes i
Vehicle Positions og ser ut som fasiten — det er jo toget som melder sin egen
kurs. To ting viste seg:

1. **Bare Flytoget publiserer det.** 15 av 15 FLT-tog, 0 av 65 Vy, 0 av 5
   Go-Ahead. Bygger du pila på `bearing` alene, får Gardermobanen piler og
   resten av landet ikke — og det ser ut som en feil i kartet, ikke som et hull
   i dataene.
2. **Det Flytoget sender er ikke en live kurs.** Målt over åtte hentinger på
   fire minutter, for tog i bevegelse hele tida:

   | Tog | Kjørte | `bearing` endret seg | Faktisk kurs endret seg |
   |---|---|---|---|
   | 3798 | 6,0 km | 1° | 27° |
   | 3805 | 8,4 km | 5° | 30° |
   | 3800 | 1,2 km | 1° | 28° |

   `bearing` står altså stille mens toget svinger seg gjennom
   Romeriksporten. Det er en konstant for hele avgangen — grovt sett «denne
   turen går nordøstover» — ikke retningen toget peker nå. Mot faktisk
   bevegelse bommet den med **26° i median og 116° på det verste**, og
   avviket har ikke fortegn: det er ikke en konvensjonsfeil man kan korrigere
   bort, det er bare grovt.

Derfor: **bevegelse slår operatørens eget tall.** `bearing` er nå siste utvei,
brukt på tre tog av nitti.

**Rutetabellen finnes for ett tilfelle: toget som står stille.** En retning
utledet av bevegelse krever bevegelse. Et tog på en perrong har ingen — og det
er nettopp der folk ser etter pila. `punktlighet._retning_na()` gir retningen
fra forrige mot neste stopp, som er der toget skal videre. Uten den var
dekningen 15 % rett etter oppstart; med den er den 74 % umiddelbart og rundt
85 % når bevegelsesmålingen har fått ti sekunder på seg.

**Tog uten pil er tog uten retning, ikke en feil.** Rundt 11–16 av 90 til
enhver tid: spøkelsestog uten fersk posisjon, tog som står stille og aldri har
rukket å bevege seg, og tog som nettopp har kommet inn i feeden. Målt over en
kveld: **hvert eneste tog som flyttet seg mer enn 25 meter fikk en retning.**
Ingen unntak. Det er den grensen som betyr noe.

**Én ting til, funnet av proben og verdt å huske i andre sammenhenger:**
beregnede SJ-posisjoner kan gli **bakover**. Når Journey Planner skyver en
forventet avgangstid senere, synker brøken mellom to stopp, og toget flytter
seg bakover langs sporet til neste henting. F6 tog 51 flyttet seg 169 m
bakover mens neste stopp sto uendret. Pila er upåvirket — den følger sporets
tangent — men enhver måling som bruker «hvor langt flyttet toget seg» som
fasit, må vite om det. `prober/sjekk_retning.py` skiller derfor mellom målte
og beregnede posisjoner før den roper varsku.

## Driftsmeldinger og avviksringene

Bygget 20. august, lagt om 21. — se «Ringene blinket av seg selv» lenger nede
for hvordan markeringen virker i dag. Nyhetsstripa nederst til venstre viser én
driftsmelding om gangen fra SIRI-SX; hover markerer det meldingen gjelder,
klikk låser markeringen og flytter kameraet dit.

Markeringen er et femte kartlag (`avvik-glod`, `avvik-ring`, `avvik-strek-glod`
og `avvik-strek`), lagt mellom ruteopptegningen og togprikkene. Rekkefølgen er
valgt: et tog som står stille midt i en stengt strekning skal ikke skjules av
markeringen som forklarer hvorfor det står.

**Fire ting som ble målt mot feeden, og som styrer hvordan `avvik.py` ser ut.**
De står utførlig i modulens egen docstring; her er de i kortform, fordi tre av
dem er felletyper som går igjen andre steder i dette prosjektet:

- **Vy publiserer under `NSB`, ikke `VYG`.** Samme selskap, to kodrom, avhengig
  av hvilket API du spør. `VYG` — det kodrommet kjøretøyene og linjene kommer
  under overalt ellers — ga **null** situasjoner. `NSB` ga 45. Spør du bare om
  det som virker riktig, blir stripa tom og alt ser ut til å virke.
- **Bare `NSB` og `GOA` svarer.** Flytoget og SJ Nord publiserer ingen
  driftsmeldinger. Nordlandsbanen og Bergensbanen står altså uten avviksvarsel
  i kartet, og det er ikke vår feil — men det er verdt å vite før noen tror
  stripa dekker hele landet. Vi spør om alle fem uansett, så det ordner seg
  selv den dagen de begynner.
- **`severity` kan ikke bære fargen alene.** Av 56 meldinger var 45 «normal».
  En heis ute av drift og en innstilt avgang har samme alvorlighetsgrad i
  dataene. Fargen må derfor utledes av teksten i tillegg — og da må reglene
  være **smale**: «stengt» alene tok med «Venterom stengt grunnet hærverk»,
  som er en låst dør og ikke en stengt bane.
- **Sammendraget er for kort til å stå alene.** `summary` er «Toget står»,
  «Forsinket», «Ta neste tog». Det er `description` som sier «Toget står på
  Sørumsand» — og det er den som ruller forbi. Sammendraget vises bare når det
  bærer et ord teksten mangler («Sørtoget:», «Arendalsbanen R50:»).

**To feil som ble funnet av proben og ikke av øyet**, og som er verdt å nevne
fordi begge så riktige ut i grensesnittet:

1. **Planlagt vedlikehold ble rødt.** Åtte «Bane NOR utfører
   vedlikeholdsarbeid»-meldinger inneholder både «stengt» og «buss for tog» i
   beskrivelsen, og traff dermed nøkkelordene for høyeste nivå. Men de er
   varslet uker i forveien, og operatøren har selv merket dem `noImpact`.
   Fiksen er en **rekkefølge**, ikke et nytt nøkkelord: `noImpact` sjekkes før
   teksten. Stripa så helt normal ut med feilen i — den var bare rød hele tida.
2. **Grønt forsvant helt.** Meldingene sorteres etter viktighet og klippes ved
   15. Sorterte man rett etter alvorlighetsgrad, fylte fem røde og ti gule hele
   taket, og «Gardermoen: togene kjører med normal fart igjen» falt utenfor
   hver gang. Derfor to lister i `avvik.py`: `NIVAER` er alvor og brukes når to
   like meldinger slås sammen, `VISNINGSORDEN` er nyhetsverdi og setter
   `rettet` foran `middels`. At feilen var usynlig er poenget — ingenting så
   galt ut, den grønne fargen fantes bare aldri.

**Sammenslåing.** Feeden gjentar seg kraftig: 59 situasjoner ble til 15
meldinger 20. august. «Ta neste tog mellom Stabekk og Skøyen» kommer én gang
per berørt linje, «Færre vogner» én gang per avgang. Nøkkelen er **teksten**,
ikke `situationNumber` — det er nettopp fordi numrene er forskjellige at
duplikatene finnes.

## Stripa rullet gårsdagen og oktober

Ryddet 21. august. Konstantene `MAKS_ALDER_TIMER` og `VARSEL_TIMER`, funksjonen
`_klokke`, og punkt 5 i `prober/sjekk_avvik.py`.

Stripa var full — femten meldinger, pent rullende, ingenting som så galt ut.
Målt på feeden 21. august var **to av de femten fra de siste to timene**:

| Hva sto i stripa | Antall av 15 |
|---|---|
| Planlagt arbeid som starter **i framtida**, 18 t til 1370 t (57 døgn) fram | 6 |
| 30–68 timer gammelt («Ta andre tog fra Skøyen og Lysaker» hadde rullet i tre døgn) | 7 |
| Fra de siste to timene | **2** |

Samtidig nådde de ferskeste meldingene — 13 og 75 minutter gamle, om vogner og
avganger akkurat nå — **aldri fram til stripa i det hele tatt.** De ble presset
ut av taket på femten.

**Tre årsaker, og den første er den fine.**

1. **Framtidsdaterte meldinger ble rangert som de ferskeste.** Sorteringen
   brukte `_sortert = start or na` og sorterte på `-timestamp`. En starttid 57
   døgn fram har det største tidsstempelet i hele feeden — og la seg derfor
   øverst. Hele info-delen av stripa var planlagt arbeid, sortert med det
   *fjerneste* først. Feilen er selvforsterkende: jo lenger fram arbeidet var
   varslet, desto ferskere så det ut.
2. **Ingenting utløp på alder.** Det eneste som fjernet en melding var
   `endTime < na`, og **16 av 50 situasjoner har ingen `endTime`**. «Venterom
   stengt grunnet hærverk. Åpnes høsten 2026» hadde da vært aktiv i 143 døgn.
3. **Ingen sjekket at meldingen hadde begynt.** 9 av 50 hadde ikke det.

**Fiksen er tre porter og en ny klokke.**

`_klokke()` leser `versionedAtTime` først, så `creationTime`, så starttiden.
Feltet lå i APIet hele tiden og ble ikke brukt. Det kan ikke ligge i framtiden
— null av 50 gjorde det — og det fanger i tillegg at operatøren har *oppdatert*
en løpende melding: elleve av 50 var oppdatert etter at de ble laget. Det
fjerner årsak 1 i seg selv, uten noen grense.

Portene i `til_meldinger` spør så om tre ulike ting, i rekkefølge: er den over
(`endTime`), har den begynt (`VARSEL_TIMER = 0`), er den fortsatt fersk
(`MAKS_ALDER_TIMER = 12`). Den siste er den eneste som fanger meldingene uten
`endTime`, og de er en tredjedel av feeden.

**Prisen er en kortere stripe, og det er meningen.** Samme feed ga 15 meldinger
før og 9–10 etter, alle i kraft nå, median 1,7 t. Stripa skal si hva som skjer
nå, ikke fylle femten plasser.

**Kvalitetssikringen er at alderen er synlig.** Undertittelen viste
`04:23:00` — et klokkeslett leseren måtte regne fra selv, og en melding fra i
natt så like fersk ut som en fra ett minutt siden. Nå står det `2 t siden`
(`ago()` fantes allerede i app.js). Serveren luker bort alt eldre enn grensen,
så dette er andre lag med samme sikring: skulle stripa bli gammel likevel,
står det der i stedet for å være skjult.

Samme prinsipp for `foreldet`: når hentingen feiler, serverer app.py forrige
sett med `foreldet: true`. Frontend leste ikke flagget, så et gammelt sett
rullet videre som om det var ferskt. Nå står det `1 / 10 · ikke oppdatert` i
telleren.

`/api/avvik` svarer i tillegg med `forkastet`, `eldsteTimer` og
`maksAlderTimer`, slik at det kan leses av uten å kjøre proben. Telleren er
ikke pynt: slutter Entur å sette `endTime`, eller endrer de hvordan
`versionedAtTime` fylles, viser det seg som et tall som vokser — ikke som en
stripe som stille blir kort.

**Proben måler nå ferskheten** (punkt 5) og feiler på tre ting som hver
tilsvarer en av årsakene over: at selv den ferskeste meldingen er over tre
timer gammel, at en melding er eldre enn grensen, og at en melding er datert i
framtiden.

## Seks like meldinger tok seks plasser

Ryddet 21. august, rett etter ferskhetsryddingen over. `_grupper`,
`_gruppenokkel`, `MIN_GRUPPE`, `GRUPPE_MAKS_SAMMENDRAG`.

Sammenslåingen nøklet på **eksakt tekst**, og det fanget bare den ene av to
former for gjentakelse. Den andre er tekster som sier nesten det samme:

    Denne avgangen kjører dessverre med 3 vogner i stedet for 6 vogner.
    Denne avgangen kjører dessverre med 4 vogner i stedet for 8 vogner.
    Denne avgangen kjører dessverre med 5 vogner i stedet for 10 vogner.

Tre linjer i stripa som sier det samme, og de tok **seks av ti plasser**.

**Nøkkelen er `summary`, ikke en normalisering av tallene.** Alle femten
situasjonene bar sammendraget «Færre vogner» — operatørens egen merkelapp,
skrevet nettopp for å gruppere. Den er stabil, og den ryker ikke hvis noen
skriver «fire» i stedet for «4». Å strippe sifrene ut av beskrivelsen ville
vært den åpenbare fiksen og den skjøre.

**To vakter mot å slå sammen for mye.** Nivået er en del av nøkkelen, så en
innstilling og en heis ikke kan havne på samme linje. Og bare sammendrag
under `GRUPPE_MAKS_SAMMENDRAG` (40 tegn) brukes — over det er det en setning
og ikke en merkelapp. «Sørtoget: Bane NOR utfører vedlikeholdsarbeid.» er 46
tegn og gjentas sju ganger, men der ligger innholdet i beskrivelsen (hvilke
stasjoner, buss for tog), så den skal stå for seg.

**Terskelen teller avganger, ikke tekster** — og det var ikke åpenbart.
Første forsøk krevde tre like *tekster*. Etter aldersfiltreringen sto «Færre
vogner» igjen som **to** tekster, så terskelen slo aldri til — men de to
dekket 2 og 8 avganger. Ti avganger tok fortsatt to plasser i stripa. Hver
situasjon er nøyaktig én `AffectedServiceJourney`, så avgangstallet er
eksakt, og det er avgangene som gjør det til et mønster.

Kravet er derfor: **minst to ulike tekster, og minst `MIN_GRUPPE` avganger til
sammen.** Første ledd finnes fordi én tekst som dekker mange avganger allerede
er dekkende — «Ta neste tog mellom Stabekk og Skøyen» sier hvor, mens «Ta
neste tog» ikke gjør det. Den trenger ikke ny tekst, bare tallet.

Resultatet: `Færre vogner · L1, L2 · 10 avganger · 1 t siden`. Én linje.

**En felle i implementasjonen, verdt å kjenne igjen:** hodene i hver gruppe
noteres *under* sammenslåingen og ikke ved å regne nøkkelen på nytt etterpå.
`_flett` hever nivået til det strengeste i gruppen, og nivået er en del av
nøkkelen — en gruppe som fikk hevet nivå ville ikke kjent seg igjen, og
teksten ville blitt stående som det første medlemmets i stedet for
sammendraget. Samme klasse feil som å muterte en dict mens man itererer over
nøklene den er indeksert på.

**En bonus-herding underveis:** `_tid()` returnerte naive tidspunkter hvis
Entur sendte en verdi uten sone. Sammenligningen mot `na` kaster da TypeError
inne i løkka som bygger meldingene — én rar tidsstreng ville altså tatt ned
hele endepunktet, ikke bare den ene meldingen. Alle 50 verdiene hadde sone,
så feilen var latent. Nå leses en manglende sone som UTC.

## Ringene blinket av seg selv, og en stengt bane så ut som to prikker

Bygget 21. august. `avvik.paafor_kart`, `jernbanenett.strekning`,
`avvik-strek`-laget i app.js, punkt 4 i `prober/sjekk_avvik.py`.

Kartkoblingen hadde to feil som begge så helt normale ut.

**1. Markeringen fulgte rotasjonen og ikke leseren.** `visNyhet()` kalte
`merkAvvikIKart()` hver gang stripa byttet melding. Kartet blinket altså i
ringer hvert sjette sekund, på steder ingen hadde spurt om — og når man endelig
klikket på en melding, var det ikke til å se forskjell på et svar og et
tilfeldig blink. Nå tegnes ingenting av seg selv: **hover viser, klikk låser og
flytter kameraet.** Klikk på den samme igjen låser opp. Klikk i tom kartflate
rydder, samme handler som allerede tømte «valgt rute».

At hover *bare* peker er et valg: et kamera som rykker fordi markøren strøk
forbi en liste er den sikreste måten å miste det man holdt på å se på.

**2. En stengt strekning ble tegnet som ringer rundt endestasjonene.** «Buss
for tog mellom Kristiansand og Gjerstad» er ikke to steder det er noe galt med
— det er de 126 kilometrene mellom dem. Meldingene har derfor fått en **form**,
og den avgjøres i `avvik.paafor_kart`:

| Form | Når | Hva kartet tegner |
|---|---|---|
| `strekning` | 3+ stasjoner i kjede, eller «mellom X og Y» i teksten | Banen, rutet på ekte spor |
| `steder` | 1–2 stasjoner | Ring rundt hver |
| `""` | ingen stedfesting | ingenting |

**Kjeden er både beviset og oppskriften.** Rammer en melding tre stasjoner
eller flere, er de nesten alltid en sammenhengende rekke langs én bane — «Ta
neste tog mellom Sagdalen og Oslo S» kom med alle tolv stasjonene på
Hovedbanen mellom dem. Og de tolv er samtidig det som gjør at ruting *uten
observasjoner* treffer riktig trasé: korteste vei Oslo S–Sagdalen går gjennom
Romeriksporten, men korteste vei Oslo S – Alna – Nyland – Grorud – Sagdalen kan
bare gå om Hovedbanen. Det er den samme mekanikken som `rute()` bruker
korridoren til, med stasjonene i observasjonenes rolle. Se `strekning()` i
`jernbanenett.py` og sjekk 6 i selvtesten der.

**Rekkefølgen måtte finnes, for `affects` er usortert.** «Ta neste tog» kom
med atten L2-stasjoner i rekkefølgen Lysaker, Vevelstad, Myrvoll, Oslo S,
Nationaltheatret, Nordstrand … Rutet slik sikksakker streken gjennom Oslo seks
ganger. `_kjede()` sorterer med nærmeste nabo fra den enden som ligger lengst
fra alt annet — eksakt så lenge nabostasjoner ligger nærmere hverandre enn
stasjoner som ikke er naboer, som er akkurat hva en jernbanelinje er. Å
projisere på korden i stedet ville brutt sammen på hver bane som svinger.

**Teksten er den andre kilden, og den er den viktigste.** Målt på feeden
21. august hadde de to hardeste meldingene i stripa **null stoppesteder**:

    Toget er innstilt mellom Stavanger og Oslo S.          F5, 0 steder
    ... buss for tog mellom Kristiansand og Gjerstad ...   F5, 0 steder

Begge var altså umulige å klikke fram i kartet før dette. `_endepunkter()`
leser «mellom X og Y» og slår navnene opp i `stasjoner.geojson`. Oppslaget
tar det **lengste** navnetreffet fra hver kant, slik at «mellom Kristiansand og
Gjerstad for Sørtoget» gir Gjerstad og ikke «Gjerstad for Sørtoget», og
«mellom Sagdalen og Oslo S» gir Oslo S og ikke Oslo. Finner den ikke begge
navnene, gir den opp helt — ett endepunkt og en gjetning er verre enn
ingenting. Målt på feeden kl. 14 gikk kartkoblingen fra 6 av 8 til **7 av 8**
meldinger; den ene som ble igjen er «Færre vogner», som er gruppert og ikke har
noe sted i det hele tatt.

**Prisen er et rutesøk i endepunktet, og den er lav.** Søket ligger i en tråd
(`asyncio.to_thread`) fordi det regner i stedet for å vente på nett, og hver
etappe bufres på stasjonsparet i `Jernbanenett`. Nettet er uforanderlig, så et
svar er like gyldig i morgen. Målt: 241 ms for første `/api/avvik` etter
omstart — der spornettet på 856 kB også lastes — og 2 ms for de neste. Svaret
vokste fra 8 til 20 kB, det meste av det Sørlandsbanen tegnet ut i 548 punkter.

**To bremser mot at én melding tar hele svaret.** `MAKS_ETAPPER` (40) tynner
kjeden jevnt i stedet for å klippe den — klipping ville flyttet den ene enden
innover i landet, så en melding om Oslo S–Stavanger endte i Kristiansand. Og
`STREKNING_TOLERANSE_M` (80 m) forenkler streken før den sendes; `jernbanenett`
sitt eget 20 m er finere enn en piksel før man er zoomet inn på en bydel.

**Vetoet er halve `_troverdig`.** Uten observasjoner finnes ikke nærhets-
sjekken, men lengdesjekken gjør det: en etappe som er over `MAKS_OMVEI` ganger
luftlinja har vært innom feil bane. Faller en etappe, tegnes resten med et hull
i stedet for at hele meldingen blir usynlig — derfor er `strekninger` en liste
av linjer og ikke én linje.

**Proben teller formene** (punkt 4), og det er den tellingen som fanger neste
feil av denne typen. Faller `strekning` til null, har enten spornettet
forsvunnet eller operatørene sluttet å skrive «mellom X og Y» — og da tegner
kartet igjen to ringer rundt endestasjonene i en stengt bane, som ser helt
normalt ut. Den lister også hvilke strekninger som kom fra teksten alene, og
hvilke meldinger med 3+ steder som *likevel* ikke ble en strekning.
