"""Regresjonstest for filteret som tar ferdige tog ut av kartet. Uten nett.

Kjøres fra prosjektroten:
    python prober/test_ferdige.py

Bakgrunnen
----------
Et kjøretøy slutter ikke å sende når det ankommer endestasjonen. Det rygger inn
på hensettingsanlegget, og GPS-en følger med. Natt til 22. august sto RE10 339
fire kilometer nord for Lillehammer, på Hovemoen, merket «6 min 12 s forsinket»
- og 8 av 15 tog i feeden var i samme tilstand. Bane NOR tegner dem ikke.

Spøkelsesfilteret på alder fanger dem ikke: de sender helt fint, tre av dem var
under ett minutt gamle. Filteret her spør om noe annet - har Journey Planner
målt at turen er over?

Det som testes er like mye hva filteret IKKE skal gjøre. Å skjule et tog er en
sterkere handling enn å tegne det litt feil, så regelen krever en måling og
ikke fravær av en.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
import sjnord


def tog(nummer: str, linje: str = "RE10", ref: str = None) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [10.42, 61.14]},
        "properties": {
            "trainNumber": nummer,
            "line": linje,
            "journeyRef": ref if ref is not None else f"VYG:ServiceJourney:{nummer}_1-R",
        },
    }


NA = datetime.now(timezone.utc)


def treff(status: str, slutt_min_siden: float | None) -> dict:
    slutt = None if slutt_min_siden is None else NA - timedelta(minutes=slutt_min_siden)
    return {"delay": 145.0, "status": status, "dato": "2026-08-21",
            "retning": None, "slutt": slutt}


def test_ferdig_tas_ut():
    """Hovedtilfellet: RE10 339 på Hovemoen, ankom for en halvtime siden."""
    feats = [tog("339")]
    jp = {"VYG:ServiceJourney:339_1-R": treff("FERDIG", 30)}
    igjen, fjernet = app._uten_ferdige(feats, jp)
    assert fjernet == 1 and igjen == [], "ferdig tog ble ikke tatt ut"
    print("ferdig tog        OK   tatt ut av kartet")


def test_nadetid():
    """Et tog som nettopp ankom skal stå litt til.

    Uten nådetiden blinker ankomster ut i det øyeblikket toget stopper, og det
    ser ut som en feil i kartet.
    """
    feats = [tog("339")]
    ref = "VYG:ServiceJourney:339_1-R"

    ferskt = app.FERDIG_NADETID / 60 / 2          # halve nådetiden
    igjen, fjernet = app._uten_ferdige(feats, {ref: treff("FERDIG", ferskt)})
    assert fjernet == 0, "toget forsvant før nådetiden var ute"

    gammelt = app.FERDIG_NADETID / 60 * 2
    igjen, fjernet = app._uten_ferdige(feats, {ref: treff("FERDIG", gammelt)})
    assert fjernet == 1, "toget ble stående etter nådetiden"
    print(f"nådetid           OK   {app.FERDIG_NADETID:.0f} s, begge sider målt")


def test_underveis_beholdes():
    """Et tog som kjører skal aldri tas ut."""
    feats = [tog("339")]
    jp = {"VYG:ServiceJourney:339_1-R": treff("underveis", None)}
    igjen, fjernet = app._uten_ferdige(feats, jp)
    assert fjernet == 0 and len(igjen) == 1
    print("underveis         OK   beholdt")


def test_skjuler_ikke_pa_fravaer():
    """Det viktigste i hele fila: fravær av data er ikke en fullført tur.

    Tre måter å ikke vite noe på, og ingen av dem er grunn til å skjule et tog:

      * ingen treff i Journey Planner i det hele tatt
      * status «ingen tider» - turen finnes, men uten stoppetider å måle mot
      * status «ikke startet» - som også er slik FEIL KJØREDATO ser ut
        (problem 3). Skjulte vi på den, ville en datofeil blitt usynlig i
        stedet for synlig, og det er den dyre retningen å bomme i.

    Lærdom 8 handler om skript som konkluderer fra fravær. Dette er vernet.
    """
    feats = [tog("339")]
    ref = "VYG:ServiceJourney:339_1-R"

    for navn, jp in [
        ("ingen JP-treff", {}),
        ("tom ordbok for turen", {ref: {}}),
        ("ingen tider", {ref: treff("ingen tider", None)}),
        ("ikke startet", {ref: treff("ikke startet", None)}),
        # FERDIG, men uten sluttidspunkt: vi vet at turen er over, men ikke
        # NÅR, og da kan vi ikke måle nådetiden. Behold.
        ("FERDIG uten slutt", {ref: treff("FERDIG", None)}),
    ]:
        igjen, fjernet = app._uten_ferdige(feats, jp)
        assert fjernet == 0, f"skjulte et tog på {navn}"
    print("fravær            OK   fem måter å ikke vite på, ingen skjuler")


def test_tog_uten_journeyref():
    """Et tog uten journeyRef kan ikke slås opp, og skal bli stående."""
    feats = [tog("339", ref="")]
    igjen, fjernet = app._uten_ferdige(feats, {"": treff("FERDIG", 300)})
    assert fjernet == 0, "tomt journeyRef ble brukt som oppslagsnøkkel"
    print("uten journeyRef   OK   beholdt")


def test_siste_stopp_males_pa_ankomst():
    """Endestasjonen har ingen avgang, så avgangsfeltene måler ingenting.

    Målt på RE10 339 natt til 22. august:

        aimedDepartureTime   01:30:00    expectedDepartureTime  01:36:12
        aimedArrivalTime     01:30:00    expectedArrivalTime    01:32:25

    Toget ankom 2 min 25 s for sent - som er nøyaktig det Vehicle Positions
    meldte - mens avgangsfeltene ga 6 min 12 s for en avgang som aldri skjer.
    Kartet viste 6 min 12 s.

    Det er ikke bare et pent tall: `velg_instans` avgjør om turen er over ved
    å se om nå ligger etter siste stopp, så det oppblåste tallet holdt turen
    «underveis» i fire minutter etter at toget sto stille.
    """
    kall = [
        {
            "aimedDepartureTime": "2026-08-21T22:57:00+02:00",
            "expectedDepartureTime": "2026-08-21T22:57:05+02:00",
            "aimedArrivalTime": None,
            "expectedArrivalTime": None,
            "realtimeState": "modified",
            "quay": {"name": "Drammen stasjon", "latitude": 59.74, "longitude": 10.20},
        },
        {
            "aimedDepartureTime": "2026-08-22T01:30:00+02:00",
            "expectedDepartureTime": "2026-08-22T01:36:12+02:00",
            "aimedArrivalTime": "2026-08-22T01:30:00+02:00",
            "expectedArrivalTime": "2026-08-22T01:32:25+02:00",
            "realtimeState": "modified",
            "quay": {"name": "Lillehammer stasjon", "latitude": 61.115, "longitude": 10.461},
        },
    ]
    tidslinje = sjnord._timeline(kall)
    assert len(tidslinje) == 2

    siste = tidslinje[-1]
    avvik = sjnord._measured_delay(siste)
    assert avvik == 145.0, f"siste stopp måles på avgang, ikke ankomst: {avvik}"

    # Mellomstopp skal fortsatt måles på avgang - der ER det en avgang.
    assert sjnord._measured_delay(tidslinje[0]) == 5.0

    # Og reserven: har Entur ingen ankomsttider, faller vi tilbake på avgang
    # i stedet for å miste stoppet.
    uten = [kall[0], dict(kall[1], aimedArrivalTime=None, expectedArrivalTime=None)]
    reserve = sjnord._timeline(uten)
    assert len(reserve) == 2, "siste stopp forsvant da ankomsttidene manglet"
    assert sjnord._measured_delay(reserve[-1]) == 372.0
    print("endestasjon       OK   ankomst brukes, avgang er reserven")


if __name__ == "__main__":
    print("ferdige tog + endestasjon — regresjonstest\n")
    test_ferdig_tas_ut()
    test_nadetid()
    test_underveis_beholdes()
    test_skjuler_ikke_pa_fravaer()
    test_tog_uten_journeyref()
    test_siste_stopp_males_pa_ankomst()
    print("\nAlt grønt.")
