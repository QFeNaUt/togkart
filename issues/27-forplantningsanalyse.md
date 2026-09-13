---
title: "Forplantningsanalyse: er toget forsinket på grunn av toget foran?"
labels: enhancement, data
---

Faglig den vanskeligste: krever posisjon, retning og sporkapasitet samtidig.

**Retningsbrikken falt på plass 20. august.** Den sto som sperre fordi
`bearing` finnes for de færreste — og det gjør den fortsatt — men retningen
utledes nå av bevegelse i stedet og dekker rundt 85 % av togene. Se
«Kjøreretning» i `docs/kartlag.md`.

Det som gjenstår er **sporkapasiteten**: å vite at to tog er på *samme* spor og
ikke på hver sin parallelle linje. Sporgeometrien hjelper — når togene ligger
på linjen, kan avstand måles langs sporet i stedet for i luftlinje.

Blokkerer «Flaskehalsmarkører: følgefeilmåling».
