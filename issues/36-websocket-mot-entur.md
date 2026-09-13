---
title: "Websocket mot Entur i stedet for polling"
labels: enhancement
---

Entur tilbyr `wss://api.entur.io/realtime/v2/vehicles/subscriptions` for
kontinuerlig strøm i stedet for polling.

Merk hva det gjør med struping: taket i `strupe.py` teller utgående HTTP-kall,
og en abonnementsstrøm teller ikke der. Kvoten mot `ET_CLIENT_NAME` må måles på
nytt før dette tas i bruk. Se `docs/sikkerhet.md` punkt 2.
