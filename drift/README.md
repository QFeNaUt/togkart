# Drift

Alt som handler om at kartet står i morgen, ikke om hva det viser.

| Fil | Innhold |
|---|---|
| [cloudflare.md](cloudflare.md) | Struping, sikkerhetsheadere og CSP som skal settes opp i dashbordet |
| [tunnel-og-tjeneste.md](tunnel-og-tjeneste.md) | Cloudflare Tunnel, systemd-unit, `.env` i prod, backup av databasen |
| [docker.md](docker.md) | Samme oppsett i container: `Dockerfile`, `compose.yaml`, cloudflared i samme nettverksnavnerom |
| `env.produksjon` | Mal for `.env` på serveren |
| `oppdater.sh` | Utrulling: hent, installer om nødvendig, **legg ut `togkart.service` hvis den er endret**, start om, verifiser |
| `togkart.service` | systemd-unit'en. Kopieres til `/etc/systemd/system/`, skrives ikke av |

Sikkerhetsgjennomgangen står i [../docs/sikkerhet.md](../docs/sikkerhet.md).

---

## Kan den kjøre på M720Q-en?

Ja, men marginen er mindre enn denne seksjonen påsto fram til 21.
september 2026. Målt på appen som den står:

| | Målt |
|---|---|
| Minne, uvicorn-prosessen | **~630 MB** i platå, **1 122 MB på topp** under oppvarming. Målt 21. september 2026, etter at traseene ble lagt flatt; før den endringen samme dag var platået ~890 MB. Her sto det **72 MB** fram til da, og det tallet er grunnen til at LXC-en ble gitt 1 GB. Se «Minnet er det som binder» under |
| CPU i ro | tilnærmet null — én Entur-henting per 60 s fra bakgrunnsjobben, oftere bare hvis noen ser på |
| Disk, statiske filer | 342 kB til sammen (`hovedbaner.geojson` er den store med 181 kB) |
| Disk, historikk | **Målt over 9 døgn, 13.–21. september 2026: 78 731 rader i døgnet, 178,6 MB til sammen, 1 786 MB i likevekt med 90 dagers rotasjon.** Anslagene som sto her — 37 MB i døgnet og 3,3 GB — var for høye. `vedlikehold.py --status` skriver ut alle tre tallene selv |
| Utgående nett | konstant, uavhengig av antall besøkende — cachene deler ett kall mellom alle faner |

Den siste raden er den viktigste for driftsbildet: **belastningen mot Entur
vokser ikke med trafikken.** Ti besøkende og ti tusen gir samme antall kall ut.
Det som vokser med trafikken er statiske filer og JSON ut av din egen server, og
det er en oppgave en mini-PC ikke merker.

En M720Q — typisk i5-8500T med 8–32 GB — er kraftig overdimensjonert for dette.
Du kjører allerede stromkart.no på samme maskin, så mønsteret er bevist.

## Minnet er det som binder

Skrevet 21. september 2026, etter at togkartet.no lå nede med **Cloudflare
Error 1033** og tallene over var årsaken.

**Appen bruker ~900 MB, ikke 38 MB.** De 72 og 38 MB-ene i tabellene over ble
målt i august, før sporgeometrien og før bakgrunnsjobben. `Sportrase` i
`sporgeometri.py` holder `punkter: list[Punkt]` og `kumulativ: list[float]` —
to parallelle lister av Python-objekter per trasé — og `_FORBEREDT` i
`sjnord.py` holder én trasé per tur uten målt posisjon. Det fyller seg opp over
de første ti minuttene etter oppstart og flater ut rundt 900 MB. Det er ikke en
lekkasje: RSS synker sakte etterpå, og alle mellomlagrene i appen er avgrenset.

**Containeren hadde 1024 MB.** Appen lå dermed på rundt 90 % av taket fra
13. september, og arbeidssettet svinger med hvor mange tog som trenger
sporgeometri gjennom døgnet. 20.–21. september fikk det ikke plass:

```
oom_kill 2                  uvicorn drept, Restart=always startet den igjen
high 83178210               83 millioner reclaim-hendelser - 86 % CPU på å lete
                            etter minne som ikke fantes
```

**Og det var ikke appen som tok ned nettstedet.** `Restart=always` gjorde
jobben sin hver gang. Det som falt var `cloudflared`, som kjører i samme
container og ble sultet så hardt at QUIC-handshakene timet ut. Cloudflare mistet
alle fire forbindelsene og svarte 1033 i timevis, mens appen selv var oppe.

Tre ting fulgte av det:

1. **2 GB, ikke 1.** `pct set 106 -memory 2048`, og det står i
   containerkonfigurasjonen så det overlever omstart. Verten har 7,6 GB totalt
   og ~2,4 GB ledig — **ikke hev videre uten å se på `free -h` først.**
2. **`MemoryHigh` og `MemoryMax` i `togkart.service`.** Et tak på tjenesten
   gjør at en app som løper løpsk dør alene i stedet for å dra hele containeren
   med seg. Da overlever tunnelen, og du får et blunk i stedet for timer.
3. **Mål, ikke gjett.** To hypoteser ble forkastet på måling underveis:
   vedlikeholdsjobben topper på **61 MB**, ikke hundrevis, og ingen av
   mellomlagrene i appen vokser fritt. Kommandoene som avgjorde det står i
   `../docs/feilsoking.md` under «Siden er nede og `pct exec` henger».

### Traseene ligger flatt nå

Gjort 21. september 2026, samme dag. `Sportrase` lagret `punkter` som en
`list[tuple[float, float]]` og `kumulativ` som en `list[float]`. Begge er
`array("d")` nå, med koordinatene flatt etter hverandre.

Målt på en trasé med 20 000 punkter, altså en Nordlandsbane:

| | Per punkt | Én trasé | 200 traseer |
|---|---|---|---|
| Før | 153 byte | 2,91 MB | 582 MB |
| Nå | **25 byte** | **0,47 MB** | **95 MB** |

**Faktoren er 6,1, ikke en tierpotens.** Tierpotensen sto her da seksjonen ble
skrevet, og var et anslag ingen hadde regnet på.

Grunnen til at en liste er så dyr: den lagrer ikke to tall per punkt. Den
lagrer én listeslot (8 B), ett tuple-objekt (56 B) og to selvstendige
`float`-objekter (24 B hver) — 112 byte der dataen er 16. En `array("d")`
lagrer rå doubler rett etter hverandre.

To ting det var verdt å passe på:

- **`projiser()` leser rått ut av `xy`.** Løkka går gjennom titusenvis av
  segmenter per stopp. Gikk den veien om et `punkt(i)`-oppslag, ville den
  bygget to tupler per segment og gitt tilbake i allokering det den flate
  lagringen sparer i minne. Derfor `_projiser_pa_koordinater`, som tar rå tall.
- **Det finnes ingen `.punkter` lenger, med vilje.** En slik property ville
  bygget hele lista med tupler på nytt ved hvert oppslag — altså gitt tilbake
  hele besparelsen, og gjort det stille.

`bisect` virker like godt på en `array` som på en liste, så binærsøket i
`_segment_for` er urørt. Ingenting utenfor `sporgeometri.py` rørte feltene.
Selvtesten gir samme geometri som før: F6 Hamar–Oslo lufthavn er fortsatt
72,7 km langs spor mot 66,8 km i luftlinje, med 11,9 km største avvik.

#### Hva det ga på hele appen

Målt på serveren rett etter utrulling, samme prosess fulgt i tolv minutter:

| | Platå | Topp under oppvarming |
|---|---|---|
| Før | ~890 MB | — |
| Etter | **~630 MB** | **1 122 MB** |

**260 MB spart, rundt 29 %.** Regnet baklengs fra faktoren 6,1 var traseene
altså rundt 310 MB av de gamle 890, og er nå ~51 MB. De resterende ~580 MB er
noe annet — `timeline` og `offsets` i `_FORBEREDT`, snapshot-cachen,
avvikscachen og uvicorn selv. **Det tallet er utledet, ikke målt**, og er verdt
å måle før noen bygger på det.

**Toppen er nå høyere enn platået, og det er verdt å vite.** `fra_punkter` tar
imot `list[Punkt]` fra `decode_polyline` og bygger arrayet ut av den, så mens
en trasé konstrueres finnes begge representasjonene samtidig. Med 60 nye
traseer per runde under oppvarming gir det **1 122 MB**, avlest som
høyvannsmerke i `memory.peak` og ikke stukket ut av en prøve.

Marginen er 278 MB opp til `MemoryHigh=1400M` og 478 MB opp til
`MemoryMax=1600M`, og `memory.events` står på `high 0` — strupingen har aldri
slått inn. Det er godt nok, og feilmodusen er dessuten mild: `MemoryHigh`
bremser, den dreper ikke. Men marginen spises av en travlere dag med flere
turer uten GPS, så den er verdt å lese av igjen når noe endrer seg:

```bash
cat /sys/fs/cgroup/system.slice/togkart.service/memory.peak    # høyvannsmerket
cat /sys/fs/cgroup/system.slice/togkart.service/memory.events  # `high` over 0 = strupet
```

Blir den for tynn, er det to veier: la `decode_polyline` skrive rett inn i et
`array("d")` så mellomlista aldri finnes, eller heve taket i
`togkart.service`. Den første er den riktige; den andre er den raske.

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

**Og bak Eidsivas CGNAT fantes det ikke noe alternativ.** Hjemmelinja har
ingen offentlig IPv4-adresse, så portvideresending er ikke mulig i det hele
tatt. Se «Nettverket hjemme» under.

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
| Adresse | `192.168.2.56`, fast i Proxmox — se «Nettverket hjemme» |
| Ressurser | 2 kjerner, **2 GB RAM**, 16 GB på `local-lvm` — appen bruker **~630 MB** i platå og 1 122 MB på topp. Sto på 1 GB og «38 MB» til 21. september; se «Minnet er det som binder» |
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

## Nettverket hjemme

Avlest i Eidsiva Digitals kundeportal og målt fra hjemmenettet 14. september
2026.

### Bak Carrier-Grade NAT

```
WAN IPv4-adresse   100.65.192.212
```

Adresser i `100.64.0.0/10` er ikke offentlige. Eidsiva deler én offentlig
IPv4-adresse mellom mange kunder, og kundeportalen sier det selv:
portvideresending, DMZ og UPnP er utilgjengelig for kunder bak CGNAT.

**Ingen utenfra kan nå noe i huset over IPv4, uansett hva som åpnes.** Lenger
opp kalles Cloudflare Tunnel en bedre sikkerhetsposisjon enn nginx på en åpen
443. Det stemmer, men valget fantes aldri: en tunnel som ringer ut var den
eneste måten togkartet.no og stromkart.no kunne komme på nett. Det samme gjelder
Tailscale.

Står noen senere og lurer på hvorfor en portvideresending ikke virker: det er
ikke oppsettet som er feil.

### DHCP

| | |
|---|---|
| Område | `192.168.2.100` – `192.168.2.249` |
| Leasetid | 12 timer |
| Gateway og DHCP-server | `192.168.2.1` |

Ruteren er en Zyxel fra Eidsiva. Den svarer ikke med noe nettpanel fra
hjemmenettet — testet over Wi-Fi på port 80, 443, 8080, 8443 og 8000 — og
innstillingene over står i kundeportalen.

### Adresseplan

Faste adresser under `.100`, alt fra DHCP over.

| Adresse | Maskin | |
|---|---|---|
| `.1` | ruteren | |
| `.50` | Proxmox-verten (`server`) | fast |
| `.51` | adguard (101) | fast i Proxmox |
| `.52` | homepage (104) | fast i Proxmox |
| `.55` | stromkart (105) | fast i Proxmox |
| `.56` | togkart (106) | fast i Proxmox, fra 14. september |
| `.57` | uptimekuma (102) | fast i Proxmox, fra 14. september |
| `.58` | skipssporer (200) | fast i Proxmox, fra 14. september |

103 (`debian`) og VM 100 (Home Assistant) står på DHCP.

### Hvorfor fast adresse i Proxmox, og ikke reservasjon i ruteren

Uptime Kuma (102) mistet IPv4-adressen sin rundt 1. juli og var blind i 75
dager uten at noen merket det. Alle overvåkerne feilet med `Network is
unreachable`, og den kunne heller ikke sende varsel. En omstart ga den **samme
adresse** tilbake.

Feilen var altså ikke hvilken adresse ruteren delte ut, men at DHCP-klienten i
containeren sluttet å fornye leasen. En reservasjon i ruteren hadde ikke
hjulpet. Med 12 timers leasetid er en fornyelse som feiler i et halvt døgn nok
til at adressen forsvinner.

En fast adresse i Proxmox fjerner avhengigheten helt. Bytt `ip=dhcp` i `net0`,
legg til `gw`, og behold `hwaddr` — ellers får containeren ny MAC-adresse:

```bash
pct set 106 -net0 name=eth0,bridge=vmbr0,firewall=1,gw=192.168.2.1,hwaddr=BC:24:11:63:9D:B1,ip=192.168.2.56/24,type=veth
pct reboot 106
pct exec 106 -- ip -4 -brief addr show eth0
```

**Omstarten er ikke valgfri.** `pct set` på en kjørende container legger den
faste adressen til med én gang, men DHCP-klienten som startet ved oppstart
kjører videre og holder på den gamle leasen. Målt 14. september: alle tre
containerne viste to adresser, for eksempel `192.168.2.147/24 192.168.2.56/24`,
helt til de ble startet på nytt. Så lenge begge står der, er containeren
fortsatt avhengig av DHCP. Etter omstart sto bare den faste igjen — og da vet
du samtidig at den overlever neste strømbrudd.

For togkart påvirker selve adressebyttet ikke nettstedet: tunnelen ringer ut,
og Cloudflare-ruten peker på `127.0.0.1`. Omstarten tar det ned i noen
sekunder. DNS i containeren påvirkes heller ikke;
Proxmox styrer den fra vertens innstillinger.

Hvilke gjester som har hvilket nettverksoppsett:

```bash
for id in $(pct list | awk 'NR>1{print $1}'); do
  echo "$id $(pct config $id | grep -E '^(hostname|net0)' | tr '\n' ' ')"
done
```

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
