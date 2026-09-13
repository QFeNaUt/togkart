---
title: "togkartet.no er ikke i Cloudflare, så kantvernet finnes ikke"
labels: sikkerhet, drift
---

Denne saken het «Sikkerhetsheadere og CSP er formulert, men ikke satt opp».
**Headerne er satt opp** siden 23. august — `app.py` sender alle fem som
middleware, `prober/sjekk_headere.py` vokter dem i CI, og
`drift/cloudflare.md` punkt 1 er strøket fra lista over ting å gjøre.

Det som står igjen er kanten, og det er ikke fem minutter i et dashbord: det
er en DNS-migrering først.

```
| Domene         | Navnetjenere                    | I Cloudflare |
| stromkart.no   | ian / kinsley .ns.cloudflare.com | ja          |
| togkartet.no     | ns1 / ns2 / ns3 .hyp.net         | nei         |
```

`togkartet.no` er registrert, men står parkert hos Domeneshop og svarer ikke på
HTTPS. Det finnes altså ingen sone å legge regler i, og reglene kan ikke
opprettes for en sone som ikke er der. **Legg dem heller ikke i
`stromkart.no`-sonen** — de er skrevet for `/api/search` og
`/api/statistikk/`, og strupetallene er utledet av hva TogKarts frontend gjør.

Rekkefølgen, som står utførlig i `drift/cloudflare.md`:

1. Legg til `togkartet.no` i Cloudflare og bytt navnetjenerne hos Domeneshop.
2. Sett opp tunnelen — `drift/tunnel-og-tjeneste.md`.
3. Så de tre gjenstående reglene. **Regel 3, ratebegrensningen, er den
   viktigste.**
4. Sett `TOGKART_BAK_CLOUDFLARE=1` i `.env`, ellers teller `strupe.py` hele
   internett i én bøtte. Verifiser med
   `curl -s https://togkartet.no/api/health` — `"bakCloudflare": true`.

Til det er gjort, er vernet det `strupe.py` gir alene. Det er ikke ingenting —
bakstopperen struper per klient, og taket mot Entur er uansett bare i appen —
men per-klient-delen ligger da i din egen prosess i stedet for på kanten, og
koster deg båndbredden inn i huset.

**Funnet som er verdt å ta med når CSP-en en gang skal røres:** flisvertene
står ikke i koden. `app.js` peker på `basemaps.cartocdn.com`, den peker videre
på en `tiles.json`, og den oppgir fire helt andre verter (`tiles-a` til
`tiles-d`). En CSP skrevet ut fra `index.html` alene blir riktig for alt
unntatt det kartet faktisk tegner med.
