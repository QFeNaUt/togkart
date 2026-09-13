"""Regresjonstest for ringdiagrammets telling. Krever ikke nett.

Kjøres fra prosjektroten:
    python prober/test_telling.py

Bakgrunnen
----------
Natt til 22. august sto det «10 tog i trafikk» i overskriften og «100 % i rute»
i ringen under, mens F6 405 lå på kartet ni minutter og femten sekunder
forsinket. Begge tallene var riktige hver for seg og fortalte to forskjellige
historier:

    #count  ->  meta.count   = 10   alt som tegnes, målt og beregnet
    ringen  ->  meta.counts  =  6   bare de MÅLTE togene

Teller og nevner kom fra hver sin populasjon. Ringen kunne aldri gå opp, og
fire tog manglet i oppdelingen uten at noe sa fra.

Begrunnelsen for å holde de beregnede utenfor gjaldt POSISJONEN - den er
interpolert, ikke målt. Men ringen handler om FORSINKELSEN, og den kommer fra
Journey Planner for beregnede og målte tog likt, gjennom samme kodevei. F6 405
hadde `delaySource: journey-planner` akkurat som F5 726, som ble talt.

Følgen var ikke bare et tall som ikke gikk opp: fjerntogene er de som oftest er
forsinket, og de er nettopp de uten GPS. Tallet ble systematisk for pent.

Invarianten som testes
----------------------
    meta.count == sum(counts.values()) + stale
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app


def tog(band: str, *, computed: bool = False, stale: bool = False) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [10.0, 60.0]},
        "properties": {"band": band, "computed": computed, "stale": stale},
    }


def test_invarianten():
    """Alt som tegnes skal enten telle i et bånd eller være et spøkelse."""
    features = [
        tog("i_rute"),
        tog("i_rute"),
        tog("forsinket"),
        tog("i_rute", computed=True),
        tog("mye", computed=True),
        tog("ukjent", stale=True),
    ]
    counts, stale, beregnet = app._tell_band(features)
    assert sum(counts.values()) + stale == len(features), (
        f"{sum(counts.values())} + {stale} != {len(features)}"
    )
    print(f"invariant         OK   {len(features)} tegnet = "
          f"{sum(counts.values())} talt + {stale} spøkelse")


def test_beregnede_teller():
    """Selve feilen: et beregnet, forsinket tog skal ligge i «forsinket».

    Uten dette står ringen på 100 % i rute mens et SJ-tog er ni minutter
    forsinket på kartet.
    """
    counts, _stale, beregnet = app._tell_band([
        tog("i_rute"),
        tog("forsinket", computed=True),
    ])
    assert counts["forsinket"] == 1, "beregnet tog havnet ikke i båndet sitt"
    assert counts["i_rute"] == 1
    assert beregnet == 1, "andelen beregnede ble ikke talt"
    print("beregnet teller   OK   forsinket beregnet tog havner i «forsinket»")


def test_spokelser_teller_ikke():
    """Den ene utelatelsen som er forsvarlig, og den gjelder tallet.

    Et spøkelsestog har et avvik som vokser mot en rutetid toget aldri
    innfrir. Det er ubrukelig som måling - i motsetning til et beregnet togs
    avvik, som er hentet fra Journey Planner som alle andres.
    """
    counts, stale, _b = app._tell_band([
        tog("mye", stale=True),
        tog("i_rute"),
    ])
    assert stale == 1
    assert counts["mye"] == 0, "spøkelsestoget ble talt"
    assert sum(counts.values()) == 1
    print("spøkelser         OK   tegnes, men teller ikke")


def test_alle_band_finnes():
    """Frontend leser hvert bånd ved navn og viser 0 når det er tomt.

    Mangler en nøkkel, står tallet som «–» i panelet uten at noe feiler.
    """
    counts, _s, _b = app._tell_band([])
    assert set(counts) == set(app.BAND_ORDER), counts
    assert all(v == 0 for v in counts.values())
    print(f"alle bånd         OK   {', '.join(app.BAND_ORDER)}")


def test_tomt_snapshot():
    """Ingen tog er ikke en feil - natta finnes."""
    counts, stale, beregnet = app._tell_band([])
    assert sum(counts.values()) == 0 and stale == 0 and beregnet == 0
    print("tomt snapshot     OK   null tog, null feil")


if __name__ == "__main__":
    print("ringdiagrammets telling — regresjonstest\n")
    test_invarianten()
    test_beregnede_teller()
    test_spokelser_teller_ikke()
    test_alle_band_finnes()
    test_tomt_snapshot()
    print("\nAlt grønt.")
