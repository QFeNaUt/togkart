"""Klient mot Entur sitt Vehicle Positions API.

Alt som har med Entur å gjøre bor i denne fila. app.py vet ingenting om GraphQL
- den får ferdig GeoJSON tilbake. Det gjør at du kan bytte datakilde senere uten
å røre resten av appen.
"""

import logging
import re
import time
from datetime import datetime, timezone

import httpx

from materiell import rolling_stock
from sporgeometri import avstand_m, retning_grader

log = logging.getLogger(__name__)

ENTUR_URL = "https://api.entur.io/realtime/v2/vehicles/graphql"

# ---------------------------------------------------------------------------
# GraphQL-spørringen
# ---------------------------------------------------------------------------
# NB: Kjør `python prober/smoketest.py` FØR du stoler på denne. Feltene under
# `line { ... }` er de jeg er minst sikker på. Får du "Cannot query field ..."
# i loggen, fjern det feltet her og prøv igjen. Utforsk skjemaet i
# https://api.entur.io/graphql-explorer/vehicles-v2
QUERY = """
{
  vehicles(mode: RAIL) {
    vehicleId
    delay
    bearing
    speed
    lastUpdated
    serviceJourney {
      id
    }
    datedServiceJourney {
      id
    }
    line {
      lineRef
      lineName
      publicCode
    }
    location {
      latitude
      longitude
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Punktlighetsbånd
# ---------------------------------------------------------------------------
# Tersklene er ikke tilfeldige: norsk jernbane regner et lokaltog som "i rute"
# hvis det er inntil 3 min 59 sek forsinket, og fjerntog inntil 5 min 59 sek.
# Vi bruker den samme logikken som utgangspunkt. Juster fritt.
#
# ID-ene her må matche BANDS i static/app.js.
BAND_ORDER = ["i_rute", "grense", "forsinket", "mye", "ukjent"]

# Et tog som ikke har rapportert posisjon på fem minutter står ikke stille i
# skogen - det har sluttet å sende. `delay` fortsetter likevel å vokse mot en
# rutetid toget aldri innfrir. Slike tog må ut av punktlighetsstatistikken,
# ellers måler du ikke forsinkelse, men hvor lenge siden dataene forsvant.
STALE_AFTER_SECONDS = 300


def _age_seconds(last_updated: str | None, now: datetime) -> float | None:
    """Hvor gammel er posisjonen, i sekunder?"""
    if not last_updated:
        return None
    try:
        seen = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (now - seen).total_seconds())


def delay_band(delay_seconds: float | None) -> str:
    """Oversett forsinkelse i sekunder til et navngitt bånd."""
    if delay_seconds is None:
        return "ukjent"
    if delay_seconds < 240:  # under 3:59 - inkluderer tog som går før rute
        return "i_rute"
    if delay_seconds < 360:  # 4:00-5:59
        return "grense"
    if delay_seconds < 900:  # 6:00-14:59
        return "forsinket"
    return "mye"


# Tognummeret ligger i journey-ID-en, ikke i vehicleId. Formatet varierer
# mellom operatører, og det finnes ingen generell regel - bare to mønstre vi
# har observert:
#
#   VYG:DatedServiceJourney:1214_GJOe-OSL_26-08-17   -> 1214  (nummer først)
#   GOA:DatedServiceJourney:706_STV-OSL_26-08-17     -> 706   (nummer først)
#   FLT:ServiceJourney:1-2614-3549-20260817          -> 3549  (nummer før dato)
#
# Hos Flytoget er "1-2614" konstant for alle tog; det er leddet foran datoen
# som identifiserer kjøringen. Slutter ID-en på åtte sammenhengende siffer,
# er det en kompakt dato, og nummeret står rett foran.
#
# vehicleId er ikke et generelt alternativ: hos Flytoget og Go-Ahead er det
# togsettet ("71-15" = BM71 sett 15), så ti ulike tog fikk samme nummer.
#
# Men det finnes ett tilfelle der journey-ID-en ikke KAN svare, og der
# vehicleId kan. Sammenkoblede løp bytter tognummer underveis, og Entur gir
# begge halvdelene samme ServiceJourney:
#
#   VYG:ServiceJourney:838-341_515579-R
#     vehicleId 838-2026-08-19   -> er tog 838
#     vehicleId 341-2026-08-20   -> er tog 341
#
# Begge fikk 838 av regelen «nummeret står først», og to tog 32 km fra
# hverandre ble til ett. Konsekvensen var ikke bare en feil etikett: `_tog_id`
# i historikk.py nøkler på (linje, tognummer), så de to togene smeltet sammen
# til én rad-serie i databasen, og medianen i analyse.py ble et blandingstall
# av to forskjellige tog.
#
# Vy legger tognummeret i vehicleId, etterfulgt av kjøredatoen
# ("838-2026-08-19"). Det mønsteret - siffer, bindestrek, ISO-dato - treffer
# ingen av settnumrene til Flytoget ("71") eller Go-Ahead ("73-08"), og kan
# derfor stå først uten å ta dem med seg.
#
# Målt mot feeden 20. august: 22 av 22 Vy-kjøretøy hadde formen, ingen FLT
# eller GOA hadde den, og 21 av 22 ga samme svar som journey-ID-en. Det
# tjueandre var feilen over. Utvalget er lite - `sjekk.py tognummer` er
# testen som sier fra hvis forholdet endrer seg.
#
# Dette er en tolkning av tekstformat, ikke en garantert kontrakt. Kjør
# `sjekk.py tognummer` etter endringer - unikhetstesten fanger opp om den ryker.
_COMPACT_DATE = re.compile(r"^\d{8}$")
_VY_VEHICLE_ID = re.compile(r"^(\d+)-\d{4}-\d{2}-\d{2}$")


def _train_number(vehicle: dict) -> str:
    """Trekk tognummeret ut av kjøretøyet.

    Tom streng hvis ingen av mønstrene passer. Det er med vilje: bedre å vise
    ingenting enn et tall som ser riktig ut og ikke er det.
    """
    # Vy-mønsteret først. Det er det eneste som kan skille de to halvdelene av
    # et sammenkoblet løp fra hverandre, siden de deler journey-ID.
    egen = _VY_VEHICLE_ID.match(str(vehicle.get("vehicleId") or ""))
    if egen:
        return egen.group(1)

    for key in ("datedServiceJourney", "serviceJourney"):
        reference = (vehicle.get(key) or {}).get("id") or ""
        if "ServiceJourney:" not in reference:
            continue

        parts = re.split(r"[-_]", reference.split("ServiceJourney:", 1)[1])
        if not parts:
            continue

        # Flytoget-mønsteret: nummeret står rett foran den kompakte datoen.
        if len(parts) >= 2 and _COMPACT_DATE.match(parts[-1]) and parts[-2].isdigit():
            return parts[-2]

        # Vy- og Go-Ahead-mønsteret: nummeret står først.
        if parts[0].isdigit():
            return parts[0]

    return ""


def _line_code(line: dict | None) -> str:
    if not line:
        return ""
    # publicCode forsvant for 90 av 101 tog i noen minutter 18. august og kom
    # tilbake av seg selv. Faller vi tilbake på rå lineRef, står det
    # "VYG:Line:R13" i popup-en. Klipp som _nokkel i app.py gjør.
    return str(line.get("publicCode") or line.get("lineRef") or "").rsplit(":", 1)[-1]


def _line_name(line: dict | None) -> str:
    """Strekningen: "Oslo S-Gjøvik". Sier langt mer enn koden alene."""
    if not line:
        return ""
    return str(line.get("lineName") or "")


# ---------------------------------------------------------------------------
# Dobbeltsett
# ---------------------------------------------------------------------------
# Et tog kan bestå av to sett koblet sammen, og hvert sett har sin egen sender.
# F5 tog 706 på Sørlandsbanen kom 18. august inn som to kjøretøy - 73-16 og
# 73-04 - med samme tognummer, samme forsinkelse og samme lastUpdated, null
# meter fra hverandre. Begge posisjonene var ekte. Bane NOR viser ett tog fordi
# de teller tog, ikke vogner.
#
# To prikker oppå hverandre er lette å overse i kartet, men de teller to ganger
# i ringdiagrammet og i all punktlighetsstatistikk - en systematisk skjevhet
# som treffer nettopp de lengste togene.
#
# Avstandskravet er ikke en detalj. Deler to kjøretøy tognummer OG står langt
# fra hverandre, er det ikke et dobbeltsett - da har `_train_number` tolket
# feil, og det er nøyaktig feilen `sjekk.py tognummer` finnes for å fange. Slår vi dem
# sammen uansett, skjuler vi den. Derfor: langt unna -> behold begge, og si
# fra i loggen.
COUPLED_MAX_METERS = 1000


# Avstandsregningen lå her som en egen haversine til 20. august - den tredje
# kopien i prosjektet. Nå brukes `sporgeometri.avstand_m`, som denne fila
# allerede importerte til retningsutregningen.
#
# Den er en flat tilnærming og ikke haversine, og det er greit på denne
# skalaen: målt mot den gamle utgaven skiller de 0,112 % på det verste, altså
# 1,1 meter ved tusenmeterstersklen. Avgjørelsen under handler om å skille
# null meter fra førti kilometer.
#
# Proben `sjekk_duplikater.py` har fortsatt sin egen kopi, og det er med vilje:
# den skal se det kartet ser, uten å arve prosjektets egne antakelser.


def _freshness(feature: dict) -> tuple[float, str]:
    """Sorteringsnøkkel: ferskeste posisjon først, så ID for et stabilt svar."""
    age = feature["properties"].get("ageSeconds")
    return (float("inf") if age is None else float(age), feature["properties"]["id"])


def _merge_coupled(features: list[dict]) -> tuple[list[dict], int]:
    """Slå sammen kjøretøy som er samme tog. Returnerer (features, antall slått).

    Grupperer på (linje, tognummer). Kjøretøy uten tognummer står alltid alene:
    da vet vi ikke hva som hører sammen, og en tom streng ville samlet alt vi
    ikke klarte å tolke i én stor haug.
    """
    grupper: dict = {}
    for index, feature in enumerate(features):
        properties = feature["properties"]
        key = (
            (properties["line"], properties["trainNumber"])
            if properties["trainNumber"]
            else ("uten tognummer", index)
        )
        grupper.setdefault(key, []).append(feature)

    merged: list[dict] = []
    coupled_count = 0

    for (line, number), group in grupper.items():
        if len(group) == 1:
            merged.append(group[0])
            continue

        group.sort(key=_freshness)
        anchor = group[0]["geometry"]["coordinates"]
        spread = max(
            avstand_m(tuple(anchor), tuple(f["geometry"]["coordinates"]))
            for f in group
        )

        if spread > COUPLED_MAX_METERS:
            log.warning(
                "%s tog %s: %d kjøretøy med samme tognummer, men %.1f km fra "
                "hverandre. Beholder begge - kjør `python sjekk.py tognummer`",
                line, number, len(group), spread / 1000,
            )
            merged.extend(group)
            continue

        # Posisjonen kommer fra det ferskeste kjøretøyet: en ekte måling, ikke
        # et gjennomsnitt av to. ID-en sorteres derimot alfabetisk, slik at
        # "73-04:73-16" er den samme nøkkelen hver eneste henting - uavhengig
        # av hvilken rekkefølge Entur nevner dem i.
        by_id = sorted(group, key=lambda f: f["properties"]["id"])
        ids = [f["properties"]["id"] for f in by_id if f["properties"]["id"]]

        base = group[0]
        properties = base["properties"]
        properties["id"] = ":".join(ids)
        properties["vehicleIds"] = ids
        properties["coupled"] = len(group)

        units = [
            str((f["properties"].get("stock") or {}).get("unit"))
            for f in by_id
            if (f["properties"].get("stock") or {}).get("unit")
        ]
        if properties.get("stock") and len(units) > 1:
            # Kopi, ikke endring: rolling_stock() deler oppslagstabellen i
            # materiell.py mellom alle tog av samme type. Skriver du i den,
            # står det "16 + 4" på hvert eneste BM73 i kartet.
            stock = dict(properties["stock"])
            stock["unit"] = " + ".join(units)
            stock["units"] = units
            properties["stock"] = stock

        merged.append(base)
        coupled_count += 1

    if coupled_count:
        log.info(
            "Slo sammen %d dobbeltsett (%d prikker ble %d)",
            coupled_count, len(features), len(merged),
        )

    return merged, coupled_count


# ---------------------------------------------------------------------------
# Kjøreretning
# ---------------------------------------------------------------------------
# Pila i kartet trenger å vite hvilken vei toget kjører. Entur har et felt for
# det - `bearing` - og det er verdt å vite to ting om det før du stoler på det.
#
# DET FØRSTE: NESTEN INGEN PUBLISERER DET. Målt mot feeden 20. august:
#
#     FLT   15 av 15  (100 %)
#     VYG    0 av 65  (0 %)
#     GOA    0 av  5  (0 %)
#
# Femten av åttifem tog, alle på Gardermobanen. Bygger du pila på `bearing`
# alene, får Flytoget piler og resten av landet ikke - og det ser ut som en
# feil i kartet, ikke som et hull i dataene.
#
# DET ANDRE, OG VERRE: DET FLYTOGET PUBLISERER ER IKKE EN LIVE KURS. Målt over
# åtte hentinger på fire minutter, for tog som var i bevegelse hele tida:
#
#     tog 3798:  kjørte 6,0 km. bearing endret seg 1°. Faktisk kurs: 27°.
#     tog 3805:  kjørte 8,4 km. bearing endret seg 5°. Faktisk kurs: 30°.
#     tog 3800:  kjørte 1,2 km. bearing endret seg 1°. Faktisk kurs: 28°.
#
# `bearing` står altså nesten stille mens toget svinger seg gjennom
# Romeriksporten. Det er en konstant for hele turen - grovt sett «denne
# avgangen går nordøstover» - og ikke retningen toget peker nå. Holdt opp mot
# faktisk bevegelse bommet den med 26° i median og 116° på det verste.
#
# DERFOR ER PRIORITERINGEN OMVENDT AV DEN OPPLAGTE: bevegelse slår operatørens
# eget tall. Rekkefølgen står i app.py, der de tre kildene møtes. Denne
# funksjonen gjør bare den ene av dem.
#
# Å UTLEDE RETNING AV BEVEGELSE er to ting vanskeligere enn det ser ut:
#
#   ET TOG SOM STÅR STILLE HAR INGEN RETNING, men GPS-en støyer noen meter
#   uansett. Uten en nedre grense ville pila snurret tilfeldig rundt på hvert
#   eneste tog som står på en perrong. Derfor MIN_METER: under den terskelen
#   oppdaterer vi ikke retningen - vi BEHOLDER den forrige. Et tog som står på
#   Oslo S peker fortsatt dit det kjørte inn.
#
#   RETNINGEN MÅ OVERLEVE at toget står stille, men ikke at det snur. Et tog
#   som snur på en endestasjon skal få ny retning så snart det har flyttet seg
#   langt nok - og det får det, siden vi da måler mellom to punkter som ligger
#   MIN_METER fra hverandre i den nye retningen.
MIN_METER = 25.0

# Hvor lenge en husket retning får leve uten å bli bekreftet. Et tog som har
# stått stille i et kvarter har vi ingen grunn til å tro noe om lenger - da er
# det like gjerne snudd, koblet fra, eller ute av drift.
RETNING_LEVETID_S = 900.0


def paafor_retning(
    features: list[dict], minne: dict, na: float | None = None
) -> dict:
    """Utled kjøreretning av bevegelse, og returner nytt minne.

    Setter `heading` (kompassgrader) og `headingSource: bevegelse` på de togene
    som har flyttet seg nok til at retningen er ekte. De andre rører vi ikke -
    app.py har to grovere kilder å falle tilbake på.

    `minne` er {tog-id: (lon, lat, retning|None, tidspunkt)} fra forrige kall.
    Funksjonen eier ikke tilstanden selv; app.py holder den, på samme måte som
    den holder cachene. Det gjør at en test kan mate den to kunstige hentinger
    etter hverandre og se nøyaktig hva som skjer.
    """
    na = na if na is not None else time.monotonic()
    nytt: dict = {}

    for feature in features:
        p = feature["properties"]
        tog_id = p.get("id") or ""
        lon, lat = feature["geometry"]["coordinates"]
        forrige = minne.get(tog_id)

        # Husket retning tas med videre som utgangspunkt, så et tog som står
        # stille beholder pila si i stedet for å miste den.
        retning = None
        if forrige is not None:
            f_lon, f_lat, f_retning, f_tid = forrige
            if na - f_tid < RETNING_LEVETID_S:
                retning = f_retning
                flyttet = avstand_m((f_lon, f_lat), (lon, lat))
                if flyttet >= MIN_METER:
                    utledet = retning_grader((f_lon, f_lat), (lon, lat))
                    if utledet is not None:
                        retning = utledet
            else:
                # Ankeret er for gammelt til å måle fra. Start på nytt her.
                forrige = None

        if retning is not None:
            p["heading"] = round(retning, 1)
            p["headingSource"] = "bevegelse"
        # Ellers settes nøkkelen ikke i det hele tatt. Kartet filtrerer på
        # ["has", "heading"], og en nøkkel med verdien null ville regnes som
        # til stede - da får et tog uten kjent retning en pil rett nord, som
        # er verre enn ingen pil.

        # Ankeret flyttes bare når toget faktisk har flyttet seg. Står toget
        # stille, blir det gamle punktet stående, slik at små forflytninger
        # summerer seg opp til én ekte måling i stedet for å bli målt bort
        # ti sekunder av gangen.
        if forrige is not None and avstand_m(
            (forrige[0], forrige[1]), (lon, lat)
        ) < MIN_METER:
            nytt[tog_id] = (forrige[0], forrige[1], retning, forrige[3])
        else:
            nytt[tog_id] = (lon, lat, retning, na)

    # Tog som ikke er i feeden lenger faller ut av minnet av seg selv - vi
    # bygger `nytt` fra dagens tog og ikke fra gårsdagens. Uten det ville
    # dicten vokst med hvert tog som noen gang har kjørt.
    return nytt


def to_geojson(vehicles: list[dict]) -> tuple[dict, dict]:
    """Gjør Entur-responsen om til GeoJSON + en liten oppsummering.

    Returnerer (geojson, meta). Tog uten gyldig posisjon droppes stille -
    det er normalt at noen kjøretøy mangler GPS et par oppdateringer.
    """
    features = []
    now = datetime.now(timezone.utc)

    for vehicle in vehicles:
        location = vehicle.get("location") or {}
        lat = location.get("latitude")
        lon = location.get("longitude")

        # Uten koordinater kan vi ikke tegne noe. Hopp over.
        if lat is None or lon is None:
            continue

        delay = vehicle.get("delay")
        band = delay_band(delay)

        # Flytoget og Go-Ahead legger settnummeret i vehicleId. Vy legger
        # tognummeret der, så for dem blir dette None - og det er greit.
        stock = rolling_stock(vehicle.get("vehicleId"))

        age = _age_seconds(vehicle.get("lastUpdated"), now)
        stale = age is not None and age > STALE_AFTER_SECONDS

        features.append(
            {
                "type": "Feature",
                "geometry": {
                    # GeoJSON er ALLTID [lengdegrad, breddegrad] - motsatt av
                    # hvordan de fleste tenker. Klassisk kilde til kart der
                    # alt havner i Somalia.
                    "type": "Point",
                    "coordinates": [lon, lat],
                },
                "properties": {
                    "id": vehicle.get("vehicleId") or "",
                    "trainNumber": _train_number(vehicle),
                    "journeyRef": (vehicle.get("serviceJourney") or {}).get("id") or "",
                    "line": _line_code(vehicle.get("line")),
                    "lineName": _line_name(vehicle.get("line")),
                    "delay": delay,
                    "band": band,
                    "stale": stale,
                    "ageSeconds": round(age) if age is not None else None,
                    "stock": stock,
                    "bearing": vehicle.get("bearing"),
                    "speed": vehicle.get("speed"),
                    "lastUpdated": vehicle.get("lastUpdated"),
                },
            }
        )

    # Antallet uten posisjon må telles her, før sammenslåingen - ellers blander
    # vi to helt ulike grunner til at en prikk mangler.
    without_position = len(vehicles) - len(features)

    # Dobbeltsett slås sammen FØR opptellingen. Gjør vi det etterpå, teller
    # toget fortsatt to ganger i ringdiagrammet, og da har vi bare flyttet
    # feilen dit den er vanskeligere å se.
    features, coupled = _merge_coupled(features)

    counts = {band: 0 for band in BAND_ORDER}
    stale_count = 0
    for feature in features:
        properties = feature["properties"]
        # Spøkelsestog tegnes fortsatt, men teller ikke. De er informasjon om
        # datakvalitet, ikke om punktlighet.
        if properties["stale"]:
            stale_count += 1
        else:
            counts[properties["band"]] += 1

    active = len(features) - stale_count
    geojson = {"type": "FeatureCollection", "features": features}
    meta = {
        "count": len(features),
        "active": active,
        "stale": stale_count,
        "dropped": without_position,
        "coupled": coupled,
        "counts": counts,
        "fetchedAt": now.isoformat(timespec="seconds"),
    }
    return geojson, meta


async def fetch_trains(client_name: str, timeout: float = 10.0) -> tuple[dict, dict]:
    """Hent alle aktive tog fra Entur.

    Kaster httpx.HTTPError ved nettverksproblemer og RuntimeError hvis
    GraphQL-spørringen selv er ugyldig. app.py bestemmer hva som skjer da.
    """
    headers = {"ET-Client-Name": client_name}

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(ENTUR_URL, json={"query": QUERY}, headers=headers)
        response.raise_for_status()
        payload = response.json()

    # GraphQL svarer med HTTP 200 selv når spørringen er feil - feilene ligger
    # i en egen "errors"-nøkkel. Dette er den vanligste fellen med GraphQL.
    if payload.get("errors"):
        messages = "; ".join(e.get("message", "?") for e in payload["errors"])
        raise RuntimeError(f"Entur avviste spørringen: {messages}")

    vehicles = (payload.get("data") or {}).get("vehicles") or []
    log.info("Hentet %d kjøretøy fra Entur", len(vehicles))
    return to_geojson(vehicles)
