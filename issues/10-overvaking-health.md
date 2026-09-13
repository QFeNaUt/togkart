---
title: "Overvåking: ingen ser på /api/health"
labels: drift
---

Fortsatt sant, og fortsatt et par klikk: **Traffic → Health Checks** i
Cloudflare, pekt mot `https://togkart.no/api/health`.

Blokkert av det samme som resten av kantarbeidet — se «togkart.no er ikke i
Cloudflare, så kantvernet finnes ikke». Det finnes ingen sone å legge en
health check i før domenet er flyttet fra Domeneshop.

**Selve endepunktet er gjort klart 23. august**, og det var det ikke før:

- **Statuskoden er nok som kriterium.** Det svarte 200 uansett hvor galt det
  sto til, med `"ok": false` gjemt i kroppen. En overvåker på
  standardinnstillingene ville meldt at alt var i orden mens kartet sto tomt.
  Nå er det 503 når `ok` er usann. Du *kan* legge til `"ok": true` som
  kroppssjekk i tillegg, men du trenger det ikke.
- **Ett minutt er en trygg frekvens.** Sjekken henter bare fra Entur når
  tallene er eldre enn `TOGKART_HELSE_TTL` (fem minutter). Før dette bommet
  hver eneste sjekk på cachen — `CACHE_TTL` er ti sekunder — og en bom er
  Vehicle Positions, Journey Planner, rutedata for togene uten GPS og en
  skriving til historikk.db. Målt: 2,3 sekunder kaldt mot 1,5 millisekunder
  varmt.
- **`alderSekunder`** sier hvor gamle tallene er, så et varsel kan skille «nede
  nå» fra «har stått en stund».

Når sonen er på plass:

1. Traffic → Health Checks → Create.
2. Sti `/api/health`, forventet kode `200`, intervall 60 s.
3. Varsling til e-post eller webhook.

Verifiser at kriteriet virker begge veier før du stoler på det — en
helsesjekk som aldri har vært rød, er en helsesjekk du ikke vet noe om.
`prober/sjekk_helse.py` viser hvordan de fire tilstandene tvinges fram
lokalt.
