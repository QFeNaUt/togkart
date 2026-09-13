---
title: "Forsinkelsesminutter per linjesegment"
labels: enhancement, data
---

`historikk.py` logger hvert tog ved hver endring, `analyse.py` regner medianer
per operatør, og `avvik.py` henter SIRI-SX. Det som **ikke** finnes er
oppdelingen per linjesegment.

Samme mangel som flaskehalskartet sto og ventet på — der ble aksen bygget ved å
utlede sted av `lat`/`lon` mot stasjonsregisteret. Den samme aksen er det denne
saken trenger.
