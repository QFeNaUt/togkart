---
title: "Se et ekte tog stå på riktig sted: visuell sammenligning mot Bane NOR"
labels: verifisering
---

Siste rest av problem 2 (interpolasjonen gikk i rett linje). Fiksen er
implementert og verifisert i tall; det som gjenstår er én visuell kontroll.

```powershell
python prober/sjekk_geometri.py
```

Kjør på dagtid. Steg 4 rangerer togene etter hvor langt fiksen flyttet dem, og
det øverste er det som er verdt å slå opp på togkart.banenor.no.

Se `docs/undersokelser.md`, problem 2.
