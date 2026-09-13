# Struping og sikkerhetsheadere i Cloudflare

Skrevet 21. august 2026, da det ble klart at dokumentasjonen beskrev feil topologi.

Den sa «legg ratebegrensningen i reverse-proxyen — `limit_req` i nginx
eller `rate_limit` i Caddy». Det er riktig råd for en app bak en proxy du
eier. Denne appen skal stå der stromkart.no står, og der finnes ingen slik
proxy:

```
nettleser  ->  Cloudflare (kant)  ->  Cloudflare Tunnel  ->  cloudflared  ->  uvicorn
                                                             (i LXC-en)      127.0.0.1:8000
```

`cloudflared` er en utgående tunnel, ikke en reverse proxy. Den har ingen
`limit_req`, ingen headerkonfigurasjon og ingen cache. Alt som skulle stått i
nginx må derfor enten stå **foran** — i Cloudflare — eller **i appen**.

Det er ikke et tap. Struping i Cloudflare skjer på kanten, før trafikken har
kostet deg båndbredde inn i huset, og den ser den ekte IP-adressen uten at du
må tro på en header.

---

## Sonen er på plass

Flyttet **13. september 2026**. Domenet er `togkartet.no` — ikke `togkart.no`,
som dette dokumentet sa i tre uker — og registraren er **Uniweb**
(GROUP.ONE NORWAY AS), ikke Domeneshop.

| Domene | Navnetjenere | I Cloudflare |
|---|---|---|
| `stromkart.no` | `ian.ns.cloudflare.com`, `kinsley.ns.cloudflare.com` | ja |
| `togkartet.no` | `ian.ns.cloudflare.com`, `kinsley.ns.cloudflare.com` | ja |

Begge fikk samme navnetjenerpar, siden de ligger på samme konto.

Rekkefølgen som ble fulgt, for den som skal gjøre det igjen med et nytt domene:

1. Cloudflare → Add a site → **Connect a domain** (ikke Transfer — den flytter
   selve registreringen, og det trengs ikke).
2. Slett A-postene Cloudflare importerer fra den gamle sonen. De pekte på
   registrarens parkeringsside, og tunnelen lager sin egen CNAME i steg 5.
3. **Slå av DNSSec hos registraren først.** Uniweb hadde satt i gang en
   aktivering samme dag. Hadde DS-posten rukket ut i `.no`-sonen før
   navnetjenerne ble byttet, ville domenet blitt *utilgjengelig* — Cloudflare
   svarer usignert, og en validerende resolver nekter da å levere svaret i det
   hele tatt. Det retter seg ikke før DS-posten er ute igjen.
4. Bytt navnetjenerne hos registraren. Hos Uniweb ligger det under
   **Navnetjener**, ikke under DNS-administrasjon.
5. Tunnelen — `tunnel-og-tjeneste.md`.

**To klokker, og de er ikke den samme.** Norid tar imot endringen i
registerdatabasen med én gang — den var synlig i oppslaget på `norid.no`
innen minutter — men publiserer den i `.no`-sonen periodisk. Kommandoen som
skiller de to:

```powershell
nslookup -norecurse -type=NS togkartet.no charm.norid.no
```

Den spør registeret direkte og går utenom alle mellomlagre. Google svarte med
den gamle delegeringen i timevis etter at endringen var et faktum.

## Det du må gjøre selv

Tre ting i Cloudflare-dashbordet, og ett i appen. Rekkefølgen spiller ingen
rolle. Regel 3 er den viktigste.

Regel 1 sto her til 23. august og er strøket fra lista: `app.py` sender
sikkerhetsheaderne selv nå. Avsnittet står igjen fordi det forklarer hvorfor,
og fordi CSP-en der er referansen selvtesten måler koden mot.

### 0. Tunnelen, og hvorfor `TOGKART_BAK_CLOUDFLARE` må settes

`cloudflared` kobler seg til uvicorn fra maskinens egen loopback. Sett fra
appen kommer derfor **alle** forespørsler fra `127.0.0.1`. Uten noe mer ville
bakstopperen i `strupe.py` telt hele internett i én bøtte, og strupet alle
besøkende samtidig første gang noen hamret.

Cloudflare legger den ekte adressen i `CF-Connecting-IP`. Appen leser den
headeren bare når `TOGKART_BAK_CLOUDFLARE=1` **og** forespørselen kom fra
loopback — begge deler, ellers kunne hvem som helst som når porten direkte
sendt en ny adresse per forespørsel og aldri møtt en grense.

```bash
# .env i produksjon
TOGKART_MILJO=prod
TOGKART_BAK_CLOUDFLARE=1
```

Sett du bare den ene av dem, virker appen — men den ene halvparten av vernet
du tror du har, finnes ikke. Sjekk med:

```bash
curl -s https://togkartet.no/api/health | python -m json.tool
# "strupe": { "aktiv": true, "bakCloudflare": true, ... }
```

### 1. Sikkerhetsheadere — ~~gjør~~ **appen sender dem selv fra 23. august**

De fem headerne under settes av `app.py`, som middleware ytterst i stakken, og
går gjennom tunnelen som alle andre svar. **Du trenger ikke gjøre noe her.**

Begrunnelsen står i `../docs/sikkerhet.md` punkt 3, og den korte versjonen er
at dette punktet ventet på en DNS-migrering: en Transform Rule trenger en sone
å ligge i, og `togkartet.no` står fortsatt hos registraren. En policy som venter
er ingen policy.

**Ikke sett dem opp her i tillegg.** «Set static» overskriver det origin
sender, og da har du to steder som beskriver samme policy — og bare det ene
blir testet. `prober/sjekk_headere.py` sammenligner `app.py` med tabellen
under og roper hvis de spriker; et dashbord kan den ikke lese.

Det ene argumentet for likevel å gjøre det: **Cloudflares egne feilsider går
ikke gjennom origin.** Er tunnelen nede, svarer Cloudflare med en 502 den har
laget selv, og den bærer ingen headere fra `app.py`. Det er en statisk
feilside uten noe av appen i seg, så risikoen er liten — men vil du dekke den
også, er dette oppskriften, og da må verdiene holdes ordrett like:

**Rules → Transform Rules → Modify Response Header → Create rule.**
Navn: `Sikkerhetsheadere`. Uttrykk: `true` (alle forespørsler).

| Handling | Header | Verdi |
|---|---|---|
| Set static | `X-Content-Type-Options` | `nosniff` |
| Set static | `Referrer-Policy` | `strict-origin-when-cross-origin` |
| Set static | `X-Frame-Options` | `DENY` |
| Set static | `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` |
| Set static | `Content-Security-Policy` | se under |

CSP-en, på én linje — **dette er referansen `prober/sjekk_headere.py` måler
`app.py` mot**, så en endring her uten en endring der får CI til å feile:

```
default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob: https://*.cartocdn.com; connect-src 'self' https://basemaps.cartocdn.com https://*.cartocdn.com; worker-src blob:; child-src blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'none'
```

**Jokertegnet i `*.cartocdn.com` er ikke latskap, og det er den ene linja
her som ble målt og ikke lest.** Første utkast listet de to vertene som står
i kildekoden: `basemaps.cartocdn.com` (som `app.js` henter stilarket fra) og
`tiles.basemaps.cartocdn.com` (som stilarket peker på for skrifter og
ikoner). Begge lastet fint. Flisene gjorde det ikke — for stilarket peker
videre på en `tiles.json`, og den oppgir fire helt andre verter:

```
https://tiles-a.basemaps.cartocdn.com/vectortiles/carto.streets/v1/{z}/{x}/{y}.mvt
https://tiles-b...   https://tiles-c...   https://tiles-d...
```

Det er tre ledd ned i en kjede som starter i én URL i `app.js`. En CSP
skrevet ut fra `index.html` alene blir derfor riktig for alt unntatt det
kartet faktisk tegner med. `https://*.cartocdn.com` dekker alle seks vertene,
og `https://basemaps.cartocdn.com` må stå ved siden av fordi et
CSP-jokertegn ikke matcher domenet uten prefiks.

Lakmustesten om du bytter bakgrunnskart: følg kjeden helt ut — `style.json`
→ `tiles.json` → flis-URL-ene — og se hva som faktisk går over nettet, i
stedet for hva koden ser ut til å be om.

Retestet 23. august, etter at MapLibre flyttet inn i `static/vendor/` og
`https://unpkg.com` ble strøket fra `script-src` og `style-src`. Policyen ble
pålagt av en lokal proxy foran uvicorn, og siden lyttet på
`securitypolicyviolation`: **null brudd**. Et kart bygget på det vendede
bundtet lastet ferdig og tegnet 125 flistrukne objekter — så blob-workeren og
flisrørledningen tåler `script-src 'self'` — og `/api/trains`, `/api/avvik` og
`/api/statistikk/operatorer` svarte alle 200 under `connect-src 'self'`.

Testet 21. august med den forrige policyen pålagt lokalt, mot samme side uten
den:
bakgrunnskartet tegner (kystlinjer, landegrenser, stedsnavn), alle
CARTO-kallene svarer 200, og konsollen gir nøyaktig de samme meldingene —
16 baner, 335 stasjoner, ingen `Refused to load`. Policyen strammer uten å
ta noe med seg.

Én ting å vite når du tester selv: **flisene dukker ikke opp i
nettverksfanen.** MapLibre henter dem i en web worker, og verktøy som lytter
på hovedtråden ser dem ikke. Bruk øynene — tegner kartet land og hav, kom
flisene fram.

Fire forbehold, så ingen av dem kommer som en overraskelse:

- **~~`'unsafe-inline'` for stil.~~ Borte 23. august.** Policyen hadde den
  fordi `app.js` bygget sju `style="..."`-attributter for datafarger. De er
  klasser nå — `.f-c-low` og familien i `app.css` — og både `script-src` og
  `style-src` står på `'self'` uten unntak. `prober/sjekk_headere.py`
  håndhever begge, så det er ikke noe man kan skli tilbake til uten at CI
  sier fra.
- **`script-src 'self'` uten unntak — og det er nytt.** Til 23. august sto
  `https://unpkg.com` i både `script-src` og `style-src`, fordi MapLibre ble
  hentet derfra. Filene ligger nå i `static/vendor/` og serveres av oss, så
  begge er strøket. Legger du inn et bibliotek fra et CDN igjen, må verten inn
  her — og da er `../docs/sikkerhet.md` punkt 4 verdt å lese på nytt før du
  gjør det.
- **`worker-src blob:` og `child-src blob:`** trenger MapLibre: den tegner
  vektorfliser i web workers lastet fra en blob-URL. `child-src` står der
  som reserve for eldre nettlesere som ikke kjenner `worker-src`.
- **`img-src` må matche karttjeneren din.** Verdiene over er for CARTO sine
  mørke fliser. Bruker `index.html` en annen `style`-URL, må verten inn her
  og i `connect-src`, ellers blir kartet svart.

Verifiser etterpå — CSP-feil er stille i alt annet enn konsollen:

```bash
curl -sI https://togkartet.no/ | grep -i "content-security\|x-content-type\|referrer"
```

Og åpne kartet med utviklerkonsollen oppe. Står det `Refused to load` der,
mangler en kilde i policyen.

### 2. Cachereglene — to av dem

**Caching → Cache Rules.** Begge satt opp 13. september og målt utenfra.

**`API utenom cache`**

```
Uttrykk:   starts_with(http.request.uri.path, "/api/")
Handling:  Bypass cache
```

Uten den kan Cloudflare finne på å servere `/api/trains` fra kanten, og da
viser kartet tog som var der for fem minutter siden — med riktig format,
riktig antall og riktige farger. Ingenting ser galt ut. Det er den verste
sorten feil.

Merk at `/api/` svarer `cf-cache-status: DYNAMIC` både med og uten regelen:
stiene har ingen filendelse Cloudflare cacher som standard, så utfallet var
riktig fra før. Regelen er et vern mot at standardoppførselen endrer seg
uten at noen sier fra, ikke en retting av noe som var galt.

**`GeoJSON caches lenge`**

```
Uttrykk:     ends_with(http.request.uri.path, ".geojson")
Handling:    Eligible for cache
Edge TTL:    1 måned  (ignorer cache-control fra origin)
Browser TTL: 1 dag
```

**Denne var ikke pynt.** Målt før den ble lagt inn:

```
app.js                 121 kB   HIT       Cloudflare betaler
maplibre-gl.js         803 kB   HIT
app.css + theme.css     32 kB   HIT
stasjoner.geojson       82 kB   DYNAMIC   DU betaler
hovedbaner.geojson     185 kB   DYNAMIC
jernbanenett.geojson   856 kB   DYNAMIC
```

Cloudflare cacher etter filendelse som standard, og `.geojson` står ikke på
lista. Hver eneste sidelasting hentet altså **1,12 MB GeoJSON fra
M720Q-en**, opp hjemmelinja, mens de 954 kB med JavaScript ble servert fra
Oslo. Etter regelen: 67 kB fra opphavet per førstegangsbesøk i stedet for
1 196 kB. **Attenkeren mindre.**

Én måned i edge-TTL er trygt fordi filene bare endres av byggeskriptene, og
de kjører ikke i produksjon. Oppdaterer du banegeometrien, må du tømme
cachen manuelt (**Caching → Configuration → Purge Everything**).

### 3. Ratebegrensning — én regel, ikke tre

**Security → Security rules → Rate limiting rules.**

Dette punktet beskrev fram til 13. september tre regler — 3a for søket,
3b for statistikken, 3c for resten — hver med et ett-minutts vindu. **Ingen
av delene kan gjøres på Free-planen.** Dashbordet sier `0/1 rules`, og
`Period` har bare ett valg: 10 sekunder. Ett minutt krever Pro.

Det som står ute:

```
Navn:              API-struping
Uttrykk:           starts_with(http.request.uri.path, "/api/")
Characteristics:   IP
Rate:              30 requests per 10 seconds
Handling:          Block
Duration:          10 seconds
```

**30 og ikke 20, som delingen ville gitt.** 120 per minutt tåler at noen
bruker 40 forespørsler på tre sekunder og så er stille; 20 per 10 sekunder
gjør det ikke. Og ekte bruk er klumpete: en sidelasting fyrer av fire
API-kall med én gang, søket koster noen til, og to faner dobler alt. Et kort
vindu er strengere enn tallet ser ut til, så terskelen må opp for å
kompensere.

Prinsippet, som er verdt mer enn tallet: **still en ratebegrensning slik at
den aldri treffer en ekte bruker.** Det den skal stoppe gjør titalls kall i
sekundet, ikke to — forskjellen mellom 20 og 30 betyr ingenting for den, og
alt for deg med tre faner åpne. En grense som blokkerer én av tjue besøkende
blir slått av etter en uke, og da har du ingen.

**Verifisert utenfra**, 60 kall med 10 samtidige:

```
32 x 200, så 429 for resten

HTTP/1.1 429 Too Many Requests
Server: cloudflare
CF-RAY: a3aa52723895712a-OSL
Content-Type: text/plain
Retry-After: 9
```

`CF-RAY` slutter på `OSL`: stoppet i Oslo, aldri ned opplinja. Det
avgjørende er hva som **mangler** — ingen `content-security-policy`. Hadde
`strupe.py` svart, ville headeren vært der, satt av middleware etter at
forespørselen nådde uvicorn. Den er borte, altså nådde den aldri M720Q-en.
`avviste: 0` i `/api/health` bekrefter det samme fra andre siden.

Slik skiller du de to 429-ene fra hverandre når du feilsøker senere.

### 4. Det Cloudflare ikke kan gjøre for deg

Dette er hele grunnen til at `strupe.py` finnes, og det er verdt å lese en
gang til hvis reglene over føles som at jobben er gjort:

**Kvoten hos Entur henger på `ET_CLIENT_NAME`, ikke på den besøkendes IP.**

Regel 3a sier «du får 20 søk i minuttet». Hundre IP-er som hver holder seg
pent innenfor gir 2000 søk i minuttet ut av `/api/search` — alle signert med
ditt klientnavn, alle helt innenfor regelen, ingen av dem blokkert av
Cloudflare. Da er det du som blir ratebegrenset av Entur, og søket slutter å
virke for alle.

En per-IP-grense kan ikke uttrykke «til sammen». Derfor ligger det en bøtte
i `strupe.py` som ikke teller besøkende i det hele tatt, bare utgående kall:
`ENTUR_GEOKODER`, 60 i minuttet for hele appen. Går den tom, svarer
`/api/search` 503 og Entur får være i fred.

Den ser du i helsesjekken:

```bash
curl -s https://togkartet.no/api/health | python -m json.tool
```

```json
"strupe": {
  "aktiv": true,
  "bakCloudflare": true,
  "botter": 43,
  "avviste": 0,
  "enturKvote": { "perMinutt": 60, "igjen": 30.0, "kapasitet": 30.0 }
}
```

`enturKvote.igjen` godt under `kapasitet` over tid betyr at noe hamrer på
søket, eller at søkecachen ikke treffer. `avviste` som vokser jevnt betyr at
bakstopperen gjør jobb Cloudflare skulle tatt — sjekk at reglene i punkt 3
faktisk er aktive.

---

### 5. TLS-innstillingene

**SSL/TLS → Overview → Configure.** Satt 13. september.

| Innstilling | Verdi |
|---|---|
| Encryption mode | **Full (Strict)** |
| Always Use HTTPS | **På** |
| Minimum TLS Version | 1.2 |
| HSTS | **av inntil videre** |

Nye soner står på **Automatic SSL/TLS**, som lar Cloudflare velge selv — den
sto på `Full` da den ble sjekket. Full (Strict) krever normalt et gyldig
sertifikat på opphavsserveren, men med tunnel finnes det ingen TCP-forbindelse
til et origin i det hele tatt: `cloudflared` bærer trafikken over sin egen
autentiserte QUIC-forbindelse. Innstillingen kan altså ikke bryte noe, og den
er riktig posisjon hvis oppsettet en gang endres.

**Always Use HTTPS var den som manglet.** Målt før: `http://togkartet.no`
svarte 200 uten omdirigering. Målt etter: 301 til `https://`, ett hopp.
Advarselen om omdirigeringssløyfer gjelder ikke her — `app.py` har ingen egen
HTTPS-omdirigering.

**HSTS står igjen med vilje.** Den instruerer nettleseren om å nekte ren HTTP
mot domenet i hele max-age-perioden, og den kan ikke rulles tilbake ved å slås
av: nettleserne husker uansett. Slå den på når oppsettet har stått et døgn, med
seks måneder og **uten** preload.

## Testing

Fra en maskin **utenfor** huset (ellers går trafikken ikke gjennom
Cloudflare, og du tester ingenting):

```bash
# Skal gi noen 200 og deretter 429 fra Cloudflare
for i in $(seq 1 40); do
  curl -s -o /dev/null -w "%{http_code} " "https://togkartet.no/api/search?q=test$i"
done; echo
```

Blir alle 200, er regelen ikke aktiv — se etter om den står i «Log»-modus i
stedet for «Block».

Lokalt, uten Cloudflare, tester du bakstopperen med samme løkke mot
`http://127.0.0.1:8000`. Da er det `strupe.py` som svarer 429, ikke kanten.

---

## Oppsummert

| Vern | Hvor | Fil / sted |
|---|---|---|
| Per-IP-struping | Cloudflare WAF | punkt 3 over |
| Per-IP-struping, bakstopper | appen | `strupe.py`, `GRENSER` |
| **Tak på utgående Entur-kall** | **appen — kan ikke løses foran** | `strupe.py`, `ENTUR_GEOKODER` |
| Sikkerhetsheadere og CSP | Cloudflare Transform Rules | punkt 1 over |
| API utenom cache | Cloudflare Caching Rules | punkt 2 over |
| TLS og sertifikat | Cloudflare | automatisk |
| Skjule opphavs-IP | Cloudflare Tunnel | automatisk — ingen port åpnes |

Legg merke til den siste raden. Med tunnel er det ingen portvideresending og
ingen åpen port mot internett i det hele tatt, og punkt 6 i «Hva som skal til»
i den gamle utrullingslista — DNS, portåpning, DDNS — faller bort. `cloudflared` ringer ut.
Det er en vesentlig bedre sikkerhetsposisjon enn nginx på en åpen 443, og den
kom du til gratis ved å gjenbruke stromkart-oppsettet.
