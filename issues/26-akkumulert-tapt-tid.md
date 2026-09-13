---
title: "Akkumulert tapt tid i dag"
labels: enhancement, frontend
---

Sum av forsinkelsesminutter gjennom døgnet. Krever døgnlagring, som finnes
(`dogn`-tabellen i `historikk.db`).

Blokkeres i praksis av «Historikken logges bare når noen ser på kartet» —
summen er misvisende så lenge det ikke logges om
natta.
