# Tunnel, tjeneste og database på M720Q-en

Konkret oppsett for LXC-en. Utført 13. september 2026 på **LXC 106**, Debian
13, unprivileged.

Se `cloudflare.md` for det som skal settes opp i dashbordet (struping, cache)
og for hvordan sonen kom på plass.

---

## 1. Tunnelen

**Fjernstyrt tunnel med token.** Dette punktet beskrev fram til 13. september
en *lokalt styrt* tunnel — `cloudflared tunnel login`, en `config.yml` med
ingress-regler, og `cloudflared tunnel route dns`. Det er en helt annen
oppskrift enn den som ble brukt, og de to skal ikke blandes: i en fjernstyrt
tunnel ligger ingress-konfigurasjonen i dashbordet, og tokenet *er* hele
konfigurasjonen på maskinen.

**Opprett tunnelen** i Zero Trust → Networks → Tunnels & Mesh → Create a
tunnel → **Cloudflared**. Kall den `togkart`. Kopier tokenet — `eyJ...` — fra
installasjonskommandoen dashbordet viser. Hvilket operativsystem du velger i
nedtrekkslista spiller ingen rolle; tokenet er det samme.

**Installer i LXC-en:**

```bash
curl -L -o /tmp/cloudflared.deb   https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
dpkg -i /tmp/cloudflared.deb
cloudflared service install eyJ...
```

Kommandoen både lager og starter `cloudflared.service`. Ingen `config.yml`,
ingen `credentials-file`, ingen `tunnel route dns`. Tokenet legges i
`/etc/cloudflared/token`.

**Ruten settes i dashbordet**, ikke på maskinen: tunnelens side → fanen
**Published application routes** → Add:

| Felt | Verdi |
|---|---|
| Subdomain | tomt |
| Domain | `togkartet.no` |
| Path | tomt |
| Type | **HTTP** |
| URL | `127.0.0.1:8000` |

Det steget lager CNAME-en i sonen selv. Ikke opprett noen DNS-post for hånd.

**Pass på nabofanen.** «Hostname routes» ser ut som det samme, men er privat
tilgang gjennom WARP-klienten og gjør ingenting tilgjengelig på internett.
Kjennetegnet: det skjemaet spør ikke om tjeneste og URL. Riktig skjema må ha
begge, for det er koblingen til `127.0.0.1:8000` som er hele poenget.

**`HTTP`, ikke HTTPS.** Uvicorn snakker rent HTTP på loopback, og den
etappen forlater ikke containeren. TLS-en ligger i Cloudflare-enden. Velger
du HTTPS, må uvicorn servere et sertifikat for `127.0.0.1` som `cloudflared`
deretter må instrueres om å ikke verifisere — seremoni uten gevinst.

**Én cloudflared per tunnel.** Den som kjører for stromkart i LXC 105 er en
annen tunnel med et annet token. Ikke gjenbruk.

**Ingen port åpnes.** `cloudflared` ringer ut. DNS mot hjemmet,
portvideresending og DDNS faller helt bort, og opphavs-IP-en eksponeres
aldri.

Verifiser:

```bash
systemctl status cloudflared --no-pager
journalctl -u cloudflared -n 20 --no-pager     # «Registered tunnel connection» x4
```

Fire forbindelser er normalt — to datasentre, to forbindelser hver. Målt ved
utrulling: `osl02` og `arn07`, over QUIC.

---

## 2. Appen som systemd-tjeneste

**Fila ligger i git som `drift/togkart.service`.** Kopier den, ikke skriv den
av:

```bash
cp /opt/togkart/drift/togkart.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now togkart
systemd-analyze verify /etc/systemd/system/togkart.service   # skal være stille
```

Grunnen til at den ligger i git og ikke bare som en blokk her: innliming av
den i en nettleserkonsoll mislyktes to ganger under utrullingen 13.
september, begge ganger ved at `[Unit]` på første linje forsvant. Resultatet
var en tjeneste som kjørte, men uten `After=network-online.target` — den
ville startet før nettverket ved en omstart av containeren, og ingenting
hadde sagt fra. `systemd-analyze verify` er det som fanger det; den skriver
ingenting når alt er i orden.

Innholdet, for lesing:

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
