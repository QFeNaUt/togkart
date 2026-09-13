"""Stemmer posisjonene våre med rutetabellen?

Kjør:  python prober/sjekk_posisjon.py

Hva proben gjør
---------------
For hvert tog med GPS finnes det en helt uavhengig andremening om hvor det er:
rutetid pluss sporgeometri, altså nøyaktig det regnestykket `sjnord.py` gjør
for tog UTEN GPS. Proben regner ut begge og måler avstanden mellom dem.

Den trenger ingen fasit utenfra. Det er poenget: den fanger en posisjon som har
løpt løpsk uten at noen må sammenlikne med Bane NOR for hånd. Hadde den
eksistert 17. august, ville den ropt da SJ-togene ble tegnet ute i Mjøsa - 11,9
km er langt utenfor det to metoder normalt spriker.

Hva tallene betyr
-----------------
Målt på 75 tog 21. august kl. 11:38, med forsinkelsen regnet inn:

    median  0,96 km      p90  3,98 km      verste  8,44 km      over 10 km: 0

Det er spredningen mellom to måter å bestemme en posisjon på, ikke en feil.
GPS-en måler hvor toget ER; rutetiden sier hvor et tog som ligger så langt
etter ruta SKULLE vært, og de to er ikke det samme punktet - toget kjører fort
mellom stasjoner og står stille på dem.

Forsinkelsen MÅ regnes inn
--------------------------
Uten den er det ikke posisjonene man måler, men forsinkelsen om igjen. Samme
måling uten korreksjon ga median 2,31 km og verste 68,58 km, og de verste var
nøyaktig de mest forsinkede togene:

    F5 706   +62 min forsinket   ->  68,58 km ukorrigert, 3,11 km korrigert
    F4 607   +25 min             ->  33,67 km            8,44 km
    F5 707   +19 min             ->  28,84 km            3,34 km

Har Journey Planner sanntid for turen, ligger forsinkelsen allerede i
`expectedDepartureTime` og ingen korreksjon trengs. Har den det ikke - typisk
for en dag som har vært - flyttes klokka tilbake med togets eget avvik.

Merk hva korreksjonen IKKE er: den er ikke uavhengig. Avviket er målt mot
rutetabellen, og vi bruker det til å flytte rutetabellen. Sammenlikningen sier
derfor «posisjonen er forenlig med et tog som ligger så langt etter ruta», og
det er svakere enn en ekte andremening. Den fanger likevel det den er laget
for: en posisjon som ligger et annet sted enn noen versjon av ruta kan
forklare.

Ingenting her endrer noe. Proben leser.
"""
import asyncio
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402

import entur  # noqa: E402
import sjnord  # noqa: E402
from sporgeometri import avstand_m, bygg_trase  # noqa: E402

# Under dette antallet sier proben ingenting om medianen. En median av tre tog
# er ikke en måling - samme grunn som MINST_UTVALG i sjekk_dekning.py.
MINST_UTVALG = 15

# Grensene er satt godt over det som ble målt (median 0,96, verste 8,44), så
# proben sier fra når noe har flyttet seg vesentlig - ikke hver gang den
# kjøres. Det den skal fange er at posisjonene begynner å drive, ikke at de
# spriker litt.
MAKS_MEDIAN_KM = 3.0
MAKS_ENKELT_KM = 15.0


async def hoved() -> int:
    load_dotenv()
    navn = os.getenv("ET_CLIENT_NAME", "").strip()
    if not navn:
        print("ET_CLIENT_NAME er ikke satt. Kopier .env.example til .env.")
        return 1

    feil = 0
    headers = {"ET-Client-Name": navn}
    na = datetime.now().astimezone()
    na_utc = datetime.now(timezone.utc)

    print("=" * 72)
    print("1. TOG MED MÅLT POSISJON")
    print("=" * 72)

    geo, _ = await entur.fetch_trains(navn)
    malte = geo["features"]
    refs = {
        f["properties"]["journeyRef"]: f
        for f in malte
        if f["properties"].get("journeyRef")
    }
    print(f"  {len(malte)} tog i feeden, {len(refs)} med tur-ID")

    if not refs:
        print("\n  Ingen tog å måle. Kjører det tog nå?")
        return feil

    # --- 2. Andremeningen ---------------------------------------------
    print()
    print("=" * 72)
    print("2. HVA RUTETABELLEN SIER OM DE SAMME TOGENE")
    print("=" * 72)

    datoer = [na.date().isoformat(), (na - timedelta(days=1)).date().isoformat()]
    sjnord._FORBEREDT.clear()
    per_dato: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=60.0) as klient:
        for dato in datoer:
            turer, _ = await sjnord._turer_for_dato(
                klient, headers, dato, list(refs)
            )
            per_dato[dato] = turer

    hentet = len({t for d in datoer for t in per_dato[d]})
    print(f"  rutedata hentet for {hentet} av {len(refs)} turer, "
          f"kjøredato {' og '.join(datoer)}")

    rader = []
    hopp: Counter = Counter()
    for ref, trekk in refs.items():
        kandidater = {}
        for dato in datoer:
            tur = per_dato[dato].get(ref)
            if not tur:
                continue
            tidslinje = sjnord._timeline(tur.get("estimatedCalls") or [])
            if tidslinje:
                kandidater[dato] = tidslinje

        dato, tidslinje, status = sjnord.velg_instans(kandidater, na)
        if status != "underveis" or len(tidslinje) < 2:
            hopp[status if status != "underveis" else "for få stopp"] += 1
            continue

        tur = per_dato[dato][ref]
        stopp = [(s["lon"], s["lat"]) for s in tidslinje]
        trase, offsets, _ = bygg_trase(tur.get("pointsOnLink"), stopp)

        egenskaper = trekk["properties"]
        forsinkelse = egenskaper.get("delay")
        har_sanntid = any(
            (c.get("realtimeState") or "").lower() not in ("", "scheduled")
            for c in (tur.get("estimatedCalls") or [])
        )
        # Med sanntid ligger forsinkelsen allerede i tidene. Uten den flyttes
        # klokka tilbake med togets eget avvik - se modul-docstringen.
        klokke = na_utc
        if not har_sanntid and forsinkelse is not None:
            klokke = na_utc - timedelta(seconds=float(forsinkelse))

        plassert = sjnord._position_now(tidslinje, klokke, trase, offsets)
        if not plassert:
            hopp["ingen beregnet posisjon"] += 1
            continue

        lon, lat = plassert[0], plassert[1]
        vlon, vlat = trekk["geometry"]["coordinates"]
        rader.append({
            "km": avstand_m((lon, lat), (vlon, vlat)) / 1000,
            "linje": egenskaper.get("line") or "?",
            "nr": egenskaper.get("trainNumber") or "?",
            "stale": bool(egenskaper.get("stale")),
            "sanntid": har_sanntid,
            "metode": plassert[5],
            "fra": plassert[4],
            "til": plassert[3],
        })

    if hopp:
        print(f"  utenfor målingen: {dict(hopp)}")
    print(f"  {len(rader)} tog kunne sammenliknes")

    # --- 3. Avviket ---------------------------------------------------
    print()
    print("=" * 72)
    print("3. AVSTAND MELLOM MÅLT OG BEREGNET POSISJON")
    print("=" * 72)

    if not rader:
        print("  Ingen tog underveis med brukbar tidslinje. Prøv på dagtid.")
        return feil

    avvik = sorted(r["km"] for r in rader)
    antall = len(avvik)
    median = statistics.median(avvik)
    p90 = avvik[int(antall * 0.90)]
    print(f"  median   {median:6.2f} km      (målt 21. august: 0,96)")
    print(f"  p90      {p90:6.2f} km      (målt 21. august: 3,98)")
    print(f"  verste   {avvik[-1]:6.2f} km      (målt 21. august: 8,44)")

    uten_sanntid = sum(1 for r in rader if not r["sanntid"])
    if uten_sanntid:
        print(f"\n  {uten_sanntid} av {antall} turer mangler sanntid i Journey "
              f"Planner;\n  for dem er klokka flyttet med togets eget avvik.")

    verstinger = sorted(rader, key=lambda r: -r["km"])[:10]
    print(f"\n  {'km':>7} {'linje':<6}{'tog':<7}{'spor':<9}{'sanntid':<9} strekning")
    for r in verstinger:
        merke = "  (spøkelse)" if r["stale"] else ""
        print(f"  {r['km']:>7.2f} {str(r['linje']):<6}{str(r['nr']):<7}"
              f"{r['metode']:<9}{('ja' if r['sanntid'] else 'nei'):<9}"
              f"{r['fra']} -> {r['til']}{merke}")

    # --- 4. Vaktene ---------------------------------------------------
    if antall < MINST_UTVALG:
        print(f"\n  For få tog ({antall}) til å si noe om medianen. Vakten "
              f"krever minst {MINST_UTVALG}.")
        return feil

    if median > MAKS_MEDIAN_KM:
        print(f"\n  FEIL: medianavviket er {median:.2f} km, over grensen på "
              f"{MAKS_MEDIAN_KM}.")
        print("  Enten har posisjonene begynt å drive, eller så er det noe galt")
        print("  med sporgeometrien. Kjør prober/sjekk_geometri.py.")
        feil += 1

    # Spøkelsestog holdes utenfor enkeltvakten: en posisjon som er tjue minutter
    # gammel SKAL ligge langt fra der ruta sier toget er nå, og det er ikke en
    # feil i posisjonen - det er et tog som har sluttet å sende.
    langt_unna = [r for r in rader if r["km"] > MAKS_ENKELT_KM and not r["stale"]]
    if langt_unna:
        print(f"\n  FEIL: {len(langt_unna)} tog ligger over {MAKS_ENKELT_KM} km "
              f"fra der ruta plasserer dem:")
        for r in langt_unna[:8]:
            print(f"    {r['linje']} {r['nr']}  {r['km']:.1f} km  "
                  f"{r['fra']} -> {r['til']}")
        print("  Ingen av dem er spøkelsestog, så alderen forklarer det ikke.")
        feil += 1

    print()
    if feil:
        print(f"{feil} ting å se på.")
    else:
        print("Posisjonene stemmer med rutetabellen.")
    return 1 if feil else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(hoved()))
