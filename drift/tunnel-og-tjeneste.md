# Tunnel, tjeneste og database på M720Q-en

Konkret oppsett for LXC-en. Utført 13. september 2026 på **LXC 106**, Debian
13, unprivileged.

Se `cloudflare.md` for det som skal settes opp i dashbordet (struping, cache)
og for hvordan sonen kom på plass.

---

## 0. Containeren i Proxmox

Laget med **Create CT** i nettgrensesnittet. Ressursene og begrunnelsen for dem
står i `README.md`. Her står det som ikke sto noe sted, og som var feil.

**«Start after created» er ikke «Start at boot».** Avkryssingen på siste side i
opprettelsesveiviseren starter containeren én gang, rett etter at den er laget.
Det er noe annet enn å starte den når verten starter. Den innstillingen ligger
under **Options → Start at boot**, og står av som standard.

14. september sto både `togkart` (106) og `stromkart` (105) uten den. Begge
nettstedene ville blitt liggende av etter en omstart av verten, for eksempel
etter et strømbrudd eller en kjerneoppdatering, og ingenting ville sagt fra.
For stromkart hadde det vært slik i minst 84 dager. Det slo aldri til, fordi
verten hadde stått oppe i 140 dager.

```bash
pct set 106 --onboot 1 --startup order=3
```

**AdGuard starter først.** De andre gjestene trenger DNS:

```bash
pct set 101 --startup order=1,up=15
```

`order=1` starter AdGuard først, og `up=15` venter 15 sekunder før neste gjest.
Proxmox starter gjester med rekkefølge før gjester uten, og stopper dem i
motsatt rekkefølge, så det er nok å sette rekkefølge på dem det gjelder.

For togkart er rekkefølgen ikke kritisk. Starter den før DNS er klar, feiler de
første hentingene, og bakgrunnsjobben prøver igjen etter 60 sekunder. Men det
er ryddigere at den ikke må.

**Sjekk alle gjestene på én gang**, fra Proxmox-shellet:

```bash
for id in $(pct list | awk 'NR>1{print $1}'); do
  echo "$id $(pct config $id | grep -E '^(hostname|onboot|startup)' | tr '\n' ' ')"
done
```

En gjest uten `onboot: 1` starter ikke av seg selv.

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

`/api/health` hjelper ikke hvis ingen ser på den.

**Statuskoden er nok som kriterium.** Endepunktet svarer 503 når `ok` er usann,
så standardinnstillingen «2xx = frisk» er riktig i enhver overvåker. Til 23.
august svarte det 200 uansett, med `"ok": false` gjemt i kroppen. En overvåker
på standardverdier ville da sagt at alt var i orden mens kartet sto tomt.

**Ett minutt er en fin frekvens.** Sjekken henter bare fra Entur når tallene er
eldre enn `TOGKART_HELSE_TTL` (fem minutter). Siden 14. september holder
bakgrunnsjobben dessuten cachen varm, så en sjekk treffer nesten alltid ferske
tall.

### Hvor sjekken står, avgjør hva den ser

| Overvåker | Ser | Ser ikke |
|---|---|---|
| **Uptime Kuma** (LXC 102) mot `https://togkartet.no/api/health` | appen, bakgrunnsjobben, tunnelen, Cloudflare, DNS | at **verten eller hjemmelinja** er nede — da er Uptime Kuma borte selv |
| **Utenfor huset** mot samme adresse | alt over, **og** at huset er borte | — |

Det finnes ikke noe tredje alternativ der Uptime Kuma sjekker appen «innenfra».
Uvicorn binder `127.0.0.1` inne i LXC 106, så `http://<106>:8000` kan ikke nås
fra en annen container. Det er med vilje (punkt 2). Den offentlige adressen er
eneste vei, og den går ut på internett og tilbake gjennom tunnelen — altså hele
kjeden.

### Uptime Kuma

| Felt | Verdi |
|---|---|
| Monitor Type | **HTTP(s)** |
| URL | `https://togkartet.no/api/health` |
| Heartbeat Interval | 60 |
| Accepted Status Codes | `200-299` |

**Uten en varsling er det bare et dashbord ingen ser på.** Legg inn en under
Settings → Notifications (e-post, Telegram, ntfy) og koble den til overvåkeren.

### Utenfor huset

Uptime Kuma kan ikke melde at strømmen er borte i huset den selv står i. Det
krever en sjekk et annet sted. Cloudflare har **Health Checks** (Traffic →
Health Checks), pekt mot samme adresse. **Det er ikke verifisert om den er
tilgjengelig på Free-planen.** Er den ikke det, gjør en gratis ekstern
uptime-tjeneste samme jobb.

### Se den bli rød

En sjekk som aldri har feilet, vet du ingenting om:

```bash
systemctl stop togkart      # i LXC 106
# vent til overvåkeren er rød og varselet har kommet
systemctl start togkart
```

`prober/sjekk_helse.py` vokter endepunktet og feltene i det uten nett og uten
database, og står i CI.
