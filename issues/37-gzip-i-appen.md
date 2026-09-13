---
title: "Cloudflare komprimerer, men ikke på strekningen som koster deg noe"
labels: drift, enhancement
---

Målt 13. september mot produksjon:

```
/api/trains            32 398 B  ->   4 744 B    zstd, 85 % mindre
/api/avvik             25 090 B  ->   5 848 B    77 %
jernbanenett.geojson  856 348 B  -> 303 347 B    65 %
index.html             12 811 B  ->   4 176 B    67 %
```

Ser bra ut, og er det — for den besøkende. Men **komprimeringen skjer på
kanten**. `app.py` har ingen `GZipMiddleware`, så kjeden er:

```
uvicorn  --rått-->  cloudflared  --rått-->  Cloudflare  --zstd-->  nettleser
```

Strekningen som går opp hjemmelinja di er den ukomprimerte. Cloudflare pakker
om først i Oslo.

`GZipMiddleware` i `app.py` ville kuttet den strekningen med rundt 80 % for
API-svarene. Cloudflare pakker om til zstd for den besøkende uansett — den
forhandler med klienten, ikke med origin.

**Hvorfor det ikke haster:** på 400/400 Mbit er hundre samtidige seere 16
Mbit/s vedvarende, altså 4 % av opplinja. Det er ikke et problem i dag.

**Hvorfor det er verdt å gjøre likevel:** taket ligger i pollingen, ikke i
sidelastingen. `/api/trains` på 31 kB hvert 15. sekund metter 400 Mbit ved
rundt 2 500 samtidige seere. Med GZip blir de 31 kB til rundt 5, og taket
flytter seg til godt over 10 000.

En linje kode for en femdobling av hvor mange som kan se på samtidig.

**Mål etterpå**, ikke bare anta: `curl -s -o /dev/null -w "%{size_download}"`
mot origin gjennom tunnelen, og se at `Content-Encoding: gzip` kommer fra
uvicorn og ikke bare fra Cloudflare.
