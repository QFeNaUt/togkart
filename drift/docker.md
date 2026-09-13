# TogKart i Docker

`drift/tunnel-og-tjeneste.md` beskriver uvicorn under systemd, rett på verten.
Denne beskriver det samme oppsettet i container, som er det M720Q-en skal
kjøre. Alt som handler om Cloudflare-sonen, tunnelen og reglene i dashbordet
står fortsatt der — dette er bare emballasjen rundt appen.

Tre filer i prosjektroten: `Dockerfile`, `compose.yaml`, `.dockerignore`.
Begrunnelsene ligger som kommentarer i filene; her står rekkefølgen.

## Første gang

```bash
# 1. Katalogen databasen skal ligge i, eid av uid-en i containeren.
sudo mkdir -p /var/lib/togkart
sudo chown -R 10001:10001 /var/lib/togkart

# 2. Konfigurasjon.
cp drift/env.produksjon .env          # rediger: HISTORIKK_DB, ET_CLIENT_NAME
echo "TUNNEL_TOKEN=ey..." > .env.tunnel
chmod 600 .env.tunnel

# 3. Bygg og start.
docker compose build
docker compose up -d
docker compose logs -f togkart
```

`HISTORIKK_DB` skal peke på bind mountet — `/var/lib/togkart/historikk.db`, den
stien som allerede står i `drift/env.produksjon`. Ligger databasen i
containerens skrivelag i stedet, forsvinner all historikk neste gang du bygger,
og den kan ikke gjenskapes.

Har du en `historikk.db` fra utviklingsmaskinen du vil ta med, kopier den **med
de tre filene** eller ta en `sqlite3 .backup` først. En `cp` av bare
`.db`-filen i WAL-modus gir deg en database som mangler de nyeste
transaksjonene.

## Tre ting som er annerledes enn under systemd

| | systemd på verten | I container |
|---|---|---|
| Binding | `--host 127.0.0.1` | `--host 0.0.0.0` — containergrensen er isolasjonen, og ingen port publiseres |
| cloudflared | egen systemd-unit på samme vert | egen container, men **i samme nettverksnavnerom** |
| Omstart | `Restart=always` | `restart: unless-stopped` |

Den midterste er den eneste som kan gå galt i stillhet.
`network_mode: "service:togkart"` gjør at cloudflared kobler seg til
`127.0.0.1:8000` innenfra, og det er nettopp betingelsen `_loopback()` i
`strupe.py` krever før den stoler på `CF-Connecting-IP`. Flytter du cloudflared
ut i en vanlig nabocontainer, kommer trafikken fra docker-broen, headeren
ignoreres uten et ord i loggen, og strupen teller hele internett i én bøtte.

## Verifiser etter utrulling

```bash
docker compose ps                       # begge skal stå "Up", togkart "healthy"
docker compose exec togkart python sjekk.py alle
docker compose exec togkart python prober/sjekk_helse.py
curl -s https://togkart.no/api/health | grep -E 'ok|bakCloudflare'
```

`"bakCloudflare": true` er den ene linja som ikke kan utledes av at siden
laster. Alt annet ser riktig ut helt til noen hamrer.

Helsesjekken i `Dockerfile` bruker `/api/health` og statuskoden alene — den er
503 når `ok` er usann. Merk at Docker **ikke** starter en container på nytt
fordi den er `unhealthy`; det er et signal til `docker ps`, ikke en
selvhelbredelse. Overvåkingen som faktisk varsler deg, er Cloudflare Health
Checks — se issue «Overvåking: ingen ser på /api/health».

## Backup

Uendret fra `tunnel-og-tjeneste.md`, og den skal kjøre **på verten**: filen
ligger på verten, og `sqlite3 .backup` tar et konsistent øyeblikksbilde uten å
stoppe skriveren.

```cron
0 3 * * 0 sqlite3 /var/lib/togkart/historikk.db ".backup '/var/backups/togkart-$(date +\%F).db'" && find /var/backups -name 'togkart-*.db' -mtime +28 -delete
```

Har verten ikke `sqlite3`, gjør containeren det samme:

```bash
docker compose exec -T togkart python -c "
import sqlite3, historikk
with historikk.kobling(skrivbar=False) as kilde:
    mottaker = sqlite3.connect('/var/lib/togkart/backup.db')
    with mottaker: kilde.backup(mottaker)
    mottaker.close()"
```

Vedlikeholdsjobben inne i appen ruller opp og roterer, men den tar ikke backup.
Det er to forskjellige jobber.

## Oppdatering og tilbakerulling

```bash
git pull
docker compose build && docker compose up -d
```

Tilbakerulling er `git checkout <revisjon> && docker compose up -d --build`.
Det forutsetter at koden faktisk ligger i git — per 13. september 2026 gjør den
ikke det, og da finnes det ingen revisjon å rulle tilbake til.

Byggeskriptene (`lagbaner.py`, `lagjernbanenett.py`, `lagstasjoner.py`) er holdt
ute av imaget av `.dockerignore`. De skriver filer som ligger i git, og skal
kjøres lokalt med resultatet sjekket inn. Er de ikke i containeren, kan de
heller ikke kjøres der ved et uhell.

## Hva som er målt

Bygget og kjørt 13. september 2026, på `togkart:test` fra denne `Dockerfile`:

| | Resultat |
|---|---|
| Bygg | Går gjennom. uvloop og httptools kom som ferdige `cp313`-hjul — 3.13-valget holder |
| Image | 256 MB |
| `GET /` | 200 |
| `GET /api/health` | 200, `"ok": true`, 95 tog hentet fra Entur inne i containeren |
| `GET /api/docs` | 404 med `TOGKART_MILJO=prod` |
| Sikkerhetsheadere | Alle fem, CSP-en ordrett som i `app.py` |
| `prober/sjekk_headere.py` | Alt grønt |
| `prober/sjekk_helse.py` | Alt grønt |
| Historikk | Skrives fra containeren — 282 observasjoner, `journal_mode = wal` |
| Bruker | uid 10001, filene i bind mountet eid av den |
| Klokke | `Sun Sep 13 14:03:28 CEST 2026` — `TZ` virker |
| `HEALTHCHECK` | Går til `healthy` innen start-perioden |
| `read_only: true` | Virker, og står nå i `compose.yaml` |
| Byggeskriptene | Ikke i imaget |

To ting ble funnet ved å måle, og begge er rettet:

- **`.dockerignore` er ikke rekursiv.** `__pycache__/` traff bare roten, og
  `prober/__pycache__` fulgte med inn i imaget. Mønstrene er nå `**/`.
- **`drift/` kan ikke utelates.** `prober/sjekk_headere.py` punkt 5 leser
  `drift/cloudflare.md` for å sjekke at koden og dokumentasjonen sier det
  samme om CSP-en, og falt på `FileNotFoundError` inne i containeren.

Det som fortsatt ikke er prøvd, er cloudflared-halvdelen: den krever et
tunneltoken og en Cloudflare-sone, og sonen finnes ikke før `togkart.no` er
flyttet fra Domeneshop. `network_mode` er altså resonnert fram fra
`_loopback()` i `strupe.py`, ikke observert. Det er `"bakCloudflare": true`
i `/api/health` som avgjør om det stemmer, og den kan først leses når tunnelen
står.
