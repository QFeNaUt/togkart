---
title: "Kjør målestokk- og min_bit-sjekken systematisk på alle seksten baner"
labels: data, verifisering
---

Hverken målestokk-sjekken eller `min_bit`-sjekken er kjørt systematisk på alle
seksten banene. Begge er billige — `km` mot korridorlengde, og verste hull ved
`--min-bit 0` mot `--min-bit 300` — og **begge har funnet noe hver gang de er
kjørt**: Bratsbergbanen, Drammenbanen og Gardermobanen.

De tre feilene hadde motsatt fortegn og samme rot: i alle tre tilfellene var
dataene riktige og fasiten feil.

To ting å huske:

- `km` må måle alle navnene i `osm`, ikke bare det første.
- `python lagbaner.py --bane X` skriver `hovedbaner.geojson` med **bare** den
  ene banen. Bygg alt med `python lagbaner.py` før du laster kartet.

Se `docs/erfaringer.md`, lærdom 13.
