# Erfaringer

Lærdommene fra dette prosjektet, destillert. De lange målingene bak hver av dem
står i [undersokelser.md](undersokelser.md).

Dette er ikke dokumentasjon av hvordan API-et virker i dag — det finner du i
[arkitektur.md](arkitektur.md). Dette er hvorfor det ble slik.

---

## Tjueen ting dette prosjektet lærte om datakvalitet

Hver av disse så ut som gyldige data, og hver var feil:

1. **Forsinkelser som vokste** på tog som hadde sluttet å sende. Løsning:
   spøkelsesdeteksjon på alder, og tallene holdes utenfor statistikken.

2. **Togsett forkledd som tognumre.** `71-15` var BM71 sett 15, ikke tog 71.
   Fire ulike Flytog fikk samme nummer.

3. **En test som målte treffrate i stedet for riktighet.** Regexen traff på
   100 % — og ga feil svar for en fjerdedel. Den ekte testen var unikhet.

4. **En sjekk som spurte om kodespaket fantes**, ikke om det kjørte tog. SJN
   var i feeden med tre busser, og skriptet meldte «SJ ER I FEEDEN».

5. **Prognoser som var kopier av rutetabellen.** `realtimeState: updated` med
   forventet tid lik planlagt tid. Nullen var ikke en måling — den var fravær
   av en måling.

6. **En beregnet posisjon som så ut som en målt.** F6 lå like skarpt i kartet
   som et GPS-tog, 11 km fra sporet. Riktig tid, riktig brøk, feil geometri —
   og ingenting i prikken som røpet at den var gjettet. Løsningen var ikke bare
   å regne riktigere, men å merke posisjonen med metoden den ble til med.

7. **To prikker som begge var riktige — av motsatt grunn.** RE10 308 lå to
   steder fordi to rørledninger regnet på samme tog. F5 706 ligger to steder
   fordi toget faktisk har to sendere. Samme symptom, motsatt årsak: den ene
   var en feil i koden, den andre en feil i antakelsen om at ett tog er én ting.

8. **Et skript som kunne konkludere fra fravær, gjorde det.** Tre ganger.
   `sjekk.py spokelser` meldte «hypotesen faller» da `monitoredCall` var tomt —
   `framme(v)` er `False` både når toget ikke er framme og når vi ikke vet.
   `sjekk_punktlighet.py` meldte «bekreftet» fordi den talte ferdige tog i
   stedet for uenige. Og natta 19. august meldte den «ingen sanntid» om en
   instans som ikke hadde kjørt ennå. Alle tre er i skript skrevet for å unngå
   nettopp denne feilen.

9. **En teller som telte feil enhet.** `querySourceFeatures().length` ga 17 ved
   ett zoomnivå og 35 ved et annet, mens antallet baner sto stille på 13.
   MapLibre deler GeoJSON-en i fliser, og en bane som krysser fire fliser ligger
   der fire ganger. Tallet så plausibelt ut hele veien — 17 er et helt rimelig
   antall jernbanestrekninger i Norge. Det var bare ikke det vi trodde vi målte.

10. **To lag i samme farge med ulik nøyaktighet.** Det dobbelttegnede sporet var
    ikke en feil i geometrien — det var 150 meters RDP-toleranse vist på et
    zoomnivå laget aldri var ment for. Og motsatt: hullet der Randsfjordbanen
    manglet var usynlig over zoom 8, fordi det andre laget tegnet sporet uansett.
    **Et lag som overlapper et annet, skjuler både dets unøyaktighet og dets
    hull.**

**11. Om dokumentasjon, ikke data.** **En kommentar
som viser til en fil som er flyttet, lyver like effektivt som feil data.**
`entur.py` ba deg kjøre `tognummer.py` etter at den ble slått sammen til
`sjekk.py`. Kommentaren var riktig da den ble skrevet, og det er nettopp derfor
ingen så på den igjen.

**12. Om å lese tall som beveger seg.** De to punktlighetslinjene i loggen — Journey Planner: N av M fra punktlighet.py og Punktlighet påført: N av M fra app.py — skrives i ulik takt. JP-cachen fornyes hvert 60. sekund; snapshotet bygges hvert 10.–15. Mellom to JP-hentinger påfører app.py en inntil minutt gammel JP-ordbok på et ferskt målesett, så «påført»-tallet kan vandre 79 → 78 → 80 mens «Journey Planner»-tallet står stille. Det ser ut som to tall som burde vært like, men ikke er det — de er bare målt på ulike tidspunkt. Kryssjekken gjelder kun rebuildene der begge linjene skrives samtidig. Et tall som beveger seg er ikke feil før du vet hvor ofte det har lov til å bevege seg — motstykket til at et tall som står for stille (13 identiske nuller) kan være det.

**13. Om målestokken i stedet for målingen.** **En fasit som er feil,
gjør riktige data mistenkelige.** Bratsbergbanen sto oppført med 74 km fordi
tallet så offisielt ut. Den er ~45. Følgen var ikke bare en unøyaktig
kommentar: `km` er terskelen som avgjør om OSM-hentingen godtas, så en
*komplett* henting på 47,4 km havnet på 52 % av en innbilt lengde og lå i et
halvt år som «fortsatt åpen, årsak uklar». Begge hypotesene i dokumentasjonen pekte på
dataene — manglende navngiving, for hardt filter — og ingen av dem på tallet vi
målte mot. Sjekk fasiten før du feilsøker det den dømmer. Og legg merke til at
symptomet var *for lavt, men ikke lavt nok til å utløse noe*: hadde 74 vært 200,
ville Entur-reserven slått inn og skjult saken helt.

Den lærdommen viste seg å ha to haler til, funnet samme kveld på Drammenbanen
og Gardermobanen. Den første: **en målestokk må måle det du faktisk henter.**
Begge banene henter *to* navn fra OSM, og begge sto oppført med bare det enes
lengde. Feltet het `km` og banen het Drammenbanen, så tallet så riktig ut helt
til man spurte hva det var lengden *av*. Den andre: **et avvik som aldri
forsvinner, slutter å bli lest.** De to lå på 174 % og 278 % og ga MERK ved
hver eneste bygging i månedsvis. Et varsel som alltid står på, er ikke et
varsel — og prosjektets egen feilsøkingsliste sier at nettopp denne MERK-linjen
«ikke bare er støy». Den hadde rett; ingen sjekket.

Legg merke til at de tre feilene har motsatt fortegn og samme rot. Bratsbergbanen
var målt for stort, de to andre for smått, og i alle tre tilfellene var dataene
riktige. Når man skal velge mellom å mistro målingen og å mistro fasiten, er
fasiten den billigste å kontrollere — og den er hittil den som har tatt feil
hver gang.

**14. Om å måle treffraten en gang til, tjue dager senere.** Da dekningen mot
Journey Planner skulle bygges, var spørsmålet om tur-ID-ene fra de to kildene
kunne sammenliknes rått. Målingen sa **79 av 79 eksakt treff**, og null ekstra
når kodeområdet ble klippet vekk først. Det så avklart ut, og filteret ble
skrevet på den antakelsen.

Fem tog ble tegnet to ganger. Vehicle Positions svarer for noen Vy-tog med en
`DatedServiceJourney`-ID der Journey Planner gir en `ServiceJourney`-ID:

    VP  VYG:DatedServiceJourney:1931_OSL-RST_26-08-21
    JP  VYG:ServiceJourney:1931_443485-R

Samme tog, to ID-rom. **Målingen talte hvor mange som matchet, ikke hvor mange
som var samme tog uten å matche.** De 79 var riktige; det var de fem utenfor
utvalget som var saken, og de kunne bare finnes ved å spørre om noe annet —
«finnes det tog som opptrer i begge kildene under hver sin ID?»

Dette er lærdom 3 om igjen, i et prosjekt som har lærdom 3 skrevet ned. Det
sier noe om hvor lett den er å gå på: en treffrate på 100 % ser ut som et svar
og er et spørsmål. Fasiten var ikke tallet, men motprøven.

**15. Om et felt som fylles ut selv om det ikke betyr noe.** Endestasjonen har
ingen avgang. Journey Planner fyller likevel ut `aimedDepartureTime` og
`expectedDepartureTime` på siste stopp, og differansen mellom dem så ut som en
forsinkelse. RE10 339 ble tegnet «6 min 12 s forsinket» på Hovemoen:

    aimedDepartureTime  01:30:00      expectedDepartureTime  01:36:12   ->  372 s
    aimedArrivalTime    01:30:00      expectedArrivalTime    01:32:25   ->  145 s

Toget ankom 2 min 25 s for sent. Det var Vehicle Positions sitt tall — den
kilden prosjektet hadde konkludert med at man ikke kan stole på (problem 1).
**Her var VP riktig og JP feil**, og det er verdt å merke seg: en kilde som er
systematisk dårligere er ikke dårligere overalt, og en kilde man har byttet til
er ikke riktig overalt.

Feilen var ikke bare et pent tall. `velg_instans` avgjør om en tur er over ved
å se om nå ligger etter siste stopp, så det oppblåste tidspunktet holdt turen
«underveis» i fire minutter etter at toget sto stille. Ett felt som ble lest
feil forplantet seg til et spørsmål om noe helt annet.

Nær slektning av lærdom 5: nullen som ikke var en måling, men fravær av en.
Her er det motsatte — et tall som ikke er null og likevel ikke måler noe.

**16. Om en prosent uten nevner.** Dekningsproben har en vakt: sier den at over
60 % av turene mangler posisjon, er noe galt. Kjørt 02:10 om natta fant den
**to** turer i vinduet, begge uten posisjon, og meldte «100 % mangler posisjon
— FEIL». Ingenting var galt; det går knapt tog forbi Østlandsknutepunktene
klokka to.

En terskel på en andel trenger et minste utvalg, ellers måler den hvor lite
data den fikk. Det er lærdom 3 i ny drakt, og den satt i en probe som ble
skrevet nettopp for å fange den slags.

**17. Om å ikke lagre nok til å kunne angre.** Da det ble klart at ferdige tog
hadde forurenset historikken, var neste spørsmål hvor mye — og hvilke rader som
kunne renses bort. Svaret var: ingen, med sikkerhet. `observasjoner` lagrer
`delay`, `band`, `stale` og `position_method`, men **ikke om turen var i gang
da raden ble skrevet**. Nettopp det feltet er det eneste som skiller halen fra
turen.

Nærmeste vi kom var en proxy — «siste sammenhengende rekke med identisk avvik,
innenfor 6 km» — som ga 17 av 1475 turer og er et overtall, fordi en tur kan
avslutte med et stabilt avvik helt lovlig. Godt nok til å bestemme seg, for
dårlig til å rense med.

Følgen var at tre døgn måtte kastes ut av et arkiv som ellers aldri slettes.
Kostnaden ved å ikke lagre ett flagg var altså ikke at spørsmålet ble
vanskelig, men at det ble **uavgjørbart** — og at data som var riktige måtte
kastes sammen med dem som ikke var det.

Merk at det ikke er et argument for å lagre alt. Det er et argument for å
spørre, når man designer en tabell: *hvilket spørsmål vil noen stille om denne
raden den dagen noe viser seg å være galt?* Her var svaret «var dette et tog i
trafikk», og det var ett heltall unna.

**18. Om at «nærmest» ikke er det samme som «riktig».** Flaskehalskartet leser
avviket for en stasjonspassering der toget var **nærmest** stasjonen. Det er en
god regel: uten stoppetider er det nærmeste vi kommer «toget var her».

Den slår feil på nøyaktig ett sted, og det er det stedet som betyr mest. Et tog
som parkerer på hensetting etter endt tur står ofte **nærmere plattformen enn
det var da det kjørte forbi** — Voss 30 m, Arna 20 m, Skien 40 m. Da vinner den
parkerte observasjonen, og et frosset avvik leses av som om det var ankomsten.
Strekningen inn mot endestasjonen får skylda for tid som gikk tapt etter at
turen var over.

Avstandsvernet på 500 meter hjalp ikke, for det var skrevet mot en annen feil
(parallelle baner). Bare Hovemoen, som ligger fire kilometer fra Lillehammer,
falt utenfor — og det var tilfeldig.

Lærdommen er ikke at regelen er feil. Den er at **et utvalgskriterium som er
riktig for én tilstand kan være systematisk feil for en annen**, og at
tilstanden må avgjøres først. Her var spørsmålet «kjørte toget» og ikke «hvor
nær var det».

**19. Om et avvik uten målestokk.** «Medianavviket mot Bane NOR var 2,1 km»
sto i tre uker som et tall man ikke kunne gjøre noe med. Er det mye? Det finnes
ikke noe svar før man vet hva to metoder normalt spriker.

Målestokken lå der hele tiden og kostet en ettermiddag: for hvert tog med GPS
kan posisjonen regnes ut en gang til fra rutetid og sporgeometri — samme
regnestykke vi allerede gjør for tog uten GPS. Svaret ble **0,96 km i median og
null tog over 10 km**. Da faller det opprinnelige funnet fra hverandre i to
deler som må behandles hver for seg: medianen på 2,1 km er uinteressant, og de
elleve utliggerne er hele saken.

Underveis dukket lærdom 5 opp igjen i ny drakt. Ukorrigert ga målingen median
2,31 km og verste 68,58, og tallene så ut som et posisjonsproblem. De var det
ikke: Journey Planner dropper sanntid for en dag som har vært, så tidslinja var
ren rutetabell, og avviket målte **forsinkelsen om igjen**. F5 706 lå 62
minutter etter ruta og dermed 68 km fra der ruta plasserte det. Etter å ha
flyttet klokka tilbake med togets eget avvik: 3,11 km.

Et avvik er ikke en måling før man vet hva det måles mot, og hva det ellers
kunne vært et mål på.

**20. Om to riktige tall som forteller hver sin historie.** Overskriften sa
«10 tog i trafikk». Ringen under sa «100 % i rute». Begge var riktige. På
kartet mellom dem lå F6 405 ni minutter forsinket.

Tallene kom fra hver sin populasjon: overskriften telte alt som tegnes, ringen
bare de togene som hadde GPS. Ingen av dem var feil, og nettopp derfor var det
vanskelig å se — en feil sum hadde ropt, to riktige tall gjorde det ikke.

Begrunnelsen for utelatelsen hadde vandret. Den var skrevet om **posisjonen**:
en interpolert posisjon er ikke en måling. Ringen handler om **forsinkelsen**,
og den kommer fra Journey Planner for beregnede og målte tog likt. Et argument
som er riktig om én egenskap ved en rad ble brukt til å utelate raden helt.

Følgen var ikke nøytral. De utelatte var fjerntogene, som er de som oftest er
forsinket, så tallet ble systematisk for pent — i den retningen ingen
kontrollerer.

Vernet er en invariant og ikke en påstand: `count == sum(counts) + stale`. Den
kan testes, og den ville ropt fra dag én.

**21. Om et felt som finnes to steder.** `transportSubmode` sier hva slags
trafikk en togtur er — lokaltog, regiontog, fjerntog, nattog. Entur har det på
**både** linja og turen, og bare det ene er fylt ut:

    Line.transportSubmode             «unknown» for alle 24 Vy-linjer,
                                      og for Go-Ahead og SJ
    ServiceJourney.transportSubmode   utfylt for 381 av 381 turer

Den opplagte veien er å lese det på linja. Linjer er få, stabile og lette å
cache, mens turer er tusenvis i døgnet. Hadde vi gjort det, ville svaret vært
«unknown» for tre av fire operatører, og den rimelige konklusjonen vært at
Entur ikke har feltet.

Fella er ikke at data manglet. Den er at **et tomt svar fra riktig felt på feil
nivå ser nøyaktig ut som fravær av data**. Det som avslørte det var å spørre om
begge i samme spørring, for de samme turene — ikke å lete videre etter andre
felt.

Slektning av lærdom 14, der de to ID-rommene også bare ble synlige når man
spurte om begge samtidig. Når et felt kan finnes på flere nivåer i en modell,
er det billig å hente alle én gang og se hvilke som svarer.

---

**Fellesnevneren:** tomt felt og nullverdi ser like ut i en tabell og betyr helt
forskjellige ting. Når et tall ser for pent ut — 13 identiske nuller, 100 %
treffrate, 251 av 251 `scheduled` — er det som regel ikke virkeligheten du
måler.
