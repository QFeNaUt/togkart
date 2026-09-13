---
title: "Overvåking: ingen ser på /api/health"
labels: drift
---

**Blokkeringen er borte siden 13. september** — `togkartet.no` ligger på
Cloudflare, og sonen som manglet finnes. Saken sto tidligere og ventet på
den; nå venter den bare på at noen gjør det.

Selve endepunktet er klart, og var det ikke før 23. august:

- **Statuskoden er nok som kriterium.** Det svarte 200 uansett hvor galt det
  sto til, med `"ok": false` gjemt i kroppen. En overvåker på
  standardinnstillingene ville meldt at alt var i orden mens kartet sto tomt.
  Nå er det 503 når `ok` er usann.
- **Ett minutt er en trygg frekvens.** Sjekken henter bare fra Entur når
  tallene er eldre enn `TOGKART_HELSE_TTL` (fem minutter). Målt: 2,3 sekunder
  kaldt mot 1,5 millisekunder varmt.
- **`alderSekunder`** skiller «nede nå» fra «har stått en stund».

Det som gjenstår er to steder å velge mellom, og de utelukker ikke hverandre:

1. **Cloudflare → Traffic → Health Checks.** Sti `/api/health`, forventet kode
   `200`, intervall 60 s, varsling til e-post. Sjekker utenfra, gjennom hele
   kjeden — DNS, tunnel, app. Verifiser at den er tilgjengelig på Free-planen.
2. **Uptime Kuma i LXC 102**, som allerede står og går. Sjekker innenfra og
   koster ingenting, men ser ikke om tunnelen er nede.

Den første er den som svarer på spørsmålet «kan folk se kartet». Den andre
svarer på «kjører appen».

Verifiser at kriteriet virker begge veier før du stoler på det — en
helsesjekk som aldri har vært rød, er en helsesjekk du ikke vet noe om.
`prober/sjekk_helse.py` viser hvordan de fire tilstandene tvinges fram
lokalt.
