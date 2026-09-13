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

---

**Lagt til 13. september, og det endrer dimensjoneringen.** Da maskinen kom på
plass ble datagrunnlaget målt:

```
2026-08-21    911 tog     34 587 obs
2026-08-23    502 tog     65 617 obs
2026-08-24    164 tog      1 927 obs
2026-08-22     91 tog     10 358 obs
```

Togtrafikken varierer ikke med en faktor ti mellom en fredag og en lørdag.
Dette er et mål på hvor lenge nettleseren sto åpen. Time 07 UTC — altså
klokka ni om morgenen — har **null** observasjoner i hele materialet, mens
17–21 UTC har tolv ganger så mange som morgenrushet.

Og det er verre enn hull i en logg: `rushtidsprofil()` i `analyse.py` leser
disse rå radene direkte gjennom `_hent_rader()`. **Rushtidsprofilen regnes ut
av et utvalg vektet etter når noen så på kartet**, og morgenrushet er den
dårligst dekkede delen av døgnet. Det samme gjelder `flaskehals.py`.

**Konsekvens for dimensjoneringen:** driftsdokumentet sier 5 MB i døgnet og
443 MB i likevekt. De tallene er målt på det samme skjeve utvalget. Med
kontinuerlig innsamling — anslagsvis 1 000–1 500 avganger i døgnet, rundt 110
observasjoner per tog, 259 bytes per rad — lander man nærmere **34 MB i
døgnet og 3 GB ved 90 dagers oppbevaring**.

16 GB tåler det fint. Men mål veksten det første døgnet etter at jobben er
i gang, og juster `HISTORIKK_BEHOLD_DAGER` ut fra tallet i stedet for fra et
anslag som ble målt feil. Husk at `MAKS_DAGER = 90` i `app.py` må følge med
ned hvis oppbevaringen senkes.
