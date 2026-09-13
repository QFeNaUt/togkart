---
title: "Mål posisjonsavviket mot Bane NOR på nytt, paret på linje+retning+destinasjon"
labels: undersokelse, data
---

Sammenlikningen 21. august kl. 11:36–11:39 fant **11 av 73 tog over 10 km fra
Bane NORs posisjon**, verst RE11 814 med 20,4 km. Årsaken er fortsatt ukjent,
men saken er vesentlig innsnevret — se
[docs/undersokelser.md](../docs/undersokelser.md), «Posisjonsavviket mot
Bane NOR».

## Hvorfor det er verdt å måle igjen

Utliggerne er **ikke** normal spredning mellom to posisjonsmetoder. Det er nå
målt: samme minutt, samme tog, med rutetid og sporgeometri som andremening gir
**median 0,96 km, verste 8,44 km og null over 10 km**. Medianen på 2,1 km mot
Bane NOR er altså uinteressant, men de elleve er et reelt funn.

## Hva som skal gjøres

Hent et nytt uttrekk fra `TogkartTrainsLayer` og par togene på **linje, retning
og endedestinasjon** i stedet for tognummer.

Paringen på tognummer er ikke til å stole på. Entur bruker allerede to ID-rom
for samme tog — `DatedServiceJourney` mot `ServiceJourney`, se `tognokkel()` i
`sjnord.py` — så at en tredje kilde nummererer på en tredje måte er ikke
fjernt.

**Start med RE11 814.** Våre data hadde det på 59,6384/10,2272, mellom Sande og
Drammen, 48 sekunder forsinket, ikke spøkelsestog, og posisjonen var forenlig
med rutetabellen. Er Bane NORs «814» et annet fysisk tog, faller saken.

## De to hypotesene som står igjen

- **1b — ulike identifikatorer.** Ikke at Entur *unnlater* å bytte nummer, men
  at Enturs og Bane NORs tognummer betyr forskjellige ting. Avgjøres av
  ommålingen over.
- **2 — Bane NOR interpolerer mellom sporfelter.** Signalanlegget rapporterer
  ved faste punkter, og kartet deres har egne rutedata (`pathsByTrackId`) til å
  gjette imellom. Kan bare avgjøres med Bane NORs egne data.

## Hypotesen som er avlivet

> «Mange gjennomgående tog bytter nummer på Oslo S.»

Målt på alle 1051 turene 21. august bærer **8 (0,8 %) to tognumre, og alle åtte
er RE20 Oslo–Göteborg**. Byttet ligger på grensen, og Oslo S er første eller
siste stopp — ikke et sted underveis. For alle andre linjer bytter Entur ikke
nummer i det hele tatt.

Unntaket teller: RE20 sto på lista over de verste avvikene, så for den ene
linja kan hypotesen fortsatt holde.

## Imens

`prober/sjekk_posisjon.py` måler våre egne posisjoner mot rutetid og
sporgeometri, uten Bane NOR, og sier fra hvis medianen går over 3 km eller et
enkelt tog over 15 km. Den avgjør ikke denne saken, men den fanger at
posisjonene våre begynner å drive.
