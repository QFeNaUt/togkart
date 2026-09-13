---
title: "«>15 min = rødt» i nyhetsstripa leses ut av fritekst"
labels: bug, data
---

Grensen er bygget, men `_MINUTTER` i `avvik.py` leser minuttallet ut av
**fritekst**, siden SIRI-SX ikke har noe felt for det. Den slo til på «Toget
kjørte 40 minutter forsinket fra Sørumsand» og var stille i halvtimen før.

Skriver operatørene om formuleringen, blir regelen stille uten å feile.
`prober/sjekk_avvik.py` teller treffene nettopp derfor — se punkt 2 der.

Løses egentlig av «Koble nyhetsstripa til togene på kartet».
