---
title: "Tidshjul på varmekartet"
labels: enhancement, frontend
---

Den billigste av de gjenstående flaskehalssakene: aksen den manglet er nå på
plass. `flaskehalser()` tar allerede `dager`; en glidebryter trenger et
`time`-filter i `_hent_rader` og ellers ingenting nytt.

Det som er verdt å tenke gjennom først er **utvalget**: en enkelt klokketime
over en uke er fem hverdager, og det er tynt for en median.
