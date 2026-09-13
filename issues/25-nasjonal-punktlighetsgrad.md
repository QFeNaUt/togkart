---
title: "Nasjonal punktlighetsgrad"
labels: enhancement, frontend
---

Én prosentmåler for hvor stor andel av togene i drift som er i rute nå.

Krever en definert grense. Bransjestandard er under 3 minutter for lokaltog og
under 5 for region- og fjerntog — men historikkpanelet bruker bevisst **én**
terskel på 240 sekunder for alle, fordi to terskler er feil når selskaper
rangeres mot hverandre. Se `docs/arkitektur.md`, «Historikkpanelets tre valg»,
og velg bevisst.

Skriv grensen synlig i grensesnittet uansett hva du velger; et punktlighetstall
uten oppgitt terskel betyr ingenting.
