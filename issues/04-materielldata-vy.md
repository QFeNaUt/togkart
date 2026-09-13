---
title: "Vy har ingen materielldata i Entur — bør vi skrive en typisk-tabell for dem?"
labels: enhancement, data, avklaring
---

`vehicleId` er settnummeret hos Flytoget (`71-15`) og Go-Ahead (`73-04`), men
tognummeret hos Vy (`1214-2026-08-17`). SJ har ingen `vehicleId` i det hele
tatt.

SJ er dekket av `typical_stock()`, som slår opp fra linjekode og merker svaret
`typical: true`, slik at kartet kan si «vanligvis» i stedet for å påstå hvilket
sett som faktisk kjører. Vy har ingen slik tabell.

---

## Undersøkt 22. august: Entur har det ikke

Ettersøkt i begge API-ene, så ingen leter en gang til:

| Sted | Resultat |
|---|---|
| Vehicle Positions, 26 felt | ingen om materiell. `vehicleId` er det eneste, og for Vy er det tognummeret |
| `ServiceJourney.notices` | **0 av 60 turer** hadde noen |
| `ServiceJourney.publicCode` | `None` for alle |
| `ServiceJourney.privateCode` | tognummeret om igjen (1170, 535, 2270 …) |
| `JourneyPattern`, `EstimatedCall` | ingenting om materiell |

**Konklusjonen står: Entur har ingen materielldata for Vy.** Det er nå målt og
ikke antatt, og det er skrevet inn i modul-docstringen til `materiell.py`.

## Det som kom ut av letingen

`ServiceJourney.transportSubmode` er utfylt for **381 av 381 turer** — altså
hver eneste tur, for hver operatør. Det er ikke materiell, men det er MÅLT, og
det er det eneste Entur har som beskriver toget for de som ikke publiserer
settnummer. Det er nå i bruk (`service_type()` i `materiell.py`, feltet
`serviceType` på hvert tog), så et Vy-tog som før viste ingenting sier nå
«Lokaltog», «Regiontog», «Fjerntog» eller «Nattog».

**En felle det er verdt å kjenne:** feltet finnes to steder, og bare det ene er
fylt ut.

```
ServiceJourney.transportSubmode   utfylt for 381 av 381 turer
Line.transportSubmode             "unknown" for ALLE 24 Vy-linjer,
                                  og for Go-Ahead og SJ
```

Bare Flytoget og de svenske operatørene har den på linja. Leser man den på
linjenivå — som er billigere og mer opplagt, siden linjer er få og stabile —
får man «unknown» for tre av fire operatører og konkluderer med at feltet er
tomt.

## Det som gjenstår: en avklaring, ikke en oppgave

Skal Vy få en håndskrevet `LINE_STOCK`-tabell, slik SJ har?

**For:** mekanikken finnes, `typical: true` gjør påstanden ærlig, og Vy er
rundt to tredeler av togene på kartet.

**Mot:** det er en påstand noen må vedlikeholde, og prosjektet har et uttalt
prinsipp om å foretrekke en observasjon appen allerede gjør — se
avgrensningen av stasjonsutvalget i `lagstasjoner.py`. Vy kjører dessuten
blandet materiell på flere av lokallinjene, så en tabell ville vært upresis
akkurat der den ble mest lest. Og en feil rad er mye mer synlig for Vy enn for
SJ, nettopp fordi Vy er så stor del av kartet.

Trengs det, er jobben liten: `LINE_STOCK` i `materiell.py` tar linjekode →
liste med klassekoder, og `CLASSES` har allerede BM69, BM70, BM72, BM74, BM75
og EL18. Det som mangler er **fasiten**, og den må komme fra norsketog.no —
ikke fra hukommelsen.

## Løse tråder i tabellen som allerede står der

Fortsatt ubekreftet, fra den opprinnelige saken:

- Antallene er fra norsketog.no og er verifisert. **Byggeår og toppfart er fra
  åpne kilder og er ikke kontrollert.**
- BM73 finnes i A- og B-serie, BM69 i C, D, G og H. Settnummeret alene skiller
  dem ikke, så typen vises uten serie. Det er en bevisst forenkling, ikke en
  mangel.
