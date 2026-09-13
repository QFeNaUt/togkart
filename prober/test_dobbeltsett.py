"""Regresjonstest for dobbeltsett-sammenslåingen i entur.py. Krever ikke nett.

Kjøres fra prosjektroten:
    python prober/test_dobbeltsett.py
"""
import logging
import os
import sys

# Proben ligger i prober/, modulene i roten.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="   log: %(message)s")

# Enkel stubb for materiell.rolling_stock, så testen ikke avhenger av tabellen.
import types
stub = types.ModuleType("materiell")
SETT = {
    "73-16": {"type": "BM73", "kind": "sett", "unit": "16", "fleet": 20},
    "73-04": {"type": "BM73", "kind": "sett", "unit": "4", "fleet": 20},
}
stub.rolling_stock = lambda vid: SETT.get(vid)
sys.modules["materiell"] = stub

import entur

NAA = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def kjoretoy(vid, tognr, lat, lon, delay=120, line="F5"):
    return {
        "vehicleId": vid, "delay": delay, "lastUpdated": NAA,
        "location": {"latitude": lat, "longitude": lon},
        "line": {"publicCode": line, "lineName": "Stavanger-Oslo", "lineRef": f"GOA:Line:{line}"},
        "datedServiceJourney": {"id": f"GOA:DatedServiceJourney:{tognr}_STV-OSL_26-08-18"},
    }

print("entur — dobbeltsett\n")

# 1. Dobbeltsett: samme tognummer, 120 m fra hverandre
g, m = entur.to_geojson([
    kjoretoy("73-16", 706, 58.9700, 5.7331),
    kjoretoy("73-04", 706, 58.9711, 5.7331),
])
p = g["features"][0]["properties"]
assert len(g["features"]) == 1, g
assert p["id"] == "73-04:73-16", p["id"]
assert p["vehicleIds"] == ["73-04", "73-16"]
assert p["coupled"] == 2
assert p["stock"]["unit"] == "4 + 16", p["stock"]
assert sum(m["counts"].values()) == 1, m["counts"]
assert m["coupled"] == 1
print(f"dobbeltsett       OK   {p['id']}, sett {p['stock']['unit']}, teller {sum(m['counts'].values())}")

# 2. Tabellen i materiell.py skal ikke være rørt
assert SETT["73-16"]["unit"] == "16", SETT["73-16"]
print("delt tabell       OK   materiell.py uendret")

# 3. Samme tognummer, men 40 km fra hverandre: IKKE et dobbeltsett
g, m = entur.to_geojson([
    kjoretoy("73-16", 706, 58.9700, 5.7331),
    kjoretoy("73-04", 706, 59.3300, 5.7331),
])
assert len(g["features"]) == 2, g
assert m["coupled"] == 0
print("langt fra hverandre OK begge beholdt, warning i loggen")

# 4. Uten tognummer skal ingenting slås sammen
uten = kjoretoy("73-16", 706, 58.97, 5.73); uten["datedServiceJourney"] = {"id": "rot"}
uten2 = kjoretoy("73-04", 706, 58.97, 5.73); uten2["datedServiceJourney"] = {"id": "rot"}
g, m = entur.to_geojson([uten, uten2])
assert len(g["features"]) == 2, g
print("uten tognummer    OK   står alene")

# 5. Vanlig enkelttog skal se ut som før
g, m = entur.to_geojson([kjoretoy("73-16", 707, 58.97, 5.73)])
p = g["features"][0]["properties"]
assert p["id"] == "73-16" and "coupled" not in p and p["stock"]["unit"] == "16"
assert m["dropped"] == 0 and m["coupled"] == 0
print("enkelttog         OK   uendret form")

# 6. Uten posisjon telles som dropped, ikke som sammenslått
tomt = kjoretoy("73-16", 708, 58.97, 5.73); tomt["location"] = {}
g, m = entur.to_geojson([tomt, kjoretoy("73-04", 709, 58.97, 5.73)])
assert m["dropped"] == 1 and m["count"] == 1, m
print("uten posisjon     OK   dropped=1, coupled=0")

# 7. Sammenkoblet løp: ett ServiceJourney, to tognumre, to kjøretøy.
#
# Entur gir begge halvdelene av et løp som bytter nummer underveis samme
# ServiceJourney-ID ("838-341_515579-R"). Regelen «nummeret står først» ga
# derfor 838 til begge, og to Vy-tog 32 km fra hverandre ble til ett tog i
# kartet - og til én rad-serie i historikk.db, siden `_tog_id` nøkler på
# (linje, tognummer). Vy legger sitt eget nummer i vehicleId foran kjøredatoen,
# og det er det som skiller dem.
#
# Observert i feeden 20. august. Se `sjekk.py tognummer`.
def vy(vid, lat, lon):
    return {
        "vehicleId": vid, "delay": 0.0, "lastUpdated": NAA,
        "location": {"latitude": lat, "longitude": lon},
        "line": {"publicCode": "RE11", "lineName": "Eidsvoll-Oslo S-Skien",
                 "lineRef": "VYG:Line:RE11"},
        "serviceJourney": {"id": "VYG:ServiceJourney:838-341_515579-R"},
    }

g, m = entur.to_geojson([
    vy("838-2026-08-19", 60.3436, 11.2460),
    vy("341-2026-08-20", 60.6334, 11.2387),
])
numre = sorted(f["properties"]["trainNumber"] for f in g["features"])
assert numre == ["341", "838"], numre
assert len(g["features"]) == 2, "to ulike tog skal ikke slås sammen"
assert m["coupled"] == 0, m
print("sammenkoblet løp  OK   838 og 341 holdt fra hverandre")

# ...og settnumrene til Flytoget og Go-Ahead skal IKKE tolkes som tognummer,
# selv om de også har en bindestrek i seg. Det er hele grunnen til at regelen
# krever en full ISO-dato etter nummeret.
assert entur._train_number({"vehicleId": "73-08"}) == "", "settnummer er ikke tognummer"
assert entur._train_number({"vehicleId": "71"}) == "", "settnummer er ikke tognummer"
assert entur._train_number(kjoretoy("73-08", 716, 58.97, 5.73)) == "716"
print("settnummer        OK   73-08 og 71 tolkes ikke som tognummer")

print("\nAlt grønt.")
