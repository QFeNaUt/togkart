---
title: "Merking av dobbeltsett i popup"
labels: enhancement, frontend
---

`coupled` og `vehicleIds` ligger klare i svaret fra `/api/trains`. Popupen
bruker dem ikke.

Merk at `_merge_coupled()` med vilje lar to kjøretøy med samme tognummer stå
adskilt når de ligger over én kilometer fra hverandre — se «Tog som tegnes to
ganger» i `docs/undersokelser.md`.
