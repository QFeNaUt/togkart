"""Kobling fra kjøretøy-ID eller linje til togsett og materielldata.

Det finnes ingen Flightradar-aktig API for norsk jernbanemateriell. Vi har to
kilder, og de dekker forskjellige tog:

  1. vehicleId  - Flytoget og Go-Ahead legger settnummeret der:
                    "71-15"   -> BM71 sett 15
                    "18-2258" -> EL18 lok 2258
                  Vy legger tognummeret der, så for dem finnes ingenting.

  2. linje      - SJ har ingen vehicleId. Men vi vet hvilke typer som normalt
                  kjører hver bane, så F7 Nordlandsbanen kan merkes med Di 4
                  og Type 93. Dette er TYPISK materiell, ikke en måling av
                  hva som faktisk står på sporet i dag.

  3. trafikktype - Enturs `transportSubmode` på turen: lokaltog, regiontog,
                  fjerntog, nattog. Det er ikke materiell, men det er MÅLT, og
                  det er det eneste Entur har som beskriver Vy-togene. Se
                  `service_type()`.

Undersøkt 22. august, så ingen leter en gang til: Entur har INGEN materielldata
for Vy. Verken Vehicle Positions (26 felt, ingen om materiell) eller Journey
Planner har det - `notices` var tomt for 60 av 60 turer, `publicCode` var None,
og `privateCode` viste seg å være tognummeret om igjen.

Godsoperatører (CargoNet, OnRail, Green Cargo, LKAB, Hector Rail) og SJ AB
på Oslo-Stockholm er ikke i Entur i det hele tatt. Materiellet deres er
bevisst utelatt - det ville aldri blitt slått opp.

KILDER OG SIKKERHET
  Antall i parken:  norsketog.no/tog, verifisert
  EL18 effekt:      5880 kW maks / 5400 kW kontinuerlig, Wikipedia
  Type 78:          8 sett, 4 vogner, 245 km/t, 5280 kW, CAF Oaris
  Øvrige tall:      åpne kilder, skrevet inn for hånd. Kontroller mot
                    norsketog.no før du publiserer noe basert på dem.

BM73 finnes i A- og B-serie, BM69 i C, D, G og H. Settnummeret alene skiller
dem ikke, så typen vises uten serie.
"""

import re

# Kjøretøy-ID på formen "type-sett": to sifre, bindestrek, to til fire sifre.
# Vy sin "1214-2026-08-17" faller utenfor både på lengde og antall ledd.
_UNIT = re.compile(r"^(\d{2})-(\d{2,4})$")

# Lokomotiver trekker vogner; motorvognsett er selvgående. Ordbruken følger
# etter: et lok har et loknummer, et sett har et settnummer.
_LOCOMOTIVES = {"18", "4"}

CLASSES: dict[str, dict] = {
    "69": {
        "type": "BM69", "maker": "NEBB / Strømmens Verksted",
        "built": "1970–1993", "topSpeed": 130, "drive": "Elektrisk",
        "power": None, "use": "Lokaltog", "fleet": 35,
    },
    "70": {
        "type": "BM70", "maker": "Strømmens Verksted",
        "built": "1993", "topSpeed": 160, "drive": "Elektrisk",
        "power": None, "use": "Regiontog", "fleet": 6,
    },
    "71": {
        "type": "BM71", "maker": "Adtranz Strømmen",
        "built": "1997–1998", "topSpeed": 210, "drive": "Elektrisk",
        "power": None, "use": "Flytoget", "fleet": 15,
    },
    "72": {
        "type": "BM72", "maker": "AnsaldoBreda / Adtranz",
        "built": "2002", "topSpeed": 160, "drive": "Elektrisk",
        "power": None, "use": "Lokaltog", "fleet": 36,
    },
    "73": {
        "type": "BM73", "maker": "Adtranz",
        "built": "1999–2002", "topSpeed": 210, "drive": "Elektrisk, krengende",
        "power": None, "use": "Fjerntog og intercity", "fleet": 20,
    },
    "74": {
        "type": "BM74", "maker": "Stadler (FLIRT)",
        "built": "2012–", "topSpeed": 200, "drive": "Elektrisk",
        "power": None, "use": "Regiontog", "fleet": 53,
    },
    "75": {
        "type": "BM75", "maker": "Stadler (FLIRT)",
        "built": "2012–", "topSpeed": 200, "drive": "Elektrisk",
        "power": None, "use": "Lokaltog", "fleet": 83,
    },
    "76": {
        "type": "BM76", "maker": "Stadler (FLIRT)",
        "built": "2018–", "topSpeed": 200, "drive": "Bimodal (kontaktledning + diesel)",
        "power": None, "use": "Regiontog på delvis elektrifiserte baner", "fleet": 14,
    },
    "78": {
        "type": "BM78", "maker": "CAF (Oaris)",
        "built": "2021–", "topSpeed": 245, "drive": "Elektrisk",
        "power": 5280, "use": "Flytoget", "fleet": 8,
    },
    "92": {
        "type": "BM92", "maker": "Duewag",
        "built": "1984–1985", "topSpeed": 140, "drive": "Diesel",
        "power": None, "use": "Uelektrifiserte baner", "fleet": 3,
    },
    "93": {
        "type": "BM93", "maker": "Bombardier (Talent)",
        "built": "2000–2002", "topSpeed": 140, "drive": "Diesel",
        "power": None, "use": "Uelektrifiserte baner", "fleet": 15,
    },
    "18": {
        "type": "EL18", "maker": "Adtranz / SLM",
        "built": "1996–1997", "topSpeed": 200, "drive": "Elektrisk",
        "power": 5400, "use": "Fjerntog og nattog", "fleet": 16,
    },
    "4": {
        "type": "Di 4", "maker": "Henschel",
        "built": "1980–1981", "topSpeed": 140, "drive": "Dieselelektrisk",
        "power": 2460, "use": "Fjerntog på Nordlandsbanen", "fleet": 4,
    },
}

# SJ har ingen vehicleId. Men vi vet hvilke typer som normalt kjører hver bane.
# Merk: dette er hva som VANLIGVIS går der, ikke hva som faktisk kjører i dag.
# Norske tog påpeker selv at operatørene bytter materiell etter behov.
LINE_STOCK: dict[str, list[str]] = {
    "F6": ["73", "18"],   # Dovrebanen Oslo-Trondheim
    "F7": ["4", "93"],    # Nordlandsbanen Trondheim-Bodø
    "R60": ["93"],        # Rørosbanen
    "R65": ["93"],        # Raumabanen Dombås-Åndalsnes
    "R70": ["76"],        # Trønderbanen
    "R75": ["93"],        # Saltenpendelen Bodø-Rognan
}


# Enturs `transportSubmode`, oversatt. Dette er IKKE materiell - det sier hva
# slags trafikk turen er, ikke hvilket sett som kjører den. Men det er det
# eneste Entur har som beskriver toget for de operatørene som ikke publiserer
# settnummer, og det er MÅLT og ikke skrevet inn: 381 av 381 turer hadde en
# verdi 22. august.
#
# Merk hvor feltet ligger. `ServiceJourney.transportSubmode` er fylt ut for
# alle; `Line.transportSubmode` er `unknown` for samtlige 24 Vy-linjer, for
# Go-Ahead og for SJ. Bare Flytoget og de svenske operatørene har den på
# linja. Leser man den på linjenivå - som er det billigste og mest opplagte -
# får man «unknown» for tre av fire operatører og konkluderer med at feltet
# er tomt. Det er det ikke; det ligger bare et annet sted.
TRAFIKKTYPE: dict[str, str] = {
    "local": "Lokaltog",
    "regionalRail": "Regiontog",
    "longDistance": "Fjerntog",
    "nightRail": "Nattog",
    "airportLinkRail": "Flytog",
    "internationalRail": "Utenlandstog",
    "touristRailway": "Turisttog",
    "rackAndPinionRailway": "Tannhjulsbane",
    "railShuttle": "Pendeltog",
    "suburbanRailway": "Forstadsbane",
    "crossCountryRail": "Fjerntog",
    "highSpeedRail": "Høyhastighetstog",
    "carTransportRailService": "Biltog",
    "sleeperRailService": "Nattog",
    "specialTrain": "Ekstratog",
    "replacementRailService": "Erstatningstog",
}


def service_type(submode: str | None) -> str | None:
    """Enturs `transportSubmode` som norsk ord, eller None.

    `unknown` er ikke en trafikktype - det er Enturs måte å si at feltet ikke
    er utfylt, og da skal kartet heller ikke si noe.
    """
    if not submode:
        return None
    return TRAFIKKTYPE.get(submode.strip())


def _describe(class_code: str, unit: str | None = None) -> dict | None:
    entry = CLASSES.get(class_code)
    if not entry:
        return None
    described = dict(entry)
    described["kind"] = "lok" if class_code in _LOCOMOTIVES else "sett"
    if unit:
        described["unit"] = unit.lstrip("0") or unit
    return described


def rolling_stock(vehicle_id: str | None) -> dict | None:
    """Materielldata fra vehicleId. None når ID-en ikke er et settnummer."""
    if not vehicle_id:
        return None
    match = _UNIT.match(vehicle_id.strip())
    if not match:
        return None
    class_code, unit = match.groups()
    return _describe(class_code, unit)


def typical_stock(line_code: str | None) -> dict | None:
    """Typisk materiell for en SJ-linje. Merket `typical` slik at kartet kan
    si "vanligvis" i stedet for å påstå hvilket sett som faktisk kjører."""
    if not line_code:
        return None
    codes = LINE_STOCK.get(line_code.strip().upper())
    if not codes:
        return None

    parts = [_describe(code) for code in codes]
    parts = [p for p in parts if p]
    if not parts:
        return None

    lead = dict(parts[0])
    lead["typical"] = True
    lead["type"] = " + ".join(p["type"] for p in parts)
    lead.pop("unit", None)
    return lead


if __name__ == "__main__":
    print("=== Fra vehicleId (Flytoget og Go-Ahead) ===")
    for sample in ["71-15", "78-04", "73-04", "69-52", "18-2258",
                   "1214-2026-08-17", None, "99-01"]:
        found = rolling_stock(sample)
        if not found:
            print(f"  {str(sample):<20} (ingen materielldata)")
            continue
        power = f" · {found['power']} kW" if found["power"] else ""
        print(
            f"  {str(sample):<20} {found['type']} {found['kind']} {found['unit']:<6}"
            f"{found['topSpeed']} km/t · {found['drive']}{power}"
        )

    print("\n=== Fra linje (SJ Nord) ===")
    for line in ["F6", "F7", "R70", "R65", "RE30"]:
        found = typical_stock(line)
        if not found:
            print(f"  {line:<6} (ingen kobling)")
            continue
        print(f"  {line:<6} {found['type']:<12} {found['drive']}")

    print("\n=== Fra trafikktype (alle operatører, også Vy) ===")
    for kode in ["local", "regionalRail", "longDistance", "nightRail",
                 "airportLinkRail", "unknown", None]:
        navn = service_type(kode)
        print(f"  {str(kode):<18} {navn or '(ingen)'}")

    # Selvtest. `python materiell.py` kjøres som en av de nettløse sjekkene,
    # så påstandene hører hjemme her og ikke bare i en utskrift noen må lese.
    assert rolling_stock("71-15")["type"] == "BM71"
    assert rolling_stock("18-2258")["kind"] == "lok"
    # Vy legger tognummeret i vehicleId. Det skal aldri tolkes som et sett.
    assert rolling_stock("1214-2026-08-17") is None
    assert rolling_stock(None) is None
    assert typical_stock("F6")["typical"] is True
    assert typical_stock("L1") is None, "Vy har ingen typisk-tabell, med vilje"

    assert service_type("local") == "Lokaltog"
    assert service_type("longDistance") == "Fjerntog"
    # `unknown` er Enturs måte å si at feltet ikke er utfylt. Det er ikke en
    # trafikktype, og kartet skal ikke oversette det til noe som ser ut som en.
    assert service_type("unknown") is None
    assert service_type(None) is None
    assert service_type("noeEnturFinnerPaaSenere") is None

    print("\nAlt grønt.")
