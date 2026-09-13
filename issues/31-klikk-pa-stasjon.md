---
title: "Klikk på stasjon gir neste avganger"
labels: enhancement, frontend
---

NSR-ID-en ligger allerede i `stasjoner.geojson`, og det er nøkkelen
`estimatedCalls` trenger. Neste avganger i en popup er dermed nærmere enn det
ser ut — `lagstasjoner.py` spør allerede om nøyaktig den spørringen ved
bygging, så formen på svaret er kjent.
