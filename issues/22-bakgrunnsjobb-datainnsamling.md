---
title: "Historikken logges bare når noen ser på kartet"
labels: enhancement, drift, prioritet-hoy
---

I dag skrives historikken av `asyncio.create_task()` inne i `/api/trains`:
står nettleseren lukket, logges ingenting. Det er grunnen til at `historikk.db`
har hull om natta, og det er en reell begrensning for enhver trendanalyse —
`jernbanenett.py` forkaster normalt rundt 50 % av strekningene, og det meste er
nettopp nattehull.

To veier, og de er ikke like:

- **`asyncio.create_task()` ved oppstart** i `app.py`, som poller i samme
  prosess uansett om noen spør. Mye mindre å sette opp, og nok så lenge
  serveren uansett står og går. Men den dør med serveren, og den gjør
  `/api/trains` sin cache til noe to ting skriver til.
- **Egen prosess** (APScheduler, eller en `while True` med `asyncio.sleep`)
  som poller og skriver, mens FastAPI bare leser. Riktigst, og det som må til
  om appen skal kjøre på M720Q-en døgnet rundt. Koster en prosess til å holde
  i live.

Den første er nesten sikkert riktig først: den er en ettermiddags arbeid og
fjerner nattehullene umiddelbart. Den andre er riktig når det finnes en maskin
som skal stå på uansett.
