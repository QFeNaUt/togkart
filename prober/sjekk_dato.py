"""Får den nøstede spørringen riktig kjøredato?

Observert 18. august 01:25: 47 turer hentet, null kjørt ferdig, 46 som ikke
har startet, nærmeste avgang 8,6 timer fram. Den ytre spørringen så 12 timer
bakover, så minst noen av turene må ha vært ferdige. Noe stemmer ikke.

Hypotesen er at `serviceJourney { estimatedCalls }` uten `date`-parameter
returnerer dagens instans av ruta, ikke instansen som traff den ytre
spørringen. Denne proben måler det direkte:

    Den ytre estimatedCall sier når toget går fra knutepunktet.
    Den nøstede lista inneholder det samme stoppestedet.
    Står det samme tidspunkt begge steder, er det samme tur.
    Står det ulikt, har vi fått feil instans - og vet nøyaktig hvor mye feil.

Kjør fra prosjektroten:  python prober/sjekk_dato.py
"""

import asyncio
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from dotenv import load_dotenv

import sjnord
from prober.felles import JOURNEY_URL, EnturFeil, sporr_async

SKJEMA = """
{
  __type(name: "EstimatedCall") {
    fields { name }
  }
}
"""

QUERY = """
query Dato($id: String!, $start: DateTime!, $range: Int!) {
  stopPlace(id: $id) {
    estimatedCalls(
      startTime: $start
      timeRange: $range
      numberOfDepartures: 30
      whiteListedModes: [rail]
    ) {
      __DATO__
      aimedDepartureTime
      quay { id name }
      serviceJourney {
        id
        line { publicCode }
        estimatedCalls {
          __DATO__
          aimedDepartureTime
          quay { id }
        }
      }
    }
  }
}
"""


def linje(tekst: str, verdi) -> None:
    print(f"   {tekst:.<45} {verdi}")


def klokke(tidspunkt: datetime | None) -> str:
    return tidspunkt.astimezone().strftime("%d.%m %H:%M") if tidspunkt else "—"


# `be_om()` sto her og ga hele nyttelasten videre UTEN å se på `errors`.
# Førstekallet under gjorde `(payload.get("data") or {})` på den, så en avvist
# spørring ble til et tomt felt-sett og proben meldte «date MANGLER» - et målt
# svar på et spørsmål den aldri fikk stille. `sporr_async` kaster i stedet.


async def hent() -> tuple[list[dict], bool]:
    """Returnerer (ytre kall med nøstede turer, om date-feltet var med)."""
    now = datetime.now().astimezone()
    variabler = {
        "start": (now - timedelta(hours=sjnord.LOOKBACK_HOURS)).isoformat(
            timespec="seconds"
        ),
        "range": (sjnord.LOOKBACK_HOURS + sjnord.FORWARD_HOURS) * 3600,
    }

    async with httpx.AsyncClient(timeout=25.0) as client:
        print("1. Skjema")
        data = await sporr_async(client, JOURNEY_URL, SKJEMA)
        felter = (data.get("__type") or {}).get("fields") or []
        har_dato = "date" in {f["name"] for f in felter}
        linje("date på EstimatedCall", "FINNES" if har_dato else "MANGLER")

        sporring = QUERY.replace("__DATO__", "date" if har_dato else "")

        # Fjernknutepunktene alene. Proben ser etter nattog som krysser
        # midnatt, og de går på langbanene - de travle Østlands-stasjonene
        # ville bare gitt hundrevis av lokaltog med samme kjøredato.
        punkter = [
            sid for sid, (navn, _, _) in sjnord.knutepunkter().items()
            if navn in sjnord.KNUTEPUNKT_FJERNT
        ]
        print(f"\n   Henter fra {len(punkter)} fjernknutepunkter ...")
        svar = await asyncio.gather(
            *(
                sporr_async(client, JOURNEY_URL, sporring, {**variabler, "id": stop_id})
                for stop_id in punkter
            ),
            return_exceptions=True,
        )

    kall = []
    for data in svar:
        # `return_exceptions=True` over, så en EnturFeil kommer hit som et
        # objekt og ikke som et kast. Ett knutepunkt som feiler skal ikke ta
        # med seg de andre.
        if isinstance(data, Exception):
            print(f"   Knutepunkt feilet: {data}")
            continue
        sted = data.get("stopPlace") or {}
        kall.extend(sted.get("estimatedCalls") or [])

    return kall, har_dato


def sammenlign(kall: list[dict], har_dato: bool) -> None:
    print("\n2. Ytre avgang mot nøstet avgang på samme stoppested")

    na = datetime.now(timezone.utc)
    treff, bom, ikke_funnet = [], [], 0
    fortid = 0
    sett = set()

    for ytre in kall:
        tur = ytre.get("serviceJourney") or {}
        tur_id = tur.get("id")
        if not tur_id or tur_id in sett:
            continue
        sett.add(tur_id)

        ytre_tid = sjnord._parse(ytre.get("aimedDepartureTime"))
        kai = (ytre.get("quay") or {}).get("id")
        if ytre_tid is None or not kai:
            continue

        if ytre_tid < na:
            fortid += 1

        nostet_tid = None
        for indre in tur.get("estimatedCalls") or []:
            if ((indre.get("quay") or {}).get("id")) == kai:
                nostet_tid = sjnord._parse(indre.get("aimedDepartureTime"))
                break

        if nostet_tid is None:
            ikke_funnet += 1
            continue

        timer = (nostet_tid - ytre_tid).total_seconds() / 3600
        rad = (
            timer,
            (tur.get("line") or {}).get("publicCode") or "",
            sjnord._journey_number(tur_id),
            ytre.get("date") if har_dato else None,
            ytre_tid,
            nostet_tid,
        )
        (treff if abs(timer) < 0.02 else bom).append(rad)

    linje("turer sammenlignet", len(treff) + len(bom) + ikke_funnet)
    linje("samme tidspunkt (riktig instans)", len(treff))
    linje("ulikt tidspunkt (feil instans)", len(bom))
    linje("stoppestedet fantes ikke i nøstet liste", ikke_funnet)
    linje("ytre avganger som allerede har vært", fortid)

    if not bom:
        print("\n3. Konklusjon")
        print("   Den nøstede spørringen får samme instans som traff. Kjøredato")
        print("   er ikke forklaringen på det tomme kartet - se etter noe annet.")
        return

    bom.sort(key=lambda r: abs(r[0]), reverse=True)
    avvik = sorted(abs(r[0]) for r in bom)

    linje("median avvik", f"{statistics.median(avvik):.1f} t")
    linje("størst avvik", f"{max(avvik):.1f} t")

    vanlige = Counter(round(r[0]) for r in bom).most_common(3)
    linje("vanligste avvik", ", ".join(f"{t:+d} t ({n} turer)" for t, n in vanlige))

    print(f"\n   {'linje':<7}{'tog':<7}{'kjøredato':<12}{'ytre':<13}{'nøstet':<13}avvik")
    print("   " + "-" * 63)
    for timer, line, nummer, dato, ytre_tid, nostet_tid in bom[:10]:
        print(
            f"   {line:<7}{nummer:<7}{str(dato or '—'):<12}"
            f"{klokke(ytre_tid):<13}{klokke(nostet_tid):<13}{timer:+.1f} t"
        )

    print("\n3. Konklusjon")
    print("   Den nøstede estimatedCalls returnerer en annen instans enn den som")
    print("   traff den ytre spørringen. Problem 3 er ikke lenger uobservert.")
    print("   Turene flyttes framover i tid, og mellom midnatt og første avgang")
    print("   står kartet tomt for SJ-tog.")

    if har_dato:
        datoer = Counter(str(r[3]) for r in bom if r[3])
        if datoer:
            print("\n   Kjøredatoer på de feilende ytre kallene:")
            for dato, antall in datoer.most_common():
                print(f"     {dato}  {antall} turer")


async def main() -> int:
    load_dotenv()
    client_name = os.getenv("ET_CLIENT_NAME", "").strip()
    if not client_name:
        print("ET_CLIENT_NAME mangler. Sett den i .env før du kjører denne.")
        return 1

    print("sjekk_dato — får den nøstede spørringen riktig kjøredato?\n")

    kall, har_dato = await hent()
    if not kall:
        print("\n   Ingen kall hentet. Kjør sjnord.py først og se hva som feiler.")
        return 1

    sammenlign(kall, har_dato)
    return 0


if __name__ == "__main__":
    # `sporr_async` kaster nå på en avvist spørring i stedet for å gi et tomt
    # svar videre. Det er hele poenget med byttet - men da må noen ta imot,
    # ellers bytter vi en stille feil mot en stacktrace. Samme form som
    # bunnen av sjekk_punktlighet.py.
    try:
        raise SystemExit(asyncio.run(main()))
    except EnturFeil as feil:
        print(f"\nEntur avviste spørringen: {feil}")
        raise SystemExit(1)
    except httpx.HTTPError as feil:
        print(f"\nNettverksfeil: {feil}")
        raise SystemExit(1)
