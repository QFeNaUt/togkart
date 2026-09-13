"""Virker sporgeometrien mot ekte Entur-data?

Fire spørsmål, i den rekkefølgen de er verdt å stille. Hvert steg stopper hvis
det forrige feilet, så du slipper å lete i feil ende av kjeden.

    1. Finnes feltet pointsOnLink på ServiceJourney i skjemaet?
    2. Kommer det data i det, eller bare tomme strenger?
    3. Ligger stasjonene faktisk på traseen vi får?
    4. Hvor langt flytter fiksen togene som kjører akkurat nå?

Spørsmål 1 alene er ikke nok. Se lærdom 4 i docs/erfaringer.md: skriptet som
kodespaket fantes, ikke om det kjørte tog, meldte «SJ ER I FEEDEN» om tre
busser. Et felt kan finnes i skjemaet og være null for hver eneste tur.

Kjør fra prosjektroten:  python prober/sjekk_geometri.py
"""

import asyncio
import logging
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

# Python legger mappen skriptet ligger i på søkestien, ikke prosjektroten.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from dotenv import load_dotenv

import sjnord
from sporgeometri import avstand_m, bygg_trase, decode_polyline, offsets_for_stopp

SKJEMA = """
{
  __type(name: "ServiceJourney") {
    fields { name }
  }
}
"""


def kort(journey_id: str) -> str:
    """SJN:ServiceJourney:431_49904-R -> tog 431"""
    nummer = sjnord._journey_number(journey_id)
    return f"tog {nummer}" if nummer else journey_id[-20:]


def linje(tekst: str, verdi) -> None:
    print(f"   {tekst:.<45} {verdi}")


# ---------------------------------------------------------------------------
# 1. Skjema
# ---------------------------------------------------------------------------

async def sjekk_skjema(client_name: str) -> bool:
    print("1. Skjema")

    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.post(
            sjnord.JOURNEY_URL,
            json={"query": SKJEMA},
            headers={"ET-Client-Name": client_name},
        )
        response.raise_for_status()
        payload = response.json()

    felter = ((payload.get("data") or {}).get("__type") or {}).get("fields") or []
    navn = {f["name"] for f in felter}

    if not navn:
        linje("kunne ikke lese ServiceJourney", "FEIL")
        return False

    finnes = "pointsOnLink" in navn
    linje("pointsOnLink på ServiceJourney", "FINNES" if finnes else "MANGLER")

    if not finnes:
        beslektet = sorted(n for n in navn if "point" in n.lower() or "link" in n.lower())
        linje("beslektede felter", ", ".join(beslektet) or "ingen")
        print("\n   Skjemaet har endret seg. Se etter geometrien på JourneyPattern,")
        print("   eller hent den via en trip-spørring der hvert leg har pointsOnLink.")

    return finnes


# ---------------------------------------------------------------------------
# 2. Data
# ---------------------------------------------------------------------------

def sjekk_data(journeys: dict) -> list[str]:
    print("\n2. Data")

    med_streng, tomme = [], []
    for journey_id, journey in journeys.items():
        punkter = (journey.get("pointsOnLink") or {}).get("points")
        (med_streng if punkter else tomme).append(journey_id)

    linje("turer hentet", len(journeys))
    linje("med pointsOnLink", len(med_streng))
    linje("uten, eller tom streng", len(tomme))

    if not med_streng:
        print("\n   Feltet finnes, men er tomt for hver eneste tur. Da er det ikke")
        print("   sporgeometrien som er problemet - det er dekningen.")
        return []

    antall = [
        len(decode_polyline(journeys[j]["pointsOnLink"]["points"])) for j in med_streng
    ]
    linje(
        "punkter per trasé",
        f"{min(antall)} – {max(antall)} (median {int(statistics.median(antall))})",
    )

    # Hva betyr egentlig `length`? I OTP er den antall punkter, ikke meter, men
    # det er verdt å se etter selv i stedet for å tro på det.
    print("\n   Enturs `length` mot det vi regner ut:")
    print(f"   {'tur':<12}{'length':>9}{'punkter':>10}{'km':>9}")
    for journey_id in med_streng[:5]:
        oppgitt = journeys[journey_id]["pointsOnLink"].get("length")
        trase = (sjnord._FORBEREDT.get(journey_id) or {}).get("trase")
        punkter = len(decode_polyline(journeys[journey_id]["pointsOnLink"]["points"]))
        km = f"{trase.lengde_m / 1000:.1f}" if trase else "—"
        print(f"   {kort(journey_id):<12}{str(oppgitt):>9}{punkter:>10}{km:>9}")

    print("\n   Stemmer `length` med punktantallet, teller Entur punkter og")
    print("   dekoderen er enig med kilden. Stemmer den med km, er det meter.")

    return med_streng


# ---------------------------------------------------------------------------
# 3. Stasjoner mot trasé
# ---------------------------------------------------------------------------

def sjekk_snapping(journeys: dict, med_streng: list[str]) -> None:
    print("\n3. Stasjoner mot trasé")

    verstinger, forkastet = [], []

    for journey_id in med_streng:
        forberedt = sjnord._FORBEREDT.get(journey_id) or {}
        trase = forberedt.get("trase")
        if trase is None:
            forkastet.append(journey_id)
            continue

        stopp = [(s["lon"], s["lat"]) for s in forberedt["timeline"]]
        _, avvik = offsets_for_stopp(trase, stopp)
        verstinger.append((max(avvik), journey_id))

    linje("turer med brukbar trasé", len(verstinger))
    linje("forkastet av bygg_trase", len(forkastet))

    # En forkastet trasé er ikke nødvendigvis en feil - det kan være en tur
    # med geometri for feil bane. Men årsaken bør stå svart på hvitt.
    for journey_id in forkastet:
        forberedt = sjnord._FORBEREDT.get(journey_id) or {}
        stopp = [(s["lon"], s["lat"]) for s in forberedt.get("timeline") or []]
        _, _, arsak = bygg_trase(journeys[journey_id].get("pointsOnLink"), stopp)
        linje(f"  {kort(journey_id)} forkastet fordi", arsak)

    if not verstinger:
        print("\n   Ingen traséer overlevde kvalitetssjekken. Kjør med")
        print("   logging.DEBUG for å se begrunnelsen per tur.")
        return

    tall = sorted(v for v, _ in verstinger)
    linje("verste stopp-avvik, median", f"{statistics.median(tall):.0f} m")
    verst, hvem = max(verstinger)
    linje("verste enkelttur", f"{verst:.0f} m  ({kort(hvem)})")

    print("\n   Stasjonspunkter ligger typisk 10–30 m fra sporet. Er medianen")
    print("   der, snapper vi mot riktig bane.")


# ---------------------------------------------------------------------------
# 4. Hva fiksen flytter
# ---------------------------------------------------------------------------

def sjekk_flytting(journeys: dict) -> None:
    print("\n4. Hva fiksen flytter (tog underveis nå)")

    med = {f["properties"]["id"]: f for f in sjnord.positions(journeys)}

    # Regn en gang til uten geometri, slik kartet så ut før 17. august.
    # Dette ødelegger cachen, så det må være siste steg.
    for entry in sjnord._FORBEREDT.values():
        entry["trase"] = None
    uten = {f["properties"]["id"]: f for f in sjnord.positions(journeys)}

    if not med:
        diagnose_tomt(journeys)
        return

    rader = []
    for journey_id, feature in med.items():
        gammel = uten.get(journey_id)
        if not gammel:
            continue
        p = feature["properties"]
        flyttet = avstand_m(
            tuple(gammel["geometry"]["coordinates"]),
            tuple(feature["geometry"]["coordinates"]),
        )
        rader.append((flyttet, p["line"], p["trainNumber"], p["positionMethod"],
                      f"{p['previousStop']} → {p['nextStop']}"))

    rader.sort(reverse=True)

    print(f"\n   {'linje':<7}{'tog':<7}{'metode':<11}{'flyttet':>9}   strekning")
    print("   " + "-" * 68)
    for flyttet, line, nummer, metode, strekning in rader:
        merke = "langs spor" if metode == "track" else "LUFTLINJE"
        print(f"   {line:<7}{nummer:<7}{merke:<11}{flyttet/1000:>7.1f} km   {strekning}")

    langs = [r for r in rader if r[3] == "track"]
    print()
    linje("tog plassert langs spor", f"{len(langs)} av {len(rader)}")
    if langs:
        avstander = sorted(r[0] for r in langs)
        linje("median flytting", f"{statistics.median(avstander)/1000:.1f} km")
        linje("størst flytting", f"{max(avstander)/1000:.1f} km")

    print("\n   Ta det toget som flyttet seg mest, og slå det opp i Bane NOR sitt")
    print("   kart. Er den nye posisjonen den riktige, er problem 2 lukket.")


def diagnose_tomt(journeys: dict) -> None:
    """Ingen tog underveis - er det riktig, eller mangler vi noen?

    Alle 47 turene er hentet, så det er ikke dekningen som svikter. Enten er
    de faktisk ferdige, eller så har vi fått feil kjøredøgn. Det siste er
    problem 3 i docs/undersokelser.md, og nattogene er stedet det ville dukket opp.
    """
    na = datetime.now(timezone.utc)
    ferdige = ikke_startet = uten_tidslinje = 0
    sist_ferdig = neste_start = None

    for journey_id in journeys:
        tidslinje = (sjnord._FORBEREDT.get(journey_id) or {}).get("timeline") or []
        if len(tidslinje) < 2:
            uten_tidslinje += 1
        elif tidslinje[-1]["at"] < na:
            ferdige += 1
            sist_ferdig = max(sist_ferdig or tidslinje[-1]["at"], tidslinje[-1]["at"])
        elif tidslinje[0]["at"] > na:
            ikke_startet += 1
            neste_start = min(neste_start or tidslinje[0]["at"], tidslinje[0]["at"])

    print("   Ingen SJ-tog underveis akkurat nå.\n")
    linje("klokka er", datetime.now().astimezone().strftime("%H:%M %d.%m"))
    linje("turer som er kjørt ferdig", ferdige)
    linje("turer som ikke har startet", ikke_startet)
    linje("turer uten brukbar tidslinje", uten_tidslinje)

    if sist_ferdig:
        minutter = (na - sist_ferdig).total_seconds() / 60
        linje("siste ankomst var for", f"{minutter:.0f} min siden")
    if neste_start:
        minutter = (neste_start - na).total_seconds() / 60
        linje("neste avgang om", f"{minutter:.0f} min")

    print("\n   Er klokka mellom 23 og 07 og alt står som «kjørt ferdig», er dette")
    print("   verdt en nærmere titt: Nordlandsbanens nattog Trondheim-Bodø går")
    print("   rundt 23 og ankommer neste morgen. Mangler det, er problem 3 i")
    print("   docs/undersokelser.md ikke lenger uobservert.")


# ---------------------------------------------------------------------------

async def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="   %(message)s")

    client_name = os.getenv("ET_CLIENT_NAME", "").strip()
    if not client_name:
        print("ET_CLIENT_NAME mangler. Sett den i .env før du kjører denne.")
        return 1

    print("sjekk_geometri — sporgeometri mot ekte Entur-data\n")

    if not await sjekk_skjema(client_name):
        return 1

    print("\n   Henter rutedata ...")
    journeys = await sjnord.fetch_journeys(client_name)
    if not journeys:
        print("\n   Ingen turer hentet. Kjør sjnord.py først og se hva som feiler.")
        return 1

    med_streng = sjekk_data(journeys)
    if not med_streng:
        return 1

    sjekk_snapping(journeys, med_streng)
    sjekk_flytting(journeys)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
