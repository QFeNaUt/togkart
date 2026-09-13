"""
lagstasjoner.py — bygg `static/stasjoner.geojson` fra Enturs stoppestedsregister.

Hvorfor dette skriptet finnes
-----------------------------
Bakgrunnskartet tegner byer, ikke stasjoner. En by-prikk står der rådhuset
ligger; en stasjon står der sporet går. På Bergensbanen er det halvannen
kilometer mellom de to, og et kart som viser tog har bruk for det andre punktet.

Samme deling som `lagbaner.py`: dette er et **byggeskript**, ikke en del av
appen. Kjør det når du vil, sjekk resultatet inn i git, og la appen servere en
statisk fil. Ingen API-kall i drift.

Fra fem håndplukkede til samtlige
---------------------------------
Første utgave hadde fem stasjoner skrevet inn for hånd, hentet én og én fra
geocoderen. Den ruten skalerer ikke til tre hundre, og den svarer heller ikke
på spørsmålet «hvilke stasjoner FINNES det». Nå spørres Journey Planner om
det direkte, i to steg:

  1. `stopPlacesByBbox(filterByInUse: true)` gir hvert stoppested i bruk innen
     boksen — omtrent 50 000. De med `rail` blant transportmodusene er 446.
  2. `estimatedCalls` på hvert av dem sier hvem som faktisk kjører dit det
     neste døgnet.

Hva «aktiv jernbanestrekning» betyr her
---------------------------------------
En stasjon er med hvis **minst ett av togselskapene kartet viser tog for** har
en avgang derfra: Vy, Flytoget, Go-Ahead eller SJ Nord (`TOGSELSKAP`). Ikke
«ligger i Norge», og ikke «finnes i registeret».

Regelen er valgt fordi den er den eneste som kan leses ut av dataene i stedet
for å tegnes med en grense noen må vedlikeholde, og fordi den gir nøyaktig de
stasjonene kartets egne tog kan stå på. Tre følger er verdt å vite om:

  - **Museumsbaner faller ut.** Grovane (Setesdalsbanen), Krøderen
    (Krøderbanen) og Løkken (Thamshavnbanen) står i registeret, men har ingen
    avganger. De er ikke aktive strekninger i denne betydningen.
  - **Svenske stasjoner på norske ruter blir med.** Charlottenberg, Karlstad
    og Stockholm C ligger på F1, som Vy Tåg kjører. Toget kartet tegner kan
    stoppe der, så stasjonen hører hjemme i filen.
  - **Rene svenske strekninger faller ut.** Bastuträsk og Abisko har bare
    SJ AB (`SJV`), og forsvinner.

`operatorer` skrives på hver stasjon, så du kan se *hvorfor* den kom med.

Bergensbanen ligger under `VYG:Authority:VYT`, ikke under `VYG:Authority:VY`.
Filtreres det på myndighet i stedet for kodeområde, forsvinner Bergen, Voss,
Finse og Geilo ut av kartet. Derfor kodeområde.

Feltene i filen
---------------
  - `navn`       — det som skal stå i kartet.
  - `id`         — NSR-ID-en. Nøkkelen mot resten av Entur: stoppetider,
                   avganger, `estimatedCalls`. Den dagen en stasjon skal vise
                   noe mer enn sin egen posisjon, er koblingen allerede der.
  - `viktighet`  — 1 for oversiktsstasjonene i `OVERSIKTSSTASJONER`, 2 for
                   resten. Det er dette `app.js` deler zoomnivå på.
  - `avganger`   — togavganger målt over ett døgn, ved bygging. Et øyeblikks-
                   bilde, ikke en konstant: en søndag gir andre tall enn en
                   tirsdag. Ligger der fordi det er den ærligste kilden til en
                   finere inndeling enn `viktighet` den dagen tre hundre
                   prikker viser seg å være for mange.
  - `operatorer` — kodeområdene som hadde avganger derfra.
  - `kilde`      — hvor raden kom fra, som `positionMethod` i sjnord.py.

Kjøring
-------
    python lagstasjoner.py                 # hent alle og skriv filen
    python lagstasjoner.py --selvtest      # tolkning og filtrering, uten nett
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

JOURNEY_URL = "https://api.entur.io/journey-planner/v3/graphql"

UT = Path("static/stasjoner.geojson")

# Sør, vest, nord, øst — samme boks som lagbaner.py bruker mot Overpass.
# Her er den både søkeområde og sperre: en koordinat utenfor boksen er ikke et
# stoppested vi spurte om, uansett hva det heter.
NORGE_BBOX = (57.0, 4.0, 72.0, 32.0)

# Kodeområdene til togselskapene kartet viser tog for. Samme fire som i
# `README.md` sin kildetabell og i `analyse.OPERATORNAVN`. Har ingen av dem en
# avgang fra stasjonen det neste døgnet, er ikke stasjonen en del av det
# togtilbudet kartet tegner.
#
# Kodeområde og ikke myndighet, med vilje: Bergensbanen kjøres av
# `VYG:Authority:VYT` og Oslo-Stockholm av `VYG:Authority:TAG`, mens resten av
# Vy ligger under `VYG:Authority:VY`. Filtreres det på myndighet, forsvinner
# halve Vestlandet.
TOGSELSKAP = {"VYG", "FLT", "GOA", "SJN"}

# Stasjonene som skal være synlige når hele Norge er i bildet — `viktighet` 1.
# Landsdelshovedsteder med endestasjonsfunksjon; en redaksjonell liste, ikke en
# rangering etter trafikk. Oslo S har flest avganger i landet, Bodø har få, og
# begge hører hjemme på et oversiktskart av samme grunn: de forteller hvor
# jernbanenettet ender.
#
# NSR-ID og ikke navn, fordi ID-en er det som ikke endrer seg. Mangler en av
# dem i svaret fra Entur, sier skriptet fra i stedet for å skrive en fil der
# Oslo S stille har falt til viktighet 2.
OVERSIKTSSTASJONER = {
    "NSR:StopPlace:59872": "Oslo S",
    "NSR:StopPlace:59983": "Bergen stasjon",
    "NSR:StopPlace:61291": "Stavanger stasjon",
    "NSR:StopPlace:59977": "Trondheim S",
    "NSR:StopPlace:58952": "Bodø stasjon",
}

# Hvor mange stoppesteder vi spør om avganger for per kall. 446 stasjoner i
# bolker på 20 er 23 kall. Større bolker gir færre kall, men Entur bruker
# lengre tid per svar og en timeout koster hele bolken.
BOLK = 20

# Ett døgn, og nok avganger til at Oslo S ikke treffer taket. Vinduet er
# grunnen til at `avganger` er sammenlignbart mellom stasjoner: alle måles over
# like lang tid.
#
# Taket er ikke kosmetikk. Treffer en stasjon det, er `avganger` sensurert -
# tallet sier «minst så mange», ikke «så mange» - og da er de travleste
# stasjonene plutselig like travle. Første forsøk sto på 400, og fem
# Oslo-stasjoner lå akkurat der. Byggingen sier fra hvis det skjer igjen.
TIDSVINDU_SEKUNDER = 86400
MAKS_AVGANGER = 1200


# ---------------------------------------------------------------------------
# 1. Henting
# ---------------------------------------------------------------------------

def _klient() -> httpx.Client:
    navn = os.getenv("ET_CLIENT_NAME", "").strip()
    if not navn:
        print("ADVARSEL: ET_CLIENT_NAME er ikke satt i .env", file=sys.stderr)
    return httpx.Client(timeout=180.0, headers={"ET-Client-Name": navn})


def _sporr(klient: httpx.Client, sporring: str, variabler: dict | None = None) -> dict:
    """POST mot Journey Planner. Sjekker `errors` selv.

    GraphQL svarer 200 på en spørring som feilet - feilen ligger i kroppen, og
    `data` er da null. Samme grunn som `punktlighet._post` har den sjekken:
    uten den ser en avvist spørring ut som et tomt register.
    """
    svar = klient.post(JOURNEY_URL, json={"query": sporring,
                                          "variables": variabler or {}})
    if svar.status_code >= 400:
        raise RuntimeError(f"Journey Planner {svar.status_code}: {svar.text[:200]}")

    kropp = svar.json()
    if kropp.get("errors"):
        beskjed = "; ".join(f.get("message", "?") for f in kropp["errors"])
        raise RuntimeError(f"Journey Planner avviste spørringen: {beskjed[:300]}")
    return kropp["data"]


SPORRING_STOPPESTEDER = """
{
  stopPlacesByBbox(minimumLatitude: %f, minimumLongitude: %f,
                   maximumLatitude: %f, maximumLongitude: %f,
                   filterByInUse: true) {
    id
    name
    latitude
    longitude
    transportMode
  }
}
"""

SPORRING_AVGANGER = """
query($ids: [String]!, $antall: Int!, $vindu: Int!) {
  stopPlaces(ids: $ids) {
    id
    estimatedCalls(numberOfDepartures: $antall, timeRange: $vindu,
                   whiteListedModes: [rail]) {
      serviceJourney { line { authority { id } } }
    }
  }
}
"""


def hent_stoppesteder(klient: httpx.Client) -> list[dict]:
    """Alle stoppesteder i bruk innenfor boksen som har jernbane blant seg."""
    sor, vest, nord, ost = NORGE_BBOX
    data = _sporr(klient, SPORRING_STOPPESTEDER % (sor, vest, nord, ost))
    alle = data.get("stopPlacesByBbox") or []
    jernbane = [s for s in alle if "rail" in _modus(s)]
    print(f"  {len(alle)} stoppesteder i bruk, {len(jernbane)} med jernbane")
    return jernbane


def hent_avganger(klient: httpx.Client, ider: list[str]) -> dict[str, dict]:
    """{stoppestedId: {"avganger": int, "operatorer": set[str]}} for hver ID.

    Stasjoner uten svar får en tom oppføring i stedet for å mangle. Kallerne
    skal kunne slå opp hvilken som helst ID de sendte inn.
    """
    ut: dict[str, dict] = {i: {"avganger": 0, "operatorer": set()} for i in ider}

    for start in range(0, len(ider), BOLK):
        bolk = ider[start:start + BOLK]
        data = _sporr(klient, SPORRING_AVGANGER, {
            "ids": bolk, "antall": MAKS_AVGANGER, "vindu": TIDSVINDU_SEKUNDER,
        })

        for stoppested in data.get("stopPlaces") or []:
            if not stoppested:
                continue
            avganger = stoppested.get("estimatedCalls") or []
            ut[stoppested["id"]] = {
                "avganger": len(avganger),
                "operatorer": {
                    kode for kode in (_kodeomrade(a) for a in avganger) if kode
                },
            }

        print(f"  avganger: {min(start + BOLK, len(ider))}/{len(ider)}",
              end="\r", flush=True)
        # Entur ber om at klienter er snille. En femtedels sekund mellom
        # bolkene er ingenting for et byggeskript som kjøres én gang i uka.
        time.sleep(0.2)

    print(" " * 40, end="\r")
    return ut


# ---------------------------------------------------------------------------
# 2. Tolkning og filtrering — rene funksjoner, testes uten nett
# ---------------------------------------------------------------------------

def _modus(stoppested: dict) -> list[str]:
    """`transportMode` er en liste for noen stoppesteder og en streng for
    andre. Begge former er gyldige i svaret; bare den ene er lett å søke i."""
    modus = stoppested.get("transportMode")
    if modus is None:
        return []
    return list(modus) if isinstance(modus, list) else [modus]


def _kodeomrade(avgang: dict) -> str:
    """`VYG:Authority:VYT` -> `VYG`. Tom streng hvis noe mangler underveis."""
    linje = ((avgang.get("serviceJourney") or {}).get("line") or {})
    myndighet = (linje.get("authority") or {}).get("id") or ""
    return myndighet.split(":", 1)[0]


def tolk_stoppested(stoppested: dict, trafikk: dict) -> tuple[dict | None, list[str]]:
    """Gjør ett stoppested om til en GeoJSON-feature, eller til None.

    Returnerer (feature, merknader). `None` betyr at stasjonen ikke hører
    hjemme i filen - ikke at noe gikk galt. Merknader er ting du skal se, ikke
    ting som skal stoppe byggingen.
    """
    merknader: list[str] = []

    lat = stoppested.get("latitude")
    lon = stoppested.get("longitude")
    if lat is None or lon is None:
        return None, [f"{stoppested.get('name') or stoppested.get('id')}: uten koordinat"]

    sor, vest, nord, ost = NORGE_BBOX
    if not (vest < lon < ost and sor < lat < nord):
        # Den dyreste feilen i hele prosjektet, i sin registerutgave: hadde vi
        # lest latitude inn i lon-plassen, ville hver stasjon havnet i Somalia
        # og ingenting i JSON-en røpet det. Sperren står selv om vi spurte med
        # nøyaktig denne boksen - da fanger den at feltene byttet plass.
        return None, [f"{stoppested.get('name')}: ({lon}, {lat}) ligger utenfor boksen"]

    operatorer = set(trafikk.get("operatorer") or set())
    norske = sorted(operatorer & TOGSELSKAP)
    if not norske:
        # Ikke en merknad. Museumsbaner og rene svenske strekninger skal falle
        # ut stille - det er regelen som virker, ikke en feil som oppsto.
        return None, []

    nsr = stoppested.get("id") or ""
    navn = stoppested.get("name") or nsr

    if nsr in OVERSIKTSSTASJONER and navn != OVERSIKTSSTASJONER[nsr]:
        merknader.append(
            f"navn hos Entur er «{navn}», oversiktslista sier «{OVERSIKTSSTASJONER[nsr]}»"
        )

    return (
        {
            "type": "Feature",
            "properties": {
                "navn": navn,
                "id": nsr,
                "viktighet": 1 if nsr in OVERSIKTSSTASJONER else 2,
                "avganger": trafikk.get("avganger", 0),
                # Hvorfor stasjonen kom med. Samme tanke som `kilde` i
                # hovedbaner.geojson og `positionMethod` i sjnord.py: du skal
                # kunne se på dataene hvordan de ble til.
                "operatorer": norske,
                "kilde": "journey-planner",
            },
            "geometry": {
                "type": "Point",
                # Fem desimaler er drøyt én meter. Stasjoner flytter seg ikke,
                # og filen skal være liten når den har tre hundre av dem.
                "coordinates": [round(lon, 5), round(lat, 5)],
            },
        },
        merknader,
    )


def bygg_features(stoppesteder: list[dict],
                  trafikk: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Kjør `tolk_stoppested` over hele lista. Sorterer på navn, slik at to
    kjøringer av samme register gir to like filer - da viser `git diff` hva
    som faktisk endret seg, og ikke at rekkefølgen ble en annen."""
    features, merknader = [], []

    for stoppested in stoppesteder:
        feature, egne = tolk_stoppested(
            stoppested, trafikk.get(stoppested.get("id") or "", {})
        )
        merknader.extend(egne)
        if feature:
            features.append(feature)

    features.sort(key=lambda f: f["properties"]["navn"])
    return features, merknader


# ---------------------------------------------------------------------------
# 3. Bygg filen
# ---------------------------------------------------------------------------

def bygg() -> None:
    with _klient() as klient:
        print("Henter stoppesteder i bruk …")
        stoppesteder = hent_stoppesteder(klient)
        if not stoppesteder:
            print("Ingen stoppesteder med jernbane — skriver ikke fil.",
                  file=sys.stderr)
            raise SystemExit(1)

        print(f"Henter avganger for {len(stoppesteder)} stasjoner "
              f"(døgn, bolker på {BOLK}) …")
        trafikk = hent_avganger(klient, [s["id"] for s in stoppesteder])

    features, merknader = bygg_features(stoppesteder, trafikk)

    if not features:
        print("Ingen stasjoner overlevde filteret — skriver ikke fil.",
              file=sys.stderr)
        raise SystemExit(1)

    for merknad in merknader:
        print(f"  MERK: {merknad}", file=sys.stderr)

    # Oversiktsstasjonene er de eneste som er synlige når hele Norge er i
    # bildet. Faller en av dem ut - fordi Entur endret ID, eller fordi den
    # ikke hadde avganger i vinduet - blir kartet tomt på zoom 4 uten at noe
    # ser galt ut. Derfor sjekkes de eksplisitt.
    funnet = {f["properties"]["id"] for f in features}
    borte = [f"{navn} ({nsr})" for nsr, navn in OVERSIKTSSTASJONER.items()
             if nsr not in funnet]
    if borte:
        print(f"\nADVARSEL: oversiktsstasjoner mangler i filen: "
              f"{', '.join(borte)}", file=sys.stderr)
        print("  Zoom 4 vil stå uten stasjoner. Er NSR-ID-en byttet?",
              file=sys.stderr)

    ider = [f["properties"]["id"] for f in features]
    duplikater = {i for i in ider if ider.count(i) > 1}
    if duplikater:
        print(f"\nADVARSEL: samme stoppested flere ganger: "
              f"{', '.join(sorted(duplikater))}", file=sys.stderr)

    UT.parent.mkdir(parents=True, exist_ok=True)
    UT.write_text(
        json.dumps({"type": "FeatureCollection", "features": features},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    per_selskap = Counter()
    for f in features:
        for kode in f["properties"]["operatorer"]:
            per_selskap[kode] += 1

    print(f"\nSkrev {UT} — {len(features)} stasjoner, "
          f"{UT.stat().st_size / 1024:.1f} kB")
    print(f"  viktighet 1 (synlige på zoom 4): "
          f"{sum(1 for f in features if f['properties']['viktighet'] == 1)}")
    print("  stasjoner per selskap: "
          + ", ".join(f"{k} {n}" for k, n in per_selskap.most_common()))

    travleste = sorted(features, key=lambda f: -f["properties"]["avganger"])[:5]
    print("  flest avganger i døgnet: "
          + ", ".join(f"{f['properties']['navn']} {f['properties']['avganger']}"
                      for f in travleste))

    ved_taket = [f["properties"]["navn"] for f in features
                 if f["properties"]["avganger"] >= MAKS_AVGANGER]
    if ved_taket:
        print(f"\nADVARSEL: {len(ved_taket)} stasjoner traff taket på "
              f"{MAKS_AVGANGER} avganger: {', '.join(ved_taket[:6])}",
              file=sys.stderr)
        print("  `avganger` er sensurert for disse - de ser like travle ut nå. "
              "Hev MAKS_AVGANGER.", file=sys.stderr)

    # Samme forbehold som i lagbaner.py: filen skrives fra bunnen hver gang,
    # så en stasjon som falt ut er borte fra kartet, ikke bare fra kjøringen.
    print(f"  {len(stoppesteder) - len(features)} stoppesteder falt fra "
          f"(ingen avganger fra {', '.join(sorted(TOGSELSKAP))})")


# ---------------------------------------------------------------------------
# 4. Selvtest
# ---------------------------------------------------------------------------

def _sted(nsr: str, navn: str, lon: float, lat: float, modus=("rail",)) -> dict:
    return {"id": nsr, "name": navn, "longitude": lon, "latitude": lat,
            "transportMode": list(modus)}


def selvtest() -> None:
    oslo_s = _sted("NSR:StopPlace:59872", "Oslo S", 10.75305, 59.91036)
    vy = {"avganger": 380, "operatorer": {"VYG", "FLT"}}

    # Vanlig stasjon med norsk trafikk.
    feature, merknader = tolk_stoppested(oslo_s, vy)
    assert feature is not None, "Oslo S skulle vært med"
    assert not merknader, f"rent treff ga merknader: {merknader}"
    assert feature["properties"]["viktighet"] == 1, "Oslo S skal ha viktighet 1"
    assert feature["properties"]["operatorer"] == ["FLT", "VYG"], \
        feature["properties"]["operatorer"]
    assert feature["properties"]["avganger"] == 380
    assert feature["geometry"]["coordinates"] == [10.75305, 59.91036]

    # En stasjon utenfor oversiktslista får viktighet 2.
    feature, _ = tolk_stoppested(
        _sted("NSR:StopPlace:441", "Auli stasjon", 11.3407, 60.0568),
        {"avganger": 24, "operatorer": {"VYG"}},
    )
    assert feature["properties"]["viktighet"] == 2

    # Bare svensk trafikk: ut, og stille.
    feature, merknader = tolk_stoppested(
        _sted("NSR:StopPlace:63620", "Bastuträsk station", 20.0, 64.7),
        {"avganger": 14, "operatorer": {"SJV"}},
    )
    assert feature is None, "rent svensk stasjon skulle vært filtrert bort"
    assert not merknader, "filtrering er ikke en merknad"

    # Museumsbane: i registeret, men ingen kjører dit.
    feature, _ = tolk_stoppested(
        _sted("NSR:StopPlace:1", "Krøderen stasjon", 9.8, 60.2),
        {"avganger": 0, "operatorer": set()},
    )
    assert feature is None, "stasjon uten avganger skulle vært filtrert bort"

    # Svensk stasjon på norsk rute: skal MED. Vy Tåg kjører F1 dit.
    feature, _ = tolk_stoppested(
        _sted("NSR:StopPlace:2", "Charlottenberg station", 12.2, 59.88),
        {"avganger": 6, "operatorer": {"VYG", "SJV"}},
    )
    assert feature is not None, "Charlottenberg skulle vært med — Vy kjører dit"
    assert feature["properties"]["operatorer"] == ["VYG"], \
        "bare selskapene kartet viser skal stå i operatorer"

    print("filtrering   OK   norsk med, rent svensk ut, museumsbane ut, "
          "grensestasjon med")

    # Byttet lat og lon. Skal aldri slippe gjennom.
    for lon, lat in ((59.91036, 10.75305), (0.0, 0.0)):
        feature, merknader = tolk_stoppested(
            _sted("NSR:StopPlace:3", "Bytting", lon, lat), vy
        )
        assert feature is None, f"({lon}, {lat}) skulle vært avvist"
        assert merknader, "avvist koordinat skal si fra"

    # Et stoppested uten koordinat er ikke et sted.
    feature, merknader = tolk_stoppested(
        {"id": "NSR:StopPlace:4", "name": "Uten sted", "transportMode": ["rail"]}, vy
    )
    assert feature is None and merknader

    print("koordinater  OK   byttet lat/lon avvist, manglende koordinat avvist")

    # transportMode kommer som liste for noen og streng for andre.
    assert _modus({"transportMode": ["rail", "bus"]}) == ["rail", "bus"]
    assert _modus({"transportMode": "rail"}) == ["rail"]
    assert _modus({}) == []

    # Kodeområdeuttrekket, inkludert de tomme formene.
    assert _kodeomrade({"serviceJourney": {"line": {"authority": {"id": "VYG:Authority:VYT"}}}}) == "VYG"
    assert _kodeomrade({"serviceJourney": {"line": {"authority": None}}}) == ""
    assert _kodeomrade({}) == ""

    print("tolkning     OK   transportMode som liste og streng, "
          "kodeområde med tomme ledd")

    # Sortering og duplikatfrihet i lista som skrives.
    steder = [
        _sted("NSR:StopPlace:10", "Ås stasjon", 10.79, 59.66),
        _sted("NSR:StopPlace:11", "Arna stasjon", 5.46, 60.42),
        _sted("NSR:StopPlace:12", "Bare svensk", 20.0, 64.0),
    ]
    trafikk = {
        "NSR:StopPlace:10": {"avganger": 40, "operatorer": {"VYG"}},
        "NSR:StopPlace:11": {"avganger": 30, "operatorer": {"VYG"}},
        "NSR:StopPlace:12": {"avganger": 3, "operatorer": {"SJV"}},
    }
    features, _ = bygg_features(steder, trafikk)
    assert [f["properties"]["navn"] for f in features] == ["Arna stasjon", "Ås stasjon"], \
        [f["properties"]["navn"] for f in features]

    print(f"bygg         OK   {len(features)} av {len(steder)} med, sortert på navn")

    # Oversiktslista selv.
    assert len(set(OVERSIKTSSTASJONER)) == len(OVERSIKTSSTASJONER)
    assert all(nsr.startswith("NSR:StopPlace:") for nsr in OVERSIKTSSTASJONER)
    print(f"oversiktsliste OK {len(OVERSIKTSSTASJONER)} stasjoner, unike NSR-ID-er")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selvtest", action="store_true",
                    help="test tolkning og filtrering, uten nett")
    args = ap.parse_args()

    if args.selvtest:
        selvtest()
    else:
        bygg()
