---
title: "Koble nyhetsstripa til togene på kartet"
labels: enhancement, data
---

Den ekte forsinkelsen i minutter ligger allerede i `/api/trains`, per tog. En
melding som rammer linje R14 kunne fargelegges etter hva R14-togene faktisk
gjør nå, i stedet for etter hva teksten sier. Det er den eneste veien til et
ærlig «>15 min»-kriterium — se saken om at grensen leses ut av fritekst.

Krever at `affects` sine `serviceJourney`-ID-er matches mot `journeyRef` —
begge finnes, og de er samme rom.
