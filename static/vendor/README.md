# Tredjepartskode

Filene her er **ikke skrevet i dette prosjektet**. De er hentet uendret fra
utgiveren og sjekket inn, slik at kartet ikke er avhengig av at et CDN er oppe
— og slik at ingen andre enn den som gjør en commit her kan endre hvilken kode
som kjører i nettleseren.

## MapLibre GL JS 4.7.1

| | |
|---|---|
| Utgiver | [maplibre/maplibre-gl-js](https://github.com/maplibre/maplibre-gl-js) |
| Lisens | 3-Clause BSD — [LICENSE.txt for v4.7.1](https://github.com/maplibre/maplibre-gl-js/blob/v4.7.1/LICENSE.txt) |
| Hentet fra | `https://unpkg.com/maplibre-gl@4.7.1/dist/` |
| Hentet | 23. august 2026 |

| Fil | Bytes | SHA-256 |
|---|---|---|
| `maplibre-gl.js` | 803 086 | `be9633c4d870e26fb37f1cfe5c5a77181667114003ea16207ac7850d8da8add1` |
| `maplibre-gl.css` | 65 534 | `576b085fdd9487a65a19215328c1e086c07ce5bf6da09b666b3806d3d008dae9` |

Lisensteksten er ikke kopiert hit fordi den følger med i fila: `maplibre-gl.js`
åpner med en `@license`-blokk som peker på den. `maplibre-gl.css` er minifisert
uten banner, slik utgiveren selv publiserer den.

## Verifiser at filene er uendret

```powershell
Get-FileHash static\vendor\maplibre-gl.js  -Algorithm SHA256
Get-FileHash static\vendor\maplibre-gl.css -Algorithm SHA256
```

Summene skal stemme med tabellen over. Gjør de ikke det, er filene endret etter
at de ble hentet — og det er noe å finne ut av før noe rulles ut.

## Bytt til en nyere versjon

```powershell
$v = "4.7.1"   # sett den nye versjonen her
foreach ($f in "maplibre-gl.js", "maplibre-gl.css") {
  Invoke-WebRequest "https://unpkg.com/maplibre-gl@$v/dist/$f" -OutFile "static\vendor\$f"
  Get-FileHash "static\vendor\$f" -Algorithm SHA256
}
```

Tre ting skal følge med i samme commit:

1. **Tabellen over** — versjon, dato og nye sjekksummer. Uten dem er
   verifiseringen over verdiløs.
2. **Sjekk at fila ikke har fått nye eksterne referanser.** 4.7.1 henter
   ingenting utenfra: de eneste `http`-forekomstene i de to filene er
   SVG-navnerom i data-URI-er og lenkene i lisensbanneret. Skulle en nyere
   versjon peke på et CDN, er avhengigheten tilbake — og da må CSP-en i
   `../../drift/cloudflare.md` vite om det.
3. **Åpne kartet og se at det tegner.** MapLibre henter vektorflisene i en web
   worker, og en worker som ikke starter er stille i nettverksfanen. Tegner
   kartet land og hav, kom flisene fram.
