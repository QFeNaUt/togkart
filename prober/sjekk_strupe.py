"""Verifiser at ratebegrensningen virker - og at den ikke rammer ekte bruk.

Kjør:  python prober/sjekk_strupe.py         (krever uvicorn på 8000)
       python prober/sjekk_strupe.py --uten-server   (bare bøttematematikken)

En strupe har to måter å være feil på, og de er like alvorlige:

  1. **For slapp.** Den slipper gjennom det den skulle stoppet. Det ser ut
     som at alt virker, helt til Entur ratebegrenser klientnavnet ditt.
  2. **For stram.** Den stopper det den skulle sluppet gjennom. Det ser ut
     som at appen er i stykker, og det er verre - for da er den det.

Proben måler begge veier. Sjekk 1 er den viktigste av dem, og den som
faktisk speiler bruk: tre faner som laster kartet og poller i to minutter
skal IKKE møte en eneste 429. Blir de strupet, er grensene i `strupe.py`
satt for lavt, og det er en feil selv om vernet «virker».

Sjekk 4 er den som ikke kan testes gjennom HTTP i det hele tatt, og som er
hele grunnen til at `strupe.py` finnes ved siden av Cloudflare-reglene: at
per-IP-grenser ikke summerer seg til et tak mot Entur.

Proben sender ekte forespørsler til din egen server. Den snakker aldri med
Entur - `/api/search` går bare videre til geocoderen ved cachebom, og
søkeordene her er de samme hver gang.
"""

import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

import strupe
from strupe import ENTUR_GEOKODER, GRENSER, Botte, Botter, klasse_for, klient_nokkel

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.getenv("TOGKART_BASE", "http://127.0.0.1:8000")
FEIL = 0


def melding(ok: bool | None, tekst: str, detalj: str = "") -> None:
    global FEIL
    merke = {True: "OK  ", False: "FEIL", None: "    "}[ok]
    print(f"  {merke}  {tekst}")
    if detalj:
        for linje in detalj.split("\n"):
            print(f"        {linje}")
    if ok is False:
        FEIL += 1


def _hvil(klasse: str) -> None:
    """Vent til bøtta for `klasse` er full igjen.

    Uten dette måler sjekk N restene av sjekk N-1, og hele proben blir en
    kjede der bare den første målingen betyr noe.
    """
    g = GRENSER[klasse]
    time.sleep(min(20.0, g.kapasitet / g.per_sekund))


# ---------------------------------------------------------------------------

def sjekk_1_ekte_bruk(c: httpx.Client) -> None:
    """Det viktigste: en strupe som rammer vanlige folk er en feil."""
    print("\n1. Ekte bruk skal gå uhindret gjennom")

    _hvil("statisk")
    koder = []

    for _ in range(3):                      # tre faner laster kartet
        for sti in ("/index.html", "/app.css", "/theme.css", "/app.js",
                    "/hovedbaner.geojson", "/jernbanenett.geojson",
                    "/stasjoner.geojson"):
            koder.append(c.get(BASE + sti).status_code)
        koder.append(c.get(BASE + "/api/trains").status_code)
        koder.append(c.get(BASE + "/api/avvik").status_code)
        koder.append(c.get(BASE + "/api/statistikk/operatorer?dager=7").status_code)
        koder.append(c.get(BASE + "/api/statistikk/rush?dager=14").status_code)
        koder.append(c.get(BASE + "/api/flaskehalser?dager=7").status_code)

    for _ in range(8):                      # to minutter med polling, x3 faner
        for _ in range(3):
            koder.append(c.get(BASE + "/api/trains").status_code)

    telling = Counter(koder)
    melding(
        429 not in telling,
        f"{len(koder)} forespørsler fra tre faner: {dict(telling)}",
        "" if 429 not in telling else
        f"{telling[429]} ble strupet. Grensene i strupe.py er for lave -\n"
        "dette er hva en helt vanlig bruker gjør.",
    )


def sjekk_2_hamring(c: httpx.Client) -> None:
    """Og en strupe som ikke tar i, verner ingenting."""
    print("\n2. Hamring skal stoppes")

    for klasse, sti, antall in (
        ("sok", "/api/search?q=proeve", 40),
        ("tung", "/api/statistikk/operatorer?dager=7", 50),
    ):
        _hvil(klasse)
        g = GRENSER[klasse]
        koder = [c.get(BASE + sti + f"&n={n}" if "?" in sti else BASE + sti).status_code
                 for n in range(antall)]
        telling = Counter(koder)
        slapp = telling.get(200, 0) + telling.get(503, 0)

        melding(
            telling.get(429, 0) > 0,
            f"{klasse}: {antall} kall ga {dict(telling)}",
            f"grensen er {g.per_minutt:.0f}/min med klump {g.kapasitet:.0f}",
        )
        # Klumpen pluss påfyllet under kjøringen. Litt slark, for løkka tar
        # tid og bøtta fylles mens den går.
        melding(
            slapp <= g.kapasitet + 5,
            f"{klasse}: {slapp} slapp gjennom, klumpen er {g.kapasitet:.0f}",
        )


def sjekk_3_svaret(c: httpx.Client) -> None:
    """Et 429 uten Retry-After er en vegg uten skilt."""
    print("\n3. Avvisningen skal være til å forstå")

    _hvil("sok")
    svar = None
    for n in range(40):
        r = c.get(BASE + f"/api/search?q=hamring{n}")
        if r.status_code == 429:
            svar = r
            break

    if svar is None:
        melding(False, "Fikk aldri en 429 å se på")
        return

    melding("retry-after" in svar.headers,
            f"Retry-After = {svar.headers.get('retry-after')}")
    melding(svar.headers.get("cache-control") == "no-store",
            f"Cache-Control = {svar.headers.get('cache-control')}",
            "Uten no-store kan Cloudflare cache avvisningen og servere den\n"
            "til alle andre." if svar.headers.get("cache-control") != "no-store" else "")
    try:
        kropp = svar.json()
        melding("error" in kropp, f"Kroppen er JSON: {kropp}")
    except ValueError:
        melding(False, "Kroppen er ikke JSON")


def sjekk_4_entur_taket() -> None:
    """Det per-IP-grenser ikke kan uttrykke.

    Dette kan ikke testes gjennom HTTP: for å komme dit måtte proben faktisk
    sendt tusen søk fra hundre adresser, og de som slapp gjennom ville gått
    videre til Entur - altså brukt av den kvoten sjekken skal verne. Vi kaller
    bøttene direkte i stedet.
    """
    print("\n4. Taket mot Entur - det Cloudflare ikke kan sette")

    strupe._nullstill()
    naa = time.monotonic()

    per_ip_slapp = 0
    entur_slapp = 0
    for n in range(100):                    # hundre adresser
        for _ in range(5):                  # fem søk hver - innenfor 20/min
            if strupe._botter.ta((f"203.0.113.{n}", "sok"), GRENSER["sok"], naa) == 0.0:
                per_ip_slapp += 1
                if strupe.ta_entur_kall() == 0.0:
                    entur_slapp += 1

    melding(
        per_ip_slapp == 500,
        f"Per IP: {per_ip_slapp} av 500 er innenfor grensen",
        "Hver enkelt adresse oppfører seg pent. En per-IP-regel - i\n"
        "Cloudflare eller her - ser ingenting galt.",
    )
    melding(
        entur_slapp <= ENTUR_GEOKODER.kapasitet,
        f"Mot Entur: {entur_slapp} slapp ut, taket er "
        f"{ENTUR_GEOKODER.kapasitet:.0f} i klumpen "
        f"({ENTUR_GEOKODER.per_minutt:.0f}/min)",
        f"Uten denne bøtta ville {per_ip_slapp} kall gått til geocoderen\n"
        "under ditt ET-Client-Name, alle innenfor regelen.",
    )
    strupe._nullstill()


def sjekk_5_klassifisering() -> None:
    """Et endepunkt som faller i feil klasse er strupet med feil tall."""
    print("\n5. Klassifisering av stier")

    ventet = {
        "/api/search": "sok",
        "/api/statistikk/operatorer": "tung",
        "/api/statistikk/rush": "tung",
        "/api/flaskehalser": "tung",
        "/api/trains": "sanntid",
        "/api/avvik": "sanntid",
        "/api/health": "sanntid",
        "/api/route/SJN:ServiceJourney:1": "sanntid",
        "/api/et-endepunkt-som-ikke-finnes": "annet",
        "/": "statisk",
        "/app.js": "statisk",
        "/jernbanenett.geojson": "statisk",
    }
    galt = {s: klasse_for(s) for s, k in ventet.items() if klasse_for(s) != k}
    melding(
        not galt,
        f"{len(ventet)} stier havner i riktig klasse",
        "\n".join(f"{s}: fikk {f}, ventet {ventet[s]}" for s, f in galt.items()),
    )

    # Nye endepunkter skal aldri være ustrupet ved et uhell.
    melding(
        klasse_for("/api/noe-helt-nytt") == "annet",
        "Et framtidig /api/-endepunkt er strupet fra første dag",
    )


def sjekk_6_klienten() -> None:
    """Bak tunnelen er `scope['client']` alltid 127.0.0.1."""
    print("\n6. Hvem forespørselen telles på")

    hode = [(b"cf-connecting-ip", b"203.0.113.7")]
    ekte = strupe.BAK_CLOUDFLARE
    try:
        strupe.BAK_CLOUDFLARE = False
        melding(
            klient_nokkel({"client": ("127.0.0.1", 1), "headers": hode}) == "127.0.0.1",
            "Uten TOGKART_BAK_CLOUDFLARE leses headeren ikke",
        )
        strupe.BAK_CLOUDFLARE = True
        melding(
            klient_nokkel({"client": ("127.0.0.1", 1), "headers": hode}) == "203.0.113.7",
            "Fra loopback med flagget: CF-Connecting-IP gjelder",
        )
        melding(
            klient_nokkel({"client": ("198.51.100.4", 1), "headers": hode})
            == "198.51.100.4",
            "Fra en ekte adresse: headeren ignoreres",
            "Ellers kunne hvem som helst som når porten direkte sendt en ny\n"
            "adresse per forespørsel og aldri møtt en grense.",
        )
        melding(
            klient_nokkel({"client": ("127.0.0.1", 1),
                           "headers": [(b"x-forwarded-for", b"203.0.113.9")]})
            == "127.0.0.1",
            "X-Forwarded-For leses aldri",
        )
    finally:
        strupe.BAK_CLOUDFLARE = ekte

    if not strupe.BAK_CLOUDFLARE:
        melding(
            None,
            "TOGKART_BAK_CLOUDFLARE er IKKE satt her",
            "Riktig lokalt. I produksjon bak tunnelen MÅ den settes, ellers\n"
            "teller strupen hele internett i én bøtte. Se drift/cloudflare.md.",
        )


def sjekk_7_minnetak() -> None:
    """Telleren skal ikke vokse med antall besøkende."""
    print("\n7. Taket på antall bøtter")

    samling = Botter(maks=200)
    naa = 1000.0
    for n in range(500):
        samling.ta((f"10.{n // 256}.0.{n % 256}", "sok"), GRENSER["sok"], naa)
    melding(
        len(samling) <= 200,
        f"500 adresser gav {len(samling)} bøtter (tak: 200)",
        "Nøkkelen er brukerstyrt. Uten tak er dette ikke en teller, men en\n"
        "lekkasje som vokser med antall unike besøkende.",
    )

    # En full bøtte er bit for bit den samme som en fersk. Å kaste den er
    # gratis, og det er den ryddingen som skal skje først.
    samling2 = Botter(maks=100)
    for n in range(100):
        samling2.ta((f"10.0.0.{n}", "sok"), GRENSER["sok"], naa)
    samling2.ta(("10.0.1.1", "sok"), GRENSER["sok"], naa + 3600)
    melding(
        len(samling2) == 1,
        "Bøtter som er fulle igjen ryddes gratis",
    )


def main() -> int:
    uten_server = "--uten-server" in sys.argv

    print("\nSjekk av ratebegrensningen")
    print(f"  strupe.PAA = {strupe.PAA}, BAK_CLOUDFLARE = {strupe.BAK_CLOUDFLARE}")

    if not strupe.PAA:
        print("\n  Strupen er slått AV (TOGKART_STRUPE). Ingenting å måle.\n")
        return 1

    if not uten_server:
        try:
            with httpx.Client(timeout=30) as c:
                c.get(BASE + "/api/health")
                sjekk_1_ekte_bruk(c)
                sjekk_2_hamring(c)
                sjekk_3_svaret(c)
        except httpx.HTTPError as exc:
            print(f"\n  Fikk ikke kontakt med {BASE}: {exc}")
            print("  Start uvicorn, eller kjør med --uten-server.\n")
            return 1
    else:
        print("\n  (--uten-server: hopper over sjekk 1-3)")

    sjekk_4_entur_taket()
    sjekk_5_klassifisering()
    sjekk_6_klienten()
    sjekk_7_minnetak()

    print()
    if FEIL:
        print(f"{FEIL} funn som bør ses på.\n")
        return 1
    print("Strupen verner uten å være i veien.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
