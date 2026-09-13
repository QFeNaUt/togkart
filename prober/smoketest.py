"""Sjekk at Entur faktisk leverer togdata før du bygger videre.

Kjør:  python prober/smoketest.py

Dette svarer på de to spørsmålene hele prosjektet hviler på:
  1. Finnes det tog i feeden i det hele tatt?
  2. Er `delay` utfylt? (Uten den har du ingen fargekoding.)
"""

import os
import sys
from collections import Counter
from pathlib import Path

# Python legger skriptets EGEN mappe på sys.path, ikke den du står i. Uten
# denne linja feiler `python prober/smoketest.py` med «No module named entur»,
# selv om du gjør nøyaktig det README sier. Samme shim som de fem andre probene
# som importerer fra roten har.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from dotenv import load_dotenv

from entur import ENTUR_URL, QUERY, delay_band

load_dotenv()

client_name = os.getenv("ET_CLIENT_NAME", "").strip()
if not client_name:
    print("! ET_CLIENT_NAME mangler. Lag en .env-fil først (se .env.example).")
    raise SystemExit(1)

print(f"Spør Entur som '{client_name}' ...\n")

response = httpx.post(
    ENTUR_URL,
    json={"query": QUERY},
    headers={"ET-Client-Name": client_name},
    timeout=10,
)
print(f"HTTP {response.status_code}")
payload = response.json()

# GraphQL svarer 200 selv ved ugyldig spørring - feilene ligger her.
if payload.get("errors"):
    print("\n! Spørringen ble avvist:")
    for error in payload["errors"]:
        print("   -", error.get("message"))
    print("\n  Fjern feltet det klages på i QUERY i entur.py og prøv igjen.")
    raise SystemExit(1)

vehicles = (payload.get("data") or {}).get("vehicles") or []

with_location = [v for v in vehicles if (v.get("location") or {}).get("latitude")]
with_delay = [v for v in vehicles if v.get("delay") is not None]
with_line = [v for v in vehicles if (v.get("line") or {}).get("publicCode")]

print(f"\nTog totalt:      {len(vehicles)}")
print(f"Med posisjon:    {len(with_location)}")
print(f"Med delay:       {len(with_delay)}")
print(f"Med linjekode:   {len(with_line)}")

if with_delay:
    bands = Counter(delay_band(v["delay"]) for v in with_delay)
    print("\nFordeling på punktlighetsbånd:")
    for band, count in bands.most_common():
        print(f"   {band:12s} {count}")

if vehicles:
    print("\nEksempel på ett kjøretøy:")
    for key, value in vehicles[0].items():
        print(f"   {key:14s} {value}")

# --- Konklusjon -------------------------------------------------------------
print("\n" + "-" * 50)
if not vehicles:
    print("STOPP: Ingen tog i feeden. Sjekk om `mode: RAIL` er riktig enum,")
    print("       eller om jernbane rett og slett ikke rapporterer posisjon.")
elif not with_location:
    print("STOPP: Tog finnes, men ingen har koordinater. Kartet blir tomt.")
elif not with_delay:
    print("ADVARSEL: Ingen tog har `delay`. Fargekodingen blir ensfarget grå.")
    print("          Punktlighet må da hentes fra Journey Planner v3 i stedet.")
else:
    print("OK: Datagrunnlaget holder. Kjør `uvicorn app:app --reload`.")
