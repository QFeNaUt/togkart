import os
import argparse
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import sys

from dotenv import load_dotenv

# Fra den tidligere tognummer.py i roten
from entur import _train_number
from historikk import DB_PATH
from prober.felles import VEHICLES_URL, EnturFeil, sporr

# Norske tegn og piler i utskriften. Uten dette faller sjekken over med
# UnicodeEncodeError i det øyeblikket noen rørlegger den videre til en fil
# eller en annen kommando - da er stdout cp1252 på Windows, ikke konsollen.
# Alle probene i prober/ har hatt denne linja hele tiden; sjekk.py manglet den.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

def ask_entur(query: str, timeout: int = 15) -> dict:
    """Spør Vehicle Positions, og avslutt pent hvis Entur avviser spørringen.

    Selve kallet ligger i `prober.felles`. Det som er igjen her er hvordan
    DETTE verktøyet vil dø: `sjekk.py` kjøres for hånd, og en stacktrace er
    feil svar på «feltnavnet ditt finnes ikke». Se felles.py for hvorfor de
    fem utgavene av denne funksjonen ble til én.
    """
    try:
        return sporr(VEHICLES_URL, query, timeout=timeout)
    except EnturFeil as feil:
        innrykket = "\n   - ".join(feil.meldinger)
        raise SystemExit(f"! Entur avviste spørringen:\n   - {innrykket}")

# --- Hjelpefunksjoner ---

def base_kind(type_info: dict) -> str:
    """Skrell vekk NON_NULL og LIST for å finne den egentlige typen."""
    while type_info.get("ofType"):
        type_info = type_info["ofType"]
    return type_info.get("kind", "")

def mins(seconds) -> str:
    return f"{seconds / 60:.1f} min"

def codespace_of(vehicle: dict) -> str:
    space = vehicle.get("codespace")
    if isinstance(space, dict):
        return space.get("codespaceId") or "?"
    ref = (vehicle.get("line") or {}).get("lineRef") or ""
    return ref.split(":")[0] if ":" in ref else "?"

def leading_digits(text: str) -> str:
    """Den gamle, naive metoden for tognummer."""
    digits = ""
    for char in (text or ""):
        if not char.isdigit():
            break
        digits += char
    return digits

# --- Underkommandoer ---

def cmd_diagnose():
    """Forsinkelsestall og feltdekning. Tidligere diagnose.py i roten."""
    INTROSPECT = """
    {
      __type(name: "VehicleUpdate") {
        fields {
          name
          type { kind name ofType { kind name ofType { kind name } } }
        }
      }
    }
    """
    fields = ask_entur(INTROSPECT)["__type"]["fields"]
    available = {f["name"]: base_kind(f["type"]) for f in fields}

    print("=== Felt på VehicleUpdate ===")
    print("  " + ", ".join(sorted(available)))

    SUBFIELDS = {
        "line": "publicCode lineName lineRef",
        "location": "latitude longitude",
        "codespace": "codespaceId",
        "operator": "operatorRef name",
        "serviceJourney": "id",
    }
    WANTED = [
        "vehicleId", "delay", "monitored", "bearing", "speed", "lastUpdated",
        "vehicleStatus", "occupancy", "inCongestion",
        "line", "location", "codespace", "operator",
    ]

    selection = []
    for name in WANTED:
        kind = available.get(name)
        if kind is None:
            continue
        if kind in ("OBJECT", "INTERFACE", "UNION"):
            sub = SUBFIELDS.get(name)
            if sub:
                selection.append(f"{name} {{ {sub} }}")
        else:
            selection.append(name)

    skipped = [n for n in WANTED if n not in available]
    if skipped:
        print(f"\n  (finnes ikke, hoppet over: {', '.join(skipped)})")

    vehicles = ask_entur("{ vehicles(mode: RAIL) { " + " ".join(selection) + " } }")["vehicles"]
    delays = sorted(v["delay"] for v in vehicles if v.get("delay") is not None)

    print(f"\n=== Fordeling ({len(delays)} tog med delay) ===")
    for label, index in [
        ("min   ", 0), ("10 %  ", len(delays) // 10), ("median", len(delays) // 2),
        ("90 %  ", len(delays) * 9 // 10), ("maks  ", -1),
    ]:
        print(f"  {label}  {mins(delays[index]):>12}")

    print("\n=== De 10 verste ===")
    for v in sorted(vehicles, key=lambda v: v.get("delay") or 0, reverse=True)[:10]:
        line = (v.get("line") or {}).get("publicCode") or "?"
        extra = f"monitored={v.get('monitored')}" if "monitored" in available else ""
        status = v.get("vehicleStatus") or ""
        print(f"  {mins(v['delay']):>12}  {line:<8} {codespace_of(v):<6} {extra} {status}")

    for field in ("monitored", "vehicleStatus"):
        if field not in available:
            continue
        print(f"\n=== Delay fordelt på {field} ===")
        groups = defaultdict(list)
        for v in vehicles:
            if v.get("delay") is not None:
                groups[v.get(field)].append(v["delay"])
        for value, values in groups.items():
            values.sort()
            over15 = sum(1 for d in values if d >= 900)
            print(
                f"  {field}={str(value):<12} n={len(values):<4} "
                f"median={mins(values[len(values)//2]):>10}   over 15 min: {over15}"
            )

    print("\n=== Per operatør ===")
    per_space = defaultdict(list)
    for v in vehicles:
        if v.get("delay") is not None:
            per_space[codespace_of(v)].append(v["delay"])
    for space, values in sorted(per_space.items(), key=lambda kv: -len(kv[1])):
        values.sort()
        over15 = sum(1 for d in values if d >= 900)
        print(
            f"  {space:<6} n={len(values):<4} "
            f"median={mins(values[len(values)//2]):>10}   over 15 min: {over15}"
        )

    now = datetime.now(timezone.utc)
    ages = [
        (now - datetime.fromisoformat(v["lastUpdated"].replace("Z", "+00:00"))).total_seconds()
        for v in vehicles if v.get("lastUpdated")
    ]
    if ages:
        ages.sort()
        print("\n=== Alder på siste posisjon ===")
        print(f"  median  {mins(ages[len(ages)//2]):>12}")
        print(f"  eldste  {mins(ages[-1]):>12}")
        print(f"  eldre enn 10 min: {sum(1 for a in ages if a > 600)} av {len(ages)}")

    print("\n=== Utfylte felt ===")
    for field in ("bearing", "speed", "monitored", "vehicleStatus", "occupancy"):
        if field in available:
            filled = sum(1 for v in vehicles if v.get(field) is not None)
            print(f"  {field:<14} {filled} av {len(vehicles)}")

    print("\n=== Linjer i trafikk ===")
    lines = Counter((v.get("line") or {}).get("publicCode") or "?" for v in vehicles)
    print("  " + ", ".join(f"{code}({n})" for code, n in lines.most_common(20)))

def cmd_tognummer():
    """Unikhet i tognummeruttrekket. Tidligere tognummer.py i roten."""
    QUERY = """
    {
      vehicles(mode: RAIL) {
        vehicleId
        line { publicCode }
        codespace { codespaceId }
        serviceJourney { id }
        datedServiceJourney { id }
      }
    }
    """
    vehicles = ask_entur(QUERY)["vehicles"]
    
    per_space = defaultdict(list)
    for v in vehicles:
        per_space[(v.get("codespace") or {}).get("codespaceId") or "?"].append(v)

    print(f"=== Unikhetstest ({len(vehicles)} tog) ===")
    print("  To tog i trafikk samtidig kan ikke dele tognummer.\n")

    for space, group in sorted(per_space.items()):
        print(f"  {space}  ({len(group)} tog)")

        for label, extract in [
            ("vehicleId (gammel)", lambda v: leading_digits(v.get("vehicleId"))),
            ("journey-ID (ny)   ", _train_number),
        ]:
            numbers = [n for n in (extract(v) for v in group) if n]
            unique = len(set(numbers))
            duplicates = [n for n, c in Counter(numbers).most_common() if c > 1]

            if not numbers:
                verdict = "ingen numre tolket"
            elif unique == len(group):
                verdict = "OK"
            else:
                verdict = f"GJENBRUKT: {', '.join(duplicates[:4])}"

            print(f"    {label}  {unique:>3} unike av {len(group):<4} {verdict}")
        print()

    print("=== Eksempler med ny metode ===")
    for space, group in sorted(per_space.items()):
        for v in group[:3]:
            line = (v.get("line") or {}).get("publicCode") or "?"
            journey = (v.get("datedServiceJourney") or v.get("serviceJourney") or {}).get("id")
            print(f"  {space:<5} {line:<7} tog {_train_number(v) or '—':<7} {journey}")

    missing = [v for v in vehicles if not _train_number(v)]
    print("\n" + "-" * 58)
    if not missing:
        print("OK: alle tog fikk tognummer fra journey-ID-en.")
    else:
        print(f"MERK: {len(missing)} tog uten tognummer. ID-ene deres:")
        for v in missing[:5]:
            print("   ", (v.get("serviceJourney") or {}).get("id"),
                  "|", (v.get("datedServiceJourney") or {}).get("id"))

def cmd_operatorer():
    """Hvem publiserer i feeden. Tidligere operatorer.py i roten."""
    print("Henter alle kjøretøy i Norge (kan ta noen sekunder) ...")
    QUERY = """
    {
      vehicles {
        mode
        codespace { codespaceId }
        operator { operatorRef }
        location { latitude }
      }
    }
    """
    vehicles = ask_entur(QUERY, timeout=60)["vehicles"]

    print(f"Totalt: {len(vehicles)} kjøretøy\n")

    grid = defaultdict(lambda: defaultdict(int))
    for v in vehicles:
        space = (v.get("codespace") or {}).get("codespaceId") or "?"
        grid[space][v.get("mode") or "?"] += 1

    print("=== Operatør × transportmodus ===")
    for space in sorted(grid):
        modes = ", ".join(f"{m}: {n}" for m, n in sorted(grid[space].items()))
        print(f"  {space:<8} {modes}")

    RAIL_SPACES = {
        "VYG": "Vy",
        "FLT": "Flytoget",
        "GOA": "Go-Ahead Nordic",
        "SJN": "SJ Norge",
        "SJV": "SJ (variant)",
        "VYT": "Vy Tåg",
        "NSB": "NSB (gammel kode)",
    }

    print("\n=== Kjente togoperatører ===")
    for code, name in RAIL_SPACES.items():
        count = sum(grid.get(code, {}).values())
        status = f"{count} kjøretøy" if count else "IKKE I FEEDEN"
        print(f"  {code:<6} {name:<20} {status}")

    rail = [v for v in vehicles if (v.get("mode") or "").lower() == "rail"]
    without = [v for v in rail if not (v.get("location") or {}).get("latitude")]
    print(f"\n=== Tog ===")
    print(f"  med mode=rail:      {len(rail)}")
    print(f"  uten posisjon:      {len(without)}")

    print("\n" + "-" * 56)
    rail_by_space = {
        space: modes.get("rail", 0) + modes.get("RAIL", 0) for space, modes in grid.items()
    }
    sj_trains = sum(rail_by_space.get(code, 0) for code in ("SJN", "SJV", "NSB"))

    if sj_trains:
        print(f"SJ HAR {sj_trains} TOG i feeden. Da er det spørringen vår som")
        print("filtrerer dem bort, ikke datamangel.")
    else:
        sj_other = sum(grid.get("SJN", {}).values())
        print("BEKREFTET: SJ publiserer ingen togposisjoner.")
        if sj_other:
            print(f"           (SJN har {sj_other} kjøretøy, men ingen av dem er tog -")
            print("            trolig buss for tog.)")
        print("           Tog nord for Lillehammer kan ikke hentes herfra.")
        print("           Punktlighet må hentes fra Journey Planner v3 via")
        print("           stoppestedsavganger i stedet for kjøretøyposisjon.")

    print("\n=== Publiserer tog ===")
    for space, count in sorted(rail_by_space.items(), key=lambda kv: -kv[1]):
        if count:
            print(f"  {space:<6} {count}")

def base_type(type_info: dict) -> tuple[str, str]:
    """Skrell vekk NON_NULL og LIST. Returner (navn, kind) på den egentlige typen.

    GraphQL pakker typer i lag: [MonitoredCall!]! er LIST rundt NON_NULL rundt
    OBJECT. Bare det innerste laget har navn - og det navnet er det du må bruke
    hvis du vil slå opp typen etterpå.
    """
    while type_info.get("ofType"):
        type_info = type_info["ofType"]
    return type_info.get("name") or "?", type_info.get("kind") or "?"


SKJEMA_SPORRING = """
{
  __type(name: "%s") {
    name
    kind
    fields {
      name
      type {
        kind name
        ofType { kind name ofType { kind name ofType { kind name } } }
      }
    }
  }
}
"""

# Felt vi alltid vil ha øye på når vi leter etter en målt forsinkelse.
INTERESSANTE = (
    "time", "aimed", "expected", "actual", "delay",
    "arrival", "departure", "progress",
)


def cmd_skjema(typenavn: str = "VehicleUpdate", dybde: int = 2):
    """Skriv ut hvilke felt en type i Entur-skjemaet faktisk har.

    Skrevet fordi antakelser om skjemaet har kostet dette prosjektet mer tid
    enn noe annet enkelt problem. `pointsOnLink` lå på et annet nivå enn
    ventet, `speed` er tomt for samtlige tog, `vehicleStatus` likeså, og
    `monitoredCall` viste seg å ikke ha tidsfelt der vi trodde. Det er
    billigere å spørre enn å gjette, og et gjettet felt velter hele spørringen.

    Objektfelt følges nedover til `dybde`. Typer som allerede er skrevet ut
    hoppes over, ellers går den i ring på selvrefererende typer.
    """
    besokt = set()
    funn = []

    def skriv(navn: str, nivaa: int, sti: str):
        if navn in besokt or nivaa > dybde:
            return
        besokt.add(navn)

        data = ask_entur(SKJEMA_SPORRING % navn).get("__type")
        innrykk = "  " * nivaa
        if not data:
            print(f"{innrykk}=== {navn}: FINNES IKKE i skjemaet ===")
            return

        felter = data.get("fields") or []
        print(f"\n{innrykk}=== {navn} ({len(felter)} felt) ===")

        barn = []
        for felt in sorted(felter, key=lambda f: f["name"]):
            feltnavn = felt["name"]
            type_navn, type_kind = base_type(felt["type"])
            full_sti = f"{sti}.{feltnavn}" if sti else feltnavn

            treff = any(ord_ in feltnavn.lower() for ord_ in INTERESSANTE)
            merke = "   <--" if treff else ""
            print(f"{innrykk}  {feltnavn:<26} {type_navn:<20} {type_kind}{merke}")

            if treff:
                funn.append((full_sti, type_navn, type_kind))
            if type_kind in ("OBJECT", "INTERFACE"):
                barn.append((type_navn, full_sti))

        for barnenavn, barnesti in barn:
            skriv(barnenavn, nivaa + 1, barnesti)

    skriv(typenavn, 0, "")

    print("\n=== Felt som kan bære en tid eller et avvik ===")
    if funn:
        for sti, type_navn, type_kind in funn:
            print(f"  {sti:<46} {type_navn:<20} {type_kind}")
        print("\n  Er et av disse et tidspar (aimed/expected), har vi en målt")
        print("  punktlighet å sammenligne `delay` med. Er de bare referanser")
        print("  og rekkefølge, ligger målingen ikke i denne feeden.")
    else:
        print("  Ingen. Det er også et svar - da ligger målingen et annet sted.")


def skalarfelt(typenavn: str) -> list[str]:
    """Navnene på alle skalar- og enum-felt i en type.

    Lar oss be om et objektfelt uten å vite på forhånd hva som ligger i det.
    Ber du om et objektfelt uten å si hvilke underfelt du vil ha, avviser
    GraphQL HELE spørringen - og da mister du også feltene du var ute etter.
    """
    data = ask_entur(SKJEMA_SPORRING % typenavn).get("__type")
    if not data:
        return []
    blader = []
    for felt in data.get("fields") or []:
        _, kind = base_type(felt["type"])
        if kind in ("SCALAR", "ENUM"):
            blader.append(felt["name"])
    return blader


def _tverrmodus(call_felt: list[str]):
    """Er feltene tomme for alle, eller bare for tog?

    Skiller "Entur mangler støtte" fra "våre operatører publiserer det ikke".
    Samme spørsmål som lærdom 4: at et kodespak finnes i feeden betyr ikke at
    det kjører tog, og at et felt finnes i skjemaet betyr ikke at noen fyller
    det ut.
    """
    print("\nHenter alle kjøretøy i Norge for å se om feltene er tomme")
    print("overalt, eller bare for tog (kan ta noen sekunder) ...")

    call = "monitoredCall { " + " ".join(call_felt) + " }" if call_felt else ""
    alle = ask_entur("{ vehicles { mode destinationRef " + call + " } }", timeout=60)["vehicles"]

    per_mode = defaultdict(lambda: [0, 0, 0])
    for v in alle:
        rad = per_mode[v.get("mode") or "?"]
        rad[0] += 1
        if v.get("monitoredCall"):
            rad[1] += 1
        if v.get("destinationRef"):
            rad[2] += 1

    print(f"\n  modus          n   monitoredCall   destinationRef")
    print("  " + "-" * 52)
    for mode, (n, c, d) in sorted(per_mode.items()):
        print(f"  {mode:<10} {n:>6}   {c:>13}   {d:>14}")

    andre = sum(
        c for mode, (n, c, d) in per_mode.items() if (mode or "").lower() != "rail"
    )
    print("\n" + "-" * 70)
    if andre:
        print("Feltene ER utfylt for andre transportmidler. Da er det")
        print("togoperatørene som ikke publiserer dem - ikke Entur som mangler")
        print("støtte. Det er ingenting vi kan rette på fra vår side.")
    else:
        print("Feltene er tomme i hele feeden, for alle transportmidler.")
    print()
    print("Uansett hvilken av delene: Vehicle Positions kan ikke svare på")
    print("problem 1. Den målte punktligheten må hentes fra Journey Planner")
    print("v3 - samme kilde og samme metode som sjnord.py bruker for SJ.")


def cmd_spokelser():
    """Vokser `delay` på tog som er ferdige med turen sin?

    17. august meldte Flytoget median 17 minutters forsinkelse, og FLY1 meldte
    19 minutter på en strekning som tar 20 - med en posisjon som var 16
    sekunder gammel. Et tog kan ikke være nesten en hel reisetid forsinket og
    samtidig rapportere aktivt.

    `vehicleStatus` er tomt for alle tog og `monitored` er true for alle, så
    ingen av dem skiller gruppene. `monitoredCall` har ingen tidsfelt - det er
    undersøkt, se `sjekk.py skjema MonitoredCall`. Men den har `stopPointRef`
    og `vehicleAtStop`, og `VehicleUpdate` har `destinationRef`. Er neste stopp
    lik destinasjonen og toget står der, er turen over. Vokser `delay` videre
    etter det, måler den ikke lenger noe som skjer.

    Denne sjekken beviser ingenting midt på dagen. Kjør den mellom 22 og 24,
    når settene er ferdige for dagen. Det er da hypotesen har noe å måle.
    """
    fields = ask_entur(SKJEMA_SPORRING % "VehicleUpdate")["__type"]["fields"]
    available = {f["name"]: base_type(f["type"]) for f in fields}

    ONSKET = [
        "vehicleId", "delay", "lastUpdated", "monitored", "vehicleStatus",
        "destinationRef", "destinationName", "originName",
    ]
    selection = [
        navn for navn in ONSKET
        if available.get(navn, ("", ""))[1] in ("SCALAR", "ENUM")
    ]
    selection += [
        "line { publicCode }",
        "codespace { codespaceId }",
        "serviceJourney { id }",
        "datedServiceJourney { id }",
    ]

    manglet = [n for n in ONSKET if n not in available]
    if manglet:
        print(f"  (finnes ikke, hoppet over: {', '.join(manglet)})")

    # monitoredCall og progressBetweenStops er objekter. Vi spør skjemaet hva
    # de inneholder i stedet for å gjette - se lærdommen i cmd_skjema.
    call_navn = available.get("monitoredCall", ("", ""))[0]
    call_felt = skalarfelt(call_navn) if call_navn and call_navn != "?" else []
    if call_felt:
        selection.append("monitoredCall { " + " ".join(call_felt) + " }")

    prog_navn, prog_kind = available.get("progressBetweenStops", ("", ""))
    prog_felt = skalarfelt(prog_navn) if prog_kind in ("OBJECT", "INTERFACE") else []
    if prog_felt:
        selection.append("progressBetweenStops { " + " ".join(prog_felt) + " }")

    vehicles = ask_entur("{ vehicles(mode: RAIL) { " + " ".join(selection) + " } }")["vehicles"]

    def call_of(v):
        return v.get("monitoredCall") or {}

    def framme(v):
        """Er neste stopp det samme som destinasjonen?"""
        neste = call_of(v).get("stopPointRef")
        mal = v.get("destinationRef")
        return bool(neste and mal and neste == mal)

    med_call = [v for v in vehicles if call_of(v)]
    print(f"=== {len(vehicles)} tog ===")
    print(f"  med monitoredCall.................... {len(med_call)}")
    print(f"  med destinationRef.................. "
          f"{sum(1 for v in vehicles if v.get('destinationRef'))}")
    print(f"  vehicleAtStop=true.................. "
          f"{sum(1 for v in vehicles if call_of(v).get('vehicleAtStop'))}")
    print(f"  neste stopp = destinasjon........... {sum(1 for v in vehicles if framme(v))}")

    # VAKT. Tomt felt og nullverdi ser like ut i en tabell og betyr helt
    # forskjellige ting. Er ingenting utfylt, KAN vi ikke svare - og da skal
    # skriptet si nettopp det, ikke regne videre og trekke en konklusjon av
    # fravær. Uten denne meldte proben "hypotesen faller" 18. august, mens
    # sannheten var at den ikke hadde målt noe som helst.
    if not med_call and not any(v.get("destinationRef") for v in vehicles):
        print("\n" + "=" * 70)
        print("IKKE MÅLBART: feltene finnes i skjemaet, men er tomme for")
        print("samtlige tog. Samme historie som speed og vehicleStatus.")
        print("Ingen konklusjon kan trekkes herfra - verken for eller imot.")
        print("=" * 70)
        _tverrmodus(call_felt)
        return

    print("\n=== Eksempler: neste stopp mot destinasjon ===")
    print("  Se på formatene FØR du stoler på likhetstesten over. Er neste")
    print("  stopp en Quay og destinasjonen en StopPlace, blir de aldri like")
    print("  som strenger, og tallet over er null av feil grunn.")
    for v in med_call[:4]:
        call = call_of(v)
        line = (v.get("line") or {}).get("publicCode") or "?"
        print(f"\n  {line:<6} tog {_train_number(v) or '—':<6} "
              f"order={call.get('order')}  vedStopp={call.get('vehicleAtStop')}")
        print(f"     neste  {call.get('stopPointRef')}")
        print(f"     mål    {v.get('destinationRef')}   ({v.get('destinationName')})")
        if prog_felt:
            print(f"     progresjon  {v.get('progressBetweenStops')}")

    def fordeling(tittel, nokkel):
        grupper = defaultdict(list)
        for v in vehicles:
            if v.get("delay") is not None:
                grupper[nokkel(v)].append(v["delay"])
        print(f"\n=== Delay fordelt på {tittel} ===")
        for verdi, tall in sorted(grupper.items(), key=lambda kv: str(kv[0])):
            tall.sort()
            over15 = sum(1 for d in tall if d >= 900)
            print(f"  {str(verdi):<12} n={len(tall):<4} "
                  f"median={mins(tall[len(tall)//2]):>10}   over 15 min: {over15}")

    fordeling("vehicleAtStop", lambda v: call_of(v).get("vehicleAtStop"))
    fordeling("neste stopp = destinasjon", framme)

    now = datetime.now(timezone.utc)

    def alder(v):
        sett = v.get("lastUpdated")
        if not sett:
            return None
        return (now - datetime.fromisoformat(sett.replace("Z", "+00:00"))).total_seconds()

    print("\n=== Tog over 15 minutter ===")
    verstinger = sorted(
        (v for v in vehicles if (v.get("delay") or 0) >= 900),
        key=lambda v: -v["delay"],
    )
    if not verstinger:
        print("  Ingen. Det er ikke et svar - det er feil klokkeslett.")
        print("  Hypotesen handler om tog som er ferdige for dagen.")
        print("  Kjør denne igjen mellom 22 og 24.")
    else:
        print("  linje  tog      delay    alder  vedStopp  framme  destinasjon")
        print("  " + "-" * 68)
        for v in verstinger:
            line = (v.get("line") or {}).get("publicCode") or "?"
            a = alder(v)
            print(
                f"  {line:<6} {_train_number(v) or '—':<6} "
                f"{mins(v['delay']):>9} "
                f"{(mins(a) if a is not None else '?'):>8} "
                f"{str(call_of(v).get('vehicleAtStop')):>9} "
                f"{str(framme(v)):>7}  {v.get('destinationName') or '?'}"
            )

        ved_mal = sum(1 for v in verstinger if framme(v) or call_of(v).get("vehicleAtStop"))
        print("\n" + "-" * 70)
        if ved_mal == len(verstinger):
            print("SAMTLIGE forsinkede tog står stille ved et stopp eller er framme.")
            print("Da måler `delay` en tur som er over. Fiksen hører hjemme i")
            print("entur.py, ved siden av STALE_AFTER_SECONDS: er toget framme,")
            print("skal det ut av punktlighetsstatistikken på samme måte som")
            print("spøkelsestogene - tegnes, men ikke telles.")
        elif ved_mal:
            print(f"{ved_mal} av {len(verstinger)} står ved et stopp eller er framme.")
            print("Delvis treff. Se på de andre: har de fersk posisjon og er")
            print("underveis, finnes det ekte forsinkelser her også, og da må")
            print("filteret være presist og ikke bare strengt.")
        else:
            print("INGEN av dem er framme eller står stille. Da faller")
            print("hypotesen, og forsinkelsene er trolig ekte. Sjekk mot")
            print("Bane NOR før du melder det som et issue.")


# ---------------------------------------------------------------------------
# Samlet helsesjekk
# ---------------------------------------------------------------------------
# `alle` svarer på ETT spørsmål: har noe flyttet på seg siden sist? Derfor gir
# den dommer og ikke fordelinger. Fem fulle utskrifter etter hverandre blir
# 200 linjer, og en helsesjekk du slutter å lese er verre enn ingen - den gir
# følelsen av kontroll uten innholdet. Detaljene ligger i underkommandoene.
#
# Alt her kommer fra ÉN spørring. Det er ikke bare raskere: kjører sjekkene
# hver sin henting, sammenligner du tall fra ulike øyeblikk, og da blir små
# avvik til spøkelser du jakter forgjeves på.
ALLE_SPORRING = """
{
  vehicles(mode: RAIL) {
    vehicleId
    delay
    speed
    bearing
    lastUpdated
    monitored
    vehicleStatus
    monitoredCall { stopPointRef }
    line { publicCode lineRef }
    codespace { codespaceId }
    location { latitude longitude }
    serviceJourney { id }
    datedServiceJourney { id }
  }
}
"""


def _tur_id(v: dict) -> str:
    return ((v.get("datedServiceJourney") or v.get("serviceJourney")) or {}).get("id") or ""


def _median(tall: list[float]) -> float:
    tall = sorted(tall)
    return tall[len(tall) // 2] if tall else 0.0


def cmd_linjekoder(vehicles=None):
    """Har togene linjekode, eller bare rå NeTEx-referanse?

    18. august manglet `publicCode` for 90 av 101 tog i noen minutter, og var
    tilbake ved neste kjøring. Det er verre enn et felt som alltid mangler:
    du bygger på det fordi det virker, og så står det «VYG:Line:R13» i
    popup-en en tirsdag ettermiddag uten at noe er endret hos deg.
    """
    if vehicles is None:
        vehicles = ask_entur(ALLE_SPORRING)["vehicles"]

    mangler = [v for v in vehicles if not (v.get("line") or {}).get("publicCode")]
    print(f"=== Linjekoder ({len(vehicles)} tog) ===")
    print(f"  med publicCode....... {len(vehicles) - len(mangler)}")
    print(f"  uten................. {len(mangler)}")

    if not mangler:
        print("\n  Alle tog har linjekode.")
        return

    print("\n  Uten publicCode, per operatør:")
    for space, antall in Counter(codespace_of(v) for v in mangler).most_common():
        print(f"    {space:<6} {antall}")
    print("\n  Eksempler på hva _line_code faller tilbake på:")
    for v in mangler[:5]:
        print(f"    {(v.get('line') or {}).get('lineRef')}")
    print("\n  Dette blir linjenavnet i popup-en. Klipp på siste kolon i")
    print("  entur._line_code, slik _nokkel i app.py allerede gjør.")


def cmd_historikk():
    """Skriver historikk.py faktisk noe? Leser historikk.db, ingen nett.

    Ingen egen dom her - `sjekk.py alle` handler om Entur-feeden akkurat nå,
    og historikken er en annen tidsskala. Denne sjekken svarer på ett
    spørsmål: har databasen fått nye rader nylig, eller står den stille mens
    serveren tror den logger?
    """
    if not os.path.exists(DB_PATH):
        print(f"Fant ingen database på '{DB_PATH}'.")
        print("Den opprettes automatisk første gang app.py eller sjekk.py")
        print("importerer historikk.py - kjør serveren minst én gang.")
        return

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        total = con.execute("SELECT COUNT(*) AS n FROM observasjoner").fetchone()["n"]
        if total == 0:
            print(f"Databasen '{DB_PATH}' finnes, men er tom.")
            print("Ingen tog har flyttet seg eller endret forsinkelse siden")
            print("serveren startet - eller så har uvicorn ikke kjørt ennå.")
            return

        eldst, nyest = con.execute(
            "SELECT MIN(tidspunkt) AS eldst, MAX(tidspunkt) AS nyest FROM observasjoner"
        ).fetchone()
        distinkte = con.execute(
            "SELECT COUNT(DISTINCT tog_id) AS n FROM observasjoner"
        ).fetchone()["n"]

        # Grensen regnes ut i Python, ikke med SQLite sin datetime().
        #
        # Den sto som `datetime('now', '-1 hour')` til 21. august, og det er
        # en sammenligning som ser riktig ut og teller feil. SQLite svarer
        # "2026-08-21 13:24:18" - mellomrom, ingen sone - mens vi lagrer
        # "2026-08-21T14:23:33.444835+00:00". Sammenligningen er tekstlig, og
        # "T" (0x54) sorterer etter mellomrom (0x20). Dermed matcher HVER rad
        # fra dagens dato, uansett klokkeslett.
        #
        # Målt da den ble funnet: sjekken meldte 30 624 rader "siste time"
        # mens det riktige tallet var 4 856. Den talte rader i dag.
        #
        # Det gjør mer enn å vise et for høyt tall. Sjekken finnes for å
        # oppdage at loggingen har STOPPET - og en logging som stanset klokka
        # to om natta ville fortsatt meldt et friskt firesifret tall helt til
        # midnatt. Alarmen ville gått av først når den ikke lenger trengtes.
        fra = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        siste_time = con.execute(
            "SELECT COUNT(*) AS n FROM observasjoner WHERE tidspunkt > ?",
            (fra,),
        ).fetchone()["n"]

        print(f"=== {DB_PATH} ===")
        print(f"  rader totalt.......... {total}")
        print(f"  ulike tog.............. {distinkte}")
        print(f"  eldste rad............. {eldst}")
        print(f"  nyeste rad............. {nyest}")
        print(f"  siste time.............. {siste_time} rader")

        if siste_time == 0:
            now = datetime.now(timezone.utc).isoformat()
            print(f"\n  MERK: ingen rader siste time (nå: {now}).")
            print("  Enten er ingen tog i drift, eller så er uvicorn ikke")
            print("  koblet til /api/trains i det hele tatt for øyeblikket.")

        # Operatørmerkingen er det `analyse.py` grupperer på. Mangler den,
        # forsvinner radene ut av operatørrangeringen uten å si fra - de
        # havner i «utenOperator» og telles ikke med noen steder. Kolonnen
        # etterfylles av seg selv (se `_etterfyll` i historikk.py), men bare
        # for linjer appen har sett kjøre siden kolonnen kom. En linje som
        # ikke har gått siden da står igjen her.
        print("\n=== Operatørmerking ===")
        for rad in con.execute(
            "SELECT COALESCE(operator, '(mangler)') AS operator, COUNT(*) AS n "
            "FROM observasjoner GROUP BY operator ORDER BY n DESC"
        ):
            print(f"  {rad['operator']:<12} {rad['n']:>6} rader")

        umerkede = con.execute(
            "SELECT DISTINCT linje FROM observasjoner "
            "WHERE operator IS NULL AND linje <> '' ORDER BY linje"
        ).fetchall()
        if umerkede:
            linjer = ", ".join(rad["linje"] for rad in umerkede)
            print(f"\n  MERK: {len(umerkede)} linjer uten operatør: {linjer}")
            print("  De fylles inn neste gang linja kjører med serveren oppe.")

        print("\n=== Mest loggede tog (topp 5) ===")
        for rad in con.execute(
            "SELECT tog_id, COUNT(*) AS n, MIN(tidspunkt) AS start, "
            "MAX(tidspunkt) AS slutt FROM observasjoner "
            "GROUP BY tog_id ORDER BY n DESC LIMIT 5"
        ):
            print(f"  {rad['tog_id']:<12} {rad['n']:>5} rader   {rad['start']} → {rad['slutt']}")
    finally:
        con.close()


def cmd_alle():
    """Alle raske sjekker i én kjøring, med dom per sjekk."""
    print(f"sjekk.py alle - {datetime.now().strftime('%d.%m.%Y %H:%M')}\n")
    vehicles = ask_entur(ALLE_SPORRING)["vehicles"]
    dommer = []

    def dom(navn, verdi, tekst):
        dommer.append((navn, verdi, tekst))

    # -- Operatører. Hele feeden ligger i `sjekk.py operatorer`; her ser vi
    #    bare om noen av de tre som skal publisere tog har forsvunnet.
    per_space = Counter(codespace_of(v) for v in vehicles)
    borte = [k for k in ("VYG", "FLT", "GOA") if not per_space.get(k)]
    oversikt = ", ".join(f"{k} {a}" for k, a in per_space.most_common())
    if borte:
        dom("operatorer", "FEIL", f"ingen tog fra {', '.join(borte)}. {oversikt}")
    elif per_space.get("SJN"):
        dom("operatorer", "NYTT", f"SJN er i feeden med {per_space['SJN']} tog")
    else:
        dom("operatorer", "OK", oversikt)

    # -- Linjekoder
    uten_kode = sum(1 for v in vehicles if not (v.get("line") or {}).get("publicCode"))
    if uten_kode > len(vehicles) // 20:
        dom("linjekoder", "FEIL", f"{uten_kode} av {len(vehicles)} mangler publicCode")
    elif uten_kode:
        dom("linjekoder", "MERK", f"{uten_kode} av {len(vehicles)} mangler publicCode")
    else:
        dom("linjekoder", "OK", f"alle {len(vehicles)} har linjekode")

    # -- Tognummer. Dobbeltsett deler tognummer OG tur-ID, og er ikke en feil.
    #    Deler to kjøretøy nummer uten å dele tur, har _train_number tolket
    #    feil - og det er den ekte alarmen.
    kolliderer, dobbeltsett = [], 0
    for space in per_space:
        etter_nummer = defaultdict(list)
        for v in vehicles:
            if codespace_of(v) != space:
                continue
            nummer = _train_number(v)
            if nummer:
                etter_nummer[nummer].append(v)
        for nummer, delte in etter_nummer.items():
            if len(delte) < 2:
                continue
            if len({_tur_id(v) for v in delte}) == 1:
                dobbeltsett += 1
            else:
                kolliderer.append(f"{space} {nummer}")

    uten_nummer = sum(1 for v in vehicles if not _train_number(v))
    if kolliderer:
        dom("tognummer", "FEIL", f"ulike turer deler nummer: {', '.join(kolliderer[:3])}")
    elif uten_nummer:
        dom("tognummer", "FEIL", f"{uten_nummer} tog uten tognummer")
    else:
        hale = f", {dobbeltsett} dobbeltsett" if dobbeltsett else ""
        dom("tognummer", "OK", f"alle {len(vehicles)} tolket{hale}")

    # -- Forsinkelser. Aldri FEIL: vi VET at `delay` er upålitelig, se
    #    problem 1. Sjekken finnes for å se om nivået flytter seg.
    delays = [v["delay"] for v in vehicles if v.get("delay") is not None]
    over15 = sum(1 for d in delays if d >= 900)
    per_op = ", ".join(
        f"{space} {_median([v['delay'] for v in vehicles if codespace_of(v) == space and v.get('delay') is not None]) / 60:.1f}"
        for space in ("VYG", "FLT", "GOA") if per_space.get(space)
    )
    tekst = f"median {_median(delays) / 60:.1f} min ({per_op}), {over15} over 15 min"
    dom("forsinkelser", "MERK" if over15 else "OK", tekst)

    # -- Posisjon og alder
    na = datetime.now(timezone.utc)
    aldre = []
    for v in vehicles:
        sett = v.get("lastUpdated")
        if sett:
            aldre.append((na - datetime.fromisoformat(sett.replace("Z", "+00:00"))).total_seconds())
    uten_pos = sum(1 for v in vehicles if not (v.get("location") or {}).get("latitude"))
    gamle = sum(1 for a in aldre if a > 300)
    tekst = f"median {_median(aldre) / 60:.1f} min, {gamle} eldre enn 5 min, {uten_pos} uten posisjon"
    dom("posisjon", "MERK" if uten_pos else "OK", tekst)

    # -- Tomme felt. Blir noen av disse utfylt, er det GODE nyheter:
    #    monitoredCall utfylt betyr at problem 1 kan løses i denne feeden.
    nytt = []
    for felt in ("speed", "vehicleStatus", "monitoredCall"):
        utfylt = sum(1 for v in vehicles if v.get(felt))
        if utfylt:
            nytt.append(f"{felt} {utfylt}")
    if nytt:
        dom("tomme felt", "NYTT", f"nå utfylt: {', '.join(nytt)} - se problem 1")
    else:
        bearing = sum(1 for v in vehicles if v.get("bearing") is not None)
        dom("tomme felt", "OK",
            f"speed/vehicleStatus/monitoredCall tomme som før, bearing {bearing}")

    bredde = max(len(navn) for navn, _, _ in dommer)
    for navn, verdi, tekst in dommer:
        print(f"  {navn:<{bredde}}  {verdi:<4}  {tekst}")

    feil = sum(1 for _, v, _ in dommer if v == "FEIL")
    merk = sum(1 for _, v, _ in dommer if v in ("MERK", "NYTT"))
    print()
    if feil:
        print(f"  {feil} feil, {merk} merknad(er). Kjør underkommandoen for detaljer.")
    elif merk:
        print(f"  Ingen feil, {merk} merknad(er).")
    else:
        print("  Alt grønt.")
    print("\n  Ikke med her: `operatorer` (hele feeden, treg), `skjema` (tar")
    print("  argument), `historikk` (annen tidsskala, leser en fil), og")
    print("  probene i prober/ som krever natt eller uvicorn.")


# --- Hovedprogram for kommandolinjeargumenter ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Helsesjekk-verktøy for Entur-data.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("diagnose", help="Fordeling, verstinger, alder på posisjon, per operatør")
    subparsers.add_parser("tognummer", help="Er tognummeruttrekket unikt per operatør?")
    subparsers.add_parser("operatorer", help="Hvem publiserer posisjon, i hvilken modus? Treg - hele feeden")

    skjema = subparsers.add_parser(
        "skjema", help="Utforsk Entur-skjemaet: hvilke felt har en type egentlig?"
    )
    skjema.add_argument(
        "type", nargs="?", default="VehicleUpdate",
        help="Typenavn, f.eks. MonitoredCall. Standard: VehicleUpdate",
    )
    skjema.add_argument(
        "--dybde", type=int, default=2,
        help="Hvor mange nivåer ned i objektfelt vi følger (standard: 2)",
    )

    subparsers.add_parser(
        "spokelser",
        help="Vokser delay på tog som er framme? Kjør mellom 22 og 24",
    )
    subparsers.add_parser("linjekoder", help="Har togene publicCode, eller bare lineRef?")
    subparsers.add_parser("historikk", help="Skriver historikk.py faktisk noe? Leser historikk.db, ingen nett")
    subparsers.add_parser("alle", help="Alle raske sjekker i én kjøring, med dom per sjekk")

    args = parser.parse_args()

    if args.command == "diagnose":
        cmd_diagnose()
    elif args.command == "tognummer":
        cmd_tognummer()
    elif args.command == "operatorer":
        cmd_operatorer()
    elif args.command == "skjema":
        cmd_skjema(args.type, args.dybde)
    elif args.command == "spokelser":
        cmd_spokelser()
    elif args.command == "linjekoder":
        cmd_linjekoder()
    elif args.command == "historikk":
        cmd_historikk()
    elif args.command == "alle":
        cmd_alle()