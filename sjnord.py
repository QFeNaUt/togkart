"""Beregnede posisjoner for tog Vehicle Positions ikke har.

Filnavnet er historisk. Fram til 21. august regnet denne modulen posisjoner
for SJ Nord alene; nå dekker den hele hullet, av en grunn som ble målt fram:
SJ er ikke den eneste operatøren uten posisjon. **Vy publiserer GPS for bare noen av
sine egne turer.** Målt 21. august mot togkart.banenor.no lå hele linjer ute -
L2 med sju av sju turer, RE10 med fire - mens Journey Planner samtidig kjente
togene og visste hvor forsinket de var. Entur vet altså at toget ruller; det
finnes bare ingen posisjon.

Regelen er derfor ikke lenger «hvilket selskap er dette», men **«har denne
turen en posisjon akkurat nå»**. Kalleren sender inn ID-ene Vehicle Positions
har; alt annet Journey Planner kjenner er vårt å regne ut. Det er en
observasjon per henting i stedet for en liste noen må vedlikeholde - samme
tanke som stasjonsutvalget i lagstasjoner.py.

Selve regnestykket er uendret. Journey Planner vet når toget faktisk passerte
hver stasjon, og har vi tidene og koordinatene til stoppene, kan vi regne oss
fram til hvor toget er nå:

    siste stopp med tid i fortiden  ->  toget er et sted her imellom
    neste stopp med tid i framtiden

Andelen av tiden som har gått gir andelen av strekningen som er tilbakelagt.
Spørsmålet er hva "strekningen" er. Fram til 17. august var det en rett linje
mellom de to stasjonene, og da havnet togene i terrenget: F6 mellom Hamar og
Oslo lufthavn ble tegnet ute i Mjøsa fordi luftlinjen går rett sør mens sporet
buler østover om Stange og Tangen. Avviket var opp mot 11 km.

Nå følger andelen den faktiske traseen, som Entur oppgir som en kodet
polylinje i `pointsOnLink`. Se sporgeometri.py. Mangler geometrien, faller vi
tilbake til rett linje - da står `positionMethod: straight` i egenskapene, så
det er synlig i kartet og tellbart i loggen.

Posisjonen er fortsatt ikke en måling. Derfor merkes hvert punkt
`computed: true`, tegnes som ring i stedet for fylt prikk, og holdes utenfor
punktlighetsstatistikken. `computedReason` sier hvilken av de to grunnene som
gjelder - operatøren har aldri GPS, eller den har det bare ikke for denne
turen. De to betyr forskjellige ting, og et tall som blander dem kan ikke
leses.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from materiell import service_type, typical_stock
from sporgeometri import (
    bygg_trase,
    interpoler_rettlinje,
    posisjon_langs_spor,
    retning_grader,
    retning_langs_spor,
    snapp_stopp,
)

log = logging.getLogger(__name__)

JOURNEY_URL = "https://api.entur.io/journey-planner/v3/graphql"

# Knutepunktene å oppdage turer fra. Vi henter HELE ruta til hvert tog som er
# innom, ikke bare stoppet der, så ett knutepunkt per bane er nok: Trondheim S
# fanger Dovrebanen, Trønderbanen, Rørosbanen og Meråkerbanen; Bodø fanger
# Nordlandsbanen og Saltenpendelen.
#
# NAVN, ikke ID-er. Den forrige utgaven hadde seks NSR-ID-er skrevet rett inn,
# og `prober/sjekk_dekning.py` gikk i nøyaktig den fella med sine egne: to av
# seks var feil, og feilen var STILLE - `stopPlace` svarer villig for enhver
# gyldig ID, så et knutepunkt som pekte feil rapporterte null tog uten at noe
# så galt ut. Navnene slås derfor opp i `static/stasjoner.geojson`, som er
# bygget av lagstasjoner.py og er den samme fasiten resten av kartet bruker.
# Står et navn ikke der, sier `knutepunkter()` fra ved oppstart.
#
# To grupper, fordi de svarer på to forskjellige spørsmål:
#
# LOKALE er de travle stasjonene på Østlandet. Et lokaltog bruker under to
# timer fra ende til ende, så et kort vindu holder - og det MÅ være kort:
# Oslo S har over 200 togavganger på tre timer, og `numberOfDepartures` kutter
# fra den ELDSTE enden av vinduet. Målt 21. august: med tolv timers vindu og
# tak på 200 kom 200 avganger tilbake, hvorav TO med sanntid. Resten var
# formiddagen. Et for stort vindu gir altså ikke for mye data, det gir feil
# data - og det ser like fullt ut som et svar.
#
# FJERNE er alt annet. Nordlandsbanen bruker rundt ti timer fra ende til ende,
# og skal vi fange et tog midt på strekningen, må avgangen fra knutepunktet
# ligge innenfor vinduet.
KNUTEPUNKT_LOKALT = [
    "Oslo S", "Lillestrøm", "Asker", "Drammen", "Ski", "Moss",
]

KNUTEPUNKT_FJERNT = [
    # Dovrebanen, Rørosbanen, Raumabanen, Trønderbanen, Nordlandsbanen
    "Hamar", "Lillehammer", "Dombås", "Oppdal", "Røros", "Åndalsnes",
    "Trondheim S", "Steinkjer", "Mosjøen", "Mo i Rana", "Bodø",
    # Bergensbanen og Randsfjordbanen
    "Hønefoss", "Bergen", "Voss", "Myrdal", "Finse",
    # Sørlandsbanen, Jærbanen, Arendalsbanen
    "Kongsberg", "Kristiansand", "Marnardal", "Nelaug", "Arendal",
    "Egersund", "Stavanger",
    # Vestfoldbanen, Bratsbergbanen, Østfoldbanen, Gjøvikbanen, Kongsvingerbanen
    "Nordagutu", "Notodden", "Skien", "Larvik", "Halden", "Gjøvik",
    "Kongsvinger",
]

LOKALT_TILBAKE_MIN = 90
LOKALT_FRAM_MIN = 30

# Beholdt som timer fordi `prober/sjekk_dato.py` regner med dem.
LOOKBACK_HOURS = 12
FORWARD_HOURS = 2

# Taket per knutepunkt. Treffes det, kutter Entur fra den eldste enden og vi
# mister nettopp de ferskeste avgangene - se kommentaren over. Målt på den
# lista som står her: verste knutepunkt ga 84 avganger, så det er god margin.
# `_hvem_gar` sier fra hvis noen kommer nær.
MAKS_AVGANGER = 300

# Operatører som ALDRI publiserer posisjon. De tas med uansett hva Journey
# Planner sier om sanntid: rundt halvparten av SJ-avgangene får aldri en
# sanntidsoppdatering, og uten dette unntaket ville de forsvunnet helt fra
# kartet i stedet for å stå grå (se `has_realtime` i positions()).
#
# Dette er IKKE lenger hovedfilteret. Hovedfilteret er «har Vehicle Positions
# en posisjon for denne turen akkurat nå», og det avgjøres av kalleren, som
# sender inn ID-ene den nettopp har sett. Denne lista er unntaket som lar SJ
# slippe gjennom sanntidskravet - og reserven når kalleren ikke vet noe om
# hvem som har GPS (`python sjnord.py` uten videre).
#
# Kodespaket er delen foran første kolon i tur-ID-en:
#   SJN:ServiceJourney:431_49904-R   ->  SJN, aldri GPS
#   VYG:ServiceJourney:...           ->  Vy, har GPS for noen av turene sine
KODESPAKET_UTEN_GPS = {"SJN"}

# For operatører som normalt HAR GPS krever vi at Journey Planner sier
# `realtime` på avgangen. Uten det vet vi bare hva rutetabellen lover, og en
# prikk tegnet fra rutetabellen alene er ikke et tog vi har observert - det er
# en påstand om at et tog burde vært der. Målt 21. august er forskjellen stor:
# 500 turer i vinduet uten kravet, 277 med.
KREV_SANNTID_NAR_OPERATOREN_HAR_GPS = True

# Hvor mange NYE turer vi henter sporgeometri for per henting. Geometrien er
# den tunge delen - målt 4-44 kB per tur mot 1-5 kB uten - og den er den samme
# hele dagen, så den hentes én gang per tur og gjenbrukes fra `_FORBEREDT`.
#
# Taket gjelder bare kaldstart. Da ville 200 turer gitt ett kall på flere
# megabyte i samme sekund; nå fordeles det over noen hentinger, og togene som
# venter på tur tegnes med `positionMethod: straight` i mellomtiden. Det er en
# synlig og selvhelbredende degradering, ikke et stille tap.
GEOMETRI_PER_HENTING = 60

# Steg 1: hvem går, og hvilken kjøredato tilhører de? Slankt svar med vilje -
# her trenger vi bare ID og dato, ikke sporgeometri på hundre kilobyte.
QUERY_HVEM = """
query Hvem($id: String!, $start: DateTime!, $range: Int!, $tak: Int!) {
  stopPlace(id: $id) {
    estimatedCalls(
      startTime: $start
      timeRange: $range
      numberOfDepartures: $tak
      whiteListedModes: [rail]
      arrivalDeparture: both
    ) {
      date
      realtime
      serviceJourney { id line { publicCode } }
    }
  }
}
"""

# Steg 2: detaljene, med kjøredatoen eksplisitt. Bygges med aliaser slik at
# mange turer med samme dato går i én spørring - se _query_turer().
TUR_FRAGMENT = """
fragment tur on ServiceJourney {
  id
  line { publicCode name }
  transportSubmode
  __GEOMETRI__
  estimatedCalls(date: $dato) {
    aimedDepartureTime
    expectedDepartureTime
    aimedArrivalTime
    expectedArrivalTime
    realtimeState
    quay { name latitude longitude }
  }
}
"""

# Reserveløsning: den gamle samlede spørringen, uten kjøredato. Brukes bare
# hvis Entur ikke godtar date-argumentet. Da er vi tilbake til feilen fra
# 18. august - tomt kart om natta - men det er bedre enn ingen tog i det hele
# tatt, og loggen sier tydelig fra.
QUERY_SAMLET = """
query Journeys($id: String!, $start: DateTime!, $range: Int!, $tak: Int!) {
  stopPlace(id: $id) {
    estimatedCalls(
      startTime: $start
      timeRange: $range
      numberOfDepartures: $tak
      whiteListedModes: [rail]
    ) {
      realtime
      serviceJourney {
        id
        line { publicCode name }
        transportSubmode
        __GEOMETRI__
        estimatedCalls {
          aimedDepartureTime
          expectedDepartureTime
          aimedArrivalTime
          expectedArrivalTime
          realtimeState
          quay { name latitude longitude }
        }
      }
    }
  }
}
"""

# Hvor mange turer vi ber om i én spørring. Entur setter tak på hvor kompleks
# en spørring kan være, og hver tur drar med seg en polylinje.
TURER_PER_SPORRING = 20

GEOMETRI_FELT = "pointsOnLink { points length }"

# Slås av automatisk hvis Entur ikke kjenner feltet. Uten denne bryteren ville
# en skjemaendring hos Entur gjort at HELE spørringen feilet, og da forsvinner
# samtlige SJ-tog fra kartet. En degradering til luftlinje er mye bedre enn
# tom skjerm.
_GEOMETRI_I_SPORRING = True

# Samme mekanikk for kjøredatoen. Slås av hvis Entur ikke godtar `date` på
# ServiceJourney.estimatedCalls, og da faller vi tilbake til QUERY_SAMLET.
_DATO_I_SPORRING = True

# journey_id -> {"timeline": [...], "trase": Sportrase|None, "offsets": [...]|None}
#
# Traseen dekodes og stoppene snappes her, én gang per tur. Geometrien til en
# ServiceJourney endrer seg ikke i løpet av dagen - bare tidene gjør det - så
# resultatet gjenbrukes på tvers av hentinger. Det gjør at bare nye turer
# koster arbeid.
_FORBEREDT: dict[str, dict] = {}


STASJONSFIL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static", "stasjoner.geojson"
)

# navn -> NSR-ID, lest én gang. Fila endrer seg bare når lagstasjoner.py kjører.
_KNUTEPUNKT_CACHE: dict[str, tuple[str, int, int]] | None = None


def knutepunkter() -> dict[str, tuple[str, int, int]]:
    """NSR-ID -> (navn, minutter tilbake, minutter fram).

    Slår opp navnene i `static/stasjoner.geojson` i stedet for å ha ID-ene
    skrevet inn. Et knutepunkt som ikke finnes der er en STILLE feil - Entur
    svarer «ingen avganger» for enhver gyldig ID - så et navn uten treff
    logges som warning og hele banen bak det forsvinner fra beregningen.
    """
    global _KNUTEPUNKT_CACHE
    if _KNUTEPUNKT_CACHE is not None:
        return _KNUTEPUNKT_CACHE

    try:
        with open(STASJONSFIL, encoding="utf-8") as fil:
            data = json.load(fil)
    except (OSError, ValueError) as feil:
        log.error(
            "Kunne ikke lese %s: %s. Ingen knutepunkter, ingen beregnede tog.",
            STASJONSFIL, feil,
        )
        _KNUTEPUNKT_CACHE = {}
        return _KNUTEPUNKT_CACHE

    navn_til_id: dict[str, str] = {}
    for trekk in data.get("features") or []:
        egenskaper = trekk.get("properties") or {}
        navn = (egenskaper.get("navn") or "").removesuffix(" stasjon")
        if navn and egenskaper.get("id"):
            navn_til_id.setdefault(navn, egenskaper["id"])

    ut: dict[str, tuple[str, int, int]] = {}
    mangler: list[str] = []
    for navn in KNUTEPUNKT_LOKALT:
        sted = navn_til_id.get(navn)
        if sted:
            ut[sted] = (navn, LOKALT_TILBAKE_MIN, LOKALT_FRAM_MIN)
        else:
            mangler.append(navn)
    for navn in KNUTEPUNKT_FJERNT:
        sted = navn_til_id.get(navn)
        if sted:
            ut.setdefault(sted, (navn, LOOKBACK_HOURS * 60, FORWARD_HOURS * 60))
        else:
            mangler.append(navn)

    if mangler:
        log.warning(
            "%d knutepunkt(er) finnes ikke i stasjoner.geojson og er utelatt: %s. "
            "Banene bak dem får ingen beregnede tog.",
            len(mangler), ", ".join(mangler),
        )

    _KNUTEPUNKT_CACHE = ut
    return ut


def _kodespaket(tur_id: str) -> str:
    """SJN:ServiceJourney:431_49904-R -> SJN"""
    return tur_id.split(":", 1)[0]


def _aldri_gps(tur_id: str) -> bool:
    """Operatører som ikke publiserer posisjon i det hele tatt."""
    return _kodespaket(tur_id) in KODESPAKET_UTEN_GPS


def tognokkel(linje: str | None, tognummer: str | None) -> str:
    """«L1:2263» - den nøkkelen begge kildene kan uttrykke.

    Tur-ID-ene kan de derimot ikke. Målt 21. august: for fem av nitti tog ga
    Vehicle Positions en **DatedServiceJourney**-ID der Journey Planner ga en
    ServiceJourney-ID, og de to er ulike ID-rom for samme tog:

        VP  VYG:DatedServiceJourney:1931_OSL-RST_26-08-21
        JP  VYG:ServiceJourney:1931_443485-R

    Sammenlikner man bare rå ID-er, ser de fem ut som tog uten posisjon, og da
    tegner kartet dem en gang til - ved siden av prikken som allerede står der.

    Merk hvordan denne feilen skjulte seg: en måling av hvor mange ID-er som
    MATCHET ga 79 av 79 og så helt frisk ut. Det var de som ikke matchet, og
    som likevel var samme tog, som var saken. Treffrate er ikke riktighet -
    se lærdom 3 i docs/erfaringer.md.
    """
    return f"{(linje or '').rsplit(':', 1)[-1]}:{tognummer or ''}"


def skal_beregnes(
    tur_id: str,
    linje: str | None,
    har_sanntid: bool,
    har_gps: set[str] | None,
) -> bool:
    """Skal vi regne ut posisjonen for denne turen?

    `har_gps` er nøklene Vehicle Positions nettopp hadde en posisjon for, i to
    former om hverandre: rå tur-ID-er, og «linje:tognummer» fra `tognokkel()`.
    To former fordi den ene ikke holder alene - se `tognokkel`.

    Er settet None, vet vi ingenting om hvem som har GPS, og da faller vi
    tilbake til den gamle regelen: bare operatører som aldri publiserer
    posisjon. Det er den trygge retningen å bomme i - vi tegner for få tog,
    ikke to prikker på samme tog.

    Er det tomt, har Vehicle Positions svart uten et eneste kjøretøy. Det er
    ikke det samme som «ingen har GPS»; det er en feed som er nede. Behandles
    som None av samme grunn.
    """
    if _aldri_gps(tur_id):
        return True
    if not har_gps:
        return False
    if tur_id in har_gps:
        return False
    if tognokkel(linje, _journey_number(tur_id)) in har_gps:
        return False
    return har_sanntid or not KREV_SANNTID_NAR_OPERATOREN_HAR_GPS


def _geometri(mal: str) -> str:
    return mal.replace("__GEOMETRI__", GEOMETRI_FELT if _GEOMETRI_I_SPORRING else "")


def _query_turer(antall: int, med_geometri: bool = True) -> str:
    """Bygger én spørring som henter `antall` turer med samme kjøredato.

    GraphQL har ingen «hent disse ID-ene»-form for enkeltoppslag, men den har
    aliaser: samme felt kan spørres flere ganger under hvert sitt navn. Så vi
    lager t0, t1, t2 ... og lar dem dele ett fragment og én datovariabel.

        query Turer($dato: Date!, $id0: String!, $id1: String!) {
          t0: serviceJourney(id: $id0) { ...tur }
          t1: serviceJourney(id: $id1) { ...tur }
        }

    ID-ene sendes som variabler, ikke limt inn i teksten. Da slipper vi å tenke
    på anførselstegn og spesialtegn i det Entur måtte finne på å kalle en tur.
    """
    variabler = ", ".join(f"$id{i}: String!" for i in range(antall))
    felter = "\n".join(
        f"  t{i}: serviceJourney(id: $id{i}) {{ ...tur }}" for i in range(antall)
    )
    fragment = _geometri(TUR_FRAGMENT) if med_geometri else TUR_FRAGMENT.replace("__GEOMETRI__", "")
    return f"query Turer($dato: Date!, {variabler}) {{\n{felter}\n}}\n{fragment}"


def _query_samlet() -> str:
    return _geometri(QUERY_SAMLET)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _measured_delay(stop: dict) -> float | None:
    """Avvik i sekunder, men bare når stoppet faktisk har sanntidsdata.

    Uten sanntid er "forventet" identisk med rutetiden, og differansen blir
    null. Den nullen er ikke en måling - den er fravær av en måling.
    """
    if stop["aimed"] is None or stop["state"] in ("", "scheduled"):
        return None
    return (stop["at"] - stop["aimed"]).total_seconds()


def _last_measured(timeline: list[dict], index: int) -> float | None:
    """Avviket ved siste stopp toget faktisk har passert.

    Sanntidssystemet måler bakover og antar innhenting framover: stopp toget
    har vært innom har ekte avvik, mens stopp lenger fram står på rutetiden
    selv når de er merket "updated". Henter vi fra neste stopp, får vi derfor
    alltid null.

    Vi går bakover fra siste passerte stopp til vi finner en ekte måling.
    Vi leter ikke etter et avvik ulik null - har toget tatt igjen ruta, er
    null det riktige svaret.
    """
    for stop in reversed(timeline[: index + 1]):
        value = _measured_delay(stop)
        if value is not None:
            return value
    return None


def _timeline(calls: list[dict]) -> list[dict]:
    """Stoppene med brukbar tid og koordinat, i rekkefølge.

    Vi bruker forventet tid, ikke faktisk. Faktisk tid mangler på mange små
    holdeplasser selv når toget kjørte forbi - Lademoen og Sparbu på
    Trønderbanen er eksempler. Forventet tid finnes alltid.

    **Siste stopp måles på ANKOMST, ikke avgang.** Et endepunkt har ingen
    avgang, men Journey Planner fyller likevel ut avgangsfeltene, og tallet der
    er ikke en måling av noe som helst. Målt på RE10 339 natt til 22. august:

        aimedDepartureTime    01:30:00      expectedDepartureTime  01:36:12
        aimedArrivalTime      01:30:00      actualArrivalTime      01:32:25

    Toget ankom 2 min 25 s for sent - som er nøyaktig det Vehicle Positions
    meldte - mens avgangsfeltene ga 6 min 12 s for en avgang som aldri skjer.
    Kartet viste det høye tallet. Her er altså VP riktig og JP feil, motsatt av
    problem 1, og forklaringen er den samme som lærdom 5: en tid som ser ut som
    en prognose kan være fravær av en måling.

    Følgen er ikke bare et pent tall. `velg_instans` avgjør om turen er over
    ved å se om nå ligger etter siste stopp, så et oppblåst avgangstidspunkt
    holdt turen «underveis» i fire minutter etter at toget sto stille.

    Listen bygges én gang per henting, ikke per forespørsel: datoparsing av
    femten stopp for femti tog er billig, men ikke gratis, og offsetene i
    sporgeometrien er indeksert mot nettopp denne listen.
    """
    timeline = []
    siste = len(calls) - 1
    for i, call in enumerate(calls):
        quay = call.get("quay") or {}
        lat, lon = quay.get("latitude"), quay.get("longitude")

        expected = aimed = None
        if i == siste:
            expected = _parse(call.get("expectedArrivalTime"))
            aimed = _parse(call.get("aimedArrivalTime"))
        # Faller tilbake på avgang: for alle stopp unntatt det siste er det
        # riktig felt, og for det siste er det bedre enn ingenting hvis Entur
        # ikke har ankomsttidene.
        if expected is None:
            expected = _parse(call.get("expectedDepartureTime"))
            aimed = _parse(call.get("aimedDepartureTime"))

        if lat is None or lon is None or expected is None:
            continue
        timeline.append(
            {
                "at": expected,
                "aimed": aimed,
                "lat": lat,
                "lon": lon,
                "name": quay.get("name") or "",
                "state": (call.get("realtimeState") or "").lower(),
            }
        )
    return timeline


def _prepare(journeys: dict[str, dict], uten_geometri: set[str] | None = None) -> None:
    """Bygg tidslinje og sporgeometri for hver tur. Kalles etter henting.

    Dette er den tunge delen: å dekode en polylinje på titusen punkter og
    snappe femten stasjoner til den. Det tar noen titalls millisekunder per
    tur, og hører derfor hjemme her - ikke i positions(), som kjøres på hver
    forespørsel.

    `uten_geometri` er turene vi bevisst ikke ba om `pointsOnLink` for fordi
    trasen allerede lå i `_FORBEREDT`. Skillet er nødvendig: uten det ville en
    tur som mistet et stopp underveis blitt behandlet som «Entur har ingen
    geometri» og falt til luftlinje, når vi i virkeligheten bare ikke spurte.
    """
    uten_geometri = uten_geometri or set()

    for gone in set(_FORBEREDT) - set(journeys):
        del _FORBEREDT[gone]

    built = resnapped = 0
    for journey_id, journey in journeys.items():
        timeline = _timeline(journey.get("estimatedCalls") or [])
        known = _FORBEREDT.get(journey_id)

        if known is not None and len(known.get("offsets") or []) == len(timeline):
            # Geometrien er allerede dekodet for denne turen. Bare tidene er
            # nye, og de ligger i timeline.
            #
            # ANTAKELSEN, skrevet ned fordi den ikke er selvsagt: like mange
            # stopp betyr de SAMME stoppene. Byttes et stopp ut mot et annet
            # uten at antallet endrer seg, blir offsetene stående på det gamle,
            # og toget plasseres langs feil del av traseen uten at noe feiler.
            # Rutetider endrer seg gjennom dagen; stoppmønsteret gjør det bare
            # når et stopp legges til eller innstilles, og da endrer antallet
            # seg. Holder ikke det lenger, er det denne linja som må bytte
            # bryter - til for eksempel navnene på stoppene.
            known["timeline"] = timeline
            continue

        # Vi spurte ikke om geometri, men har en trasé fra før. Da er det bare
        # stoppene som har flyttet på seg, og de kan snappes mot trasen vi har.
        if (
            journey_id in uten_geometri
            and known is not None
            and known.get("trase") is not None
        ):
            stops = [(stop["lon"], stop["lat"]) for stop in timeline]
            offsets, reason = snapp_stopp(known["trase"], stops)
            if offsets is not None:
                known["timeline"] = timeline
                known["offsets"] = offsets
                resnapped += 1
                continue
            # Snappingen holdt ikke mot den gamle trasen. Be om geometrien på
            # nytt ved neste henting i stedet for å låse turen til luftlinje.
            log.debug("Tur %s måtte snappes på nytt og feilet: %s", journey_id, reason)
            _FORBEREDT[journey_id] = {
                "timeline": timeline, "trase": None, "offsets": None,
                "geometri_hentet": False,
            }
            continue

        trase = offsets = None
        if len(timeline) >= 2:
            stops = [(stop["lon"], stop["lat"]) for stop in timeline]
            trase, offsets, reason = bygg_trase(journey.get("pointsOnLink"), stops)
            if trase is None:
                log.debug("Tur %s bruker luftlinje: %s", journey_id, reason)
            else:
                built += 1

        _FORBEREDT[journey_id] = {
            "timeline": timeline,
            "trase": trase,
            "offsets": offsets,
            # Vi har stilt spørsmålet om geometri for denne turen. Svaret kan
            # ha vært «finnes ikke», og da er det ingen grunn til å spørre om
            # igjen hvert minutt - `pointsOnLink` dukker ikke opp av seg selv.
            "geometri_hentet": journey_id not in uten_geometri,
        }

    with_track = sum(1 for entry in _FORBEREDT.values() if entry["trase"] is not None)
    log.info(
        "Sporgeometri: %d av %d turer følger sporet (%d nye, %d snappet på nytt)",
        with_track, len(_FORBEREDT), built, resnapped,
    )


def _position_now(
    timeline: list[dict],
    now: datetime,
    trase=None,
    offsets: list[float] | None = None,
) -> tuple | None:
    """Finn hvor toget er akkurat nå.

    Returnerer (lon, lat, delay_sekunder, neste_stopp, forrige_stopp, metode,
    retning) eller None hvis toget ikke er underveis.

    `retning` er kompassgrader. SJ-togene er de eneste som får en retning helt
    gratis: vi vet allerede hvilket strekk toget er på og hvilken vei det går,
    så retningen er tangenten til det vi allerede har regnet ut. GPS-togene må
    utlede sin av bevegelse mellom hentinger - se `entur.paafor_retning`.
    """
    if len(timeline) < 2:
        return None

    # Utenfor ruta i tid: toget har ikke startet, eller er framme.
    if now < timeline[0]["at"] or now > timeline[-1]["at"]:
        return None

    # Offsetene er indeksert mot timeline. Er lengdene ulike, har listene kommet
    # i utakt, og da er luftlinje det ærlige svaret.
    use_track = (
        trase is not None
        and offsets is not None
        and len(offsets) == len(timeline)
    )

    for index in range(len(timeline) - 1):
        previous, following = timeline[index], timeline[index + 1]
        if not (previous["at"] <= now <= following["at"]):
            continue

        span = (following["at"] - previous["at"]).total_seconds()
        fraction = 0.0 if span <= 0 else (now - previous["at"]).total_seconds() / span

        if use_track:
            lon, lat = posisjon_langs_spor(trase, offsets, index, fraction)
            method = "track"
            # Tangenten til sporet der toget er. Følger svingene, så pila
            # peker langs skinnegangen og ikke mot neste stasjon i luftlinje.
            heading = retning_langs_spor(trase, offsets, index, fraction)
        else:
            lon, lat = interpoler_rettlinje(
                (previous["lon"], previous["lat"]),
                (following["lon"], following["lat"]),
                fraction,
            )
            method = "straight"
            # Uten sporgeometri er retningen mot neste stopp det beste vi har.
            # Den er like unøyaktig som posisjonen den hører til, og det er
            # riktig: begge to sier «omtrent hitover».
            heading = retning_grader(
                (previous["lon"], previous["lat"]),
                (following["lon"], following["lat"]),
            )

        # Bakover, ikke framover. Se _last_measured for hvorfor.
        delay = _last_measured(timeline, index)
        if delay is None:
            delay = _measured_delay(following)

        return lon, lat, delay, following["name"], previous["name"], method, heading

    return None

def velg_instans(
    kandidater: dict[str, list], na: datetime
) -> tuple[str | None, list, str]:
    """Velg den kjøredatoen hvis instans faktisk omslutter nå-tidspunktet.

    `kandidater` er kjøredato -> tidslinje. Hvilken dato vi spurte om først
    betyr ingenting; det er tidene i svaret som avgjør. Toget i feeden er ett
    bestemt tog, og bare én av instansene kan være det.

    Fire utfall, og de må holdes fra hverandre:
      underveis     en instans omslutter nå. Toget vi ser på kartet.
      FERDIG        ingen omslutter, men minst én har vært. Nyeste av dem,
                    for det er den `delay` eventuelt henger igjen fra.
      ikke startet  ingen har startet. Da har vi funnet en instans som ikke
                    har kjørt ennå - den kan ikke si noe om sanntid.
      ingen tider   turen finnes, men uten brukbare stoppetider.

    «ikke startet» er et funn, ikke et hull: slik ser feil kjøredato ut når
    man endelig ser etter den (problem 3).
    """
    brukbare = {d: tl for d, tl in kandidater.items() if len(tl) >= 2}
    if not brukbare:
        return None, [], "ingen tider"

    for dato, timeline in brukbare.items():
        if timeline[0]["at"] <= na <= timeline[-1]["at"]:
            return dato, timeline, "underveis"

    ferdige = {d: tl for d, tl in brukbare.items() if tl[-1]["at"] < na}
    if ferdige:
        dato = max(ferdige, key=lambda d: ferdige[d][-1]["at"])
        return dato, ferdige[dato], "FERDIG"

    dato = min(brukbare, key=lambda d: brukbare[d][0]["at"])
    return dato, brukbare[dato], "ikke startet"

async def _post(client, headers: dict, sporring: str, variabler: dict) -> dict:
    response = await client.post(
        JOURNEY_URL,
        json={"query": sporring, "variables": variabler},
        headers=headers,
    )
    response.raise_for_status()
    return response.json()



def _geometri_avvist(feil: list[dict] | None) -> bool:
    """Klaget Entur på pointsOnLink? Da slår vi feltet av og prøver igjen.

    Kjenner ikke Entur feltet, feiler HELE spørringen, og da forsvinner
    samtlige SJ-tog fra kartet - ikke bare geometrien. En degradering til
    luftlinje er mye bedre enn tom skjerm.
    """
    global _GEOMETRI_I_SPORRING
    if not feil or not _GEOMETRI_I_SPORRING:
        return False
    melding = feil[0].get("message") or ""
    if "pointsOnLink" not in melding:
        return False
    log.warning("Entur avviste pointsOnLink, går over til luftlinje: %s", melding)
    _GEOMETRI_I_SPORRING = False
    return True


async def _hvem_gar(
    client, headers: dict, na: datetime, stop_id: str, punkt: tuple[str, int, int]
) -> list[dict]:
    """Steg 1: turer innom ett knutepunkt, med kjøredato og sanntidsflagg.

    Vinduet er per knutepunkt. Se kommentaren over KNUTEPUNKT_LOKALT for
    hvorfor et for stort vindu er verre enn et for lite.
    """
    navn, tilbake_min, fram_min = punkt
    variabler = {
        "id": stop_id,
        "start": (na - timedelta(minutes=tilbake_min)).isoformat(timespec="seconds"),
        "range": (tilbake_min + fram_min) * 60,
        "tak": MAKS_AVGANGER,
    }
    payload = await _post(client, headers, QUERY_HVEM, variabler)
    if payload.get("errors"):
        log.warning(
            "Journey Planner klaget på %s: %s",
            navn, payload["errors"][0].get("message"),
        )
        return []
    sted = (payload.get("data") or {}).get("stopPlace") or {}
    kall = sted.get("estimatedCalls") or []

    # Entur kutter fra den ELDSTE enden når taket treffes, så et kuttet svar
    # mangler nettopp de ferskeste avgangene - de vi er ute etter. Symptomet er
    # stille: lista er full, tallet ser stort ut, og togene som mangler er de
    # som kjører nå. Derfor vokter vi grensen og ikke bare treffet.
    if len(kall) >= MAKS_AVGANGER * 0.9:
        log.warning(
            "%s ga %d av maks %d avganger. Nærmer seg taket, og et kuttet svar "
            "mister de FERSKESTE turene. Kort ned vinduet for dette knutepunktet.",
            navn, len(kall), MAKS_AVGANGER,
        )
    return kall


def _mangler_geometri(tur_id: str) -> bool:
    """Har vi allerede en trasé for denne turen?

    `pointsOnLink` for en ServiceJourney er den samme hele dagen - bare tidene
    endrer seg - så polylinjen hentes én gang og gjenbrukes fra `_FORBEREDT`.
    `_prepare` snapper stoppene på nytt mot den lagrede trasen når rutetidene
    endrer seg, så et endret antall stopp krever ikke en ny henting.
    """
    kjent = _FORBEREDT.get(tur_id)
    return kjent is None or not kjent.get("geometri_hentet")


async def _turer_for_dato(
    client, headers: dict, dato: str, ids: list[str]
) -> tuple[dict, set[str]]:
    """Steg 2: full rutedata for turene som tilhører én kjøredato.

    Returnerer (turer, uten_geometri). Den andre er ID-ene vi bevisst IKKE ba
    om `pointsOnLink` for, fordi vi har trasen fra før - `_prepare` trenger å
    vite forskjellen på «spurte ikke» og «Entur hadde ingen».
    """
    turer: dict[str, dict] = {}
    uten_geometri: set[str] = set()

    # Turene deles i to grupper, og de spørres hver for seg fordi geometrien er
    # et felt i spørringen og ikke et valg per tur. Nye først: treffer taket,
    # er det de eldste turene som venter til neste henting, og de har allerede
    # stått i kartet en runde.
    nye = [tur_id for tur_id in ids if _mangler_geometri(tur_id)]
    if len(nye) > GEOMETRI_PER_HENTING:
        log.info(
            "%d nye turer trenger sporgeometri; henter %d nå og resten neste "
            "runde. De som venter tegnes i luftlinje så lenge.",
            len(nye), GEOMETRI_PER_HENTING,
        )
        nye = nye[:GEOMETRI_PER_HENTING]
    nye_sett = set(nye)
    kjente = [tur_id for tur_id in ids if tur_id not in nye_sett]

    async def gruppe(bit: list[str], med_geometri: bool) -> bool:
        """Henter én gruppe. False betyr at datospørringen ble avvist."""
        global _DATO_I_SPORRING
        variabler = {"dato": dato}
        variabler.update({f"id{i}": tur_id for i, tur_id in enumerate(bit)})

        for _ in (1, 2):
            payload = await _post(
                client, headers, _query_turer(len(bit), med_geometri), variabler
            )
            feil = payload.get("errors")
            if feil and med_geometri and _geometri_avvist(feil):
                continue
            if feil:
                log.warning(
                    "Entur avviste datospørringen, faller tilbake til samlet "
                    "spørring uten kjøredato: %s",
                    feil[0].get("message"),
                )
                _DATO_I_SPORRING = False
                return False
            break

        data = payload.get("data") or {}
        for i in range(len(bit)):
            tur = data.get(f"t{i}")
            if tur and tur.get("id"):
                turer[tur["id"]] = tur
                if not med_geometri:
                    uten_geometri.add(tur["id"])
        return True

    for liste, med_geometri in ((nye, True), (kjente, False)):
        for start in range(0, len(liste), TURER_PER_SPORRING):
            if not await gruppe(liste[start:start + TURER_PER_SPORRING], med_geometri):
                return {}, set()

    return turer, uten_geometri


async def _hent_samlet(
    client, headers: dict, na: datetime, har_gps: set[str] | None
) -> dict:
    """Reserveløsning: den gamle spørringen, uten kjøredato."""
    punkter = knutepunkter()

    async def one(stop_id: str, punkt: tuple[str, int, int]) -> list[dict]:
        navn, tilbake_min, fram_min = punkt
        variabler = {
            "id": stop_id,
            "start": (na - timedelta(minutes=tilbake_min)).isoformat(timespec="seconds"),
            "range": (tilbake_min + fram_min) * 60,
            "tak": MAKS_AVGANGER,
        }
        for _ in (1, 2):
            payload = await _post(client, headers, _query_samlet(), variabler)
            feil = payload.get("errors")
            if feil and _geometri_avvist(feil):
                continue
            if feil:
                log.warning("Journey Planner klaget på %s: %s", navn,
                            feil[0].get("message"))
                return []
            break
        sted = (payload.get("data") or {}).get("stopPlace") or {}
        return sted.get("estimatedCalls") or []

    batcher = await asyncio.gather(
        *(one(sid, punkt) for sid, punkt in punkter.items()), return_exceptions=True
    )

    seen: dict[str, dict] = {}
    for batch in batcher:
        if isinstance(batch, Exception):
            log.warning("Knutepunkt feilet: %s", batch)
            continue
        for kall in batch:
            tur = kall.get("serviceJourney") or {}
            tur_id = tur.get("id")
            # Samme filter som i hovedveien. Reserveløsningen skal degradere
            # kjøredatoen, ikke slippe togene med GPS inn bakveien.
            if not tur_id or tur_id in seen:
                continue
            linje = (tur.get("line") or {}).get("publicCode")
            if skal_beregnes(tur_id, linje, bool(kall.get("realtime")), har_gps):
                seen[tur_id] = tur
    return seen


async def fetch_journeys(
    client_name: str,
    timeout: float = 25.0,
    har_gps: set[str] | None = None,
) -> dict[str, dict]:
    """Hent rutedata for turene Vehicle Positions ikke har posisjon for.

    `har_gps` er tur-ID-ene (`serviceJourney.id`) Vehicle Positions nettopp
    svarte med. Alt Journey Planner kjenner utenom dem er vårt å regne ut. Er
    den None eller tom, faller vi tilbake til å regne bare for operatører som
    aldri publiserer posisjon - se `skal_beregnes`.

    ID-ene fra de to kildene er de samme strengene. Det er målt, ikke antatt:
    21. august traff 79 av 79 eksakt, og null ekstra traff når kodespaket ble
    klippet vekk først. Derfor sammenliknes de rått.

    To steg, av en grunn som ble målt fram 18. august. Den nøstede formen
    `stopPlace { estimatedCalls { serviceJourney { estimatedCalls } } }` tar
    ingen kjøredato, og OTP svarer da med dagens instans av ruta - ikke
    instansen som faktisk traff spørringen. Målt klokka 01:25: 42 av 42 turer
    forskjøvet nøyaktig 24 timer, alle med kjøredato dagen før. Kartet var tomt
    for SJ-tog fra midnatt til første avgang.

    En GraphQL-variabel er den samme for hele svaret, så én spørring kan ikke gi
    gårsdagens tog én dato og dagens tog en annen. Derfor:

        1. Spør hvem som går, og hvilken kjøredato de tilhører (slankt svar).
        2. Grupper etter dato, og hent detaljene med datoen eksplisitt.

    Normalt er det én til to datoer i spill. Det blir ett slankt kall per
    knutepunkt pluss noen få tunge - og den tunge sporgeometrien lastes ned
    nøyaktig én gang per tur, ikke én gang per henting.
    """
    now = datetime.now().astimezone()
    headers = {"ET-Client-Name": client_name}
    punkter = knutepunkter()

    if not punkter:
        log.error("Ingen knutepunkter å hente fra. Ingen beregnede tog.")
        return {}

    async with httpx.AsyncClient(timeout=timeout) as client:
        seen: dict[str, dict] = {}
        uten_geometri: set[str] = set()

        if _DATO_I_SPORRING:
            # Alle knutepunktene samtidig i stedet for etter tur.
            batcher = await asyncio.gather(
                *(_hvem_gar(client, headers, now, sid, punkt)
                  for sid, punkt in punkter.items()),
                return_exceptions=True,
            )

            per_dato: dict[str, list[str]] = {}
            sett: set[str] = set()
            # Alle turer vi har tatt stilling til, også de vi forkastet. Uten
            # denne ville et tog innom tre knutepunkter blitt talt tre ganger
            # i loggen under.
            vurdert: set[str] = set()
            forkastet: dict[str, int] = {}
            for batch in batcher:
                if isinstance(batch, Exception):
                    log.warning("Knutepunkt feilet: %s", batch)
                    continue
                for kall in batch:
                    tur = kall.get("serviceJourney") or {}
                    tur_id = tur.get("id")
                    dato = kall.get("date")
                    # Samme tog dukker opp fra flere knutepunkter. Ta det én gang.
                    if not tur_id or not dato or tur_id in vurdert:
                        continue
                    vurdert.add(tur_id)
                    linje = (tur.get("line") or {}).get("publicCode")
                    if not skal_beregnes(
                        tur_id, linje, bool(kall.get("realtime")), har_gps
                    ):
                        # Enten har turen en målt posisjon - da tegner entur.py
                        # den - eller så mangler den sanntid og er bare en linje
                        # i rutetabellen.
                        kode = _kodespaket(tur_id)
                        forkastet[kode] = forkastet.get(kode, 0) + 1
                        continue
                    sett.add(tur_id)
                    per_dato.setdefault(dato, []).append(tur_id)

            if forkastet:
                log.info(
                    "Hoppet over %d turer med målt posisjon eller uten sanntid: %s",
                    sum(forkastet.values()),
                    ", ".join(f"{k} ({n})" for k, n in sorted(forkastet.items())),
                )

            if per_dato:
                log.info(
                    "Fant %d turer uten posisjon på %d kjøredato(er): %s",
                    len(sett), len(per_dato),
                    ", ".join(f"{d} ({len(v)})" for d, v in sorted(per_dato.items())),
                )
                for dato, ids in sorted(per_dato.items()):
                    turer, ingen_geometri = await _turer_for_dato(
                        client, headers, dato, ids
                    )
                    seen.update(turer)
                    uten_geometri |= ingen_geometri
                    if not _DATO_I_SPORRING:
                        # Bryteren gikk midtveis. Det vi har er ufullstendig,
                        # så vi kaster det og henter alt på nytt under.
                        seen = {}
                        uten_geometri = set()
                        break

        if not seen:
            if _DATO_I_SPORRING:
                log.warning("Ingen turer fra datospørringen - prøver samlet spørring")
            seen = await _hent_samlet(client, headers, now, har_gps)
            uten_geometri = set()

    log.info("Hentet rutedata for %d turer uten målt posisjon", len(seen))
    _prepare(seen, uten_geometri)
    return seen


def positions(
    journeys: dict[str, dict],
    now: datetime | None = None,
    hopp_over: set[str] | None = None,
) -> list[dict]:
    """Regn ut hvor hvert tog er akkurat nå, ut fra rutedata som allerede
    ligger i minnet. Ingen nettverk - kan kjøres på hver forespørsel.

    `hopp_over` er turer som har fått en målt posisjon siden rutedataene ble
    hentet. Filteret ligger HER og ikke bare i hentingen fordi de to har ulik
    takt: rutedataene caches i et minutt, mens Vehicle Positions hentes hvert
    tiende sekund. Uten dette ville et tog som begynte å sende GPS blitt tegnet
    to ganger til cachen løp ut.

    `now` kan settes i tester for å fryse tiden."""
    features = []
    now_utc = now or datetime.now(timezone.utc)
    hopp_over = hopp_over or set()

    for journey_id, journey in journeys.items():
        line = journey.get("line") or {}
        # Begge nøkkelformene, av samme grunn som i skal_beregnes().
        if journey_id in hopp_over:
            continue
        if tognokkel(line.get("publicCode"), _journey_number(journey_id)) in hopp_over:
            continue
        calls = journey.get("estimatedCalls") or []
        if any((c.get("realtimeState") or "").lower() == "canceled" for c in calls):
            continue

        # Normalt fylt av _prepare(). Faller tilbake hvis noen kaller
        # positions() med turer som ikke har vært innom fetch_journeys().
        prepared = _FORBEREDT.get(journey_id) or {"timeline": _timeline(calls)}

        placed = _position_now(
            prepared["timeline"],
            now_utc,
            prepared.get("trase"),
            prepared.get("offsets"),
        )
        if not placed:
            continue

        lon, lat, delay, next_stop, previous_stop, method, heading = placed

        # Rundt halvparten av SJ-avgangene får aldri sanntidsoppdatering -
        # de står på "scheduled" selv timer etter at toget kjørte. For dem er
        # expectedDepartureTime bare rutetiden om igjen, så forsinkelsen ville
        # blitt 0,0 og toget tegnet grønt. Det ville vært en påstand vi ikke
        # har dekning for: vi vet hvor toget SKAL være, ikke om det er i rute.
        #
        # Derfor: ingen sanntid -> ingen forsinkelse. Toget havner i "ukjent"
        # og tegnes grått, som er nøyaktig så mye vi faktisk vet.
        has_realtime = any(
            (call.get("realtimeState") or "").lower() not in ("", "scheduled")
            for call in calls
        )
        if not has_realtime:
            delay = None

        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "id": journey_id,
                    "trainNumber": _journey_number(journey_id),
                    "line": line.get("publicCode") or "",
                    "lineName": line.get("name") or "",
                    "delay": delay,
                    "stock": typical_stock(line.get("publicCode")),
                    # Trafikktype fra Entur. Ikke materiell, men målt - og for
                    # de fleste tog er det det eneste vi har. Se materiell.py.
                    "serviceType": service_type(journey.get("transportSubmode")),
                    "computed": True,
                    # Hvorfor posisjonen er beregnet. De to betyr forskjellige
                    # ting: "ingen-gps" er en operatør som aldri publiserer
                    # posisjon (SJ), "mangler-posisjon" er en som gjør det for
                    # noen av turene sine, men ikke denne. Den andre er den som
                    # kan bli borte igjen, og derfor den å telle over tid.
                    "computedReason": (
                        "ingen-gps" if _aldri_gps(journey_id) else "mangler-posisjon"
                    ),
                    # "track": fulgt langs sporet. "straight": rett linje mellom
                    # stasjonene, som før. Tell disse - blir andelen "straight"
                    # høy, har noe skjedd med pointsOnLink.
                    "positionMethod": method,
                    "realtime": has_realtime,
                    "stale": False,
                    "nextStop": next_stop,
                    "previousStop": previous_stop,
                },
            }
        )

        # Nøkkelen settes bare når vi har en retning. Kartet filtrerer på
        # ["has", "heading"], og MapLibre regner en nøkkel med verdien null
        # som til stede - da ville et tog uten kjent retning fått en pil som
        # peker rett nord. Fraværende nøkkel er det ærlige signalet.
        if heading is not None:
            features[-1]["properties"]["heading"] = round(heading, 1)

    return features


def route_line(journey_id: str) -> list[list[float]] | None:
    """Traseen for én tur, som en liste med [lon, lat].

    Klar til å legges i en GeoJSON LineString og tegnes når man klikker på et
    tog. Returnerer None for turer uten sporgeometri.
    """
    prepared = _FORBEREDT.get(journey_id)
    if not prepared or prepared.get("trase") is None:
        return None

    offsets = prepared["offsets"]
    points = prepared["trase"].delstrekning(offsets[0], offsets[-1])
    return [[lon, lat] for lon, lat in points]


async def fetch_computed(
    client_name: str, timeout: float = 25.0, har_gps: set[str] | None = None
) -> list[dict]:
    """Hent og beregn i én operasjon. Brukes av __main__ og av tester."""
    journeys = await fetch_journeys(client_name, timeout, har_gps)
    features = positions(journeys, hopp_over=har_gps)
    with_realtime = sum(1 for f in features if f["properties"]["realtime"])
    on_track = sum(1 for f in features if f["properties"]["positionMethod"] == "track")
    uten_gps = sum(
        1 for f in features if f["properties"]["computedReason"] == "ingen-gps"
    )
    log.info(
        "Beregnet posisjon for %d tog (%d fra operatør uten GPS, %d som mangler "
        "posisjon; %d med sanntid, %d kun rutetabell; %d langs spor, %d i luftlinje)",
        len(features), uten_gps, len(features) - uten_gps,
        with_realtime, len(features) - with_realtime,
        on_track, len(features) - on_track,
    )
    return features


async def hent_har_gps(client_name: str, timeout: float = 15.0) -> set[str]:
    """Tur-ID-ene Vehicle Positions har posisjon for, akkurat nå.

    Bare for `python sjnord.py` og probene. app.py har allerede svaret - det er
    snapshotet den nettopp bygget - og skal ikke hente det en gang til.
    """
    sporring = "{ vehicles(mode: RAIL) { serviceJourney { id } } }"
    async with httpx.AsyncClient(timeout=timeout) as client:
        svar = await client.post(
            "https://api.entur.io/realtime/v2/vehicles/graphql",
            json={"query": sporring},
            headers={"ET-Client-Name": client_name},
        )
        svar.raise_for_status()
        data = (svar.json().get("data") or {}).get("vehicles") or []
    return {
        v["serviceJourney"]["id"]
        for v in data
        if (v.get("serviceJourney") or {}).get("id")
    }


def _journey_number(journey_id: str) -> str:
    """SJN:ServiceJourney:431_49904-R -> 431"""
    if "ServiceJourney:" not in journey_id:
        return ""
    tail = journey_id.split("ServiceJourney:", 1)[1]
    head = tail.split("_")[0].split("-")[0]
    return head if head.isdigit() else ""


if __name__ == "__main__":
    # Kjør:  python sjnord.py
    # Viser hvilke tog som mangler posisjon i Vehicle Positions og derfor
    # beregnes, uten å gå via kartet.
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    _navn = os.getenv("ET_CLIENT_NAME", "").strip()

    async def _kjor() -> list[dict]:
        # app.py sender inn sitt eget snapshot; her må vi spørre selv.
        har_gps = await hent_har_gps(_navn)
        log.info("Vehicle Positions har posisjon for %d turer", len(har_gps))
        return await fetch_computed(_navn, har_gps=har_gps)

    trains = asyncio.run(_kjor())

    if not trains:
        print("\nIngen tog uten posisjon underveis akkurat nå.")
        raise SystemExit

    print(f"\n{'linje':<7}{'tog':<8}{'forsinkelse':>13}  {'grunn':<17} strekning")
    print("-" * 92)
    for feature in sorted(trains, key=lambda f: (f["properties"]["line"] or "")):
        p = feature["properties"]
        delay = (
            f"{p['delay'] / 60:+.1f} min" if p["delay"] is not None else "ukjent"
        )
        mark = "" if p["realtime"] else "  (kun rutetabell)"
        print(
            f"{p['line']:<7}{p['trainNumber']:<8}{delay:>13}  "
            f"{p['computedReason']:<17} {p['previousStop']} -> {p['nextStop']}{mark}"
        )
