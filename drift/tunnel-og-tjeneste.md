# Tunnel, tjeneste og database på M720Q-en

Konkret oppsett for LXC-en. Antar Debian 12 og at `cloudflared` allerede er
i bruk for stromkart — er den det, hopp til punkt 2.

Se `cloudflare.md` for det som skal settes opp i dashbordet (struping,
sikkerhetsheadere, cache).

---

## 1. Tunnelen

**Først: `togkartet.no` må inn i Cloudflare.** Domenet står i dag hos
Domeneshop (`ns1-3.hyp.net`), ikke på Cloudflare-navnetjenere. `cloudflared
tunnel route dns` under vil feile med «zone not found» til det er gjort. Legg
til siden i Cloudflare og bytt navnetjenerne hos registraren. Se
`cloudflare.md`.

```bash
# Én gang per maskin
cloudflared tunnel login
cloudflared tunnel create togkart
cloudflared tunnel route dns togkart togkartet.no
```

`/etc/cloudflared/config.yml`:

```yaml
tunnel: togkart
credentials-file: /etc/cloudflared/<tunnel-id>.json

ingress:
  - hostname: togkartet.no
    service: http://127.0.0.1:8000
    originRequest:
      # Kartet henter jernbanenett.geojson på 856 kB ved hver sidelasting.
      # Standarden på 30 s holder, men gi den litt luft på en treg linje.
      connectTimeout: 10s
      # Ingen TLS mot origin: uvicorn snakker rent HTTP på loopback, og
      # trafikken forlater aldri maskinen. TLS-en ligger i Cloudflare-enden.
      noTLSVerify: true
  - service: http_status:404
```

```bash
cloudflared service install
systemctl enable --now cloudflared
```

**Ingen port åpnes.** `cloudflared` ringer ut. Det betyr at punkt 6 i «Hva som
skal til» — DNS, portvideresending, DDNS — faller helt bort, og at
opphavs-IP-en din aldri eksponeres.

---

## 2. Appen som systemd-tjeneste

`/etc/systemd/system/togkart.service`:

```ini
[Unit]
Description=TogKart
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=togkart
WorkingDirectory=/opt/togkart
EnvironmentFile=/opt/togkart/.env

# --host 127.0.0.1: bare cloudflared skal nå den, uansett hva som skjer
#   med brannmuren.
# --no-server-header: fjerner "server: uvicorn" fra svaret. ../docs/sikkerhet.md
#   punkt 7 - gratis fingeravtrykk.
# Ingen --reload i produksjon: den dobler minnebruken og starter på nytt
#   hvis en logfil så vidt blir rørt.
ExecStart=/opt/togkart/.venv/bin/uvicorn app:app \
    --host 127.0.0.1 --port 8000 --no-server-header

Restart=always
RestartSec=5

# Appen skriver til én fil og leser resten. Da kan resten være skrivebeskyttet.
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
NoNewPrivileges=true
ReadWritePaths=/var/lib/togkart

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now togkart
journalctl -u togkart -f
```

---

## 3. `.env` i produksjon

Ferdig utfylt mal ligger i **`drift/env.produksjon`** — kopier den rett over:

```bash
scp drift/env.produksjon togkart@m720q:/opt/togkart/.env
```

Den er en mal og ikke en aktiv konfigurasjon: `load_dotenv()` leser `.env` i
prosjektroten og ingenting annet, så fila gjør ingenting der den ligger. Det
er med vilje — `TOGKART_BAK_CLOUDFLARE=1` er direkte skadelig lokalt, se
under. Innholdet:

```bash
ET_CLIENT_NAME=vegard-togkart

# Slår av /api/docs, /api/redoc og /openapi.json.
TOGKART_MILJO=prod

# Lar strupen lese CF-Connecting-IP. Uten denne teller den hele internett i
# én bøtte, fordi alt kommer fra cloudflared på loopback. Se cloudflare.md
# punkt 0 - dette er den enkleste feilen å gjøre i hele oppsettet.
TOGKART_BAK_CLOUDFLARE=1

# Databasen UT av containerens skrivelag. Ligger den inne, forsvinner all
# historikk neste gang du bygger på nytt, og den kan ikke gjenskapes.
HISTORIKK_DB=/var/lib/togkart/historikk.db

# Hvor lenge råobservasjonene lever. Må være minst like høyt som taket på
# `dager` i app.py (MAKS_DAGER = 90), ellers svarer ?dager=90 med færre døgn
# enn det ble spurt om. Appen advarer i loggen ved oppstart hvis de spriker.
HISTORIKK_BEHOLD_DAGER=90

# Når vedlikeholdsjobben kjører, lokal tid.
VEDLIKEHOLD_TIME=4
```

---

## 4. Databasen

```bash
mkdir -p /var/lib/togkart
chown togkart:togkart /var/lib/togkart
```

Tre ting er verdt å vite om den:

**Den er i WAL-modus.** Det betyr tre filer, ikke én: `historikk.db`,
`historikk.db-wal` og `historikk.db-shm`. `-wal` kan være titalls MB mellom
checkpoints, og det er normalt.

**Den skal ikke ligge på et nettverksdrev.** WAL trenger delt minne, og over
SMB eller NFS faller SQLite stille tilbake til `journal_mode=delete` — med
«database is locked» som symptom en uke senere. Appen advarer i loggen hvis
det skjer; se `_init()` i `historikk.py`.

**Bind mount, ikke volum i skrivelaget.** Kjører du dette i Docker i stedet
for LXC:

```
-v /var/lib/togkart:/var/lib/togkart
```

### Backup

Kopier **aldri** `.db`-filen med `cp` mens appen kjører. I WAL-modus ligger
de nyeste transaksjonene i `-wal`, og en `cp` av bare hovedfilen gir deg en
database som mangler dem. Bruk SQLite sin egen backup, som tar et konsistent
øyeblikksbilde uten å stoppe skriveren:

```bash
sqlite3 /var/lib/togkart/historikk.db \
  ".backup '/var/backups/togkart-$(date +%F).db'"
```

I crontab, søndag natt, med fire ukers oppbevaring:

```cron
0 3 * * 0 sqlite3 /var/lib/togkart/historikk.db ".backup '/var/backups/togkart-$(date +\%F).db'" && find /var/backups -name 'togkart-*.db' -mtime +28 -delete
```

Er plassen knapp, er `dogn`-tabellen alene verdt å ta vare på — den er noen
få MB og inneholder arkivet som aldri slettes:

```bash
sqlite3 /var/lib/togkart/historikk.db \
  ".dump dogn dogn_rullet" | gzip > /var/backups/togkart-arkiv-$(date +%F).sql.gz
```

---

## 5. Sjekk at det står

```bash
# Fra maskinen selv
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool

# Utenfra
curl -s https://togkartet.no/api/health | python3 -m json.tool
```

Fem ting å se etter i svaret:

| Felt | Skal være | Er den ikke det |
|---|---|---|
| HTTP-status | `200` | `503` betyr `ok: false`. Statuskoden er nok — du trenger ikke lese kroppen for å vite at noe er galt |
| `ok` | `true` | Entur svarer ikke, eller appen har aldri fått data |
| `alderSekunder` | under `TOGKART_HELSE_TTL` (300) | over betyr at siste hentingsforsøk feilet og at tallene du ser er gamle |
| `strupe.aktiv` | `true` | `TOGKART_STRUPE` er satt til `av` |
| `strupe.bakCloudflare` | `true` **utenfra** | `TOGKART_BAK_CLOUDFLARE` mangler — halve vernet finnes ikke |
| `strupe.enturKvote.igjen` | nær `kapasitet` | noe hamrer på søket, eller søkecachen bommer |

**Å spørre koster ingenting så lenge tallene er ferske.** Målt 23. august:
kaldt kall 2,3 sekunder — det henter Vehicle Positions, spør Journey Planner,
henter rutedata for togene uten GPS og skriver til historikk.db — og de neste
kallene 1,5 millisekunder. Grensen er `TOGKART_HELSE_TTL`, fem minutter som
standard. Sett den ikke ned mot `CACHE_TTL`: da blir hver eneste helsesjekk en
full Entur-henting igjen, og overvåkingen din blir den tyngste trafikken appen
har.

Og databasen:

```bash
cd /opt/togkart && .venv/bin/python vedlikehold.py --status
```

`Journalmodus` skal si `wal`. Sier den `delete`, ligger filen et sted WAL
ikke virker — se punkt 4.

---

## 6. Overvåking

`/api/health` hjelper ikke hvis ingen ser på den. Cloudflare har
**Health Checks** innebygget (Traffic → Health Checks) — pek den mot
`https://togkartet.no/api/health`. Da får du varsel når appen står, tunnelen
faller, eller Entur er nede, uten å sette opp noe på maskinen.

**Statuskoden er nok som kriterium.** Endepunktet svarer 503 når `ok` er
usann, så standardinnstillingen «2xx = frisk» er riktig. Du kan legge til
`"ok": true` som kroppssjekk i tillegg, men du trenger det ikke — og det er
poenget: den samme sjekken virker i en uptime-tjeneste, i en `curl -f` og i en
systemd-timer, uten at noen må huske å konfigurere kroppsmatching.

Til 23. august svarte den 200 uansett hvor galt det sto til, med `"ok": false`
gjemt i kroppen. En overvåker satt opp på standardverdiene ville sagt at alt
var i orden mens kartet sto tomt.

**Ett minutt er en fin frekvens.** Sjekken henter bare fra Entur når tallene
er eldre enn `TOGKART_HELSE_TTL` (fem minutter), så en monitor på ett minutt
koster én henting per femte sjekk. Går hentingen i stykker, blir cachen aldri
fersk igjen, og da prøver sjekken på ekte hver gang — som er nøyaktig når du
vil at den skal gjøre det.

`prober/sjekk_helse.py` vokter alt dette uten nett og uten database, og står i
CI.

Det dekker punktet om overvåking under «Krever mer innsats» i
`../docs/sikkerhet.md`.
