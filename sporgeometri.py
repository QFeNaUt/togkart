"""
sporgeometri.py — plasser tog langs sporet i stedet for i luftlinje.

Bakgrunn
--------
SJ Norge publiserer ingen GPS til Entur. Posisjonene i TogKart beregnes derfor
fra stoppetider: har toget brukt 40 % av tiden mellom to stasjoner, plasseres
det 40 % av veien mellom dem. Så lenge den veien er en rett linje, havner toget
i terrenget der sporet svinger.

Observert 17. august 2026: F6 (tog 42, Dovrebanen) mellom Hamar og Oslo
lufthavn ble tegnet nordvest for Eidsvoll, ute i Mjøsa-området. Bane NOR viste
samtidig toget rett sør for Stange — omtrent 11 km lenger øst. Årsaken er at
strekningen Hamar–Oslo lufthavn kjøres uten mellomstopp, så luftlinjen går
nesten rett sør mens sporet buler østover om Stange og Tangen.

Denne modulen bytter ut luftlinjen med den faktiske traseen, som Entur oppgir
som en Google-kodet polylinje i feltet `pointsOnLink`.

Koordinatrekkefølge
-------------------
Alle punkter er `(lon, lat)` — samme rekkefølge som GeoJSON bruker, og samme
som resten av TogKart. Skriver du `(lat, lon)` et sted, havner togene i
Somalia. Det er den vanligste feilen i denne typen kode.

Selvtest
--------
    python sporgeometri.py
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass

Punkt = tuple[float, float]  # (lon, lat)


# ---------------------------------------------------------------------------
# 1. Dekoding av Google-kodet polylinje
# ---------------------------------------------------------------------------

def _les_tall(kodet: str):
    """
    Generator som gir ut ett og ett heltall fra en kodet polylinjestreng.

    Formatet pakker hvert tall i grupper på fem bit. Hver gruppe legges i en
    ASCII-verdi ved å legge til 63, og alle grupper unntatt den siste får satt
    bit 6 (0x20) som en «det kommer mer»-markør. Tallet er lagret som
    zigzag-koding, der siste bit forteller om verdien er negativ.
    """
    index = 0
    while index < len(kodet):
        resultat = 0
        skift = 0
        while True:
            byte = ord(kodet[index]) - 63
            index += 1
            resultat |= (byte & 0x1F) << skift
            skift += 5
            if byte < 0x20:          # ingen fortsettelsesbit -> tallet er ferdig
                break
        # Zigzag: partall er positive, oddetall er negative.
        # I Python er ~x det samme som -x-1, som er nøyaktig det vi trenger.
        yield ~(resultat >> 1) if (resultat & 1) else (resultat >> 1)


def decode_polyline(kodet: str, presisjon: int = 5) -> list[Punkt]:
    """
    Dekoder en Google-kodet polylinje til en liste med (lon, lat).

    Tallene kommer parvis og er *differanser* fra forrige punkt, ikke absolutte
    koordinater. Derfor summeres de opp underveis.

    Entur bruker presisjon 5 (fem desimaler ≈ 1 meter).
    """
    faktor = 10 ** presisjon
    lat = lon = 0
    punkter: list[Punkt] = []

    tall = _les_tall(kodet)
    for dlat in tall:
        dlon = next(tall, None)
        if dlon is None:            # ujevnt antall tall -> strengen er trunkert
            break
        lat += dlat
        lon += dlon
        punkter.append((lon / faktor, lat / faktor))

    return punkter


# ---------------------------------------------------------------------------
# 2. Avstand og projeksjon
# ---------------------------------------------------------------------------
#
# Over noen kilometer kan jorda regnes som flat, så lenge man korrigerer for at
# lengdegrader ligger tettere jo lenger nord man kommer. På 60 grader nord er
# én lengdegrad omtrent halvparten så bred som ved ekvator. Det er billigere enn
# haversine og mer enn nøyaktig nok når segmentene er noen titalls meter lange.

M_PER_GRAD_LAT = 111_132.0


def _m_per_grad_lon(lat: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat))


def avstand_m(a: Punkt, b: Punkt) -> float:
    """Avstand i meter mellom to (lon, lat)-punkter."""
    midt_lat = (a[1] + b[1]) / 2
    dx = (b[0] - a[0]) * _m_per_grad_lon(midt_lat)
    dy = (b[1] - a[1]) * M_PER_GRAD_LAT
    return math.hypot(dx, dy)


def avstand_til_strekning(punkt: Punkt, a: Punkt, b: Punkt) -> float:
    """Korteste avstand i meter fra `punkt` til linjestykket a-b.

    Offentlig inngang til projeksjonen `_projiser_pa_segment` allerede gjorde.
    Den finnes fordi flaskehals.py trenger å svare på «hvor nær stasjonen
    kjørte toget», og svaret må måles mot BANEN mellom to observasjoner og
    ikke mot observasjonene selv - ellers straffer man fart.
    """
    _, avvik = _projiser_pa_segment(punkt, a, b)
    return avvik


def forenkle(punkter: list[Punkt], toleranse_m: float) -> list[Punkt]:
    """
    Fjerner punkter som ikke endrer linjens form mer enn `toleranse_m`.

    Algoritmen (Ramer–Douglas–Peucker): trekk en rett strek fra første til
    siste punkt. Finn punktet som ligger lengst fra den streken. Ligger det
    nærmere enn toleransen, kan *alt* imellom kastes — streken beskriver formen
    godt nok. Ligger det lenger unna, må det punktet beholdes, og du gjentar
    oppskriften på hver av de to halvdelene det deler linjen i.

    Nordlandsbanen har rundt 18 000 punkter med 34 meters mellomrom. Det er
    riktig oppløsning når et tog skal ligge på sporet, og feil oppløsning når
    hele Norge er 1440 piksler høy.

    Merk løkken i stedet for rekursjon: en trasé som er nesten rett gir
    ubalanserte delinger, og med 18 000 punkter kan dybden sprenge Pythons
    grense på 1000 nivåer. Vi holder «hva gjenstår å se på» i en egen liste.

    Funksjonen bodde i `lagbaner.py` til 21. august, da `jernbanenett.py`
    fikk bruk for den i drift. Den flyttet hit fordi lagbaner.py er et
    byggeskript som drar inn httpx og dotenv, og et API-svar skal ikke ha en
    Overpass-klient i importkjeden for å kunne forenkle en strek.
    """
    if len(punkter) < 3:
        return list(punkter)

    behold = [False] * len(punkter)
    behold[0] = behold[-1] = True

    stabel = [(0, len(punkter) - 1)]
    while stabel:
        start, slutt = stabel.pop()
        if slutt <= start + 1:
            continue

        verste_avvik = 0.0
        verste_i = start
        for i in range(start + 1, slutt):
            _, avvik = _projiser_pa_segment(punkter[i], punkter[start], punkter[slutt])
            if avvik > verste_avvik:
                verste_avvik = avvik
                verste_i = i

        if verste_avvik > toleranse_m:
            behold[verste_i] = True
            stabel.append((start, verste_i))
            stabel.append((verste_i, slutt))

    return [p for p, ja in zip(punkter, behold) if ja]


def retning_grader(a: Punkt, b: Punkt) -> float | None:
    """Kompassretningen fra a til b: 0 = nord, 90 = øst, 180 = sør.

    Samme flate tilnærming som `avstand_m`, og av samme grunn: over noen
    hundre meter er forskjellen fra en ekte storsirkelkurs langt mindre enn ett
    grad, og pila i kartet er ni piksler bred.

    Merk at dette IKKE er atan2(dy, dx) slik man er vant til fra matematikken.
    Kompassgrader teller med klokka fra nord, matematiske grader mot klokka fra
    øst. `atan2(dx, dy)` i den rekkefølgen gir kompassretningen direkte — det
    er ikke en skrivefeil, det er hele forskjellen.

    None når punktene er like: da finnes det ingen retning, og null grader
    ville vært en påstand om at toget kjører nordover.
    """
    midt_lat = (a[1] + b[1]) / 2
    dx = (b[0] - a[0]) * _m_per_grad_lon(midt_lat)
    dy = (b[1] - a[1]) * M_PER_GRAD_LAT
    if dx == 0.0 and dy == 0.0:
        return None
    return math.degrees(math.atan2(dx, dy)) % 360.0


def retning_langs_spor(
    trase: Sportrase,
    offsets: list[float],
    indeks: int,
    brok: float,
    meter: float = 30.0,
) -> float | None:
    """Retningen sporet peker der toget er nå.

    Tangenten, målt som retningen mellom et punkt litt bak og litt foran.
    Sporet er tegnet med noen titalls meter mellom hvert punkt, så en tangent
    mellom to nabopunkter ville hoppet fram og tilbake med tegnestøyen; 30
    meter til hver side jevner ut den støyen uten å runde av en ekte sving.

    Retningen er alltid den veien toget KJØRER, ikke den veien traseen er
    tegnet. Traseen går fra turens start til dens slutt, så de er heldigvis
    de samme — men skulle offsetene noen gang synke, faller vi til None i
    stedet for å peke motsatt vei.
    """
    d0, d1 = offsets[indeks], offsets[indeks + 1]
    if d1 <= d0:
        return None

    brok = max(0.0, min(1.0, brok))
    her = d0 + brok * (d1 - d0)
    bak = trase.punkt_ved(max(0.0, her - meter))
    fram = trase.punkt_ved(min(trase.lengde_m, her + meter))
    return retning_grader(bak, fram)


def _projiser_pa_segment(punkt: Punkt, a: Punkt, b: Punkt) -> tuple[float, float]:
    """
    Finner nærmeste punkt på linjestykket a→b.

    Returnerer (t, avvik_m), der t mellom 0 og 1 sier hvor langs segmentet det
    nærmeste punktet ligger, og avvik_m er avstanden dit fra `punkt`.
    """
    midt_lat = (a[1] + b[1]) / 2
    sx = _m_per_grad_lon(midt_lat)
    sy = M_PER_GRAD_LAT

    ax, ay = a[0] * sx, a[1] * sy
    bx, by = b[0] * sx, b[1] * sy
    px, py = punkt[0] * sx, punkt[1] * sy

    dx, dy = bx - ax, by - ay
    lengde2 = dx * dx + dy * dy
    if lengde2 == 0:
        return 0.0, math.hypot(px - ax, py - ay)

    # Skalarprojeksjon, klemt inn i [0, 1] slik at vi holder oss på segmentet.
    t = ((px - ax) * dx + (py - ay) * dy) / lengde2
    t = max(0.0, min(1.0, t))

    naer_x, naer_y = ax + t * dx, ay + t * dy
    return t, math.hypot(px - naer_x, py - naer_y)


# ---------------------------------------------------------------------------
# 3. Traseen
# ---------------------------------------------------------------------------

@dataclass
class Sportrase:
    """
    En trasé med forhåndsberegnede avstander.

    `kumulativ[i]` er antall meter fra start fram til `punkter[i]`. Den listen
    beregnes én gang, og gjør oppslag underveis til et binærsøk i stedet for en
    ny summering av hele linjen.
    """

    punkter: list[Punkt]
    kumulativ: list[float]

    # -- konstruktører ------------------------------------------------------

    @classmethod
    def fra_punkter(cls, punkter: list[Punkt]) -> "Sportrase":
        rensede = [p for i, p in enumerate(punkter) if i == 0 or p != punkter[i - 1]]
        if len(rensede) < 2:
            raise ValueError("En trasé trenger minst to ulike punkter")

        kumulativ = [0.0]
        for a, b in zip(rensede, rensede[1:]):
            kumulativ.append(kumulativ[-1] + avstand_m(a, b))
        return cls(rensede, kumulativ)

    @classmethod
    def fra_polylinje(cls, kodet: str, presisjon: int = 5) -> "Sportrase":
        return cls.fra_punkter(decode_polyline(kodet, presisjon))

    # -- oppslag ------------------------------------------------------------

    @property
    def lengde_m(self) -> float:
        return self.kumulativ[-1]

    def _segment_for(self, meter: float) -> int:
        """Indeksen til segmentet som inneholder gitt avstand fra start."""
        i = bisect_right(self.kumulativ, meter) - 1
        return min(max(i, 0), len(self.punkter) - 2)

    def punkt_ved(self, meter: float) -> Punkt:
        """Koordinatet som ligger `meter` inn langs traseen."""
        meter = max(0.0, min(meter, self.lengde_m))
        i = self._segment_for(meter)
        d0, d1 = self.kumulativ[i], self.kumulativ[i + 1]
        a, b = self.punkter[i], self.punkter[i + 1]
        if d1 <= d0:
            return a
        t = (meter - d0) / (d1 - d0)
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))

    def projiser(
        self,
        punkt: Punkt,
        fra_meter: float = 0.0,
        god_nok_m: float = 250.0,
        vindu_m: float = 2000.0,
    ) -> tuple[float, float]:
        """
        Snapper et fritt punkt til traseen.

        Returnerer (avstand_langs_sporet, avvik_fra_sporet) i meter. `fra_meter`
        begrenser søket til den delen av traseen som ligger etter et gitt punkt,
        slik at stasjonene låses i riktig rekkefølge selv om sporet passerer
        nær seg selv.

        Nordlandsbanen er 730 km, og en trasé kan ha titusenvis av punkter. Å
        skanne hele linjen for hvert av femten stopp blir dyrt. Derfor stopper
        søket når det har funnet et treff nærmere enn `god_nok_m` og deretter
        gått `vindu_m` forbi det uten å finne noe bedre. Finner det aldri noe
        godt treff, skannes hele linjen — og da avviser `bygg_trase` traseen
        uansett.
        """
        start = self._segment_for(max(0.0, fra_meter))
        beste_d = fra_meter
        beste_avvik = float("inf")

        for i in range(start, len(self.punkter) - 1):
            t, avvik = _projiser_pa_segment(punkt, self.punkter[i], self.punkter[i + 1])
            if avvik < beste_avvik:
                beste_avvik = avvik
                beste_d = self.kumulativ[i] + t * (self.kumulativ[i + 1] - self.kumulativ[i])
            if beste_avvik <= god_nok_m and self.kumulativ[i] - beste_d > vindu_m:
                break

        return max(beste_d, fra_meter), beste_avvik

    def delstrekning(self, fra_m: float, til_m: float) -> list[Punkt]:
        """
        Punktene mellom to avstander, med nøyaktige endepunkter.

        Brukes til å tegne opp ruten i kartet når man klikker på et tog.
        """
        fra_m, til_m = sorted((max(0.0, fra_m), min(til_m, self.lengde_m)))
        ut = [self.punkt_ved(fra_m)]
        for i in range(self._segment_for(fra_m) + 1, len(self.punkter)):
            if self.kumulativ[i] >= til_m:
                break
            ut.append(self.punkter[i])
        ut.append(self.punkt_ved(til_m))
        return ut


# ---------------------------------------------------------------------------
# 4. Bruk mot stoppestedene
# ---------------------------------------------------------------------------

def offsets_for_stopp(
    trase: Sportrase, stopp: list[Punkt]
) -> tuple[list[float], list[float]]:
    """
    Finner hvor langt inn på traseen hvert stoppested ligger.

    `pointsOnLink` på en ServiceJourney dekker hele turen, ikke ett strekk om
    gangen. For å vite hvilken del av linjen som hører til strekket Hamar–Oslo
    lufthavn må hvert stopp snappes til traseen først. Returnerer (offsets,
    avvik) — begge i meter, én verdi per stopp.
    """
    offsets: list[float] = []
    avvik: list[float] = []
    forrige = 0.0
    for punkt in stopp:
        d, a = trase.projiser(punkt, fra_meter=forrige)
        offsets.append(d)
        avvik.append(a)
        forrige = d
    return offsets, avvik


def snapp_stopp(
    trase: Sportrase,
    stopp: list[Punkt],
    maks_avvik_m: float = 1000.0,
) -> tuple[list[float] | None, str]:
    """
    Snapper stoppene til en trasé vi allerede har, og godkjenner resultatet.

    Returnerer (offsets, årsak). Er snappingen ubrukelig, kommer (None,
    forklaring) tilbake og kalleren faller tilbake til luftlinje.

    Skilt ut fra `bygg_trase` fordi en trasé kan overleve at rutetidene endrer
    seg: `pointsOnLink` for en ServiceJourney er den samme hele dagen, mens
    stoppene kan bli flere eller færre når en avgang innstilles underveis. Da
    skal offsetene regnes på nytt — men polylinjen skal ikke hentes en gang til.
    """
    if len(stopp) < 2:
        return None, "for få stoppesteder"

    offsets, avvik = offsets_for_stopp(trase, stopp)

    # Ligger et stoppested en kilometer fra sporet, er det ikke sporet vi har
    # fått — da er en rett linje mellom stasjonene ærligere enn en presis
    # posisjon på feil bane.
    verste = max(avvik)
    if verste > maks_avvik_m:
        return None, f"stoppested {round(verste)} m fra sporet"

    # Offsets må øke. Gjør de ikke det, har snappingen kollapset to stopp til
    # samme punkt, og brøken mellom dem blir meningsløs.
    if any(b - a < 1.0 for a, b in zip(offsets, offsets[1:])):
        return None, "stoppene fordeler seg ikke langs traseen"

    return offsets, "ok"


def bygg_trase(
    points_on_link: dict | None,
    stopp: list[Punkt],
    maks_avvik_m: float = 1000.0,
) -> tuple[Sportrase | None, list[float] | None, str]:
    """
    Bygger trasé og stopp-offsets fra et rått `pointsOnLink`-objekt.

    Returnerer (trase, offsets, årsak). Er trasen ubrukelig, kommer (None, None,
    forklaring) tilbake, og kalleren faller tilbake til luftlinje.
    """
    kodet = (points_on_link or {}).get("points")
    if not kodet:
        return None, None, "mangler pointsOnLink"

    try:
        trase = Sportrase.fra_polylinje(kodet)
    except (ValueError, IndexError) as feil:
        return None, None, f"kunne ikke dekode polylinjen: {feil}"

    offsets, aarsak = snapp_stopp(trase, stopp, maks_avvik_m)
    if offsets is None:
        return None, None, aarsak

    return trase, offsets, "ok"


def posisjon_langs_spor(
    trase: Sportrase, offsets: list[float], indeks: int, brok: float
) -> Punkt:
    """Posisjonen `brok` av veien fra stopp nr. `indeks` til det neste."""
    brok = max(0.0, min(1.0, brok))
    d0, d1 = offsets[indeks], offsets[indeks + 1]
    return trase.punkt_ved(d0 + brok * (d1 - d0))


def interpoler_rettlinje(a: Punkt, b: Punkt, brok: float) -> Punkt:
    """Dagens oppførsel, beholdt som reserveløsning."""
    brok = max(0.0, min(1.0, brok))
    return (a[0] + brok * (b[0] - a[0]), a[1] + brok * (b[1] - a[1]))


# ---------------------------------------------------------------------------
# 5. Selvtest
# ---------------------------------------------------------------------------

def _test_dekoder() -> None:
    """Testvektoren fra Googles egen dokumentasjon."""
    kodet = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    fasit = [(-120.2, 38.5), (-120.95, 40.7), (-126.453, 43.252)]
    fikk = decode_polyline(kodet)

    assert len(fikk) == len(fasit), f"fikk {len(fikk)} punkter, ventet {len(fasit)}"
    for (lon, lat), (f_lon, f_lat) in zip(fikk, fasit):
        assert abs(lon - f_lon) < 1e-6 and abs(lat - f_lat) < 1e-6, f"{lon},{lat}"

    print("dekoder            OK   3 punkter, avvik under 1e-6 grader")


def _demo_f6() -> None:
    """
    Strekningen F6 kjørte da feilen ble observert.

    Koordinatene er omtrentlige punkter langs Dovrebanen, ikke ekte
    sporgeometri fra Entur — nok til å vise hva feilen koster i meter.
    """
    spor = [
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
    trase = Sportrase.fra_punkter(spor)
    hamar, osl = spor[0], spor[-1]

    print(f"\nF6  Hamar -> Oslo lufthavn")
    print(f"  luftlinje {avstand_m(hamar, osl)/1000:5.1f} km")
    print(f"  langs spor {trase.lengde_m/1000:4.1f} km\n")

    print("  brøk   luftlinje (lon, lat)     langs spor (lon, lat)     avvik")
    print("  " + "-" * 63)
    for brok in (0.0, 0.15, 0.25, 0.40, 0.60, 0.80, 1.0):
        rett = interpoler_rettlinje(hamar, osl, brok)
        langs = trase.punkt_ved(brok * trase.lengde_m)
        feil = avstand_m(rett, langs)
        print(f"  {brok:4.2f}   {rett[0]:7.4f}, {rett[1]:7.4f}        "
              f"{langs[0]:7.4f}, {langs[1]:7.4f}      {feil/1000:5.1f} km")

    verste = max(
        avstand_m(interpoler_rettlinje(hamar, osl, b / 100),
                  trase.punkt_ved(b / 100 * trase.lengde_m))
        for b in range(101)
    )
    print(f"\n  største avvik      {verste/1000:.1f} km")


def _test_snapping() -> None:
    """Stasjoner skal snappe til traseen i riktig rekkefølge."""
    spor = [(11.00, 60.00), (11.10, 60.10), (11.30, 60.20), (11.20, 60.35)]
    trase = Sportrase.fra_punkter(spor)

    # Stasjoner lagt litt ved siden av sporet, slik ekte stasjonspunkter ligger.
    stopp = [(11.001, 60.001), (11.299, 60.201), (11.201, 60.349)]
    offsets, avvik = offsets_for_stopp(trase, stopp)

    assert offsets == sorted(offsets), "offsets må øke langs traseen"
    assert max(avvik) < 200, f"snappet {round(max(avvik))} m unna"

    midt = posisjon_langs_spor(trase, offsets, 0, 0.5)
    d, _ = trase.projiser(midt)
    assert offsets[0] < d < offsets[1], "midtpunktet må ligge mellom stoppene"

    print(f"snapping           OK   avvik under {round(max(avvik))} m, "
          f"offsets i rekkefølge")


def _test_delstrekning() -> None:
    # Tett trasé, slik ekte pointsOnLink er: mange punkter noen titalls meter fra
    # hverandre. Da må delstrekningen ta med punktene som ligger imellom.
    trase = Sportrase.fra_punkter([(11.00 + i * 0.002, 60.00 + i * 0.004)
                                   for i in range(50)])
    bit = trase.delstrekning(1000, 5000)

    assert len(bit) > 2, "skal ha med mellomliggende punkter, ikke bare endepunktene"
    assert abs(trase.projiser(bit[0])[0] - 1000) < 1
    assert abs(trase.projiser(bit[-1])[0] - 5000) < 1

    print(f"delstrekning       OK   {len(bit)} punkter mellom 1 og 5 km")


def _test_ytelse() -> None:
    """
    Nordlandsbanen i størrelsesorden: lang trasé, mange punkter, femten stopp.

    Dette kjører én gang per henting, ikke per forespørsel, men det skjer inne
    i en async-tjeneste. Bruker det sekunder, blokkerer det alt annet.
    """
    import time

    antall = 15_000
    spor = [
        (11.0 + i * 0.0004 + 0.02 * math.sin(i / 40),
         62.0 + i * 0.0006 + 0.02 * math.cos(i / 55))
        for i in range(antall)
    ]

    start = time.perf_counter()
    trase = Sportrase.fra_punkter(spor)
    bygget = time.perf_counter() - start

    stopp = [spor[i] for i in range(0, antall, antall // 15)]
    start = time.perf_counter()
    offsets, avvik = offsets_for_stopp(trase, stopp)
    snappet = time.perf_counter() - start

    assert offsets == sorted(offsets)
    assert max(avvik) < 1.0, f"stopp lagt rett på traseen snappet {max(avvik):.1f} m unna"

    print(f"ytelse             OK   {antall} punkter, {trase.lengde_m/1000:.0f} km: "
          f"bygg {bygget*1000:.0f} ms, snapping av {len(stopp)} stopp "
          f"{snappet*1000:.0f} ms")


def _test_reserveløsning() -> None:
    stopp = [(11.0, 60.0), (11.2, 60.2)]

    for beskrivelse, argument in [
        ("tomt felt", None),
        ("tom streng", {"points": ""}),
    ]:
        trase, offsets, arsak = bygg_trase(argument, stopp)
        assert trase is None and offsets is None, beskrivelse

    print("reserveløsning     OK   faller tilbake til luftlinje uten å kaste feil")


if __name__ == "__main__":
    print("sporgeometri — selvtest\n")
    _test_dekoder()
    _test_snapping()
    _test_delstrekning()
    _test_ytelse()
    _test_reserveløsning()
    _demo_f6()
