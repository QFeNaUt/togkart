"""sjekk_duplikater — finner tog som tegnes to ganger i kartet.

Krever at uvicorn kjører. Snakker bare med ditt eget API, ikke med Entur,
så den ser nøyaktig det kartet ser.

    python prober/sjekk_duplikater.py
"""

import json
import math
import urllib.request
from collections import defaultdict

API = "http://127.0.0.1:8000/api/trains"

TOGNUMMER_FELT = ("tognummer", "trainNumber", "tog", "vehicleJourneyName", "journeyNumber")
LINJE_FELT = ("linje", "line", "lineId", "publicCode", "lineRef")


def hent(url):
    with urllib.request.urlopen(url, timeout=10) as svar:
        return json.load(svar)


def struktur(data, sti="", dybde=0):
    """Skriv ut formen på svaret, så vi ser hvor togene faktisk ligger."""
    if dybde > 2:
        return
    if isinstance(data, dict):
        for nokkel, verdi in data.items():
            print(f"   {'  ' * dybde}{nokkel}: {type(verdi).__name__}"
                  + (f" ({len(verdi)})" if isinstance(verdi, (list, dict)) else ""))
            struktur(verdi, f"{sti}.{nokkel}", dybde + 1)
    elif isinstance(data, list) and data:
        struktur(data[0], f"{sti}[0]", dybde + 1)


def finn_tog(data):
    """Let rekursivt etter den lengste lista med objekter som har posisjon."""
    beste = []

    def ser_ut_som_tog(d):
        if not isinstance(d, dict):
            return False
        if "geometry" in d:
            return True
        return any(n in d for n in ("lat", "latitude", "lon", "longitude"))

    def gaa(node):
        nonlocal beste
        if isinstance(node, list):
            if node and ser_ut_som_tog(node[0]) and len(node) > len(beste):
                beste = node
            for element in node:
                gaa(element)
        elif isinstance(node, dict):
            for verdi in node.values():
                gaa(verdi)

    gaa(data)
    return beste


def posisjon(tog):
    """Returner (lon, lat) uansett om formen er GeoJSON eller flate felt."""
    geo = tog.get("geometry")
    if isinstance(geo, dict) and "coordinates" in geo:
        return tuple(geo["coordinates"][:2])
    lon = tog.get("lon", tog.get("longitude"))
    lat = tog.get("lat", tog.get("latitude"))
    return (lon, lat)


def egenskaper(tog):
    p = tog.get("properties")
    return p if isinstance(p, dict) else tog


def avstand_m(a, b):
    """Haversine mellom to (lon, lat)-punkter."""
    if None in a or None in b:
        return float("nan")
    lon1, lat1 = a
    lon2, lat2 = b
    R = 6_371_000
    f1, f2 = math.radians(lat1), math.radians(lat2)
    df = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def forste(props, kandidater):
    for navn in kandidater:
        verdi = props.get(navn)
        if verdi not in (None, ""):
            return str(verdi)
    return "?"


def main():
    data = hent(API)

    print("sjekk_duplikater\n")
    print("1. Formen på svaret fra /api/trains")
    struktur(data)
    print()

    tog = finn_tog(data)
    print(f"2. Fant {len(tog)} tog\n")
    if not tog:
        print("   Ingen liste med posisjoner i svaret. Se strukturen over —\n"
              "   ligger togene under en nøkkel proben ikke gjenkjente?")
        return

    print("3. Feltnavn på det første toget")
    print("   " + ", ".join(sorted(egenskaper(tog[0]))) + "\n")

    print("4. Fordeling per posisjonsmetode")
    metoder = defaultdict(int)
    for t in tog:
        metoder[egenskaper(t).get("positionMethod", "(mangler felt)")] += 1
    for metode, antall in sorted(metoder.items()):
        print(f"   {metode:.<40} {antall}")
    print()

    grupper = defaultdict(list)
    for t in tog:
        p = egenskaper(t)
        grupper[(forste(p, LINJE_FELT), forste(p, TOGNUMMER_FELT))].append(t)

    duplikater = {k: v for k, v in grupper.items() if len(v) > 1}
    print(f"5. Tog som finnes mer enn én gang: {len(duplikater)}\n")

    for (linje, tognr), treff in sorted(duplikater.items()):
        print(f"   {linje} tog {tognr} — {len(treff)} prikker")
        punkter = [posisjon(t) for t in treff]
        for i in range(len(punkter)):
            for j in range(i + 1, len(punkter)):
                print(f"     avstand prikk {i+1} til {j+1}: "
                      f"{avstand_m(punkter[i], punkter[j])/1000:.2f} km")

        alle_felt = set()
        for t in treff:
            alle_felt |= set(egenskaper(t))
        for felt in sorted(alle_felt):
            verdier = [egenskaper(t).get(felt) for t in treff]
            if len(set(map(str, verdier))) > 1:
                print(f"     {felt:.<28} " + " | ".join(str(v) for v in verdier))
        print()

    if not duplikater:
        print("   Ingen duplikater akkurat nå. Kjør igjen om noen minutter —\n"
              "   feilen kan være avhengig av om GPS-toget er i feeden.")


if __name__ == "__main__":
    main()
