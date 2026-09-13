"""Varmekart over flaskehalser - hvor på nettet togene mister tid.

Leser `historikk.db` og svarer med en GeoJSON-samling av strekninger mellom
nabostasjoner, hver farget etter hvor mye tid togene typisk taper på å kjøre
den. Ingen nettverkskall; samme tidsskala og samme cachemønster som
`analyse.py`.

Hva som måles
------------
For hver passering av en strekning A-B: **avviket ved B minus avviket ved A.**
Positivt tall betyr at toget tapte tid underveis, negativt at det tok inn.

Det er ikke det samme som «hvor er togene mest forsinket», og forskjellen er
hele poenget. Et tog som kommer inn til Oslo S tjue minutter for sent gjør
Oslo S rødt på et gjennomsnittskart - men forsinkelsen oppsto kanskje på
Kongsvingerbanen to timer tidligere. Skal kartet peke på flaskehalsen og ikke
på stedet der den blir synlig, må det måle ENDRINGEN over hver strekning.
Dette er også slik jernbanen selv måler seksjonstid.

Hvilke strekninger som finnes
-----------------------------
Strekningene bygges **av togene selv**. Hver observasjon får sin nærmeste
stasjon; sekvensen av nærmeste stasjoner langs en tur gir hvilke stasjoner
toget faktisk passerte, og dermed hvilke strekninger som finnes. Topologien
utledes altså av trafikken i stedet for å skrives ned.

Det gir to ting gratis:

  * **Ingen liste å vedlikeholde.** Åpner Bane NOR en ny strekning, dukker
    den opp av seg selv så snart tog har kjørt den.
  * **Bare strekninger med trafikk.** Et spor uten tog har ingen flaskehals.

Prisen er at strekningene ikke er en fasit over jernbanenettet, men et
avtrykk av hva som ble logget. Se `prober/sjekk_flaskehals.py`.

Hvor streken tegnes
-------------------
Det er et eget spørsmål fra hvilke strekninger som finnes, og det ble
besvart to ganger.

**Først med medianlinja** (`_median_linje` under): observasjonene projiseres
på korden mellom stasjonene, sorteres i bøtter, og hver bøtte bidrar med
medianen sin. Ingen ekstra datakilde, og streken bøyer seg der togene bøyde
seg.

Den holdt ikke i Oslo, Akershus og Østfold. Medianen av to parallelle baner
er ingen av dem, bøttene tømmes der traseen svinger bort fra korden, og
median av lengdegrad og median av breddegrad hver for seg er ikke et punkt på
sporet. Nationaltheatret-Oslo S bøyde seg 966 m vekk fra nærmeste skinne på
en strekning på 1,3 km, og Bryn-Lillestrøm ble tegnet med fem punkter over
13,6 km.

**Nå med rutesøk i et ekte spornett** (`jernbanenett.py`, bygget av
`lagjernbanenett.py` fra OpenStreetMap). Hvert punkt i streken er da per
definisjon et punkt på et spor. Observasjonene brukes fortsatt, men til noe
annet enn å være geometrien: de avgjør HVILKEN trasé søket får lov til å
velge, slik at Follobanen ikke blir til Østfoldbanen og Askerbanen ikke blir
til Drammenbanen.

Medianlinja er ikke fjernet. Den er reserven, og `properties.geometri` sier
hvilken av de to hvert enkelt trekk kom fra - av samme grunn som `kilde` står
i hovedbaner.geojson og `positionMethod` på hvert tog: du skal kunne se på
dataene hva du ser på.
"""

import json
import logging
import os
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import jernbanenett
from zoneinfo import ZoneInfo

from historikk import kobling, utelatte_dogn
from sporgeometri import avstand_m, avstand_til_strekning

log = logging.getLogger(__name__)

DB_PATH = os.getenv("HISTORIKK_DB", "historikk.db")
STASJONER = os.path.join("static", "stasjoner.geojson")

# ---------------------------------------------------------------------------
# Grensene, og hva hver enkelt beskytter mot
# ---------------------------------------------------------------------------
# Uten disse ble de fire verste «flaskehalsene» i første kjøring
# Marnardal-Stavanger, Marnardal-Oslo S, Gulskogen-Nyland og Kløfta-Strømmen -
# stasjonspar som ligger hundrevis av kilometer fra hverandre og ikke er
# naboer i det hele tatt. Alle fire kom av hull i loggingen: nettleseren var
# lukket mens toget kjørte, og de to observasjonene som ble igjen så ut som
# en sammenhengende kjøring.

# Hvor nær en stasjon togets BANE må ha gått for at vi sier at det passerte
# den. Målt mot banen og ikke mot nærmeste observasjon - se `_bane_avstand`.
#
# Femhundre meter er strengt, og det er meningen. Grensen finnes for å skille
# PARALLELLE BANER fra hverandre. Gardermobanen går to-tre kilometer fra
# Hovedbanen forbi Jessheim og Nordby, og Askerbanen like ved Drammenbanen
# gjennom Asker og Sandvika. Med en romslig grense ble et Flytog på
# Gardermobanen regnet som en passering av Hovedbanens stasjoner, og tapte
# minutter havnet på feil bane.
#
# Slik det synes: strekningens tegnede geometri bøyde seg lenger vekk fra
# luftlinja enn strekningen selv var lang. Ved 2500 m gjaldt det tolv
# strekninger - Jessheim-Nordby bøyde 2,3 km på en strekning på 1,5 km. Ved
# 500 m er tallet null.
# Driftsdagen er den lokale datoen, samme definisjon som analyse.py og
# vedlikehold.py bruker. Se `_hent_rader`.
OSLO = ZoneInfo("Europe/Oslo")

MAKS_STASJONSAVSTAND_M = 500

# Lengste hull mellom to observasjoner inne i én passering. Målt på databasen
# 20. august ligger medianen på 29 sekunder og 90-persentilen på 92; hull over
# seks minutter er logging som stanset, ikke et tog som kjørte sakte.
MAKS_HULL_S = 360

# En passering må ta mer enn et halvt minutt (ellers er de to observasjonene
# praktisk talt samtidige) og under halvannen time (ellers er det ikke én
# strekning, men en tur med hull i).
MIN_TID_S = 30
MAKS_TID_S = 5_400

# To nabostasjoner ligger ikke 80 km fra hverandre - ikke engang på
# Nordlandsbanen, der de lengste hoppene er rundt 50. Over dette har vi hoppet
# over stasjoner fordi de ikke ble logget.
MAKS_STREKNING_M = 80_000

# Hvor mange passeringer en strekning må ha før den vises. Én passering er en
# anekdote: et enkelt tog som sto i tjue minutter gjør ikke strekningen til en
# flaskehals. Dette er den viktigste grensen for at kartet skal være ærlig.
MIN_PASSERINGER = 5

# Antall punkter i den tegnede streken mellom to stasjoner.
GEOMETRIPUNKTER = 12


# ---------------------------------------------------------------------------
# Stasjonsoppslag
# ---------------------------------------------------------------------------
def _last_stasjoner(sti: str = STASJONER) -> list[tuple[str, float, float]]:
    with open(sti, encoding="utf-8") as fil:
        data = json.load(fil)
    ut = []
    for f in data.get("features", []):
        lon, lat = f["geometry"]["coordinates"]
        navn = f["properties"].get("navn") or ""
        if navn:
            # «Lillestrøm stasjon» -> «Lillestrøm». Samme klipp som avvik.py
            # gjør, og av samme grunn: ordet sier ingenting i et togkart.
            ut.append((navn.removesuffix(" stasjon"), lon, lat))
    return ut


class Stasjonsnett:
    """Nærmeste stasjon til et punkt, med et enkelt rutenett som indeks.

    27 000 observasjoner mot 335 stasjoner er ni millioner avstandsregninger
    hvis man leter gjennom alle hver gang. Rutenettet gjør at hvert oppslag
    ser på en håndfull naboceller i stedet - samme idé som `bisect` i
    Sportrase, én dimensjon til.

    Cellen er en kvart grad, altså rundt 28 km i nord-sør. Vi ser alltid på
    cellen og alle naboene rundt, så en stasjon opptil ~28 km unna finnes
    alltid. Er nærmeste stasjon lenger unna enn det, er toget uansett langt
    utenfor MAKS_STASJONSAVSTAND_M.
    """

    CELLE = 0.25

    def __init__(self, stasjoner: list[tuple[str, float, float]]):
        self.stasjoner = stasjoner
        self.rutenett: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, (_, lon, lat) in enumerate(stasjoner):
            self.rutenett[self._celle(lon, lat)].append(i)

    def _celle(self, lon: float, lat: float) -> tuple[int, int]:
        return (int(lon / self.CELLE), int(lat / self.CELLE))

    def naermeste(self, lon: float, lat: float) -> tuple[str | None, float]:
        cx, cy = self._celle(lon, lat)
        beste, best_avstand = None, float("inf")
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in self.rutenett.get((cx + dx, cy + dy), ()):
                    navn, slon, slat = self.stasjoner[i]
                    d = avstand_m((lon, lat), (slon, slat))
                    if d < best_avstand:
                        beste, best_avstand = navn, d
        return beste, best_avstand

    def punkt(self, navn: str) -> tuple[float, float] | None:
        for n, lon, lat in self.stasjoner:
            if n == navn:
                return (lon, lat)
        return None


# ---------------------------------------------------------------------------
# Fra observasjoner til passeringer
# ---------------------------------------------------------------------------
class Passering:
    """Toget på sitt nærmeste av én stasjon."""

    __slots__ = ("stasjon", "tid", "delay", "indeks", "avstand")

    def __init__(self, stasjon, tid, delay, indeks, avstand):
        self.stasjon = stasjon
        self.tid = tid
        self.delay = delay
        self.indeks = indeks
        self.avstand = avstand


def _bane_avstand(
    merket: list[tuple], fra: int, til: int, stasjon: tuple[float, float]
) -> float:
    """Hvor nær stasjonen toget FAKTISK kjørte, ikke hvor nær nærmeste måling lå.

    Dette skillet er hele forskjellen mellom en brukbar og en ubrukelig grense.
    Måler man avstanden til nærmeste observasjon, straffer man fart: et tog i
    130 km/t med 29 sekunder mellom målingene flytter seg over en kilometer
    mellom hver, så nærmeste måling kan ligge 500 m fra en stasjon toget kjørte
    rett gjennom. Grensen ville da luket bort ekspresstogene og beholdt
    lokaltogene - en skjevhet rett inn i tallene, siden det er ekspresstogene
    som taper mest tid i flaskehalsene.

    Derfor måles avstanden fra stasjonen til LINJESTYKKENE mellom
    observasjonene. Da spiller det ingen rolle hvor tett toget ble målt.
    Ett linjestykke på hver side av løpet tas med, slik at et løp med bare én
    observasjon også får en bane å måle mot.
    """
    beste = float("inf")
    for k in range(max(0, fra - 1), min(len(merket) - 1, til + 1)):
        a = (merket[k][4], merket[k][5])
        b = (merket[k + 1][4], merket[k + 1][5])
        beste = min(beste, avstand_til_strekning(stasjon, a, b))
    if beste == float("inf"):
        # Ett eneste punkt i hele rekka - da er punktavstanden alt vi har.
        return merket[fra][1]
    return beste


def _passeringer(merket: list[tuple], nett: "Stasjonsnett") -> list[Passering]:
    """Komprimer en merket observasjonsrekke til én rad per stasjon.

    `merket` er [(stasjonsnavn, avstand, tid, delay, lon, lat), ...] i
    tidsrekkefølge. Et tog ligger nærmest samme stasjon over mange
    observasjoner på rad; av hvert slikt løp beholder vi den ENE der toget var
    aller nærmest. Det er så nær «toget var ved stasjonen» vi kommer uten
    stoppetider, og det er det punktet avviket skal leses av på.

    Avstanden som lagres er banens avstand til stasjonen, ikke punktets - se
    `_bane_avstand`.
    """
    ut: list[Passering] = []
    i = 0
    while i < len(merket):
        j = i
        while j + 1 < len(merket) and merket[j + 1][0] == merket[i][0]:
            j += 1
        beste = min(range(i, j + 1), key=lambda k: merket[k][1])
        navn, _, tid, delay, _, _ = merket[beste]
        punkt = nett.punkt(navn)
        avstand = (
            _bane_avstand(merket, i, j, punkt) if punkt else merket[beste][1]
        )
        ut.append(Passering(navn, tid, delay, beste, avstand))
        i = j + 1
    return ut


def _gyldig(a: Passering, b: Passering, merket: list[tuple], nett: Stasjonsnett) -> bool:
    """Er dette én sammenhengende kjøring mellom to nabostasjoner?"""
    if a.stasjon == b.stasjon:
        return False
    if a.avstand > MAKS_STASJONSAVSTAND_M or b.avstand > MAKS_STASJONSAVSTAND_M:
        return False

    sekunder = (b.tid - a.tid).total_seconds()
    if not (MIN_TID_S <= sekunder <= MAKS_TID_S):
        return False

    pa, pb = nett.punkt(a.stasjon), nett.punkt(b.stasjon)
    if pa is None or pb is None or avstand_m(pa, pb) > MAKS_STREKNING_M:
        return False

    # Hull i loggingen. Uten denne ser to observasjoner med en time imellom ut
    # som en kjøring, og strekningen blir et stasjonspar som ikke er naboer.
    for k in range(a.indeks, b.indeks):
        if (merket[k + 1][2] - merket[k][2]).total_seconds() > MAKS_HULL_S:
            return False
    return True


# ---------------------------------------------------------------------------
# Geometri
# ---------------------------------------------------------------------------
def _lengde_km(linje: list[list[float]]) -> float:
    return sum(
        avstand_m((a[0], a[1]), (b[0], b[1])) for a, b in zip(linje, linje[1:])
    ) / 1000


def _geometri(
    punkter: list[tuple[float, float]],
    fra: tuple[float, float],
    til: tuple[float, float],
) -> tuple[list[list[float]], str]:
    """Streken mellom to stasjoner, og hvor den kom fra.

    Rutesøket i spornettet først. Svarer det None - fordi fila ikke er bygget,
    fordi stasjonen ikke fant spor å feste seg til, eller fordi ruten ikke
    besto troverdighetssjekkene - faller vi tilbake på medianlinja.

    Reserven er der fordi et flaskehalskart uten geometri ikke er noe kart.
    Merkelappen som følger med er der fordi en reserve som ikke kan skilles
    fra hovedveien blir usynlig når den slår inn oftere enn den skal.
    """
    spor = jernbanenett.rute(fra, til, punkter)
    if spor:
        return [[lon, lat] for lon, lat in spor], "spor"
    return _median_linje(punkter, fra, til), "median"


def _median_linje(
    punkter: list[tuple[float, float]], fra: tuple[float, float], til: tuple[float, float]
) -> list[list[float]]:
    """Reserven: bygg én strek gjennom skyen av observerte posisjoner.

    Hvert punkt projiseres på korden fra A til B og får en brøk mellom 0 og 1.
    Brøkene sorteres i bøtter, og hver bøtte bidrar med medianen av sine
    punkter. Resultatet følger sporet der sporet svinger bort fra korden -
    som er hele grunnen til at vi ikke bare tegner en rett strek.

    Medianen og ikke gjennomsnittet: en enkelt observasjon med dårlig GPS
    trekker et gjennomsnitt ut i terrenget, men flytter ikke en median.

    Retningen spiller ingen rolle. Projeksjonen bryr seg ikke om toget kjørte
    A-B eller B-A, så begge retninger bygger den samme streken.

    Svakhetene - parallelle baner som blandes, bøtter som tømmes i svinger -
    er beskrevet i modul-docstringen og er grunnen til at dette nå er
    reserven og ikke hovedveien.
    """
    dx = til[0] - fra[0]
    dy = til[1] - fra[1]
    lengde2 = dx * dx + dy * dy
    if lengde2 == 0:
        return [list(fra), list(til)]

    boetter: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for lon, lat in punkter:
        t = ((lon - fra[0]) * dx + (lat - fra[1]) * dy) / lengde2
        if 0.0 < t < 1.0:
            boetter[min(GEOMETRIPUNKTER - 1, int(t * GEOMETRIPUNKTER))].append((lon, lat))

    linje = [list(fra)]
    for b in range(GEOMETRIPUNKTER):
        i_boetta = boetter.get(b)
        if not i_boetta:
            continue
        linje.append([
            statistics.median(p[0] for p in i_boetta),
            statistics.median(p[1] for p in i_boetta),
        ])
    linje.append(list(til))
    return linje


# ---------------------------------------------------------------------------
# Hovedjobben
# ---------------------------------------------------------------------------
def _hent_rader(con: sqlite3.Connection, dager: int) -> list[sqlite3.Row]:
    """Samme to filtre som analyse._hent_rader, og av samme grunn: uten avvik
    er raden en posisjon og ikke en måling, og et spøkelsestog måler hvor
    lenge siden dataene forsvant."""
    fra = (datetime.now(timezone.utc) - timedelta(days=dager)).isoformat()
    rader = con.execute(
        """SELECT tog_id, tidspunkt, lat, lon, delay, linje, operator
           FROM observasjoner
           WHERE tidspunkt >= ? AND delay IS NOT NULL AND stale = 0
             AND lat IS NOT NULL AND lon IS NOT NULL
           ORDER BY tog_id, tidspunkt""",
        (fra,),
    ).fetchall()

    # Døgn som er erklært utroverdige teller ikke her heller. Varmekartet er
    # særlig utsatt for nettopp den forurensningen utelatelsen handler om:
    # `_passeringer` leser avviket der toget var NÆRMEST stasjonen, og et tog
    # som parkerer hundre meter fra plattformen etter endt tur er nærmere enn
    # det var da det passerte. Da leses et frosset avvik av som om det var
    # ankomsten, og strekningen inn mot endestasjonen får skylda.
    utelatte = utelatte_dogn(con)
    if not utelatte:
        return rader

    beholdt = []
    for rad in rader:
        try:
            lokal = datetime.fromisoformat(rad["tidspunkt"]).astimezone(OSLO)
        except ValueError:
            continue
        if lokal.date().isoformat() not in utelatte:
            beholdt.append(rad)
    return beholdt


def flaskehalser(dager: int = 7, min_passeringer: int = MIN_PASSERINGER) -> dict:
    """Strekninger der togene mister tid, som GeoJSON.

    Kan svare med en tom samling, og det er ikke en feil: en fersk database har
    ingen passeringer å regne på. Frontend skiller mellom «ingen data» og
    «kunne ikke hente».
    """
    nett = Stasjonsnett(_last_stasjoner())

    # Skrivebeskyttet, og med busy_timeout. Se historikk.kobling().
    with kobling(skrivbar=False) as con:
        con.row_factory = sqlite3.Row
        rader = _hent_rader(con, dager)

    per_tog: dict[str, list] = defaultdict(list)
    for r in rader:
        per_tog[r["tog_id"]].append(r)

    # (stasjon_a, stasjon_b) sortert -> samlet materiale
    strekninger: dict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "endringer": [],
            "retninger": defaultdict(list),
            "punkter": [],
            "operatorer": set(),
            "linjer": set(),
        }
    )

    forkastet = 0
    for obs in per_tog.values():
        merket = []
        for r in obs:
            navn, avstand = nett.naermeste(r["lon"], r["lat"])
            if navn is None:
                continue
            merket.append((
                navn, avstand, datetime.fromisoformat(r["tidspunkt"]),
                r["delay"], r["lon"], r["lat"],
            ))
        if len(merket) < 2:
            continue

        passeringer = _passeringer(merket, nett)
        for a, b in zip(passeringer, passeringer[1:]):
            if not _gyldig(a, b, merket, nett):
                forkastet += 1
                continue

            nokkel = tuple(sorted((a.stasjon, b.stasjon)))
            samlet = strekninger[nokkel]
            endring = b.delay - a.delay
            samlet["endringer"].append(endring)
            samlet["retninger"][f"{a.stasjon}→{b.stasjon}"].append(endring)
            samlet["punkter"].extend(
                (m[4], m[5]) for m in merket[a.indeks:b.indeks + 1]
            )
            rad = obs[0]
            if rad["operator"]:
                samlet["operatorer"].add(rad["operator"])
            if rad["linje"]:
                samlet["linjer"].add(rad["linje"])

    features = []
    paa_spor = 0
    for (a, b), samlet in strekninger.items():
        if len(samlet["endringer"]) < min_passeringer:
            continue
        pa, pb = nett.punkt(a), nett.punkt(b)
        if pa is None or pb is None:
            continue

        linje, kilde = _geometri(samlet["punkter"], pa, pb)
        if kilde == "spor":
            paa_spor += 1

        endringer = samlet["endringer"]
        retninger = [
            {
                "retning": navn,
                "passeringer": len(verdier),
                "sekunder": round(statistics.median(verdier)),
            }
            for navn, verdier in sorted(samlet["retninger"].items())
        ]

        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": linje},
            "properties": {
                "fra": a,
                "til": b,
                # «spor» = rutet gjennom jernbanenettet, «median» = reserven.
                "geometri": kilde,
                # Medianen, ikke gjennomsnittet: ett tog som sto i tjue
                # minutter skal ikke gjøre strekningen rød for alle andre.
                "sekunder": round(statistics.median(endringer)),
                "snitt": round(statistics.mean(endringer)),
                "verste": round(max(endringer)),
                "passeringer": len(endringer),
                # Målt langs streken, ikke i luftlinje. Er streken rutet
                # gjennom spornettet, er dette kjørelengden - og det er den
                # som betyr noe når man skal se om ett minutts tap er mye.
                # For medianlinjer er det fortsatt omtrent luftlinja.
                "km": round(_lengde_km(linje), 1),
                "retninger": retninger,
                "operatorer": sorted(samlet["operatorer"]),
                "linjer": sorted(samlet["linjer"]),
            },
        })

    # Verst først. Frontend tegner i denne rekkefølgen, så de røde havner
    # øverst i lagstabelen der strekninger overlapper.
    features.sort(key=lambda f: f["properties"]["sekunder"])

    log.info(
        "Flaskehalser: %d strekninger fra %d observasjoner (%d passeringer "
        "forkastet, %d strekninger under %d passeringer, %d av %d rutet på spor)",
        len(features), len(rader), forkastet,
        len(strekninger) - len(features), min_passeringer,
        paa_spor, len(features),
    )

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "dager": dager,
            "minPasseringer": min_passeringer,
            "strekninger": len(features),
            "strekningerFunnet": len(strekninger),
            "observasjoner": len(rader),
            "forkastet": forkastet,
            # Hvor mange strekker som ligger på ekte spor. Faller dette tallet
            # brått, er det spornettet eller rutingen som har fått et problem,
            # ikke togene - og da tegner kartet fortsatt, bare dårligere.
            "paaSpor": paa_spor,
        },
    }


if __name__ == "__main__":
    # Kjør:  python flaskehals.py
    # Viser de samme tallene kartet ville tegnet, uten server og nettleser.
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    data = flaskehalser(dager=int(sys.argv[1]) if len(sys.argv) > 1 else 7)
    m = data["meta"]
    print(f"\n{m['strekninger']} strekninger med minst {m['minPasseringer']} "
          f"passeringer, av {m['strekningerFunnet']} funnet\n")

    if not data["features"]:
        print("Ingen strekninger å vise. Har historikk.db data ennå?")
        raise SystemExit

    verst = sorted(data["features"], key=lambda f: -f["properties"]["sekunder"])
    print(f"{'strekning':<44}{'passer.':>8}{'median':>9}{'verste':>9}{'km':>7}")
    print("-" * 78)
    for f in verst[:15]:
        p = f["properties"]
        print(f"{p['fra'][:20] + ' – ' + p['til'][:20]:<44}{p['passeringer']:>8}"
              f"{p['sekunder'] / 60:>8.1f}m{p['verste'] / 60:>8.1f}m{p['km']:>7.1f}")

    print("\nDer togene tar inn tid igjen:")
    for f in verst[-5:]:
        p = f["properties"]
        print(f"  {p['fra']} – {p['til']}: {p['sekunder'] / 60:+.1f} min "
              f"({p['passeringer']} passeringer)")
