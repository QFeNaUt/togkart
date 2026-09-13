# Drift

Alt som handler om at kartet står i morgen, ikke om hva det viser.

| Fil | Innhold |
|---|---|
| [cloudflare.md](cloudflare.md) | Struping, sikkerhetsheadere og CSP som skal settes opp i dashbordet |
| [tunnel-og-tjeneste.md](tunnel-og-tjeneste.md) | Cloudflare Tunnel, systemd-unit, `.env` i prod, backup av databasen |
| [docker.md](docker.md) | Samme oppsett i container: `Dockerfile`, `compose.yaml`, cloudflared i samme nettverksnavnerom |
| `env.produksjon` | Mal for `.env` på serveren |
| `oppdater.sh` | Utrulling: hent, installer om nødvendig, start om, verifiser |
| `togkart.service` | systemd-unit'en. Kopieres til `/etc/systemd/system/`, skrives ikke av |

Sikkerhetsgjennomgangen står i [../docs/sikkerhet.md](../docs/sikkerhet.md).

---

## Kan den kjøre på M720Q-en?

Ja, og med svært god margin. Målt på appen som den står:

| | Målt |
|---|---|
| Minne, uvicorn-prosessen | **72 MB** arbeidssett, 59 MB privat |
| CPU i ro | tilnærmet null — én Entur-henting per 10 s, én SJ-henting per 60 s |
| Disk, statiske filer | 342 kB til sammen (`hovedbaner.geojson` er den store med 181 kB) |
| Disk, historikk | 5,0 MB i døgnet — **443 MB i likevekt** etter at rotasjonen kom på 21. august. Var 1,8 GB i året uten tak |
| Utgående nett | konstant, uavhengig av antall besøkende — cachene deler ett kall mellom alle faner |

Den siste raden er den viktigste for driftsbildet: **belastningen mot Entur
vokser ikke med trafikken.** Ti besøkende og ti tusen gir samme antall kall ut.
Det som vokser med trafikken er statiske filer og JSON ut av din egen server, og
det er en oppgave en mini-PC ikke merker.

En M720Q — typisk i5-8500T med 8–32 GB — er kraftig overdimensjonert for dette.
Du kjører allerede stromkart.no på samme maskin, så mønsteret er bevist.

## Hva som skal til

Omskrevet 21. august. Listen under sa «reverse proxy med TLS» og «DNS og
portåpning», og beskrev dermed et oppsett prosjektet ikke skal ha. Det som
fronter stromkart.no er **Cloudflare Pages foran en Cloudflare Tunnel**, og en
tunnel er ikke en reverse proxy — den ringer ut, den tar ikke imot.

Full oppskrift ligger i `drift/tunnel-og-tjeneste.md` med ferdig `config.yml`
og systemd-unit. Kortversjonen:

1. **Container.** LXC med Debian 12, eller Docker hvis det er det stromkart
   bruker. Python 3.11 eller nyere; `analyse.py` bruker `zoneinfo`, og
   `tzdata` ligger i `requirements.txt` for de imagene som ikke har
   tidssonetabeller selv.
2. **Persistent lagring for `historikk.db`.** Et bind mount, ikke containerens
   skrivelag — ligger filen inne, forsvinner all historikk neste gang du
   bygger på nytt. Sett `HISTORIKK_DB` til stien.

   To ting som er nye siden WAL kom på: databasen er nå **tre filer**
   (`.db`, `-wal`, `-shm`), og den **skal ikke ligge på et nettverksdrev** —
   WAL trenger delt minne, og over SMB eller NFS faller SQLite stille tilbake
   til `delete`. Appen advarer i loggen hvis det skjer.
3. **Backup med `sqlite3 .backup`, ikke `cp`.** I WAL-modus ligger de nyeste
   transaksjonene i `-wal`, og en `cp` av bare hovedfilen gir deg en database
   som mangler dem. Historikken kan ikke gjenskapes.
4. **`.env`.** `ET_CLIENT_NAME` (uten den ratebegrenser Entur deg hardt),
   `TOGKART_MILJO=prod`, `HISTORIKK_DB`, og — lettest å glemme, med størst
   konsekvens — **`TOGKART_BAK_CLOUDFLARE=1`**. Se punkt 6.
5. **Bind til localhost:**
   ```
   uvicorn app:app --host 127.0.0.1 --port 8000 --no-server-header
   ```
   Da er det bare `cloudflared` som kan nå den, uansett hva som skjer med
   brannmuren. En systemd-unit med `Restart=always` holder den i live.

   **Dette gjelder uvicorn rett på verten.** I en container er det motsatt:
   der er containergrensen isolasjonen, og en loopback-binding gjør appen
   utilgjengelig også for `cloudflared`. Se [docker.md](docker.md).
6. **Cloudflare Tunnel.** `cloudflared` med `service: http://127.0.0.1:8000`.

   **Og `TOGKART_BAK_CLOUDFLARE=1` i `.env`.** Tunnelen kobler seg til fra
   loopback, så uten den kommer alle forespørsler fra `127.0.0.1` sett fra
   appen — og strupen teller hele internett i én bøtte, som strupte samtlige
   besøkende i det øyeblikket noen hamret. Med den leses `CF-Connecting-IP`,
   men bare når forespørselen faktisk kom fra loopback. Sjekk etterpå at
   `/api/health` svarer `"bakCloudflare": true`.
7. **Fire regler i Cloudflare-dashbordet.** Struping, sikkerhetsheadere, CSP
   og «ikke cache `/api/`». Alle fire ferdig formulert i
   `drift/cloudflare.md` — det er kopiering, ikke design.
8. **Arbeidsmappe.** Alt kjøres fra prosjektroten. Står containeren i feil
   mappe, feiler uvicorn med `Directory 'static' does not exist`.

**Det som IKKE trengs lenger:** ingen reverse proxy, ingen Let's Encrypt,
ingen portvideresending, ingen DDNS. `cloudflared` ringer ut, så det finnes
ingen åpen port mot internett i det hele tatt og opphavs-IP-en eksponeres
aldri. Det er en vesentlig bedre sikkerhetsposisjon enn nginx på en åpen 443,
og den kom gratis ved å gjenbruke stromkart-oppsettet.

**Satt opp 13. september 2026.** Kartet kjører på
**<https://togkartet.no>** fra **LXC 106** på Proxmox-verten, ved siden av
stromkart i 105. Domenet er registrert hos Uniweb; sonen ligger på Cloudflare
med `ian`/`kinsley` som navnetjenere.

Merk navnet: det heter `togkartet.no`. Dokumentasjonen sa `togkart.no` fram
til denne datoen, og det domenet har aldri vært vårt.

Slik den faktisk står:

| | |
|---|---|
| Vert | Proxmox, LXC **106** (`togkart`), unprivileged, Debian 13 |
| Ressurser | 2 kjerner, 1 GB RAM, 16 GB på `local-lvm` — appen bruker **38 MB** |
| Python | 3.13 fra Debian, venv i `/opt/togkart/.venv` |
| Kode | `git clone` fra <https://github.com/QFeNaUt/togkart> |
| Tjeneste | systemd, `togkart.service`, uvicorn på `127.0.0.1:8000` |
| Tunnel | `cloudflared` som egen systemd-tjeneste i **samme** LXC |
| Database | `/var/lib/togkart/historikk.db`, WAL |

At `cloudflared` kjører i samme container som appen er ikke tilfeldig: da
kommer forespørslene til uvicorn fra loopback, og det er nettopp betingelsen
`_loopback()` i `strupe.py` krever før den stoler på `CF-Connecting-IP`. Se
punkt 6 over.

Verifisert etter utrulling: `https://togkartet.no/api/health` svarer
`"ok": true` med `"bakCloudflare": true`, alle fem sikkerhetsheaderne
overlever gjennom kanten, `/api/docs` er 404, og `http://` omdirigeres 301
til `https://`.

## Historikken vedlikeholder seg selv

`historikk.db` vokste uten tak og kjørte i SQLite sin standardmodus, der en
skriver sperrer alle lesere og en leser som møter låsen feiler umiddelbart.
Begge deler er rettet 21. august:

- **WAL** med `busy_timeout`. Målt begge veier på samme last: `delete` uten
  timeout gir «database is locked», WAL gir null feil.
- **Døgnarkiv.** Hvert ferdig døgn rulles til tabellen `dogn` — én rad per
  dato, operatør og linje, med turer, andel i rute, median og p90. **Den
  slettes aldri.** To døgn med 40 000 råobservasjoner blir 57 rader.
- **Utelatte døgn.** Et døgn med kjent forurensning kan holdes utenfor arkivet
  via `dogn_utelatt (dato, grunn, lagt_inn)`. Rullupen hopper over det for
  alltid — også forbi `OVERLAPP_DOGN`, som ellers regner de nyeste døgnene ut
  på nytt. `--status` skriver ut datoene med begrunnelsen. 19.–21. august 2026
  står der; se CHANGELOG.
- **Rotasjon.** Rådata eldre enn 90 dager slettes. 1,8 GB i året blir 443 MB
  i likevekt.
- **Sperren.** Ingenting slettes før arkivet har tatt igjen. Feiler rullupen,
  står slettingen stille i stedet for å kaste data ingen har tatt vare på.

Jobben kjører klokka fire om natta av seg selv. For hånd:

```powershell
python vedlikehold.py --status     # hva ligger der
python vedlikehold.py              # rull opp og roter
python prober/sjekk_historikk.py   # låsing, vekst, arkiv, samtidighet
```

Backup må tas med `sqlite3 .backup`, **ikke** `cp` — i WAL-modus ligger de
nyeste transaksjonene i `-wal`-filen. Uten `sqlite3` på maskinen gjør Python
det samme:

```python
import sqlite3, historikk
with historikk.kobling(skrivbar=False) as kilde:
    mottaker = sqlite3.connect("historikk-backup.db")
    with mottaker:
        kilde.backup(mottaker)
    mottaker.close()
```

Ta alltid en før du rører `dogn` — arkivet slettes aldri, så en feil der er
den ene som ikke retter seg selv.

## Det som er annerledes enn stromkart

- **Databasen.** Stromkart har ingen. Denne skriver rundt 5 MB i døgnet og
  trenger både persistens og backup — og backupen må tas med `sqlite3
  .backup`, ikke `cp`, siden den kjører i WAL. Den vedlikeholder seg selv: en
  jobb klokka fire om natta ruller gårsdagen til arkivet og sletter det som er
  eldre enn 90 dager.
- **Utgående avhengighet ved oppstart.** Kartet er tomt til første
  Entur-henting går gjennom. `/api/health` svarer `ok: false` mens det står
  slik, som er riktig for en oppstartssjekk.
- **Byggeskriptene skal ikke kjøre i produksjon.** `lagbaner.py` og
  `lagstasjoner.py` skriver filer som ligger i git. Kjør dem lokalt, sjekk inn
  resultatet, la containeren servere det.
