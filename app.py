"""TogKart - FastAPI-backend.

Kjør lokalt med:
    uvicorn app:app --reload

Backend gjør tre ting:
  1. Spør Entur (maks én gang per CACHE_TTL_SECONDS, uansett hvor mange
     nettlesere som er koblet til)
  2. Serverer resultatet som GeoJSON på /api/trains
  3. Serverer frontend-filene i static/
"""

import asyncio
import contextlib
import logging
import os
import time
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from entur import BAND_ORDER, delay_band, fetch_trains, paafor_retning
from sjnord import fetch_journeys, positions, route_line, tognokkel
from punktlighet import hent_forsinkelser
from historikk import logg_snapshot
from analyse import OSLO, operatorsammenligning, rushtidsprofil
from avvik import hent_avvik
from flaskehals import flaskehalser
from strupe import Strupe, status as strupestatus, ta_entur_kall
from vedlikehold import sjekk_taket, vedlikehold

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("togkart")

CLIENT_NAME = os.getenv("ET_CLIENT_NAME", "").strip()
CACHE_TTL = float(os.getenv("CACHE_TTL_SECONDS", "10"))

GEOCODER_URL = "https://api.entur.io/geocoder/v3/autocomplete"

# Rutedata for SJ tåler å caches mye lenger enn togposisjoner. Stasjonstider
# justeres langsomt; toget flytter seg raskt. Vi henter rutene sjelden og
# regner ut posisjonene ofte.
SJ_TTL = float(os.getenv("SJ_TTL_SECONDS", "60"))

# Hvor lenge et tog blir stående i kartet etter at turen er over.
#
# Et kjøretøy slutter ikke å sende når det ankommer endestasjonen. Det rygger
# inn på hensettingsanlegget, og GPS-en følger med. Målt natt til 22. august
# var 8 av 15 tog i feeden ferdige med turen sin - RE10 339 sto fire kilometer
# nord for Lillehammer, på Hovemoen, merket «6 min 12 s forsinket». Bane NOR
# tegner dem ikke, og det er riktig: de er ikke tog i trafikk.
#
# Spøkelsesfilteret på alder (`STALE_AFTER_SECONDS` i entur.py) fanger dem
# ikke. Det ser etter tog som har sluttet å SENDE; disse sender helt fint, tre
# av dem var under ett minutt gamle. De to filtrene svarer på hver sin ting.
#
# Nådetiden er der for at en ankomst ikke skal blinke ut mens man ser på den,
# og for at klokkeslett som spriker litt mellom kildene ikke skal avgjøre saken.
FERDIG_NADETID = float(os.getenv("FERDIG_NADETID_SEKUNDER", "300"))

# Taket på `dager` på alle tre statistikkendepunktene. Sto som `90` tre steder
# til 21. august; nå ett sted, fordi `vedlikehold.py` må kunne sammenligne det
# med hvor lenge råhistorikken faktisk lever. Er de to ikke enige, svarer
# `?dager=90` med det som er igjen og kaller det nitti dager.
MAKS_DAGER = 90

# Når vedlikeholdsjobben kjører, i lokal tid. Klokka fire om natta: etter
# nattogene og før morgenrushet, altså det døgnet har færrest rader å skrive
# og ingen ser på kartet mens en sletting holder på.
VEDLIKEHOLD_TIME = int(os.getenv("VEDLIKEHOLD_TIME", "4"))

# Hvor lenge etter oppstart jobben kjøres første gang. En server som startes
# klokka ni om morgenen skal ikke vente nitten timer på sin første rullup -
# og på en fersk utrulling er det den første kjøringen som fyller arkivet.
VEDLIKEHOLD_OPPSTART_SEK = float(os.getenv("VEDLIKEHOLD_OPPSTART_SEK", "60"))


def _sekunder_til_time(time_: int) -> float:
    """Sekunder til neste gang klokka er `time_` i norsk lokaltid.

    Lokaltid, ikke UTC, og med `zoneinfo` og ikke en hardkodet forskjell: to
    ganger i året er avstanden til «klokka fire i morgen» 23 eller 25 timer.
    Regner vi i UTC, vandrer jobben en time fram og tilbake gjennom året, og
    lander til slutt midt i morgenrushet.
    """
    naa = datetime.now(timezone.utc).astimezone(OSLO)
    neste = naa.replace(hour=time_, minute=0, second=0, microsecond=0)
    if neste <= naa:
        neste += timedelta(days=1)
    return (neste - naa).total_seconds()


async def _vedlikeholdsjobb() -> None:
    """Rull opp gårsdagen og slett det som er for gammelt, én gang i døgnet.

    Kjører i en tråd: rullupen leser et døgn ut av SQLite og regner medianer,
    og rotasjonen kan slette tre måneder med rader. Ingen av delene skal
    stoppe event-loopen som samtidig svarer på /api/trains.

    Svelger alle feil. Vedlikehold er et tillegg - en jobb som feiler skal
    logge og prøve igjen i morgen, ikke ta serveren med seg. Sperren i
    `roter()` sørger for at en rullup som feiler ikke fører til at noe
    slettes uarkivert.
    """
    await asyncio.sleep(VEDLIKEHOLD_OPPSTART_SEK)
    while True:
        try:
            res = await asyncio.to_thread(vedlikehold)
            log.info(
                "Vedlikehold: rullet opp %d døgn, slettet %d rader, "
                "frigjorde %.1f MB",
                res["rullup"]["dogn"], res["rotasjon"]["slettet"],
                res["plass"]["friBytes"] / 1_048_576,
            )
        except Exception as exc:  # noqa: BLE001 - aldri velte serveren for dette
            log.warning("Vedlikeholdsjobben feilet: %s", exc)

        await asyncio.sleep(_sekunder_til_time(VEDLIKEHOLD_TIME))


@asynccontextmanager
async def livslop(app: FastAPI):
    """Oppstart og avslutning.

    Var `@app.on_event("startup")` til 20. august. Dekoratoren er utfaset i
    FastAPI og fjernes i en kommende versjon; `lifespan` er erstatteren og gjør
    det samme. Alt før `yield` kjører ved oppstart, alt etter ved avslutning.

    Det sto ingenting etter `yield` til 21. august, og det var riktig så lenge
    all tilstand var dicter i minnet. Nå finnes det en oppgave som skal
    stoppes, og da er dette stedet: uten `cancel()` ville uvicorn ventet på en
    `asyncio.sleep()` som har nitten timer igjen.
    """
    if not CLIENT_NAME:
        log.warning(
            "ET_CLIENT_NAME er ikke satt. Entur rate-limiter uidentifiserte "
            "klienter hardt. Kopier .env.example til .env og fyll den ut."
        )
    else:
        log.info("Starter TogKart som '%s' (cache: %.0fs)", CLIENT_NAME, CACHE_TTL)

    sjekk_taket(MAKS_DAGER)

    # To bakgrunnsjobber. `_pollerjobb` er definert lenger ned i fila, etter
    # `get_snapshot()` som er det eneste den kaller - navnet slås opp når denne
    # funksjonen KJØRER, altså etter at hele modulen er lastet.
    jobber = [asyncio.create_task(_vedlikeholdsjobb())]
    if POLLER_PAA:
        jobber.append(asyncio.create_task(_pollerjobb()))
    else:
        log.warning(
            "TOGKART_POLLER er av. Historikken logges bare når noen ser på "
            "kartet, og da får døgnet hull der ingen gjorde det."
        )

    try:
        yield
    finally:
        for jobb in jobber:
            jobb.cancel()
        # Vent på at de faktisk er borte. Uten dette kan uvicorn rekke å lukke
        # event-loopen mens tråden fra `to_thread` fortsatt skriver.
        for jobb in jobber:
            with contextlib.suppress(asyncio.CancelledError):
                await jobb


# Interaktiv API-dokumentasjon på /api/docs er et utmerket verktøy mens du
# utvikler, og en gratis kartlegging av angrepsflaten når appen står ute. Den
# er derfor på som standard - dette kjører lokalt - og av når TOGKART_MILJO
# sier prod. Se «Enklest å forbedre» i docs/sikkerhet.md.
MILJO = os.getenv("TOGKART_MILJO", "utvikling").strip().lower()
ER_PROD = MILJO in ("prod", "produksjon")

app = FastAPI(
    title="TogKart",
    docs_url=None if ER_PROD else "/api/docs",
    redoc_url=None if ER_PROD else "/api/redoc",
    openapi_url=None if ER_PROD else "/openapi.json",
    lifespan=livslop,
)

# Ratebegrensning - før ruting, før statiske filer, før alt som gjør arbeid.
#
# Dokumentasjonen sa en gang at dette hørte hjemme i reverse-proxyen. Det
# stemmer for per-klient-delen, og den regelen står i `drift/cloudflare.md`
# opp der: struping som skjer hos Cloudflare koster deg ingen båndbredde og
# ingen CPU. Denne er bakstopperen. Se `strupe.py` for den grensen Cloudflare
# IKKE kan sette for deg - taket på utgående kall til Entur.
app.add_middleware(Strupe)


# ---------------------------------------------------------------------------
# Sikkerhetsheadere
# ---------------------------------------------------------------------------
# Samme mønster som struping, og av samme grunn: kanten er det beste stedet,
# men kanten var ikke der da disse ble skrevet. `drift/cloudflare.md` hadde
# hatt de fem ferdig formulert siden 21. august uten at de kunne settes opp -
# en Transform Rule trenger en sone å ligge i, og togkartet.no kom ikke inn i
# Cloudflare før 13. september. Til da sendte serveren ingenting.
#
# Så de står her i stedet. Det er ikke et nødvendig onde:
#
#   DE FØLGER APPEN. En policy i et dashbord gjelder ett domene bak én
#   konfigurasjon. Denne gjelder også når du kjører lokalt, når noen andre
#   kjører prosjektet, og hvis appen en dag står bak noe annet enn Cloudflare.
#
#   DE ER I GIT. Endres de, står det i en diff med en begrunnelse ved siden
#   av. Endres en Transform Rule, står det i en logg ingen leser.
#
#   DE KAN TESTES. `prober/sjekk_headere.py` kjører uten nett og uten
#   database, og er med i CI. En policy man ikke kan teste, er en policy man
#   tror man har.
#
# Verdiene er ordrett de samme som i `drift/cloudflare.md`. Endrer du én av
# dem, endre begge - og les avsnittet om `*.cartocdn.com` der før du rører
# `img-src` eller `connect-src`.

# Bakgrunnskartets verter, og de to linjene er IKKE like. Det ser ut som noe
# å rydde opp i, og er det ikke:
#
#   Jokertegnet dekker flisvertene. Stilarket i `app.js` peker videre på en
#   tiles.json, og DEN oppgir fire helt andre verter (tiles-a til tiles-d).
#   En CSP skrevet ut fra index.html alene blir riktig for alt unntatt det
#   kartet faktisk tegner med.
#
#   `basemaps.cartocdn.com` står bare i connect-src, fordi det bare er DIT
#   det hentes noe direkte: app.js fetcher style.json derfra. Sprite og
#   fliser - det img-src handler om - kommer fra tiles.basemaps.cartocdn.com,
#   som jokertegnet dekker. Et CSP-jokertegn matcher ikke domenet uten
#   prefiks, så den må skrives ut der den trengs - og ikke der den ikke gjør.
_FLISER = "https://*.cartocdn.com"
_STILARK = "https://basemaps.cartocdn.com"

CSP = "; ".join([
    "default-src 'self'",
    # Ingen unntak her. MapLibre ble hentet fra unpkg til 23. august; nå
    # ligger den i static/vendor/ og serveres av oss selv.
    "script-src 'self'",
    # Uten 'unsafe-inline' siden 23. august. app.js satte sju
    # style="..."-attributter for datafarger; de er klasser nå (`.f-c-low` og
    # familien i app.css). Et style-attributt bygget fra en streng er, sett
    # fra nettleseren, ikke til å skille fra et en angriper fikk plantet der.
    "style-src 'self'",
    f"img-src 'self' data: blob: {_FLISER}",
    f"connect-src 'self' {_STILARK} {_FLISER}",
    # MapLibre tegner vektorfliser i web workers lastet fra en blob-URL.
    # child-src står som reserve for nettlesere som ikke kjenner worker-src.
    "worker-src blob:",
    "child-src blob:",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'none'",
])

SIKKERHETSHEADERE = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # frame-ancestors i CSP-en gjør det samme og er nyere. Denne står ved
    # siden av for nettlesere som bare kjenner den gamle.
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": CSP,
}

# Swagger og ReDoc henter sitt eget JavaScript og CSS fra cdn.jsdelivr.net, og
# `script-src 'self'` gjør dem til en hvit side. Derfor står de tre stiene
# utenfor CSP-en - men BARE når appen kjører lokalt.
#
# Tom mengde i produksjon, og det er en forskjell som betyr noe. Stiene svarer
# riktignok 404 der uansett (se docs_url over), så det hadde vært fristende å
# la lista stå og kalle den ufarlig. Men da må den som leser policyen holde to
# ting i hodet samtidig for å vite at den ikke har hull. Tømmes den i stedet,
# er setningen kort nok til å stemme: ute sendes CSP-en på hvert eneste svar,
# uten unntak.
_UTEN_CSP = (
    frozenset()
    if ER_PROD
    else frozenset({"/api/docs", "/api/redoc", "/openapi.json"})
)


@app.middleware("http")
async def sikkerhetsheadere(request, call_next):
    """Legger headerne på hvert eneste svar, 429 og 404 inkludert.

    Registrert ETTER Strupe, og det er med vilje: middleware som legges til
    sist ligger ytterst, så headerne kommer også på svar Strupe avviser før
    ruting. Et unntak i en sikkerhetspolicy er en ting man glemmer.
    """
    svar = await call_next(request)
    for navn, verdi in SIKKERHETSHEADERE.items():
        if navn == "Content-Security-Policy" and request.url.path in _UTEN_CSP:
            continue
        svar.headers[navn] = verdi
    return svar

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
# Frontend spør hvert 15. sekund. Hvis du har tre faner åpne blir det tre kall
# i sekundet mot Entur - unødvendig, og en fin måte å bli rate-limited på.
# Cachen gjør at alle faner deler ett og samme kall.
#
# Låsen hindrer at flere samtidige forespørsler alle starter hvert sitt
# Entur-kall i det øyeblikket cachen går ut på dato.
_cache: dict = {"payload": None, "at": 0.0}
_lock = asyncio.Lock()

_sj_cache: dict = {"journeys": {}, "at": 0.0}

# Hvor hvert GPS-tog var forrige gang vi hentet, slik at kjøreretningen kan
# utledes av bevegelsen. {tog-id: (lon, lat, retning, tidspunkt)}.
#
# Dette er den eneste tilstanden i appen som ikke er en cache: en cache kan
# tømmes uten tap, men tømmer du denne, mister hvert Vy- og Go-Ahead-tog pila
# si til det har flyttet seg 25 meter. Den ligger her og ikke i entur.py av
# samme grunn som cachene gjør det - modulene skal kunne kalles to ganger på
# rad i en test uten å huske noe fra forrige gang.
_retning_minne: dict = {}

# Driftsmeldinger. Svaret fra Entur er stort - rundt 370 kB, fordi én melding
# om vedlikehold kan ramme et par tusen avganger og alle listes opp - og det
# endrer seg i minuttskala, ikke sekundskala. Derfor sin egen, lange cache og
# sin egen lås: uten låsen ville tre faner som lastes samtidig fyrt av tre
# 370 kB-kall mot Entur i samme sekund.
AVVIK_TTL = float(os.getenv("AVVIK_TTL_SECONDS", "60"))
_avvik_cache: dict = {"data": None, "at": 0.0}
_avvik_lock = asyncio.Lock()

# Historikkanalysene leser hele tidsvinduet ut av SQLite og regner medianer i
# Python. Det tar brøkdeler av et sekund i dag og vokser med databasen, mens
# svaret endrer seg langsomt - et døgn til med data flytter ikke en median
# nevneverdig. Derfor en egen, mye lengre cache enn togposisjonene har.
STATISTIKK_TTL = float(os.getenv("STATISTIKK_TTL_SECONDS", "300"))
_statistikk_cache: dict[tuple, tuple[float, dict]] = {}

# Stasjonssøket. Frontend venter 250 ms etter siste tastetrykk, men «Oslo S»
# skrives fortsatt som O-s-l-o-mellomrom-S, og de fem prefiksene er de samme
# fem for alle som søker. Stasjonsnavn endrer seg ikke i løpet av en dag, så
# ti minutter er kort.
SOK_TTL = float(os.getenv("SOK_TTL_SECONDS", "600"))
SOK_MAKS_TEGN = 60
SOK_MAKS_OPPSLAG = 500
_sok_cache: dict[tuple, tuple[float, dict]] = {}


async def _statistikk(navn: str, beregn, *args) -> dict:
    """Cachet, trådet analyse. `beregn` er synkron og leser SQLite, så den må
    ut av event-loopen - ellers stopper /api/trains mens medianene regnes.

    Argumentene er MED i cachenøkkelen. Sto de utenfor - som de gjorde til
    20. august - svarte `?dager=90` med det `?dager=1` hadde lagt igjen, i
    fem minutter av gangen. Det er ikke en treg cache, det er feil svar: hvem
    som spurte først avgjorde hvilket tidsvindu alle andre fikk.

    `dager` er klemt til 1-90 av kallerne, så nøkkelrommet er endelig og
    cachen kan ikke vokse uten tak.
    """
    nokkel = (navn, args)
    truffet = _statistikk_cache.get(nokkel)
    if truffet and time.monotonic() - truffet[0] < STATISTIKK_TTL:
        return truffet[1]
    data = await asyncio.to_thread(beregn, *args)
    _statistikk_cache[nokkel] = (time.monotonic(), data)
    return data


def _nokkel(properties: dict) -> tuple[str, str]:
    """(linje, tognummer) på en form som kan sammenlignes på tvers av kildene.

    Vehicle Positions gir linjen som full NeTEx-ID ("VYG:Line:RE10"), Journey
    Planner som kortkode ("RE10"). Vi klipper bort alt foran siste kolon, så
    begge blir "RE10".
    """
    line = str(properties.get("line") or "").rsplit(":", 1)[-1]
    return line, str(properties.get("trainNumber") or "")

JP_TTL = float(os.getenv("JP_TTL_SECONDS", "60"))
_jp_cache: dict = {"delays": {}, "at": 0.0}


async def get_jp_delays(journey_refs: list[str]) -> dict[str, dict]:
    """Forsinkelser fra Journey Planner, hentet sjelden. Feiler det, brukes
    forrige sett - og et tog uten treff faller uansett tilbake på VP-delay."""
    if _jp_cache["delays"] and time.monotonic() - _jp_cache["at"] < JP_TTL:
        return _jp_cache["delays"]
    try:
        _jp_cache["delays"] = await hent_forsinkelser(CLIENT_NAME, journey_refs)
        _jp_cache["at"] = time.monotonic()
    except Exception as exc:  # noqa: BLE001 - aldri velte kartet for dette
        log.warning("Kunne ikke hente punktlighet fra Journey Planner: %s", exc)
    return _jp_cache["delays"]

def _tell_band(features: list[dict]) -> tuple[dict[str, int], int, int]:
    """Tell togene per punktlighetsbånd. Returnerer (counts, stale, beregnede).

    Populasjonen er ALT som tegnes, målt og beregnet, minus spøkelsestogene.
    Det er ikke en detalj: overskriften «N tog i trafikk» leser `meta.count`,
    som er alt som tegnes, mens ringen under leser `counts`. Kommer de to fra
    hver sin populasjon, kan ringen aldri gå opp - og fire tog kan mangle i
    oppdelingen uten at noe sier fra.

    Invarianten som må holde:

        meta.count == sum(counts.values()) + stale

    `prober/test_telling.py` vokter den.
    """
    counts = {band: 0 for band in BAND_ORDER}
    stale_count = 0
    beregnet = 0
    for feature in features:
        p = feature["properties"]
        # Spøkelsestog tegnes, men teller ikke: avviket deres vokser mot en
        # rutetid toget aldri innfrir. Det er den ene utelatelsen som er
        # forsvarlig, og den handler om at TALLET er ubrukelig - ikke om at
        # posisjonen er det.
        if p.get("stale"):
            stale_count += 1
            continue
        counts[p["band"]] += 1
        if p.get("computed"):
            beregnet += 1
    return counts, stale_count, beregnet


def _uten_ferdige(
    measured: list[dict], jp: dict[str, dict]
) -> tuple[list[dict], int]:
    """Ta ut tog som Journey Planner sier har fullført turen sin.

    Returnerer (tog som fortsatt kjører, antall fjernet).

    Regelen krever TO ting, og det er med vilje: status `FERDIG` **og** et
    sluttidspunkt som ligger mer enn nådetiden tilbake. Statusen alene ville
    tatt ut et tog i samme sekund som det ankom.

    Det som IKKE er grunn nok til å skjule et tog:

      * Manglende JP-treff. Da vet vi ingenting om turen, og fravær av data er
        ikke det samme som en fullført tur. Lærdom 8 handler om nettopp dette.
      * Status `ingen tider`. Samme sak - turen finnes, men uten brukbare
        stoppetider har vi ikke målt at den er over.
      * Status `ikke startet`. Det ser ut som et tog før avgang, men det er
        også slik feil kjøredato ser ut (problem 3). Å skjule på den ville
        gjort en datofeil usynlig i stedet for synlig, og det er den dyre
        retningen å bomme i.

    Med andre ord: vi skjuler bare når vi har målt at turen er over.
    """
    na = datetime.now(timezone.utc)
    beholdt: list[dict] = []
    tatt_ut: list[tuple[str, str, float]] = []

    for feature in measured:
        p = feature["properties"]
        # Uten en tur-ID kan vi ikke slå opp turen, og da vet vi ingenting om
        # den. `or ""` som oppslagsnøkkel ville gjort et tomt felt til en
        # gyldig nøkkel - den finnes ikke i ordboka i dag, men å skjule et tog
        # er den handlingen som skal kreve mest, ikke minst.
        ref = p.get("journeyRef")
        treff = jp.get(ref) if ref else None
        slutt = (treff or {}).get("slutt")
        if (
            treff
            and treff.get("status") == "FERDIG"
            and slutt is not None
            and (na - slutt).total_seconds() > FERDIG_NADETID
        ):
            tatt_ut.append((
                p.get("line") or "?", p.get("trainNumber") or "?",
                (na - slutt).total_seconds() / 60,
            ))
            continue
        beholdt.append(feature)

    if tatt_ut:
        # Alderen er verdt å skille på. Et tog som ble ferdig for en halvtime
        # siden står på hensetting etter nettopp den turen. Et som ble ferdig
        # for tjue timer siden har en `journeyRef` som ikke er oppdatert siden
        # forrige gang settet kjørte - målt natt til 22. august på Flytogets
        # sett 71-10 og 71-08, som sto på Drammen med en tur fra formiddagen
        # dagen før. Begge skal ut av kartet, men bare den første er et tog
        # som nettopp ankom.
        #
        # Er den gamle gruppen stor midt på dagen, er det et funn: da er det
        # ikke hensetting vi ser, men tur-ID-er som henger igjen mens toget
        # kjører noe annet - og da skjuler vi tog som er i trafikk.
        nylig = [r for r in tatt_ut if r[2] < 60]
        gamle = [r for r in tatt_ut if r[2] >= 60]
        log.info(
            "Tok ut %d tog som har fullført turen sin (nådetid %.0f s): "
            "%d ferdige siste time, %d eldre. %s",
            len(tatt_ut), FERDIG_NADETID, len(nylig), len(gamle),
            ", ".join(f"{linje} {nr} ({minutter:.0f} min)"
                      for linje, nr, minutter in sorted(tatt_ut)[:8]),
        )
    return beholdt, len(tatt_ut)


def _uten_duplikater(measured: list[dict], computed: list[dict]) -> list[dict]:
    """Målt posisjon slår beregnet. Samme tog skal aldri tegnes to ganger.

    Dette er det ANDRE nettet, og de to fanger forskjellige ting. Det første er
    `positions(hopp_over=...)`, som sammenlikner `serviceJourney`-ID-er og er
    eksakt. Dette sammenlikner (linje, tognummer), og fanger det ID-ene ikke
    kan: samme fysiske tog som de to kildene kjenner under hver sin tur-ID.

    Slår det til, er det verdt å vite - derfor warning og ikke debug. Men det
    er ikke lenger et tegn på at et filter har sviktet, slik det var da
    beregningen bare gjaldt SJ.
    """
    kjente = {
        _nokkel(f["properties"])
        for f in measured
        if f["properties"].get("trainNumber")
    }

    beholdt = []
    for feature in computed:
        linje, tognummer = _nokkel(feature["properties"])
        if tognummer and (linje, tognummer) in kjente:
            log.warning(
                "Tog %s %s finnes både målt og beregnet - dropper den beregnede. "
                "De to kildene bruker trolig hver sin serviceJourney-ID for "
                "samme tog.",
                linje, tognummer,
            )
            continue
        beholdt.append(feature)
    return beholdt


async def get_journeys(har_gps: set[str]) -> dict:
    """Rutedata for turer uten målt posisjon. Feiler det, brukes forrige sett.

    `har_gps` er tur-ID-ene i snapshotet vi nettopp bygget. Den brukes bare når
    cachen er kald - da slipper vi å laste ned rutedata for tog som allerede
    har en posisjon. Mellom hentingene er det `positions(hopp_over=...)` som
    holder de to kildene fra hverandre, og den ser alltid ferske ID-er.
    """
    if _sj_cache["journeys"] and time.monotonic() - _sj_cache["at"] < SJ_TTL:
        return _sj_cache["journeys"]
    try:
        _sj_cache["journeys"] = await fetch_journeys(CLIENT_NAME, har_gps=har_gps)
        _sj_cache["at"] = time.monotonic()
    except Exception as exc:  # noqa: BLE001 - aldri velte kartet for dette
        log.warning("Kunne ikke hente rutedata for tog uten posisjon: %s", exc)
    return _sj_cache["journeys"]


async def get_snapshot() -> tuple[dict, str | None]:
    """Returner (payload, feilmelding). Feilmelding er None når alt gikk bra."""
    async with _lock:
        fresh = _cache["payload"] is not None and (
            time.monotonic() - _cache["at"] < CACHE_TTL
        )
        if fresh:
            return _cache["payload"], None

        try:
            geojson, meta = await fetch_trains(CLIENT_NAME)
        except httpx.HTTPError as exc:
            log.error("Nettverksfeil mot Entur: %s", exc)
            return _cache["payload"], "Fikk ikke kontakt med Entur"
        except RuntimeError as exc:
            log.error("%s", exc)
            return _cache["payload"], str(exc)

        measured = geojson["features"]

        # Kjøreretning til pilene i kartet. Dobbeltsettene er allerede slått
        # sammen på dette punktet - det skjer inne i fetch_trains - og det er
        # riktig rekkefølge: et dobbeltsett er ett tog med én retning, så
        # minnet skal nøkle på den sammenslåtte ID-en og ikke på hvert sett.
        global _retning_minne
        _retning_minne = paafor_retning(measured, _retning_minne)

        # Punktlighet fra Journey Planner. `delay` fra Vehicle Positions er
        # systematisk feil for en del tog (problem 1); JP måler planlagt mot
        # forventet på et navngitt stopp og er den vi kan forsvare. Uten et
        # JP-tall beholder vi VP-delay, og merker på featuren hvor tallet kom
        # fra - samme tanke som positionMethod: track/straight.
        refs = [
            f["properties"]["journeyRef"]
            for f in measured
            if f["properties"].get("journeyRef")
        ]
        jp = await get_jp_delays(refs)

        # Tog som har kjørt ferdig tas ut før noe annet regnes. Rekkefølgen er
        # ikke likegyldig: gjør vi det etterpå, teller de fortsatt i
        # ringdiagrammet og havner i historikken, og da har vi bare flyttet
        # feilen dit den er vanskeligere å se. Samme grep som `_merge_coupled`
        # i entur.py, av samme grunn.
        measured, ferdige = _uten_ferdige(measured, jp)
        meta["ferdige"] = ferdige
        geojson["features"] = measured

        from_jp = 0
        for feature in measured:
            p = feature["properties"]
            treff = jp.get(p.get("journeyRef") or "")
            if treff and treff["delay"] is not None:
                p["delay"] = treff["delay"]
                p["delaySource"] = "journey-planner"
                from_jp += 1
            else:
                p["delaySource"] = "vehicle-positions"
            p["band"] = delay_band(p["delay"])

            # Trafikktype fra Journey Planner. Ikke materiell - men Vy legger
            # tognummeret i `vehicleId` og publiserer ikke settnummer noe
            # sted, så for rundt to tredeler av kartet er dette det eneste
            # Entur har som sier noe om selve toget.
            if treff and treff.get("trafikktype"):
                p["serviceType"] = treff["trafikktype"]

            # Kjøreretningen, i prioritert rekkefølge. `bevegelse` er allerede
            # satt av paafor_retning() over; her kommer de to grovere kildene
            # som dekker toget som står stille.
            #
            # REKKEFØLGEN ER MÅLT, IKKE ANTATT, og den er motsatt av den
            # opplagte. Man skulle tro at operatørens eget `bearing` slo alt -
            # det er jo toget som melder sin egen kurs. Men Flytoget, som er
            # den eneste som publiserer feltet, sender en konstant for hele
            # turen: tog 3798 kjørte seks kilometer gjennom Romeriksporten
            # mens `bearing` flyttet seg én grad og den faktiske kursen
            # tjuesju. Mot faktisk bevegelse bommet den med 26 grader i median.
            # Se kommentaren over MIN_METER i entur.py for hele målingen.
            #
            # Rutetabellen er også grov - en rett linje fra forrige til neste
            # stasjon - men den er LOKAL: den beskriver strekket toget er på
            # nå, ikke gjennomsnittet av hele turen. Derfor står den foran.
            if p.get("heading") is None:
                if treff and treff.get("retning") is not None:
                    p["heading"] = round(treff["retning"], 1)
                    p["headingSource"] = "rutetabell"
                elif p.get("bearing") is not None:
                    p["heading"] = round(float(p["bearing"]) % 360.0, 1)
                    p["headingSource"] = "vehicle-positions"

        # punktlighet.py logger selv "Journey Planner: N av M tog fikk
        # forsinkelse" - hentingen. Denne linja teller *påføringen*: hvor mange
        # av alle togene på kartet som endte opp med et JP-tall. Teller-tallet
        # skal stemme med punktlighet-linja over; spriker de, har noe droppet
        # mellom henting og påføring (typisk en journeyRef som ikke matcher).
        log.info(
            "Punktlighet påført: %d av %d målte tog fikk JP-tall (resten VP-delay)",
            from_jp, len(measured),
        )

        # Kjøreretningen har tre kilder med fallende nøyaktighet, og fordelingen
        # mellom dem er verdt å se: `bearing` er operatørens eget tall,
        # `bevegelse` er målt mellom to hentinger, `rutetabell` er en rett linje
        # mot neste stasjon. Første henting etter oppstart har ingenting å måle
        # bevegelse mot - da er den kolonnen null, og det retter seg selv ti
        # sekunder senere. Ser du det vedvare, er det minnet som ikke fylles.
        kilder = Counter(
            f["properties"].get("headingSource")
            for f in measured
            if f["properties"].get("heading") is not None
        )
        meta["heading"] = sum(kilder.values())
        log.info(
            "Kjøreretning: %d av %d målte tog (%s)",
            meta["heading"], len(measured),
            ", ".join(f"{k}={n}" for k, n in sorted(kilder.items())) or "ingen",
        )

        meta["delayFromJourneyPlanner"] = from_jp

        # Tog uten målt posisjon regnes ut. Hvilke det er, avgjøres av
        # snapshotet vi nettopp bygget: alt Journey Planner kjenner og som ikke
        # står her, har ingen posisjon i Vehicle Positions.
        #
        # To nøkkelformer i samme sett, og begge trengs. Tur-ID-en er den
        # presise, men Vehicle Positions svarer for noen Vy-tog med en
        # DatedServiceJourney-ID der Journey Planner gir en ServiceJourney-ID -
        # ulike ID-rom for samme tog. «linje:tognummer» er det de to alltid
        # kan uttrykke likt. Se `tognokkel()` i sjnord.py for målingen.
        har_gps: set[str] = set()
        for feature in measured:
            p = feature["properties"]
            if p.get("journeyRef"):
                har_gps.add(p["journeyRef"])
            if p.get("trainNumber"):
                har_gps.add(tognokkel(p.get("line"), p["trainNumber"]))

        computed = positions(await get_journeys(har_gps), hopp_over=har_gps)
        before = len(computed)
        computed = _uten_duplikater(measured, computed)
        meta["computedDropped"] = before - len(computed)

        for feature in computed:
            p = feature["properties"]
            p["band"] = delay_band(p.get("delay"))
            # Forsinkelsen på et beregnet tog kommer alltid fra Journey Planner
            # når den finnes - det er den eneste kilden turen har.
            p["delaySource"] = "journey-planner" if p.get("realtime") else None

        geojson["features"].extend(computed)

        # Ringdiagrammet telles HER, over alt som faktisk tegnes - ikke bare
        # over de målte togene.
        #
        # Det sto lenge over `measured` alene, med den begrunnelsen at en
        # beregnet posisjon ikke er en måling. Begrunnelsen gjaldt POSISJONEN,
        # men ringen handler om FORSINKELSEN, og de to har ulik sikkerhet: et
        # beregnet tog får avviket sitt fra Journey Planner, gjennom nøyaktig
        # samme `_last_measured` som gir de målte togene sitt. F6 405 var
        # `delaySource: journey-planner` og 9 min 15 s forsinket, akkurat som
        # F5 726 var det med 91 sekunder - den ene ble talt, den andre ikke.
        #
        # Følgene var to. Overskriften «N tog i trafikk» leste `meta.count` og
        # tok med de beregnede, mens oppdelingen under leste `meta.counts` og
        # ikke gjorde det: teller og nevner kom fra hver sin populasjon, og
        # ringen kunne aldri gå opp. Og tallet ble systematisk for pent -
        # fjerntogene, som oftest er forsinket, er nettopp de uten GPS. Natt
        # til 22. august sto det «100 % i rute» med et SJ-tog ni minutter
        # forsinket på kartet.
        #
        # Spøkelsestog holdes fortsatt utenfor, og den grunnen er en annen og
        # gyldig: avviket deres vokser mot en rutetid toget aldri innfrir. Det
        # er et tall vi ikke kan forsvare, mens det beregnede toget sitt er det.
        # `analyse.py` har regnet slik hele tiden - beregnede turer teller i
        # operatørrangeringen, og `andelBeregnet` sier hvor stor andel de er.
        counts, stale_count, beregnet_talt = _tell_band(geojson["features"])

        meta["counts"] = counts
        # `stale` og `active` kom fra entur.py og talte hele feeden. Etter at
        # de ferdige togene er tatt ut, og de beregnede lagt til, beskriver de
        # en annen populasjon enn den som står i kartet. Et tall som er riktig
        # for feil utvalg er verre enn ingen tall.
        meta["stale"] = stale_count
        meta["active"] = sum(counts.values())
        # Hvor stor del av tellingen som hviler på en beregnet posisjon. Uten
        # dette ville ringen sett like sikker ut uansett hvor mye av den som
        # er interpolert - samme tanke som `andelBeregnet` i analyse.py.
        meta["countsComputed"] = beregnet_talt

        meta["computed"] = len(computed)
        meta["computedNoRealtime"] = sum(
            1 for f in computed if not f["properties"].get("realtime")
        )
        # De to grunnene til at en posisjon er beregnet må telles hver for seg.
        # "ingen-gps" er SJ og er en konstant; "mangler-posisjon" er hullet hos
        # operatører som ellers publiserer, og det er tallet som sier om
        # dekningen blir bedre eller verre. Ett samletall ville skjult begge.
        grunner = Counter(
            f["properties"].get("computedReason") for f in computed
        )
        meta["computedReasons"] = dict(grunner)
        meta["count"] = len(geojson["features"])

        log.info(
            "Beregnet %d tog inn i kartet (%s). Vehicle Positions hadde %d turer.",
            len(computed),
            ", ".join(f"{k}={n}" for k, n in sorted(grunner.items())) or "ingen",
            len(har_gps),
        )

        _cache["payload"] = {"geojson": geojson, "meta": meta}
        _cache["at"] = time.monotonic()

        # Historikk skrives i bakgrunnen - fire-and-forget, ikke await. Feiler
        # loggingen eller tar den tid, skal ikke /api/trains bli tregere for
        # det. logg_snapshot() svelger selv sine egne feil.
        asyncio.create_task(logg_snapshot(geojson["features"]))

        return _cache["payload"], None


# ---------------------------------------------------------------------------
# Bakgrunnsjobb: historikken skal ikke avhenge av at noen ser på
# ---------------------------------------------------------------------------
# Til 14. september ble historikken bare skrevet når `/api/trains` måtte hente
# på nytt - altså når en nettleser sto åpen. Målt på sju døgn: klokka ni om
# morgenen hadde NULL observasjoner, mens kveldstimene hadde tolv ganger så
# mange som morgenrushet. Det var ikke togtrafikken som varierte slik; det var
# når noen så på kartet. Og `rushtidsprofil()` i analyse.py leser nettopp de
# rå radene.
#
# Jobben kaller `get_snapshot()` og ingenting annet. Det er med vilje: da er
# det fortsatt ÉN vei inn til cachen og til historikken, bak samme lås. Issuet
# fryktet at cachen ble «noe to ting skriver til» - det unngås ved at de to
# kallerne deler skriveren i stedet for å ha hver sin.
#
# INTERVALLET ER IKKE CACHE_TTL, og det er et bevisst valg. `logg_snapshot`
# skriver når et tog har flyttet seg over 50 m, og et tog i 100 km/t gjør det
# på under to sekunder - så ved ethvert intervall over ti sekunder logges
# praktisk talt hvert tog ved hver runde. Da er det intervallet, ikke
# trafikken, som bestemmer hvor stor databasen blir:
#
#     hvert 10. sekund   ~864 000 rader/døgn    20 GB på 90 dager
#     hvert 60. sekund   ~144 000 rader/døgn   3,3 GB
#
# Observasjonene fra august ligger på én per tog hvert 49. sekund i snitt. 60
# sekunder holder derfor datatettheten på det analysene allerede er innstilt
# på. Et raskere intervall ville ikke gitt bedre tall, bare flere rader - og
# et brudd med grunnlaget de historiske tallene er regnet ut fra.
POLLER_SEKUNDER = float(os.getenv("TOGKART_POLLER_SEKUNDER", "60"))

# Nødbryter. Skal jobben stoppes i produksjon, skal det ikke kreve en
# kodeendring, et bygg og en utrulling.
POLLER_PAA = os.getenv("TOGKART_POLLER", "på").strip().lower() not in (
    "av", "off", "0", "nei", "false"
)

# Det jobben rapporterer om seg selv. En bakgrunnsjobb ingen ser på er en jobb
# du ikke vet noe om - nøyaktig samme lærdom som helsesjekken selv bygger på.
_poller: dict = {"runder": 0, "feil": 0}
_poller_sist_ok: float | None = None


async def _pollerjobb() -> None:
    """Hent et snapshot med jevne mellomrom, uansett om noen ser på kartet."""
    global _poller_sist_ok

    # Et øyeblikks pause først: uvicorn skal være ferdig med å binde porten før
    # vi legger beslag på hendelsesløkka med et Entur-kall som tar et par
    # sekunder kaldt.
    await asyncio.sleep(2)

    log.info(
        "Bakgrunnsjobb for historikk: henter hvert %.0f. sekund", POLLER_SEKUNDER
    )
    while True:
        try:
            # `get_snapshot` svelger selv nettverksfeil og returnerer forrige
            # payload med en feilmelding. Den kaster bare på det uventede.
            _, feil = await get_snapshot()
            _poller["runder"] += 1
            if feil:
                _poller["feil"] += 1
                log.warning("Bakgrunnsjobben fikk ikke ferske tall: %s", feil)
            else:
                _poller_sist_ok = time.monotonic()
        except Exception as exc:  # noqa: BLE001 - aldri velte serveren for dette
            # CancelledError arver fra BaseException og fanges IKKE her, så en
            # avslutning kommer fortsatt fram. Det er hele grunnen til at dette
            # er `Exception` og ikke `BaseException`.
            _poller["feil"] += 1
            log.warning("Bakgrunnsjobben feilet: %s", exc)

        await asyncio.sleep(POLLER_SEKUNDER)


def pollerstatus() -> dict:
    """Tilstanden til bakgrunnsjobben, til /api/health."""
    return {
        "aktiv": POLLER_PAA,
        "intervallSekunder": POLLER_SEKUNDER if POLLER_PAA else None,
        "runder": _poller["runder"],
        "feil": _poller["feil"],
        "sisteOkSekunderSiden": (
            None
            if _poller_sist_ok is None
            else round(time.monotonic() - _poller_sist_ok)
        ),
    }


@app.get("/api/trains")
async def trains() -> JSONResponse:
    """Alle aktive tog som GeoJSON."""
    payload, error = await get_snapshot()

    if payload is None:
        # Vi har aldri klart å hente data. Ingenting å vise.
        return JSONResponse(
            status_code=503,
            content={"error": error or "Ingen data tilgjengelig ennå"},
        )

    body = dict(payload)
    if error:
        # Data finnes, men de er utdaterte. Frontend viser en varsellinje.
        body["stale"] = True
        body["error"] = error
    return JSONResponse(content=body)


@app.get("/api/search")
async def search(q: str, limit: int = 8) -> JSONResponse:
    """Søk etter jernbanestasjoner via Entur Geocoder v3.

    Backend tar samtalen, ikke nettleseren. Ikke fordi ET-Client-Name er
    hemmelig - det er det ikke - men fordi nettleseren ellers ville møtt
    CORS-vegg, og fordi vi da kan strupe og cache på ett sted.

    «Strupe og cache på ett sted» sto her som en hensikt fra første dag, men
    var ikke bygget: hvert tastetrykk gikk rett videre til Entur, og `q` hadde
    ingen øvre lengde. Begge deler er rettet 20. august.
    """
    query = q.strip()
    if len(query) < 2:
        return JSONResponse(content={"results": []})

    # Ingen stasjon i Norge heter noe på over seksti tegn. Uten en øvre grense
    # kan hvem som helst sende et megabyte gjennom oss til Entur, under vårt
    # ET-Client-Name - og det er vår kvote som ryker, ikke deres.
    query = query[:SOK_MAKS_TEGN]

    truffet = _sok_cache.get((query, limit))
    if truffet and time.monotonic() - truffet[0] < SOK_TTL:
        return JSONResponse(content=truffet[1])

    # Herfra og ut går det et ekte kall til Entur, signert med vårt
    # ET-Client-Name. Bøtta spørres FØR kallet og bare ved cachebom: et treff
    # i cachen er ingen belastning på kvoten og skal ikke telle mot den.
    #
    # Dette er stedet en per-IP-grense ikke rekker. Kvoten henger på
    # klientnavnet, så tusen IP-er som hver holder seg innenfor sin egen
    # grense summerer seg til én overskridelse hos Entur. Se strupe.py.
    vent = ta_entur_kall()
    if vent:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Søket er midlertidig utilgjengelig. Prøv igjen om litt.",
                "retryAfter": max(1, int(vent + 0.999)),
            },
            headers={"Retry-After": str(max(1, int(vent + 0.999)))},
        )

    params = {
        "q": query,
        # railStation + grupper gir jernbanestasjoner og ikke busstopp.
        "layers": "stopPlace,groupOfStopPlaces",
        "stopPlaceTypes": "railStation",
        "countries": "NO",
        "limit": max(1, min(limit, 20)),
        "lang": "no",
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            # params= lar httpx kode teksten. Bygger du URL-en med f-streng,
            # knekker "Bø" og "Oslo S".
            response = await client.get(
                GEOCODER_URL, params=params, headers={"ET-Client-Name": CLIENT_NAME}
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        log.error("Geocoder svarte ikke: %s", exc)
        return JSONResponse(status_code=502, content={"error": "Søket feilet"})

    results = []
    for feature in payload.get("features", []):
        properties = feature.get("properties") or {}
        coordinates = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coordinates) != 2:
            continue
        names = properties.get("names") or {}
        results.append(
            {
                "id": properties.get("id"),
                "name": names.get("default") or "",
                # display er "Oslo S, Oslo" - navn pluss kommune.
                "label": names.get("display") or names.get("default") or "",
                "lon": coordinates[0],
                "lat": coordinates[1],
            }
        )

    svar = {"results": results}

    # Cachen har tak fordi nøkkelen er brukerstyrt. Uten det er den ikke en
    # cache, men et minne som vokser med hvert nye søkeord noen finner på -
    # og da har vi bygget lekkasjen selv. Enkleste tak som virker: er den full,
    # tømmes den. Søkeord er korte og kommer i klynger, så en tømming nå og da
    # koster ett Entur-kall, ikke en kald cache.
    if len(_sok_cache) >= SOK_MAKS_OPPSLAG:
        _sok_cache.clear()
    _sok_cache[(query, limit)] = (time.monotonic(), svar)

    return JSONResponse(content=svar)


@app.get("/api/route/{journey_id}")
async def route(journey_id: str) -> JSONResponse:
    """Traseen til én SJ-tur som GeoJSON LineString - til ruteopptegning ved
    klikk i kartet.

    `route_line()` leser fra sjnord sin modulglobale `_FORBEREDT`-cache. Den
    fylles av `fetch_journeys()` (via `_prepare()`) og holdes varm så lenge
    `/api/trains` polles. Vi kaller likevel `get_journeys()` først, for det ene
    tilfellet der cachen kan være tom: en forespørsel rett etter oppstart, før
    `/api/trains` har kjørt en gang. I praksis kjenner frontend uansett ingen
    tur-ID før `/api/trains` har svart, så dette er et sikkerhetsnett.

    Bare SJ-tog ligger i `_FORBEREDT`. Klikker frontend på et GPS-tog, eller på
    en SJ-tur uten sporgeometri (`positionMethod: straight`), får vi None her -
    og svarer 404. Frontend tegner da ingen rute, som er riktig oppførsel for
    denne MVP-en. GPS-tog vil kreve et eget on-demand oppslag mot Journey
    Planner, og er et naturlig neste steg.
    """
    await get_journeys()
    punkter = route_line(journey_id)
    if not punkter:
        return JSONResponse(
            status_code=404,
            content={"error": "Ingen trasé for denne turen"},
        )
    return JSONResponse(
        content={
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": punkter},
            "properties": {"id": journey_id},
        }
    )


@app.get("/api/statistikk/operatorer")
async def statistikk_operatorer(dager: int = 7) -> JSONResponse:
    """Rangering av operatørene på andel togturer i rute, fra historikk.db.

    Kan svare med en tom liste, og det er ikke en feil: en fersk database har
    ingen togturer å rangere ennå. Frontend skiller mellom «ingen data» og
    «kunne ikke hente», og skal kunne gjøre det uten å tolke en 500-er.
    """
    dager = max(1, min(dager, MAKS_DAGER))
    try:
        return JSONResponse(content=await _statistikk("operatorer", operatorsammenligning, dager))
    except Exception as exc:  # noqa: BLE001 - historikk skal aldri velte kartet
        log.warning("Kunne ikke regne ut operatørstatistikk: %s", exc)
        return JSONResponse(status_code=503, content={"error": "Statistikken er utilgjengelig"})


@app.get("/api/statistikk/rush")
async def statistikk_rush(dager: int = 14) -> JSONResponse:
    """Hvilke strekninger som er hardest belastet i morgen- og
    ettermiddagsrushet. Lengre vindu enn operatørrangeringen fordi den bare
    teller hverdager - to uker gir ti døgn med rush, én uke bare fem."""
    dager = max(1, min(dager, MAKS_DAGER))
    try:
        return JSONResponse(content=await _statistikk("rush", rushtidsprofil, dager))
    except Exception as exc:  # noqa: BLE001
        log.warning("Kunne ikke regne ut rushtidsprofil: %s", exc)
        return JSONResponse(status_code=503, content={"error": "Statistikken er utilgjengelig"})


@app.get("/api/flaskehalser")
async def flaskehalser_api(dager: int = 7, minPasseringer: int = 5) -> JSONResponse:
    """Varmekart over hvor togene mister tid, som GeoJSON.

    Måler ENDRINGEN i avvik over hver strekning, ikke avviket i seg selv. Et
    tog som ruller inn til Oslo S tjue minutter for sent gjør Oslo S rødt på
    et gjennomsnittskart, men forsinkelsen oppsto kanskje to timer tidligere
    et helt annet sted. Se `flaskehals.py`.

    Tom samling er et gyldig svar: en fersk database har ingen passeringer å
    regne på ennå.
    """
    dager = max(1, min(dager, MAKS_DAGER))
    # Under tre passeringer er en strekning en anekdote, ikke en måling.
    min_passeringer = max(3, min(minPasseringer, 100))
    try:
        return JSONResponse(
            content=await _statistikk(
                "flaskehalser", flaskehalser, dager, min_passeringer
            )
        )
    except Exception as exc:  # noqa: BLE001 - historikk skal aldri velte kartet
        log.warning("Kunne ikke regne ut flaskehalser: %s", exc)
        return JSONResponse(
            status_code=503, content={"error": "Flaskehalskartet er utilgjengelig"}
        )


@app.get("/api/avvik")
async def avvik() -> JSONResponse:
    """Driftsmeldinger fra SIRI-SX, ferdig sortert til nyhetsstripa.

    Tom liste er et gyldig svar og ikke en feil: en kveld uten avvik er en
    god kveld. Frontend skiller mellom «ingen meldinger» og «fikk ikke tak i
    meldingene», og skal kunne gjøre det uten å tolke en 503-er.

    Feiler hentingen, serveres forrige sett med `foreldet: true` i stedet for
    en feilmelding. En stripe som står stille med de siste kjente meldingene
    er mer verdt enn en tom boks - så lenge det står at de er gamle. Samme
    valg som `/api/trains` gjør med `stale`.
    """
    async with _avvik_lock:
        fersk = _avvik_cache["data"] is not None and (
            time.monotonic() - _avvik_cache["at"] < AVVIK_TTL
        )
        if fersk:
            return JSONResponse(content=_avvik_cache["data"])

        try:
            data = await hent_avvik(CLIENT_NAME)
        except Exception as exc:  # noqa: BLE001 - avvik skal aldri velte kartet
            log.warning("Kunne ikke hente driftsmeldinger: %s", exc)
            if _avvik_cache["data"] is None:
                return JSONResponse(
                    status_code=503,
                    content={"error": "Driftsmeldingene er utilgjengelige"},
                )
            return JSONResponse(content={**_avvik_cache["data"], "foreldet": True})

        _avvik_cache["data"] = data
        _avvik_cache["at"] = time.monotonic()
        return JSONResponse(content=data)


# Hvor gammel snapshoten får være før helsesjekken henter på nytt.
#
# Dette tallet er hele forskjellen på en helsesjekk og en last. `CACHE_TTL` er
# 10 sekunder, tilpasset en nettleser som poller hvert 15. Leste helsesjekken
# den grensen, ville HVER eneste sjekk bommet på cachen - og en bom er ikke
# billig: den henter Vehicle Positions, spør Journey Planner om forsinkelser,
# henter rutedata for togene uten GPS og skriver en runde til historikk.db.
# En monitor som ser til appen hvert minutt ville altså drevet fire
# Entur-kall i minuttet døgnet rundt, og telt dem på kvoten.
#
# Fem minutter er valgt slik at en monitor på ett minutt koster én henting per
# femte sjekk, og at svaret aldri er mer enn fem minutter gammelt. Sjekken
# beholder likevel tennene: går hentingen i stykker, blir cachen aldri fersk
# igjen, og da prøver helsesjekken på ekte hver gang - som er nøyaktig når du
# vil at den skal gjøre det.
HELSE_TTL = float(os.getenv("TOGKART_HELSE_TTL", "300"))


@app.get("/api/health")
async def health() -> JSONResponse:
    """Statussjekken en overvåker skal se på.

    Tre ting den må gjøre riktig, og som alle tre var feil før 23. august:

    ER BILLIG Å SPØRRE. Se HELSE_TTL over. En helsesjekk som utløser arbeidet
    den overvåker, er en lastgenerator med et misvisende navn.

    SVARER MED STATUSKODE OG IKKE BARE MED TEKST. Endepunktet svarte 200 uansett
    hvor galt det sto til, og `"ok": false` lå i kroppen. Det virker for en
    overvåker som er satt opp til å lese kroppen - og er blind for alle andre.
    Nå er det 503 når `ok` er usann, så en hvilken som helst monitor, en
    `curl -f` eller en systemd-sjekk ser det samme.

    SIER HVOR GAMMELT SVARET ER. `alderSekunder` er sekunder siden tallene ble
    hentet. Uten det kan ikke den som leser vite om «12 tog» er fra nå eller
    fra i går - og et tall uten alder er den slags data prosjektet ellers
    bruker mye krefter på å merke.

    `strupe` er med fordi den ellers er usynlig. En strupe som gjør jobben sin
    ser nøyaktig ut som en strupe som er slått av: alt virker. Skal noen kunne
    se om vernet står, må tallene være lesbare et sted - og `enturKvote.igjen`
    er det ene tallet som sier om kvoten din er i ferd med å brukes opp nå.
    """
    # Leses uten `_lock`. De to feltene settes ved siden av hverandre uten en
    # await imellom (se get_snapshot), så en hendelsesløkke kan ikke komme til
    # å vise en ny payload med en gammel tidsstempel.
    fersk_nok = _cache["payload"] is not None and (
        time.monotonic() - _cache["at"] < HELSE_TTL
    )

    if fersk_nok:
        payload, error = _cache["payload"], None
    else:
        payload, error = await get_snapshot()

    # Alderen leses ETTER et eventuelt forsøk. Gikk hentingen i stykker, står
    # `_cache["at"]` urørt, og alderen er da den ekte alderen på tallene vi
    # fortsatt sitter med - ikke null.
    alder = (
        None
        if _cache["payload"] is None
        else round(time.monotonic() - _cache["at"])
    )

    ok = error is None and payload is not None
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "ok": ok,
            "error": error,
            "trains": (payload or {}).get("meta", {}).get("count"),
            "alderSekunder": alder,
            "strupe": strupestatus(),
            # Av samme grunn som `strupe`: en bakgrunnsjobb som har stoppet
            # ser nøyaktig ut som en som virker, helt til noen ser etter hull
            # i historikken en uke senere. `sisteOkSekunderSiden` vesentlig
            # over `intervallSekunder` betyr at den står.
            "poller": pollerstatus(),
        },
    )


# Frontend serveres sist, slik at /api/* vinner over statiske filer.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
