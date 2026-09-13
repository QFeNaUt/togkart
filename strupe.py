"""Strupe - to helt forskjellige grenser, og forskjellen er hele poenget.

Dokumentasjonen sa en gang at ratebegrensning hører hjemme i reverse-proxyen
og ikke i appen. Det stemmer fortsatt for den delen som handler om
rettferdighet. Men appen står bak en Cloudflare-tunnel og ikke bak en nginx
du eier, og da er det verdt å være presis om hva de to grensene beskytter:

**1. Per klient - rettferdighet.** Én besøkende skal ikke kunne ta hele
serveren. Dette er den grensen Cloudflare gjør best, foran, før trafikken
koster deg noe som helst. `drift/cloudflare.md` har reglene. Den er også her,
som en bakstopper: en regel i et dashbord er ikke en garanti i koden, og
tunnelen kan i prinsippet nås fra maskinen selv.

**2. Globalt, mot Entur - kvotevern.** Denne kan IKKE løses i Cloudflare, og
det er den viktigste innsikten i denne fila.

Kvoten hos Entur henger på `ET_CLIENT_NAME`, ikke på den besøkendes IP. En
per-IP-regel - i Cloudflare eller her - sier «du får 20 søk i minuttet». Hundre
IP-er som hver holder seg pent innenfor gir 2000 søk i minuttet ut av
`/api/search`, alle signert med ditt klientnavn, alle helt innenfor regelen.
Da er det du som blir ratebegrenset, ikke dem, og søket slutter å virke for
alle. Et botnett trenger ikke engang å være ondsinnet for å få det til; det
holder at det er distribuert.

Derfor ligger det en bøtte til her som ikke teller besøkende i det hele tatt.
Den teller UTGÅENDE kall til Entur, og den har ett tall for hele appen. Går
den tom, svarer søket 503 og Entur får være i fred. Se `ENTUR_GEOKODER`.

Cachen i `app.py` gjør mesteparten av jobben - ti minutter på hvert søkeord,
og de fem prefiksene i «Oslo S» er de samme fem for alle - så bøtta ser bare
det cachen bommer på. Den er et tak, ikke en strupe: i normal drift tar den
aldri i.

---------------------------------------------------------------------------
Hvem er klienten, bak en tunnel
---------------------------------------------------------------------------
`cloudflared` kobler seg til uvicorn på 127.0.0.1. Sett fra appen kommer
derfor ALLE forespørsler fra samme adresse, og en per-IP-grense på
`scope["client"]` ville vært én felles bøtte for hele internett - som
strupet alle besøkende samtidig, første gang noen hamret.

Den ekte adressen står i `CF-Connecting-IP`. Den headeren er verdt nøyaktig
så mye som den er vanskelig å forfalske, og derfor leses den bare når to ting
er sanne: `TOGKART_BAK_CLOUDFLARE` er satt, OG forespørselen kom fra
loopback. Uten begge deler kunne hvem som helst som når porten direkte sende
en ny `CF-Connecting-IP` per forespørsel og aldri møte en grense.

Kjør `python strupe.py --selvtest` for å teste bøttene uten nett og uten
server.
"""

import logging
import os
import time
from ipaddress import ip_address

from starlette.responses import JSONResponse

log = logging.getLogger("togkart")

PAA = os.getenv("TOGKART_STRUPE", "på").strip().lower() not in (
    "av", "0", "false", "nei", "off",
)

# Leses `CF-Connecting-IP` i det hele tatt. Av som standard: lokalt er det
# ingen tunnel, og en header vi ikke trenger er en header vi ikke skal tro på.
BAK_CLOUDFLARE = os.getenv("TOGKART_BAK_CLOUDFLARE", "").strip().lower() in (
    "1", "true", "ja", "på", "on", "yes",
)


class Grense:
    """Hvor fort, og hvor mye på én gang.

    `per_sekund` er den vedvarende raten. `kapasitet` er hvor mange
    forespørsler som kan komme i én klump før raten begynner å gjelde - en
    bøtte som fylles jevnt og tømmes rykkvis.

    Klumpen er ikke slark, den er nødvendig. Åpner noen kartet, ber
    nettleseren om index.html, app.css, app.js og tre geojson-filer i samme
    øyeblikk, og /api/trains og /api/avvik rett etterpå. Med kapasitet lik
    raten ville en helt normal sidelasting truffet grensen.
    """

    __slots__ = ("navn", "kapasitet", "per_sekund")

    def __init__(self, navn: str, kapasitet: float, per_minutt: float):
        self.navn = navn
        self.kapasitet = float(kapasitet)
        self.per_sekund = per_minutt / 60.0

    @property
    def per_minutt(self) -> float:
        return self.per_sekund * 60.0

    def __repr__(self) -> str:
        return (f"Grense({self.navn}: {self.per_minutt:.0f}/min, "
                f"klump {self.kapasitet:.0f})")


# ---------------------------------------------------------------------------
# Grensene per endepunktklasse
# ---------------------------------------------------------------------------
# Tallene er utledet av hva frontend faktisk gjør, ikke gjettet. `app.js`
# poller /api/trains hvert 15. sekund og /api/avvik sjeldnere; søket venter
# 250 ms etter siste tastetrykk; statistikkpanelet hentes ved sidelasting og
# ved bytte av tidsvindu.
#
# Regelen for hver linje: ta det en ivrig, ekte bruker med tre faner gjør,
# og gang med tre. Blir grensen truffet av noen som bare bruker kartet, er
# den feil - da er det en feilmelding, ikke et vern.

GRENSER = {
    # 20 i minuttet er fire ganger det å skrive «Trondheim S» bokstav for
    # bokstav koster, og cachen tar de fleste av dem uansett. Klumpen på 10
    # dekker en rask skriving fra bunnen.
    "sok": Grense("sok", kapasitet=10, per_minutt=20),

    # Kaldt oppslag leser hele tidsvinduet ut av SQLite og regner medianer.
    # `dager` spenner 1-90 og er med i cachenøkkelen, så 90 forskjellige
    # verdier gir 90 kalde utregninger. Dette er endepunktet en angriper
    # ville valgt for å bruke CPU, og grensen er derfor den strengeste
    # målt mot hva den koster.
    "tung": Grense("tung", kapasitet=10, per_minutt=30),

    # Cachet i minnet og billig å svare på. Tre faner som poller hvert 15.
    # sekund er 12 i minuttet; her er det rom for tolv faner.
    "sanntid": Grense("sanntid", kapasitet=30, per_minutt=60),

    # Statiske filer. Én sidelasting er rundt seks forespørsler, og
    # jernbanenett.geojson alene er 856 kB - det er båndbredde, ikke CPU.
    # Cloudflare cacher dette foran uansett, så bøtta ser stort sett bare
    # det som bommer der.
    "statisk": Grense("statisk", kapasitet=60, per_minutt=180),

    # Alt annet under /api/. Finnes ikke i dag; står her fordi et nytt
    # endepunkt skal være strupet fra første dag, ikke fra den dagen noen
    # husket å legge det inn i tabellen over.
    "annet": Grense("annet", kapasitet=20, per_minutt=60),
}

# Utgående kall til Entur sin geocoder, for hele appen samlet. Se modulens
# docstring: dette er den bøtta per-IP-grenser ikke kan erstatte.
#
# 60 i minuttet er langt over det normal drift bruker. Med ti minutters cache
# på hvert søkeord er et cachebom et NYTT søkeord, og nye søkeord kommer ikke
# seksti i minuttet fra ekte mennesker. Taket er satt for å stoppe en
# distribuert hamring, ikke for å styre normal bruk - treffer du det i
# vanlig drift, er det cachen som er i stykker, ikke grensen som er for lav.
ENTUR_GEOKODER = Grense("entur-geokoder", kapasitet=30, per_minutt=60)

# Hvor mange (klient, klasse)-bøtter vi holder i minnet. Nøkkelen er
# brukerstyrt - det er en IP-adresse - så uten et tak er dette ikke en
# teller, men en lekkasje som vokser med antall unike besøkende. Samme
# lærdom som `_sok_cache` i app.py, og samme løsning.
MAKS_BOTTER = int(os.getenv("TOGKART_STRUPE_MAKS_BOTTER", "20000"))


# ---------------------------------------------------------------------------
# Bøtta
# ---------------------------------------------------------------------------

class Botte:
    """Token bucket. `tokens` er hvor mange forespørsler som er igjen nå.

    Ingen bakgrunnsjobb fyller den. Påfyllet regnes ut når noen spør, av
    tiden som er gått - en bøtte ingen har rørt på en time koster ingen CPU
    og er automatisk full. Det er hele grunnen til at token bucket er riktig
    her og ikke en teller per vindu: en teller må nullstilles av noen.
    """

    __slots__ = ("tokens", "sist")

    def __init__(self, grense: Grense, naa: float):
        self.tokens = grense.kapasitet
        self.sist = naa

    def ta(self, grense: Grense, naa: float, koster: float = 1.0) -> float:
        """Prøv å ta `koster` tokens. Returnerer 0.0 hvis det gikk, ellers
        antall sekunder til det ville gått."""
        self.tokens = min(
            grense.kapasitet,
            self.tokens + (naa - self.sist) * grense.per_sekund,
        )
        self.sist = naa

        if self.tokens >= koster:
            self.tokens -= koster
            return 0.0
        return (koster - self.tokens) / grense.per_sekund

    def full(self, grense: Grense, naa: float) -> bool:
        """Er bøtta tilbake på full kapasitet - altså umulig å skille fra en
        bøtte som aldri har vært brukt."""
        return self.tokens + (naa - self.sist) * grense.per_sekund >= grense.kapasitet


class Botter:
    """Bøttene for alle klienter, med et tak som ikke straffer noen."""

    def __init__(self, maks: int = MAKS_BOTTER):
        self._botter: dict[tuple, Botte] = {}
        self.maks = maks
        self.avviste = 0
        self.ryddet = 0

    def ta(self, nokkel: tuple, grense: Grense, naa: float) -> float:
        botte = self._botter.get(nokkel)
        if botte is None:
            if len(self._botter) >= self.maks:
                self._rydd(naa)
            botte = Botte(grense, naa)
            self._botter[nokkel] = botte

        vent = botte.ta(grense, naa)
        if vent:
            self.avviste += 1
        return vent

    def _rydd(self, naa: float) -> None:
        """Kast bøttene som er fulle igjen.

        Dette er en gratis opprydning, ikke en tilnærming: en full bøtte er
        bit for bit den samme som den vi ville laget for en klient vi aldri
        har sett. Å kaste den gir ingen fordel til noen som ble strupet, og
        tar ingen fra noen som oppførte seg.

        Skulle alle bøttene være i bruk samtidig - tjue tusen aktive IP-er
        innenfor samme minutt - hjelper ikke det, og da tømmes hele. Det er
        et bevisst valg: en overfylt teller skal gi etter, ikke vokse videre
        til minnet er brukt opp. En omgang med gratis forespørsler er
        billigere enn en OOM.
        """
        for nokkel, botte in list(self._botter.items()):
            grense = GRENSER.get(nokkel[-1]) or ENTUR_GEOKODER
            if botte.full(grense, naa):
                del self._botter[nokkel]
                self.ryddet += 1

        if len(self._botter) >= self.maks:
            log.warning(
                "Strupe: %d bøtter i bruk samtidig, tømmer alle. Er dette "
                "reelt, står du midt i noe distribuert.", len(self._botter),
            )
            self._botter.clear()

    def __len__(self) -> int:
        return len(self._botter)


# Modulglobal, som cachene i app.py: én teller for hele prosessen. Nullstilles
# ved omstart, og det er riktig - en omstart er et nytt utgangspunkt, ikke en
# fortsettelse.
_botter = Botter()

# Den globale Entur-bøtta er én enkelt bøtte, ikke en samling. Den lever her
# og ikke i `_botter` fordi den ikke har en klient å nøkles på.
_entur = Botte(ENTUR_GEOKODER, time.monotonic())


# ---------------------------------------------------------------------------
# Hvem, og hva
# ---------------------------------------------------------------------------

# Prefiksene sjekkes i rekkefølge, første treff vinner. `/api/statistikk/`
# står før `/api/` av samme grunn som `/api/*` monteres før StaticFiles i
# app.py: den mest spesifikke regelen må komme først.
_KLASSER = (
    ("/api/search", "sok"),
    ("/api/statistikk/", "tung"),
    ("/api/flaskehalser", "tung"),
    ("/api/trains", "sanntid"),
    ("/api/avvik", "sanntid"),
    ("/api/route/", "sanntid"),
    ("/api/health", "sanntid"),
    ("/api/", "annet"),
)


def klasse_for(sti: str) -> str:
    for prefiks, navn in _KLASSER:
        if sti.startswith(prefiks):
            return navn
    return "statisk"


def _loopback(vert: str) -> bool:
    try:
        return ip_address(vert).is_loopback
    except ValueError:
        return False


def klient_nokkel(scope: dict) -> str:
    """Hvem forespørselen skal telles på.

    Rekkefølgen er en tillitskjede, ikke en preferanse:

    1. Kom den fra loopback, og er vi konfigurert til å stå bak Cloudflare?
       Da er avsenderen `cloudflared`, og `CF-Connecting-IP` er satt av
       Cloudflare sine egne servere - ikke av den besøkende. Den kan brukes.
    2. Ellers er socketadressen det eneste vi vet noe om.

    Det som IKKE står her er `X-Forwarded-For`. Den er en liste hvem som
    helst kan skrive i, og bak en tunnel legger Cloudflare uansett den ekte
    adressen i sin egen header. Å lese XFF ville vært å lese noe angriperen
    kontrollerer og kalle det en identitet.
    """
    klient = scope.get("client")
    vert = klient[0] if klient else ""

    if BAK_CLOUDFLARE and vert and _loopback(vert):
        for navn, verdi in scope.get("headers", ()):
            if navn == b"cf-connecting-ip":
                ekte = verdi.decode("latin-1").strip()
                if ekte:
                    return ekte
    return vert or "ukjent"


# ---------------------------------------------------------------------------
# Den globale Entur-bøtta
# ---------------------------------------------------------------------------

def ta_entur_kall(koster: float = 1.0) -> float:
    """Be om lov til å gjøre ett utgående kall til Entur sin geocoder.

    Returnerer 0.0 hvis det er greit, ellers sekunder til det ville vært det.
    Kalles fra `/api/search` KUN ved cachebom - et treff i cachen er ikke et
    kall til Entur og skal ikke telles som ett.
    """
    if not PAA:
        return 0.0
    vent = _entur.ta(ENTUR_GEOKODER, time.monotonic(), koster)
    if vent:
        log.warning(
            "Entur-kvoten er strupt av oss selv: taket er %.0f geocoder-kall "
            "i minuttet, neste slipper gjennom om %.0f s. Enten hamrer noen "
            "på /api/search, eller så treffer ikke søkecachen.",
            ENTUR_GEOKODER.per_minutt, vent,
        )
    return vent


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class Strupe:
    """ASGI-middleware. Teller per (klient, klasse) og svarer 429.

    Skrevet som rå ASGI og ikke med `BaseHTTPMiddleware`: den siste pakker
    hver forespørsel i en anroperoppgave og en meldingskø for å kunne gi et
    Request-objekt, og alt vi trenger er stien og en header. Dette kjører for
    hver eneste fil kartet laster.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if not PAA or scope["type"] != "http":
            return await self.app(scope, receive, send)

        klasse = klasse_for(scope.get("path", ""))
        grense = GRENSER[klasse]
        nokkel = (klient_nokkel(scope), klasse)

        vent = _botter.ta(nokkel, grense, time.monotonic())
        if not vent:
            return await self.app(scope, receive, send)

        # `Retry-After` rundes opp: sier vi 0 sekunder, prøver en veloppdragen
        # klient igjen med en gang og blir avvist igjen.
        sekunder = max(1, int(vent + 0.999))
        log.info(
            "Strupet %s på %s (%s): %.0f/min, prøv igjen om %d s",
            nokkel[0], scope.get("path", ""), klasse, grense.per_minutt, sekunder,
        )
        svar = JSONResponse(
            status_code=429,
            content={
                "error": "For mange forespørsler. Prøv igjen om litt.",
                "retryAfter": sekunder,
            },
            headers={"Retry-After": str(sekunder), "Cache-Control": "no-store"},
        )
        await svar(scope, receive, send)


def status() -> dict:
    """Hva strupen har sett. Til /api/health og til probene."""
    naa = time.monotonic()
    return {
        # ASCII-nøkler. `på` ville vært penere norsk og en felle for enhver
        # klient som leser JSON-en - resten av API-et bruker heller ikke æøå
        # i nøkler, bare i verdier og feilmeldinger.
        "aktiv": PAA,
        "bakCloudflare": BAK_CLOUDFLARE,
        "botter": len(_botter),
        "maksBotter": _botter.maks,
        "avviste": _botter.avviste,
        "ryddet": _botter.ryddet,
        "enturKvote": {
            "perMinutt": round(ENTUR_GEOKODER.per_minutt),
            "igjen": round(min(
                ENTUR_GEOKODER.kapasitet,
                _entur.tokens + (naa - _entur.sist) * ENTUR_GEOKODER.per_sekund,
            ), 1),
            "kapasitet": ENTUR_GEOKODER.kapasitet,
        },
    }


def _nullstill() -> None:
    """Bare til bruk i tester og selvtest."""
    global _botter, _entur
    _botter = Botter()
    _entur = Botte(ENTUR_GEOKODER, time.monotonic())


# ---------------------------------------------------------------------------
# Selvtest
# ---------------------------------------------------------------------------

def _selvtest() -> int:
    """Bøttematematikk, klassifisering og tillitskjeden. Uten nett, uten
    server, og uten å vente på ekte tid - klokka mates inn."""
    feil = 0

    def sjekk(navn, faktisk, ventet):
        nonlocal feil
        if faktisk == ventet:
            print(f"  OK    {navn}")
        else:
            print(f"  FEIL  {navn}: fikk {faktisk!r}, ventet {ventet!r}")
            feil += 1

    print("\nstrupe.py --selvtest\n")

    print("Bøtta")
    g = Grense("test", kapasitet=5, per_minutt=60)   # 1 per sekund
    b = Botte(g, 0.0)
    sjekk("klumpen slipper gjennom", [b.ta(g, 0.0) for _ in range(5)], [0.0] * 5)
    sjekk("den sjette avvises", b.ta(g, 0.0) > 0, True)
    sjekk("og sier hvor lenge (1 s)", round(b.ta(g, 0.0), 3), 1.0)
    sjekk("ett sekund gir én token", b.ta(g, 1.0), 0.0)
    sjekk("fem sekunder gir ikke seks", b.ta(g, 6.0) == 0.0, True)
    for _ in range(4):
        b.ta(g, 6.0)
    sjekk("men aldri mer enn kapasiteten", b.ta(g, 6.0) > 0, True)

    print("\nEn time uten trafikk")
    b2 = Botte(g, 0.0)
    for _ in range(5):
        b2.ta(g, 0.0)
    sjekk("bøtta er tom", b2.ta(g, 0.0) > 0, True)
    sjekk("etter en time er den full igjen", b2.full(g, 3600.0), True)

    print("\nKlassifisering")
    sjekk("/api/search", klasse_for("/api/search"), "sok")
    sjekk("/api/statistikk/operatorer",
          klasse_for("/api/statistikk/operatorer"), "tung")
    sjekk("/api/flaskehalser", klasse_for("/api/flaskehalser"), "tung")
    sjekk("/api/trains", klasse_for("/api/trains"), "sanntid")
    sjekk("/api/route/SJN:ServiceJourney:1",
          klasse_for("/api/route/SJN:ServiceJourney:1"), "sanntid")
    sjekk("et endepunkt som ikke finnes ennå",
          klasse_for("/api/noe-nytt"), "annet")
    sjekk("/static/app.js", klasse_for("/static/app.js"), "statisk")
    sjekk("/", klasse_for("/"), "statisk")

    print("\nHvem er klienten")
    global BAK_CLOUDFLARE
    ekte = BAK_CLOUDFLARE

    hode = [(b"cf-connecting-ip", b"203.0.113.7")]
    BAK_CLOUDFLARE = False
    sjekk("uten flagget leses headeren ikke",
          klient_nokkel({"client": ("127.0.0.1", 5), "headers": hode}), "127.0.0.1")

    BAK_CLOUDFLARE = True
    sjekk("fra loopback med flagget: headeren gjelder",
          klient_nokkel({"client": ("127.0.0.1", 5), "headers": hode}), "203.0.113.7")
    sjekk("::1 er også loopback",
          klient_nokkel({"client": ("::1", 5), "headers": hode}), "203.0.113.7")
    sjekk("fra en ekte adresse: headeren ignoreres",
          klient_nokkel({"client": ("198.51.100.4", 5), "headers": hode}),
          "198.51.100.4")
    sjekk("tom header faller tilbake på socketen",
          klient_nokkel({"client": ("127.0.0.1", 5),
                         "headers": [(b"cf-connecting-ip", b"  ")]}), "127.0.0.1")
    sjekk("X-Forwarded-For leses aldri",
          klient_nokkel({"client": ("127.0.0.1", 5),
                         "headers": [(b"x-forwarded-for", b"203.0.113.9")]}),
          "127.0.0.1")
    BAK_CLOUDFLARE = ekte

    print("\nTaket på antall bøtter")
    samling = Botter(maks=100)
    g_treg = Grense("treg", kapasitet=1, per_minutt=1)
    naa = 1000.0
    for n in range(100):
        samling.ta((f"10.0.0.{n}", "sok"), GRENSER["sok"], naa)
    sjekk("hundre bøtter i bruk", len(samling), 100)
    # Alle er brukt én gang av ti, altså ikke fulle. Neste nye nøkkel utløser
    # en rydding som ikke finner noe, og da tømmes hele.
    samling.ta(("10.0.1.1", "sok"), GRENSER["sok"], naa)
    sjekk("gir etter i stedet for å vokse", len(samling) <= 100, True)

    samling2 = Botter(maks=100)
    for n in range(100):
        samling2.ta((f"10.0.0.{n}", "sok"), GRENSER["sok"], naa)
    # En time senere er alle fulle igjen, og ryddingen er gratis.
    samling2.ta(("10.0.1.1", "sok"), GRENSER["sok"], naa + 3600)
    sjekk("fulle bøtter ryddes gratis", len(samling2), 1)

    print("\nDen globale Entur-bøtta")
    _nullstill()
    sjekk("kapasiteten slipper gjennom",
          all(ta_entur_kall() == 0.0 for _ in range(int(ENTUR_GEOKODER.kapasitet))),
          True)
    sjekk("så tar den i", ta_entur_kall() > 0, True)
    sjekk("status rapporterer tomt", status()["enturKvote"]["igjen"] < 1.0, True)

    print("\nDen viktige forskjellen")
    # Hundre IP-er som HVER holder seg innenfor per-IP-grensen. Per-IP-bøttene
    # slipper alle gjennom; den globale Entur-bøtta gjør det ikke. Det er
    # nettopp dette et per-IP-tak alene ikke fanger.
    _nullstill()
    per_ip_slapp = 0
    entur_slapp = 0
    naa = time.monotonic()
    for n in range(100):
        for _ in range(5):          # 5 søk hver - godt innenfor 20/min
            if _botter.ta((f"203.0.113.{n}", "sok"), GRENSER["sok"], naa) == 0.0:
                per_ip_slapp += 1
                if ta_entur_kall() == 0.0:
                    entur_slapp += 1
    sjekk("per IP: alle 500 er innenfor", per_ip_slapp, 500)
    sjekk("mot Entur: bare klumpen slapp ut",
          entur_slapp, int(ENTUR_GEOKODER.kapasitet))
    _nullstill()

    print()
    if feil:
        print(f"{feil} feil.\n")
    else:
        print("Alt stemmer.\n")
    return 1 if feil else 0


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    sys.exit(_selvtest())
