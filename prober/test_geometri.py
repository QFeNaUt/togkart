"""Regresjonstest for sporgeometrien i sjnord.py. Krever ikke nett.

Bygger en falsk Journey Planner-respons for strekningen F6 kjørte da feilen
ble observert - Hamar til Oslo lufthavn uten mellomstopp - og sjekker at toget
havner langs sporet og ikke i Mjøsa.

    python prober/test_geometri.py
"""

import asyncio
from datetime import datetime, timedelta, timezone

# Python legger mappen skriptet ligger i på søkestien, ikke prosjektroten.
# Uten dette feiler «import sjnord» når testen kjøres fra prober/.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sjnord
from sporgeometri import Sportrase, avstand_m, decode_polyline

# Omtrentlige punkter langs Dovrebanen, sør for Hamar.
DOVREBANEN = [
    (11.0680, 60.7945),  # Hamar stasjon
    (11.1150, 60.7600),
    (11.1650, 60.7300),
    (11.1867, 60.7168),  # Stange stasjon
    (11.2400, 60.6600),
    (11.2969, 60.5947),  # Tangen stasjon
    (11.2800, 60.4800),
    (11.2596, 60.3286),  # Eidsvoll stasjon
    (11.1600, 60.2400),
    (11.0997, 60.1939),  # Oslo lufthavn stasjon
]


def encode_polyline(points, precision=5):
    """Motstykket til decode_polyline. Finnes bare her, for å lage testdata."""
    factor = 10 ** precision
    out = []
    prev_lat = prev_lon = 0

    for lon, lat in points:
        ilat, ilon = round(lat * factor), round(lon * factor)
        for delta in (ilat - prev_lat, ilon - prev_lon):
            value = delta << 1
            if delta < 0:
                value = ~value
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev_lat, prev_lon = ilat, ilon

    return "".join(out)


def fortett(punkter, steg_m=200.0):
    """Legg inn mellompunkter, slik ekte pointsOnLink er tett."""
    ut = []
    for a, b in zip(punkter, punkter[1:]):
        antall = max(1, int(avstand_m(a, b) / steg_m))
        for i in range(antall):
            t = i / antall
            ut.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    ut.append(punkter[-1])
    return ut


def lag_tur(med_geometri=True, brok=0.15):
    """Én SJ-tur på formen Journey Planner leverer, plus tidspunktet 'nå'."""
    spor = fortett(DOVREBANEN)
    avgang = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    reisetid = timedelta(minutes=70)

    tur = {
        "id": "SJN:ServiceJourney:42_12345-R",
        "line": {"publicCode": "F6", "name": "Dovrebanen"},
        "estimatedCalls": [
            {
                "aimedDepartureTime": avgang.isoformat(),
                "expectedDepartureTime": avgang.isoformat(),
                "realtimeState": "updated",
                "quay": {
                    "name": "Hamar stasjon",
                    "latitude": DOVREBANEN[0][1],
                    "longitude": DOVREBANEN[0][0],
                },
            },
            {
                "aimedDepartureTime": (avgang + reisetid).isoformat(),
                "expectedDepartureTime": (avgang + reisetid).isoformat(),
                "realtimeState": "updated",
                "quay": {
                    "name": "Oslo lufthavn stasjon",
                    "latitude": DOVREBANEN[-1][1],
                    "longitude": DOVREBANEN[-1][0],
                },
            },
        ],
    }

    if med_geometri:
        tur["pointsOnLink"] = {"points": encode_polyline(spor), "length": 0}

    return tur, avgang + reisetid * brok, Sportrase.fra_punkter(spor)


def kjor(med_geometri=True, brok=0.15):
    sjnord._FORBEREDT.clear()
    tur, na, trase = lag_tur(med_geometri, brok)
    turer = {tur["id"]: tur}
    sjnord._prepare(turer)
    return sjnord.positions(turer, now=na)[0], trase


def test_rundtur():
    """Koderen og dekoderen må være motstykker."""
    fasit = [(-120.2, 38.5), (-120.95, 40.7), (-126.453, 43.252)]
    assert encode_polyline(fasit) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert decode_polyline(encode_polyline(DOVREBANEN)) == [
        (round(lon, 5), round(lat, 5)) for lon, lat in DOVREBANEN
    ]
    print("koder/dekoder      OK   rundtur uten tap")


def test_tog_pa_sporet():
    """Hovedtesten: F6 skal ligge på sporet, ikke i Mjøsa."""
    feature, trase = kjor(med_geometri=True)
    p = feature["properties"]
    lon, lat = feature["geometry"]["coordinates"]

    assert p["positionMethod"] == "track", p["positionMethod"]

    _, avvik = trase.projiser((lon, lat))
    assert avvik < 25, f"{avvik:.0f} m fra sporet"

    # Sammenlign med den gamle oppførselen.
    rett_lon = DOVREBANEN[0][0] + (DOVREBANEN[-1][0] - DOVREBANEN[0][0]) * 0.15
    rett_lat = DOVREBANEN[0][1] + (DOVREBANEN[-1][1] - DOVREBANEN[0][1]) * 0.15
    flyttet = avstand_m((rett_lon, rett_lat), (lon, lat))

    assert lon > 11.15, f"toget ligger fortsatt vest for sporet: {lon:.4f}"
    assert flyttet > 5000, f"bare {flyttet:.0f} m unna gammel posisjon"

    print(f"tog på sporet      OK   {lat:.4f}, {lon:.4f} - {avvik:.0f} m fra sporet, "
          f"{flyttet/1000:.1f} km øst for gammel posisjon")


def test_reserveløsning():
    """Uten pointsOnLink skal alt fungere som før, bare merket."""
    feature, _ = kjor(med_geometri=False)
    lon, lat = feature["geometry"]["coordinates"]

    assert feature["properties"]["positionMethod"] == "straight"
    assert lon < 11.10, f"luftlinjen skal gå rett sør, fikk {lon:.4f}"

    print(f"reserveløsning     OK   {lat:.4f}, {lon:.4f} - merket 'straight'")


def test_hele_ruta():
    """Toget skal følge sporet hele veien, ikke bare på ett tidspunkt."""
    verste = 0.0
    for brok in (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0):
        feature, trase = kjor(med_geometri=True, brok=brok)
        lon, lat = feature["geometry"]["coordinates"]
        _, avvik = trase.projiser((lon, lat))
        verste = max(verste, avvik)

    assert verste < 25, f"verste avvik {verste:.0f} m"
    print(f"hele ruta          OK   verste avvik {verste:.1f} m over sju tidspunkt")


def test_cache():
    """Andre henting skal gjenbruke dekodet geometri, ikke bygge på nytt."""
    sjnord._FORBEREDT.clear()
    tur, _, _ = lag_tur()
    turer = {tur["id"]: tur}

    sjnord._prepare(turer)
    forste = sjnord._FORBEREDT[tur["id"]]["trase"]
    sjnord._prepare(turer)
    andre = sjnord._FORBEREDT[tur["id"]]["trase"]

    assert forste is andre, "traseen ble bygget på nytt"

    # Turer som forsvinner fra feeden skal ut av cachen.
    sjnord._prepare({})
    assert not sjnord._FORBEREDT, "cachen vokser uten grense"

    print("cache              OK   gjenbrukes, og tømmes når turen er ferdig")


def test_ruteopptegning():
    sjnord._FORBEREDT.clear()
    tur, _, _ = lag_tur()
    sjnord._prepare({tur["id"]: tur})

    linje = sjnord.route_line(tur["id"])
    assert linje and len(linje) > 100, f"fikk {len(linje or [])} punkter"
    assert all(len(p) == 2 for p in linje)
    assert sjnord.route_line("finnes-ikke") is None

    print(f"ruteopptegning     OK   {len(linje)} punkter klar til LineString")


def test_avvist_geometri():
    """Feil trasé skal avvises, ikke brukes."""
    sjnord._FORBEREDT.clear()
    tur, na, _ = lag_tur()
    # Geometri for en helt annen bane - stoppene ligger langt unna.
    tur["pointsOnLink"] = {"points": encode_polyline(
        [(5.32, 60.39), (5.60, 60.50), (6.00, 60.60)]  # et sted vest for Voss
    )}

    turer = {tur["id"]: tur}
    sjnord._prepare(turer)
    feature = sjnord.positions(turer, now=na)[0]

    assert feature["properties"]["positionMethod"] == "straight"
    print("avvist geometri    OK   feil trasé forkastet, faller til luftlinje")


TURER = {
    "SJN:ServiceJourney:42_1-R": "2026-08-17",
    "SJN:ServiceJourney:43_1-R": "2026-08-17",
    "SJN:ServiceJourney:44_1-R": "2026-08-18",
}

_ENTUR = None


class _FalskRespons:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FalskEntur:
    """Later som den er Entur, og kan settes til å avvise hvert av feltene."""

    def __init__(self, avvis_geometri=False, avvis_dato=False):
        self.avvis_geometri = avvis_geometri
        self.avvis_dato = avvis_dato
        self.sporringer = []
        self.dato_til_ids = {}

    @staticmethod
    def _feil(melding):
        return _FalskRespons({"errors": [{"message": melding}]})

    @staticmethod
    def _tur(tur_id, med_geometri):
        tur, _, _ = lag_tur(med_geometri=med_geometri)
        tur["id"] = tur_id
        return tur

    def svar(self, sporring, variabler):
        self.sporringer.append(sporring)
        geometri = "pointsOnLink" in sporring

        if self.avvis_geometri and geometri:
            return self._feil("Validation error: Field 'pointsOnLink' in type "
                              "'ServiceJourney' is undefined")

        if "$dato: Date!" in sporring:                       # steg 2
            if self.avvis_dato:
                return self._feil("Validation error: Unknown argument 'date' on "
                                  "field 'ServiceJourney.estimatedCalls'")

            # Aliasene er t0, t1, t2 ... i samme rekkefølge som id0, id1, id2.
            ids, i = [], 0
            while f"id{i}" in variabler:
                ids.append(variabler[f"id{i}"])
                i += 1

            self.dato_til_ids.setdefault(variabler["dato"], set()).update(ids)
            return _FalskRespons({"data": {
                f"t{n}": self._tur(tur_id, geometri) for n, tur_id in enumerate(ids)
            }})

        if "query Hvem" in sporring:                         # steg 1
            return _FalskRespons({"data": {"stopPlace": {"estimatedCalls": [
                {"date": dato, "serviceJourney": {"id": tur_id}}
                for tur_id, dato in TURER.items()
            ]}}})

        return _FalskRespons({"data": {"stopPlace": {"estimatedCalls": [    # samlet
            {"serviceJourney": self._tur(tur_id, geometri)} for tur_id in TURER
        ]}}})


class _FalskKlient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        return _ENTUR.svar(json["query"], json.get("variables") or {})


def _hent_fra(entur):
    """Kjør fetch_journeys mot en falsk Entur, og rydd opp etterpå."""
    global _ENTUR
    ekte = sjnord.httpx.AsyncClient
    _ENTUR = entur
    sjnord._FORBEREDT.clear()
    sjnord._GEOMETRI_I_SPORRING = True
    sjnord._DATO_I_SPORRING = True
    sjnord.httpx.AsyncClient = _FalskKlient
    try:
        return asyncio.run(sjnord.fetch_journeys("test"))
    finally:
        sjnord.httpx.AsyncClient = ekte


def test_kjoredato():
    """Hver tur skal spørres med sin egen kjøredato.

    Dette er rettelsen av problem 3. Målt 18. august 01:25: den nøstede
    spørringen ga 42 av 42 turer forskjøvet nøyaktig 24 timer, fordi den ikke
    tar imot en kjøredato. Nå grupperes turene etter dato før detaljene hentes.
    """
    entur = _FalskEntur()
    turer = _hent_fra(entur)
    sjnord._GEOMETRI_I_SPORRING = True
    sjnord._DATO_I_SPORRING = True

    assert len(turer) == len(TURER), f"fikk {len(turer)} av {len(TURER)} turer"
    assert entur.dato_til_ids == {
        "2026-08-17": {"SJN:ServiceJourney:42_1-R", "SJN:ServiceJourney:43_1-R"},
        "2026-08-18": {"SJN:ServiceJourney:44_1-R"},
    }, entur.dato_til_ids

    med_spor = sum(
        1 for e in sjnord._FORBEREDT.values() if e["trase"] is not None
    )
    assert med_spor == len(TURER), f"bare {med_spor} fikk sporgeometri"

    print(f"kjøredato          OK   {len(TURER)} turer fordelt på "
          f"{len(entur.dato_til_ids)} datoer, hver spurt med sin egen")


def test_geometri_avvist():
    """Avviser Entur pointsOnLink, skal togene overleve - bare geometrien ryke."""
    entur = _FalskEntur(avvis_geometri=True)
    turer = _hent_fra(entur)
    slatt_av = not sjnord._GEOMETRI_I_SPORRING
    sjnord._GEOMETRI_I_SPORRING = True
    sjnord._DATO_I_SPORRING = True

    assert turer, "togene forsvant da geometrien ble avvist"
    assert slatt_av, "bryteren for geometri ble ikke slått av"

    print(f"geometri avvist    OK   {len(turer)} turer overlevde uten geometri")


def test_dato_avvist():
    """Avviser Entur date-argumentet, skal vi falle tilbake til gammel form.

    Da er vi tilbake til feilen fra 18. august - tomt kart om natta - men det
    er bedre enn ingen tog i det hele tatt, og loggen sier tydelig fra.
    """
    entur = _FalskEntur(avvis_dato=True)
    turer = _hent_fra(entur)
    slatt_av = not sjnord._DATO_I_SPORRING
    sjnord._GEOMETRI_I_SPORRING = True
    sjnord._DATO_I_SPORRING = True

    assert turer, "togene forsvant da datospørringen ble avvist"
    assert slatt_av, "bryteren for kjøredato ble ikke slått av"
    assert any("query Journeys" in s for s in entur.sporringer), \
        "falt aldri tilbake til den samlede spørringen"

    print(f"kjøredato avvist   OK   {len(turer)} turer via samlet spørring")


# ---------------------------------------------------------------------------
# Filteret: hvem sin posisjon skal regnes ut?
# ---------------------------------------------------------------------------

def test_tognokkel():
    """Nøkkelen begge kildene kan uttrykke likt."""
    assert sjnord.tognokkel("L1", "2263") == "L1:2263"
    # Linjekoden kommer noen ganger med kodeområde foran.
    assert sjnord.tognokkel("VYG:Line:L1", "2263") == "L1:2263"
    # Tomme felt skal gi en nøkkel som ikke matcher noe ved et uhell.
    assert sjnord.tognokkel(None, None) == ":"
    print("tognøkkel          OK   linje og tognummer, uten kodeområde")


def test_skal_beregnes():
    """Hovedfilteret. Fire tilfeller som må holdes fra hverandre."""
    sjn = "SJN:ServiceJourney:431_49904-R"
    vy = "VYG:ServiceJourney:2263_443691-R"

    # 1. Uten kunnskap om hvem som har GPS regner vi bare for dem som aldri
    #    har det. Å bomme her skal gi for få tog, ikke to prikker på ett.
    assert sjnord.skal_beregnes(sjn, "F6", True, None)
    assert not sjnord.skal_beregnes(vy, "L1", True, None)

    # 2. Et tomt sett er en feed som er nede, ikke «ingen har GPS».
    assert not sjnord.skal_beregnes(vy, "L1", True, set())

    # 3. Med kunnskap: turen mangler posisjon og har sanntid -> beregnes.
    har = {"VYG:ServiceJourney:9999_1-R"}
    assert sjnord.skal_beregnes(vy, "L1", True, har)

    # 4. Uten sanntid vet vi bare hva rutetabellen lover. SJ er unntaket -
    #    halvparten av avgangene deres får aldri sanntid, og de skal likevel
    #    tegnes (grå).
    assert not sjnord.skal_beregnes(vy, "L1", False, har)
    assert sjnord.skal_beregnes(sjn, "F6", False, har)

    print("filter             OK   fire tilfeller holdt fra hverandre")


def test_datert_tur_id():
    """Regresjonsvern for feilen som ga fem doble prikker.

    Vehicle Positions svarer for noen Vy-tog med en DatedServiceJourney-ID,
    Journey Planner med en ServiceJourney-ID. Samme tog, ulike ID-rom. Uten
    tognøkkelen ser turen ut som et hull og blir tegnet en gang til - ved
    siden av prikken som allerede står der.
    """
    jp = "VYG:ServiceJourney:1931_443485-R"
    vp = "VYG:DatedServiceJourney:1931_OSL-RST_26-08-21"

    # Bare den rå ID-en: turen ser ut som et hull.
    assert sjnord.skal_beregnes(jp, "R22", True, {vp})

    # Med tognøkkelen i settet, slik app.py bygger det: fanget.
    har = {vp, sjnord.tognokkel("R22", "1931")}
    assert not sjnord.skal_beregnes(jp, "R22", True, har)
    print("datert tur-ID      OK   samme tog, to ID-rom, én prikk")


def test_hopp_over():
    """positions() må filtrere på nytt, med ferske ID-er.

    Rutedataene caches i et minutt, Vehicle Positions hentes hvert tiende
    sekund. Et tog som begynner å sende GPS midt i det minuttet skal falle ut
    med en gang, ikke når cachen løper ut.
    """
    sjnord._FORBEREDT.clear()
    tur, na, _ = lag_tur(med_geometri=True, brok=0.15)
    turer = {tur["id"]: tur}
    sjnord._prepare(turer)

    assert len(sjnord.positions(turer, now=na)) == 1
    assert not sjnord.positions(turer, now=na, hopp_over={tur["id"]})
    # Og via tognøkkelen, som er den som fanger de daterte ID-ene.
    nokkel = sjnord.tognokkel("F6", "42")
    assert not sjnord.positions(turer, now=na, hopp_over={nokkel})
    print("hopp over          OK   både tur-ID og tognøkkel filtrerer")


def test_computed_reason():
    """De to grunnene må kunne skilles i dataene, ikke bare i teksten."""
    sjnord._FORBEREDT.clear()
    tur, na, _ = lag_tur(med_geometri=True, brok=0.15)
    turer = {tur["id"]: tur}
    sjnord._prepare(turer)
    assert sjnord.positions(turer, now=na)[0]["properties"]["computedReason"] \
        == "ingen-gps"

    # Samme tur under et kodeområde som normalt har GPS.
    sjnord._FORBEREDT.clear()
    vy_tur = dict(tur, id="VYG:ServiceJourney:42_12345-R")
    turer = {vy_tur["id"]: vy_tur}
    sjnord._prepare(turer)
    assert sjnord.positions(turer, now=na)[0]["properties"]["computedReason"] \
        == "mangler-posisjon"
    print("grunn              OK   ingen-gps og mangler-posisjon skilt")


def test_geometri_gjenbrukes():
    """Sporgeometrien skal hentes én gang per tur, ikke én gang per henting.

    Uten dette koster den brede dekningen megabyte i minuttet: målt 4-44 kB
    per tur med geometri mot 1-5 kB uten. `_mangler_geometri` er bryteren, og
    `_prepare` må snappe stoppene på nytt mot den lagrede trasen når
    rutetidene endrer seg - ellers ville en tur som mistet et stopp falt til
    luftlinje uten at noen ba om det.
    """
    sjnord._FORBEREDT.clear()
    tur, na, _ = lag_tur(med_geometri=True, brok=0.15)
    turer = {tur["id"]: tur}

    assert sjnord._mangler_geometri(tur["id"]), "ny tur må be om geometri"
    sjnord._prepare(turer)
    assert not sjnord._mangler_geometri(tur["id"]), "geometrien ble ikke husket"
    trase_for = sjnord._FORBEREDT[tur["id"]]["trase"]
    assert trase_for is not None

    # Neste henting: samme tur, ingen pointsOnLink i svaret fordi vi ikke spurte.
    uten = dict(tur)
    uten.pop("pointsOnLink")
    sjnord._prepare({uten["id"]: uten}, uten_geometri={uten["id"]})
    assert sjnord._FORBEREDT[tur["id"]]["trase"] is trase_for, \
        "trasen gikk tapt da vi ikke spurte om den"

    # Og med et stopp EKSTRA - slik ser en rute ut når et stopp legges inn
    # eller innstilles underveis. Offsetene må regnes på nytt mot trasen vi
    # allerede har, ikke kastes til fordel for luftlinje.
    forste = uten["estimatedCalls"][0]
    siste = uten["estimatedCalls"][-1]
    midt_tid = (
        datetime.fromisoformat(forste["expectedDepartureTime"])
        + (datetime.fromisoformat(siste["expectedDepartureTime"])
           - datetime.fromisoformat(forste["expectedDepartureTime"])) / 2
    ).isoformat()
    stange = {
        "aimedDepartureTime": midt_tid,
        "expectedDepartureTime": midt_tid,
        "realtimeState": forste.get("realtimeState"),
        "quay": {
            "name": "Stange stasjon",
            "latitude": DOVREBANEN[3][1],
            "longitude": DOVREBANEN[3][0],
        },
    }
    flere = dict(uten)
    flere["estimatedCalls"] = [forste, stange, siste]
    sjnord._prepare({flere["id"]: flere}, uten_geometri={flere["id"]})
    forberedt = sjnord._FORBEREDT[tur["id"]]
    assert forberedt["trase"] is trase_for, \
        "trasen overlevde ikke et endret stoppmønster"
    assert len(forberedt["offsets"]) == len(forberedt["timeline"]) == 3, \
        "offsetene ble ikke snappet på nytt"

    # Og det som er hele poenget: toget skal fortsatt ligge på sporet etter
    # at trasen er gjenbrukt. Et gjenbruk som gir riktig antall offsets, men
    # feil posisjon, ville vært verre enn å hente geometrien på nytt.
    plassert = sjnord.positions({flere["id"]: flere}, now=na)[0]
    assert plassert["properties"]["positionMethod"] == "track", \
        "toget falt til luftlinje etter gjenbruk av trasen"
    lon, lat = plassert["geometry"]["coordinates"]
    trase_punkter = Sportrase.fra_punkter(DOVREBANEN)
    assert trase_punkter.projiser((lon, lat))[1] < 50, \
        "toget havnet utenfor sporet etter gjenbruk av trasen"

    # Motprøven mot at en tur kan bli stående med en trasé som ikke passer:
    # ryker snappingen, skal turen falle til luftlinje OG be om geometri på
    # nytt. Her fjernes stoppet i stedet for å flyttes, siden `_prepare` har
    # antall stopp som bryter - se kommentaren der.
    sjnord._FORBEREDT.clear()
    sjnord._prepare({flere["id"]: flere})
    ett_stopp = dict(uten)
    ett_stopp["estimatedCalls"] = [siste]
    sjnord._prepare({ett_stopp["id"]: ett_stopp}, uten_geometri={ett_stopp["id"]})
    assert sjnord._FORBEREDT[tur["id"]]["trase"] is None
    assert sjnord._mangler_geometri(tur["id"]), \
        "en tur som ikke lenger passer trasen må hente geometrien på nytt"
    print("geometri gjenbruk  OK   hentes én gang, snappes på nytt ved behov")


if __name__ == "__main__":
    print("sjnord + sporgeometri — regresjonstest\n")
    test_rundtur()
    test_tog_pa_sporet()
    test_reserveløsning()
    test_hele_ruta()
    test_cache()
    test_ruteopptegning()
    test_avvist_geometri()
    test_kjoredato()
    test_geometri_avvist()
    test_dato_avvist()
    test_tognokkel()
    test_skal_beregnes()
    test_datert_tur_id()
    test_hopp_over()
    test_computed_reason()
    test_geometri_gjenbrukes()
    print("\nAlt grønt.")
