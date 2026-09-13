"""Hvorfor er alle beregnede forsinkelser null?

Kjør:  python prober/sanntidsjekk.py

sjnord.py henter togruter nøstet inne i stasjonens avgangstavle:

    stopPlace { estimatedCalls { serviceJourney { estimatedCalls } } }

nordtog.py henter dem direkte:

    serviceJourney(id:) { estimatedCalls }

Den første gir +0.0 på alt. Den andre gir ekte avvik. Dette skriptet henter
SAMME tog begge veier og setter tallene ved siden av hverandre, så vi vet om
det er spørringsformen som er problemet - eller noe annet.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Se kommentaren i nordtog.py: uten denne finner ikke `prober.felles` seg selv.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from prober.felles import JOURNEY_URL, EnturFeil, sporr

load_dotenv()

TRONDHEIM = "NSR:StopPlace:59977"


def ask(query: str) -> dict:
    """Journey Planner. Tretti sekunder fordi denne nøster seg gjennom en
    hel avgangstavle, og det tar tid."""
    try:
        return sporr(JOURNEY_URL, query, timeout=30)
    except EnturFeil as feil:
        raise SystemExit("! Avvist:\n   - " + "\n   - ".join(feil.meldinger))


def delay_of(call: dict) -> str:
    aimed, expected = call.get("aimedDepartureTime"), call.get("expectedDepartureTime")
    if not aimed or not expected:
        return "—"
    delta = datetime.fromisoformat(expected) - datetime.fromisoformat(aimed)
    return f"{delta.total_seconds() / 60:+.1f}"


# --- 0. Tar ServiceJourney.estimatedCalls noen argumenter? ----------------
fields = ask("""
{
  __type(name: "ServiceJourney") {
    fields { name args { name type { kind name ofType { kind name } } } }
  }
}
""")["__type"]["fields"]

call_field = next((f for f in fields if f["name"] == "estimatedCalls"), None)
arg_names = [a["name"] for a in (call_field or {}).get("args", [])]
print("=== Argumenter til ServiceJourney.estimatedCalls ===")
print("  " + (", ".join(arg_names) if arg_names else "(ingen)"))

# --- 1. Nøstet: slik sjnord.py gjør det ----------------------------------
start = (datetime.now().astimezone() - timedelta(hours=6)).isoformat(timespec="seconds")

nested = ask(
    """
    {
      stopPlace(id: "%s") {
        estimatedCalls(startTime: "%s", timeRange: 28800,
                       numberOfDepartures: 20, whiteListedModes: [rail]) {
          serviceJourney {
            id
            line { publicCode }
            estimatedCalls {
              aimedDepartureTime
              expectedDepartureTime
              realtimeState
              quay { name }
            }
          }
        }
      }
    }
    """
    % (TRONDHEIM, start)
)["stopPlace"]["estimatedCalls"]

# Finn et tog som er underveis nå
now = datetime.now().astimezone()
chosen = None
for call in nested:
    journey = call.get("serviceJourney") or {}
    calls = journey.get("estimatedCalls") or []
    times = [
        datetime.fromisoformat(c["expectedDepartureTime"])
        for c in calls
        if c.get("expectedDepartureTime")
    ]
    if times and times[0] <= now <= times[-1]:
        chosen = journey
        break

if not chosen:
    raise SystemExit("\nIngen tog underveis fra Trondheim S akkurat nå. Prøv senere.")

journey_id = chosen["id"]
line = (chosen.get("line") or {}).get("publicCode")
print(f"\n=== Valgt tog: {line}  {journey_id} ===")

# --- 2. Direkte: slik nordtog.py gjør det --------------------------------
direct = ask(
    """
    {
      serviceJourney(id: "%s") {
        estimatedCalls {
          aimedDepartureTime
          expectedDepartureTime
          realtimeState
          quay { name }
        }
      }
    }
    """
    % journey_id
)["serviceJourney"]["estimatedCalls"]

nested_calls = chosen["estimatedCalls"]

# --- 3. Side ved side ----------------------------------------------------
print(f"\n{'stopp':<24}{'NØSTET':>22}   {'DIREKTE':>22}")
print(f"{'':<24}{'avvik  tilstand':>22}   {'avvik  tilstand':>22}")
print("-" * 74)

for index in range(max(len(nested_calls), len(direct))):
    a = nested_calls[index] if index < len(nested_calls) else {}
    b = direct[index] if index < len(direct) else {}
    name = ((a.get("quay") or b.get("quay") or {}).get("name") or "?")[:22]
    print(
        f"{name:<24}"
        f"{delay_of(a):>7}  {str(a.get('realtimeState')):<13}"
        f"{delay_of(b):>7}  {str(b.get('realtimeState')):<13}"
    )

nested_nonzero = sum(1 for c in nested_calls if delay_of(c) not in ("—", "+0.0"))
direct_nonzero = sum(1 for c in direct if delay_of(c) not in ("—", "+0.0"))

print("\n" + "-" * 74)
print(f"  Stopp med avvik ulik null - nøstet: {nested_nonzero}, direkte: {direct_nonzero}")

if direct_nonzero and not nested_nonzero:
    print("\nBEKREFTET: den nøstede spørringen mister sanntid.")
    print("           sjnord.py må hente hver rute med serviceJourney(id:).")
elif nested_nonzero and direct_nonzero:
    print("\nBegge har sanntid. Da ligger feilen et annet sted i sjnord.py.")
elif not direct_nonzero and not nested_nonzero:
    print("\nIngen av dem har avvik. Dette toget går trolig faktisk presis -")
    print("kjør på nytt om en stund, eller når du ser et forsinket tog i kartet.")
