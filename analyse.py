"""Analyse - leser historikk.db og svarer på to spørsmål.

  1. Hvem kjører mest punktlig?   -> `operatorsammenligning()`
  2. Hvor gjør rushtiden vondt?   -> `rushtidsprofil()`

Ingenting her snakker med nettet. All input er rader `historikk.py` allerede
har skrevet, og modulen kan derfor kjøres frittstående:

    python analyse.py

`app.py` eksponerer de samme to funksjonene på /api/statistikk/*, og
`static/app.js` tegner dem i panelet øverst til høyre.

---------------------------------------------------------------------------
Hva tallene her faktisk måler
---------------------------------------------------------------------------
Det er tre valg i denne fila som avgjør om tallene betyr noe, og alle tre er
verdt å kjenne før du siterer dem:

**Én terskel for alle.** Bransjen bruker to: 3:59 for lokaltog, 5:59 for
fjerntog. Den delingen er riktig når man måler et selskap mot sin egen
kontrakt, og feil når man rangerer selskaper mot hverandre - SJ kjører nesten
bare fjerntog og ville fått den milde terskelen på hele porteføljen sin, mens
Vy måles mot den strenge på lokaltogene. Rangeringen bruker derfor ÉN
terskel, den samme fire-minutters grensen som fargene på kartet, og skriver
den i grensesnittet. Se `TERSKEL_SEKUNDER`.

**En togtur, ikke en observasjon.** Databasen skriver bare når toget har
flyttet seg eller avviket har endret seg (se `historikk.py`), så antall rader
per tog sier mest om hvor langt toget kjørte. Teller vi rader, veier
Bergensbanen tyngre enn et lokaltog på Østfoldbanen fordi den er lengre.
Derfor slås alle observasjoner av samme tog samme dag sammen til én togtur
med ett tall: medianen av avvikene underveis.

**Underveis, ikke ved endestasjonen.** Offisiell punktlighet måles ved
ankomst siste stasjon. Vi måler det typiske avviket gjennom hele turen. Det
er en annen størrelse, og den er strengere mot tog som henter inn tid på
slutten. Kall den «typisk avvik underveis» - ikke «punktlighet» - hvis du
sammenligner med Bane NOR sine tall.
"""

import logging
import os
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from historikk import DB_PATH, kobling, utelatte_dogn

log = logging.getLogger("togkart")

# Norsk lokaltid. Databasen lagrer UTC - riktig for lagring, ubrukelig for et
# spørsmål som «hva skjer i morgenrushet». Rushtid er et lokalt klokkeslett,
# og sommertid flytter det to ganger i året. `zoneinfo` kan reglene; det kan
# ikke en hardkodet +2.
OSLO = ZoneInfo("Europe/Oslo")

# Grensen for «i rute». Samme 240 sekunder som `delay_band()` i entur.py
# bruker til å farge prikkene grønne, slik at panelet og kartet aldri kan
# vise to forskjellige svar på samme spørsmål. Endrer du den ene, endre den
# andre - og teksten i `index.html` som skriver grensen ut.
TERSKEL_SEKUNDER = 240

# Rushtidsvinduene, i lokal tid. Halvåpne: 6 <= time < 10.
MORGENRUSH = (6, 10)
ETTERMIDDAGSRUSH = (14, 18)

# Rush er et hverdagsfenomen. En søndag med fire tog på linja hører ikke
# hjemme i snittet for morgenrushet. datetime.weekday(): 0 = mandag.
HVERDAGER = {0, 1, 2, 3, 4}

# Under så få togturer er medianen støy, ikke et signal. En enkelt sen tur
# ville ellers kunne toppe flaskehalslista alene.
MIN_TURER = 3

# Kodeområdet fra Entur oversatt til navnet folk kjenner. Står en kode ikke
# her, vises koden som den er - bedre enn å skjule en operatør vi ikke hadde
# forutsett.
OPERATORNAVN = {
    "VYG": "Vy",
    "FLT": "Flytoget",
    "GOA": "Go-Ahead Nordic",
    "SJN": "SJ Norge",
    "VYT": "Vy Tåg",
    "NSB": "NSB",
}


# ---------------------------------------------------------------------------
# Lesing
# ---------------------------------------------------------------------------

class Togtur:
    """Alle observasjoner av ett tog én driftsdag, slått sammen til ett punkt.

    `avvik` er medianen av observasjonene, ikke gjennomsnittet: ett enkelt
    utslag - typisk et tog som står og venter på kryssing - skal ikke dra
    hele turen med seg.
    """

    __slots__ = ("dato", "tog_id", "linje", "linjenavn", "operator",
                 "avvik", "observasjoner", "beregnet", "timer")

    def __init__(self, dato, tog_id, linje, linjenavn, operator,
                 avvik, observasjoner, beregnet, timer):
        self.dato = dato
        self.tog_id = tog_id
        self.linje = linje
        self.linjenavn = linjenavn
        self.operator = operator
        self.avvik = avvik
        self.observasjoner = observasjoner
        self.beregnet = beregnet
        self.timer = timer

    @property
    def i_rute(self) -> bool:
        return self.avvik < TERSKEL_SEKUNDER


def _hent_rader(con: sqlite3.Connection, dager: int) -> list[sqlite3.Row]:
    """Observasjoner fra de siste `dager` døgn som kan brukes til punktlighet.

    To filtre gjøres her og ikke lenger ute, fordi de handler om hva en rad
    ER og ikke om hva vi spør om:

    - `delay IS NOT NULL`: uten et avvik er raden en posisjon, ikke en måling.
    - `stale = 0`: et tog som sluttet å sende har et `delay` som fortsetter å
      vokse mot en rutetid det aldri innfrir (se STALE_AFTER_SECONDS i
      entur.py). Tas de med, måler vi hvor lenge siden dataene forsvant.
    """
    fra = (datetime.now(timezone.utc) - timedelta(days=dager)).isoformat()
    return hent_rader_mellom(con, fra, None)


def hent_rader_mellom(
    con: sqlite3.Connection, fra_iso: str, til_iso: str | None
) -> list[sqlite3.Row]:
    """Samme filter som `_hent_rader`, men med begge endene oppgitt.

    Skilt ut fordi `vedlikehold.py` trenger ett DØGN og ikke et vindu bakover
    fra nå. Delingen er ikke en ryddesak: hvis rullupen hadde skrevet sin egen
    WHERE-setning, ville arkivet og sanntidsanalysen kunne svare forskjellig
    på hva en gyldig måling er - og da er tallet fra i fjor ikke lenger
    sammenlignbart med tallet fra i dag. Filteret står ett sted.

    `til_iso = None` betyr «til nå».
    """
    if til_iso is None:
        rader = con.execute(
            """SELECT tidspunkt, tog_id, linje, linjenavn, operator, delay,
                      computed, delay_source
               FROM observasjoner
               WHERE tidspunkt >= ? AND delay IS NOT NULL AND stale = 0
               ORDER BY tidspunkt""",
            (fra_iso,),
        ).fetchall()
    else:
        rader = con.execute(
            """SELECT tidspunkt, tog_id, linje, linjenavn, operator, delay,
                      computed, delay_source
               FROM observasjoner
               WHERE tidspunkt >= ? AND tidspunkt < ?
                 AND delay IS NOT NULL AND stale = 0
               ORDER BY tidspunkt""",
            (fra_iso, til_iso),
        ).fetchall()

    return _uten_utelatte(con, rader)


def _uten_utelatte(con: sqlite3.Connection, rader: list) -> list:
    """Fjern rader fra døgn som er erklært utroverdige.

    Filtreres i Python og ikke i SQL med vilje: mengden er nesten alltid tom,
    og en NOT IN med en tom liste limt inn i spørringen er en felle. Kostnaden
    er ett oppslag per rad på et sett.

    Datoen som sammenliknes er den LOKALE, samme som `_samle_turer` bruker for
    driftsdagen. Å klippe de ti første tegnene ut av ISO-strengen ville vært
    UTC og truffet feil rader rundt midnatt.
    """
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


def _samle_turer(rader) -> list[Togtur]:
    """Slå observasjoner sammen til én Togtur per (driftsdag, tog).

    Driftsdagen er den LOKALE datoen. Et nattog fra Trondheim krysser midnatt
    og blir da to turer - det er en kjent forenkling, og den er liten: SJ har
    et titalls nattavganger mot tusenvis av dagturer. Skal den fikses, er det
    her, ikke i kallerne.

    Tidsstemplene tolkes med `fromisoformat`, som leser tidssonen i teksten.
    Å klippe tegn ut av ISO-strengen ville sett ut som formatering og hoppet
    over sonen - samme felle som `clock()` i app.js advarer mot.
    """
    grupper: dict[tuple[str, str], list] = defaultdict(list)

    for rad in rader:
        try:
            lokal = datetime.fromisoformat(rad["tidspunkt"]).astimezone(OSLO)
        except ValueError:
            continue
        grupper[(lokal.date().isoformat(), rad["tog_id"])].append((lokal, rad))

    turer = []
    for (dato, tog_id), poster in grupper.items():
        avvik = [float(rad["delay"]) for _, rad in poster]
        # Linjenavn og operatør er tomme på rader skrevet før kolonnene fantes.
        # Vi tar den første raden i turen som har dem utfylt, i stedet for den
        # første raden overhodet.
        navn = next((rad["linjenavn"] for _, rad in poster if rad["linjenavn"]), "")
        operator = next((rad["operator"] for _, rad in poster if rad["operator"]), "")
        turer.append(Togtur(
            dato=dato,
            tog_id=tog_id,
            linje=poster[0][1]["linje"] or "",
            linjenavn=navn,
            operator=operator,
            avvik=statistics.median(avvik),
            observasjoner=len(poster),
            beregnet=any(rad["computed"] for _, rad in poster),
            timer={tid.hour for tid, _ in poster},
        ))

    return turer


def _median_sek(verdier: list[float]) -> int:
    return int(round(statistics.median(verdier)))


def _andel(treff: int, av: int) -> float:
    return round(treff / av, 4) if av else 0.0


def _vindu(turer: list[Togtur], dager: int) -> dict:
    datoer = sorted({t.dato for t in turer})
    return {
        "dager": dager,
        "fraDato": datoer[0] if datoer else None,
        "tilDato": datoer[-1] if datoer else None,
        "dognMedData": len(datoer),
    }


# ---------------------------------------------------------------------------
# 1. Operatørsammenligning
# ---------------------------------------------------------------------------

def operatorsammenligning(dager: int = 7) -> dict:
    """Rangering av operatørene på andel togturer i rute.

    Rangeres på andel, ikke på median: medianen er det typiske toget, andelen
    er hvor ofte løftet holdes. Det siste er spørsmålet en reisende stiller.

    `beregnet` og `kilder` følger med per operatør fordi de forteller hvordan
    tallet ble til. SJ har ingen GPS i Entur, så posisjonene deres regnes ut -
    det påvirker ikke avviket (som kommer fra Journey Planner for alle fire),
    men det er forskjellen veikartet ber om å få frem i grafen og ikke i en
    fotnote. Vi utleder den av dataene i stedet for å skrive den inn: går SJ
    en dag over til å publisere posisjoner, forsvinner merkingen av seg selv.
    """
    # Skrivebeskyttet: en analyse skal verken kunne endre noe eller ta en
    # skrivelås. `kobling()` setter også busy_timeout, som er det som gjør
    # at en lesing venter på skrivetråden i stedet for å feile med
    # "database is locked". Se historikk.kobling().
    with kobling(skrivbar=False) as con:
        con.row_factory = sqlite3.Row
        rader = _hent_rader(con, dager)

    turer = _samle_turer(rader)

    per_operator: dict[str, list[Togtur]] = defaultdict(list)
    uten_operator = 0
    for tur in turer:
        if not tur.operator:
            uten_operator += 1
            continue
        per_operator[tur.operator].append(tur)

    kilder_per_operator: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for rad in rader:
        if rad["operator"]:
            kilder_per_operator[rad["operator"]][rad["delay_source"] or "ukjent"] += 1

    rangering = []
    for kode, egne in per_operator.items():
        avvik = [t.avvik for t in egne]
        rangering.append({
            "kode": kode,
            "navn": OPERATORNAVN.get(kode, kode),
            "turer": len(egne),
            "observasjoner": sum(t.observasjoner for t in egne),
            "linjer": len({t.linje for t in egne if t.linje}),
            "andelIRute": _andel(sum(1 for t in egne if t.i_rute), len(egne)),
            "medianAvvikSekunder": _median_sek(avvik),
            "verstAvvikSekunder": int(round(max(avvik))),
            # Andel av turene der posisjonen ble regnet ut i stedet for målt.
            "andelBeregnet": _andel(sum(1 for t in egne if t.beregnet), len(egne)),
            "kilder": dict(kilder_per_operator.get(kode, {})),
        })

    # Andel i rute først. Ved lik andel vinner den med lavest median, og
    # deretter den med flest turer - et selskap med tre turer skal ikke stå
    # over et med tre hundre på et tilfeldig uavgjort.
    rangering.sort(key=lambda o: (-o["andelIRute"], o["medianAvvikSekunder"], -o["turer"]))
    for plass, operator in enumerate(rangering, start=1):
        operator["plass"] = plass

    return {
        "generert": datetime.now(timezone.utc).isoformat(),
        "terskelSekunder": TERSKEL_SEKUNDER,
        "vindu": _vindu(turer, dager),
        "turerTotalt": len(turer),
        "utenOperator": uten_operator,
        "operatorer": rangering,
    }


# ---------------------------------------------------------------------------
# 2. Rushtidsprofil
# ---------------------------------------------------------------------------

def _periode(timer: set[int]) -> list[str]:
    """Hvilke rushvinduer en tur var underveis i. Kan være begge, kan være
    ingen - et fjerntog fra Bergen er i trafikk gjennom hele ettermiddagen."""
    traff = []
    if any(MORGENRUSH[0] <= t < MORGENRUSH[1] for t in timer):
        traff.append("morgen")
    if any(ETTERMIDDAGSRUSH[0] <= t < ETTERMIDDAGSRUSH[1] for t in timer):
        traff.append("ettermiddag")
    return traff


def rushtidsprofil(dager: int = 14, topp: int = 8) -> dict:
    """Hvilke strekninger som er hardest belastet morgen og ettermiddag.

    «Belastet» måles her som avvik og trafikkmengde. Det er ikke det samme som
    passasjerbelegg - Entur publiserer ingen passasjertall i de åpne feedene,
    og et tall vi ikke har skal ikke gjettes. Et fullt tog som går presist
    havner derfor ikke på denne lista, og det er en reell begrensning: dette
    er en profil over hvor rushtiden slår ut i forsinkelse, ikke over hvor
    trangt det står.

    Rangert på median avvik, med antall togturer ved siden av. Rekkefølgen
    kunne like gjerne vært trafikkmengde; avviket er valgt fordi det er det
    som gjør en strekning vond å reise på, og fordi trafikkmengden alene
    bare ville rangert Oslo-linjene etter hvor tett de går.
    """
    # Skrivebeskyttet: en analyse skal verken kunne endre noe eller ta en
    # skrivelås. `kobling()` setter også busy_timeout, som er det som gjør
    # at en lesing venter på skrivetråden i stedet for å feile med
    # "database is locked". Se historikk.kobling().
    with kobling(skrivbar=False) as con:
        con.row_factory = sqlite3.Row
        rader = _hent_rader(con, dager)

    alle = _samle_turer(rader)

    # Bare hverdager. Datoen er allerede lokal, så den kan leses rett tilbake.
    turer = [
        t for t in alle
        if datetime.fromisoformat(t.dato).weekday() in HVERDAGER
    ]

    perioder = {}
    for navn in ("morgen", "ettermiddag"):
        per_linje: dict[str, list[Togtur]] = defaultdict(list)
        for tur in turer:
            if navn in _periode(tur.timer) and tur.linje:
                per_linje[tur.linje].append(tur)

        strekninger = []
        for linje, egne in per_linje.items():
            if len(egne) < MIN_TURER:
                continue
            avvik = [t.avvik for t in egne]
            navn_på_linje = next((t.linjenavn for t in egne if t.linjenavn), "")
            operator = next((t.operator for t in egne if t.operator), "")
            strekninger.append({
                "linje": linje,
                "strekning": navn_på_linje or linje,
                "operator": OPERATORNAVN.get(operator, operator),
                "turer": len(egne),
                "medianAvvikSekunder": _median_sek(avvik),
                "verstAvvikSekunder": int(round(max(avvik))),
                "andelIRute": _andel(sum(1 for t in egne if t.i_rute), len(egne)),
            })

        strekninger.sort(key=lambda s: (-s["medianAvvikSekunder"], -s["turer"]))

        perioder[navn] = {
            "fraTime": MORGENRUSH[0] if navn == "morgen" else ETTERMIDDAGSRUSH[0],
            "tilTime": MORGENRUSH[1] if navn == "morgen" else ETTERMIDDAGSRUSH[1],
            "turer": sum(len(v) for v in per_linje.values()),
            "strekninger": strekninger[:topp],
            # Hvor mange linjer som ble sett bort fra fordi de hadde for få
            # turer. Uten dette tallet ser en kort liste ut som lite trafikk,
            # når den egentlig er lite data.
            "utelattForFåTurer": sum(1 for v in per_linje.values() if len(v) < MIN_TURER),
        }

    return {
        "generert": datetime.now(timezone.utc).isoformat(),
        "terskelSekunder": TERSKEL_SEKUNDER,
        "minTurer": MIN_TURER,
        "vindu": _vindu(turer, dager),
        "perioder": perioder,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _minutter(sekunder: int) -> str:
    fortegn = "-" if sekunder < 0 else ""
    sekunder = abs(sekunder)
    return f"{fortegn}{sekunder // 60}:{sekunder % 60:02d}"


def _skriv_operatorer(data: dict) -> None:
    vindu = data["vindu"]
    print(f"=== Operatørsammenligning ({vindu['dognMedData']} døgn med data, "
          f"{data['turerTotalt']} togturer) ===")
    print(f"  i rute = typisk avvik under {data['terskelSekunder'] // 60} min "
          f"underveis\n")

    if not data["operatorer"]:
        print("  Ingen data ennå. La serveren stå og gå en dag.")
        return

    print(f"  {'':<3}{'Operatør':<18}{'i rute':>8}{'median':>9}"
          f"{'verst':>9}{'turer':>8}{'linjer':>8}")
    for o in data["operatorer"]:
        merke = " *" if o["andelBeregnet"] > 0 else ""
        print(f"  {o['plass']}. {o['navn'] + merke:<18}"
              f"{o['andelIRute'] * 100:>7.0f}%"
              f"{_minutter(o['medianAvvikSekunder']):>9}"
              f"{_minutter(o['verstAvvikSekunder']):>9}"
              f"{o['turer']:>8}{o['linjer']:>8}")

    if any(o["andelBeregnet"] > 0 for o in data["operatorer"]):
        print("\n  * en andel av turene har beregnet posisjon, ikke målt. "
              "Tallet er andelen,\n    ikke et ja/nei: SJ har aldri GPS, mens "
              "de andre mangler posisjon\n    for noen av turene sine.")
    if data["utenOperator"]:
        print(f"  {data['utenOperator']} togturer uten operatørmerking - rader "
              f"skrevet før kolonnen fantes.")


def _skriv_rush(data: dict) -> None:
    vindu = data["vindu"]
    print(f"\n=== Rushtidsprofil ({vindu['dognMedData']} hverdagsdøgn med "
          f"data) ===")

    for navn, periode in data["perioder"].items():
        tittel = "Morgenrush" if navn == "morgen" else "Ettermiddagsrush"
        print(f"\n  {tittel} {periode['fraTime']:02d}–{periode['tilTime']:02d}"
              f"  ({periode['turer']} togturer)")

        if not periode["strekninger"]:
            skjult = periode["utelattForFåTurer"]
            grunn = (f"{skjult} linjer har færre enn {data['minTurer']} turer"
                     if skjult else "ingen togturer i vinduet ennå")
            print(f"    Ingen strekninger å vise - {grunn}.")
            continue

        for s in periode["strekninger"]:
            print(f"    {s['linje']:<6}{_minutter(s['medianAvvikSekunder']):>7}"
                  f"  {s['andelIRute'] * 100:>3.0f}% i rute"
                  f"  {s['turer']:>3} turer   {s['strekning']}")


if __name__ == "__main__":
    # Kjør:  python analyse.py
    # Ingen nett, ingen server - bare det som allerede står i historikk.db.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"Fant ingen database på '{DB_PATH}'. "
                         "Kjør serveren minst én gang først.")
    _skriv_operatorer(operatorsammenligning())
    _skriv_rush(rushtidsprofil())
