"""Måle SJ sin faktiske punktlighet - på tog som allerede kjører.

Kjør:  python prober/nordtog.py

Forrige versjon spurte om de neste avgangene og fikk +0.0 min på alt. Det var
riktig svar på feil spørsmål: et tog som ikke har kjørt ennå har ingen
forsinkelse, prognosen er lik ruta til noe faktisk skjer.

Denne ser bakover i tid i stedet, og bruker tre felt vi ikke hentet sist:
  realtimeState        - er en sanntidsoppdatering faktisk mottatt?
  actualDepartureTime  - når toget virkelig gikk
  stopPositionInPattern - hvor langt ut i ruta toget har kommet

Til slutt dumper den én hel togrute med koordinater, for å se om vi kan
plassere SJ-tog mellom to stasjoner uten GPS.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Python legger skriptets EGEN mappe på sys.path, ikke den du står i. Uten
# denne linja finner ikke `prober.felles` seg selv når du kjører
# `python prober/nordtog.py`. Samme shim som de andre probene har.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from prober.felles import GEOCODER_URL, JOURNEY_URL, EnturFeil, hent_json, sporr

load_dotenv()

STATIONS = ["Trondheim S", "Dombås", "Røros"]
HOURS_BACK = 3


def ask(query: str) -> dict:
    """Journey Planner, med denne probens egen måte å dø på."""
    try:
        return sporr(JOURNEY_URL, query, timeout=25)
    except EnturFeil as feil:
        innrykket = "\n   - ".join(feil.meldinger)
        raise SystemExit(f"! Journey Planner avviste spørringen:\n   - {innrykket}")


def unwrap(type_info: dict) -> dict:
    while type_info.get("ofType"):
        type_info = type_info["ofType"]
    return type_info


def minutes_between(planned: str | None, real: str | None) -> str:
    if not planned or not real:
        return "—"
    delta = datetime.fromisoformat(real) - datetime.fromisoformat(planned)
    return f"{delta.total_seconds() / 60:+.1f}"


# --- 0. Hvilke argumenter tar estimatedCalls? -----------------------------
# Vi gjetter ikke på parameternavn. Vi spør.
args = ask("""
{
  __type(name: "StopPlace") {
    fields { name args { name type { kind name ofType { kind name } } } }
  }
}
""")["__type"]["fields"]

call_args = next((f["args"] for f in args if f["name"] == "estimatedCalls"), [])
arg_names = {a["name"] for a in call_args}
print("=== Argumenter til estimatedCalls ===")
print("  " + ", ".join(sorted(arg_names)))

start = (datetime.now().astimezone() - timedelta(hours=HOURS_BACK)).isoformat(
    timespec="seconds"
)

parts = ["numberOfDepartures: 25", "whiteListedModes: [rail]"]
if "startTime" in arg_names:
    parts.append(f'startTime: "{start}"')
else:
    print("  ADVARSEL: ingen startTime - kan ikke se bakover i tid.")
if "timeRange" in arg_names:
    parts.append(f"timeRange: {HOURS_BACK * 3600 + 7200}")
if "includeCancelledTrips" in arg_names:
    parts.append("includeCancelledTrips: true")
call_args_str = ", ".join(parts)

# --- 1. Faktisk punktlighet på avganger som har vært ----------------------
print(f"\n=== Avganger fra og med {start[11:16]} ({HOURS_BACK} t tilbake) ===")
print("  tid    linje   plan->forv  plan->faktisk  tilstand      mot\n")

journeys = []

for name in STATIONS:
    svar = hent_json(
        GEOCODER_URL,
        {
            "q": name,
            "layers": "stopPlace",
            "stopPlaceTypes": "railStation",
            "countries": "NO",
            "limit": 1,
        },
        timeout=10,
    )
    features = svar.get("features") or []
    if not features:
        continue
    stop_id = features[0]["properties"]["id"]

    data = ask(
        """
        {
          stopPlace(id: "%s") {
            name
            estimatedCalls(%s) {
              aimedDepartureTime
              expectedDepartureTime
              actualDepartureTime
              realtimeState
              predictionInaccurate
              cancellation
              destinationDisplay { frontText }
              serviceJourney { id line { publicCode } }
            }
          }
        }
        """
        % (stop_id, call_args_str)
    )

    place = data.get("stopPlace") or {}
    calls = place.get("estimatedCalls") or []
    print(f"  {place.get('name')}")

    for call in calls:
        aimed = call.get("aimedDepartureTime")
        journey = call.get("serviceJourney") or {}
        line = (journey.get("line") or {}).get("publicCode") or "?"
        state = call.get("realtimeState") or "?"

        if state.lower() not in ("scheduled", "?"):
            journeys.append((line, journey.get("id")))

        print(
            f"    {str(aimed)[11:16]}  {line:<6} "
            f"{minutes_between(aimed, call.get('expectedDepartureTime')):>10} "
            f"{minutes_between(aimed, call.get('actualDepartureTime')):>14}  "
            f"{state:<12} "
            f"{(call.get('destinationDisplay') or {}).get('frontText') or ''}"
        )
    print()

# --- 2. Følg ett tog langs hele ruta -------------------------------------
if not journeys:
    print("-" * 62)
    print("Ingen avganger med realtimeState ulik SCHEDULED.")
    print("Da har ikke Bane NOR meldt inn noen SJ-tog i dette tidsrommet.")
    raise SystemExit

line, journey_id = journeys[0]
print("=" * 62)
print(f"=== Hele ruta for {line} ({journey_id}) ===\n")

data = ask(
    """
    {
      serviceJourney(id: "%s") {
        estimatedCalls {
          stopPositionInPattern
          aimedDepartureTime
          expectedDepartureTime
          actualDepartureTime
          realtimeState
          quay { name latitude longitude }
        }
      }
    }
    """
    % journey_id
)

calls = (data.get("serviceJourney") or {}).get("estimatedCalls") or []
now = datetime.now().astimezone()
passed = 0

for call in calls:
    quay = call.get("quay") or {}
    aimed = call.get("aimedDepartureTime")
    actual = call.get("actualDepartureTime")
    if actual:
        passed += 1

    print(
        f"  {call.get('stopPositionInPattern'):>3}  {str(quay.get('name'))[:22]:<24} "
        f"{str(aimed)[11:16]}  forv {minutes_between(aimed, call.get('expectedDepartureTime')):>7}  "
        f"faktisk {minutes_between(aimed, actual):>7}  "
        f"{str(quay.get('latitude'))[:7]}, {str(quay.get('longitude'))[:7]}"
    )

print(f"\n  Stopp med faktisk avgangstid: {passed} av {len(calls)}")
print("\n" + "-" * 62)
print("Har stoppene koordinater og noen har actualDepartureTime, kan vi")
print("plassere toget mellom siste passerte og neste stopp - en beregnet")
print("posisjon i stedet for GPS.")
