"""Vedlikehold - gir historikk.db en levetid, og et minne som overlever den.

`historikk.py` skriver 28 777 rader i døgnet og har aldri slettet noe. Målt
20. august er det 7,1 MB i døgnet og 2,5 GB i året, uten tak. Det er ikke et
lagringsproblem i seg selv - disker er store - men det er en langsom
tilgjengelighetsfeil: `/api/statistikk` leser hele tidsvinduet ut av SQLite og
regner medianer i Python, og den lesingen vokser med tabellen. Med
`dager=90` på en database som har stått et år, er det sekunder med CPU per
kall, og det er et kall hvem som helst kan be om.

Denne modulen gjør to ting, i den rekkefølgen:

  1. **Rullup.** Hvert ferdig døgn regnes ut én gang og skrives til `dogn`,
     én rad per (dato, operatør, linje). Den tabellen slettes aldri.
  2. **Rotasjon.** Råobservasjoner eldre enn `BEHOLD_DAGER` slettes, og
     plassen frigjøres.

---------------------------------------------------------------------------
Hvorfor rullup og ikke bare sletting
---------------------------------------------------------------------------
Sletting alene er billig å skrive og dyrt å angre. Den dagen noen spør «var
Vy mer punktlig i august enn i november», er svaret borte - og det er ikke
et svar man kan regne seg fram til i ettertid. Rullupen koster noen få MB i
året og gjør spørsmålet mulig.

Det som IKKE overlever rotasjonen er posisjonene. `flaskehals.py` leser lat
og lon per observasjon for å finne hvor på strekningen tiden går tapt, og den
aksen kan ikke aggregeres til en rad per døgn uten å bli noe annet. Varmekartet
er derfor bundet til råvinduet, og det er riktig: det spør «hvor er det trangt
NÅ», ikke «hvor var det trangt i fjor».

---------------------------------------------------------------------------
Hva en rad i `dogn` betyr
---------------------------------------------------------------------------
Én togtur, ikke én observasjon - samme enhet som `analyse.py` rangerer på, og
av samme grunn (se docstringen der: teller vi rader, veier Bergensbanen
tyngre enn et lokaltog fordi den er lengre). `median_avvik_sek` er derfor
medianen av turenes medianer, ikke medianen av alle observasjonene.

Det har en konsekvens som er verdt å kjenne: **medianer kan ikke slås
sammen.** Har du 90 døgnrader, kan du legge sammen `turer` og `turer_i_rute`
og få en helt korrekt andel i rute for kvartalet - de er tellinger. Men
medianen av de 90 `median_avvik_sek`-verdiene er «det typiske døgnets typiske
tog», ikke «det typiske toget i kvartalet». De to er nære hverandre og ikke
like. Derfor lagres `turer` og `turer_i_rute` som rå tellinger og ikke som en
ferdig andel: andeler kan ikke summeres, tellinger kan.

Kjøres av seg selv én gang i døgnet fra `app.py` (se `vedlikeholdsjobb()`),
og for hånd med:

    python vedlikehold.py            # rull opp og roter
    python vedlikehold.py --status   # hva ligger der, uten å endre noe
    python vedlikehold.py --torrkjor # hva ville skjedd
    python vedlikehold.py --selvtest # mot en midlertidig database, uten nett
"""

import argparse
import logging
import os
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from analyse import (
    ETTERMIDDAGSRUSH,
    MORGENRUSH,
    OSLO,
    TERSKEL_SEKUNDER,
    _samle_turer,
    hent_rader_mellom,
)
from historikk import DB_PATH, kobling

log = logging.getLogger("togkart")

# Hvor lenge råobservasjonene lever. 90 dager er ikke et rundt tall valgt for
# å være rundt: det er taket `app.py` klemmer `dager` til på alle tre
# statistikkendepunktene. Settes dette lavere uten å senke taket der, svarer
# `?dager=90` med færre dager enn det ble spurt om, og gjør det stille.
# Se `sjekk_taket()`.
BEHOLD_DAGER = int(os.getenv("HISTORIKK_BEHOLD_DAGER", "90"))

# Døgn som allerede er rullet opp regnes ikke ut på nytt, med ett unntak: de
# nyeste. Et døgn kan ha blitt rullet opp mens det fortsatt kom rader inn -
# skjer hvis jobben kjøres manuelt midt på dagen, eller hvis serveren står i
# en annen tidssone enn den tror. To døgn med overlapp koster et par sekunder
# og fjerner hele klassen av halvferdige rader.
OVERLAPP_DOGN = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dogn (
    dato TEXT NOT NULL,
    operator TEXT NOT NULL,
    linje TEXT NOT NULL,
    linjenavn TEXT,
    turer INTEGER NOT NULL,
    turer_i_rute INTEGER NOT NULL,
    turer_beregnet INTEGER NOT NULL,
    observasjoner INTEGER NOT NULL,
    median_avvik_sek INTEGER,
    p90_avvik_sek INTEGER,
    verst_avvik_sek INTEGER,
    turer_morgen INTEGER NOT NULL,
    i_rute_morgen INTEGER NOT NULL,
    turer_ettermiddag INTEGER NOT NULL,
    i_rute_ettermiddag INTEGER NOT NULL,
    terskel_sek INTEGER NOT NULL,
    rullet TEXT NOT NULL,
    PRIMARY KEY (dato, operator, linje)
);
CREATE INDEX IF NOT EXISTS idx_dogn_dato ON dogn(dato);
CREATE INDEX IF NOT EXISTS idx_dogn_operator ON dogn(operator, dato);

-- Hvilke døgn rullupen HAR behandlet, uansett om de ga rader.
--
-- Uten denne kunne vi bare spurt `dogn` hvilke datoer den kjenner, og det var
-- første forsøk. Det er feil på en måte som ikke synes: et døgn uten
-- kvalifiserende observasjoner - natta mellom to omstarter, en dag serveren
-- var nede - får aldri en rad i `dogn`, og ble derfor regnet ut på nytt ved
-- hver eneste kjøring. Selvtesten fanget det som «rullet opp 120 døgn» tre
-- ganger på rad.
--
-- Den er også fasiten rotasjonen spør: «har jobben sett dette døgnet», ikke
-- «ga dette døgnet tall». Det første er spørsmålet som avgjør om det er
-- trygt å slette.
CREATE TABLE IF NOT EXISTS dogn_rullet (
    dato TEXT PRIMARY KEY,
    rader INTEGER NOT NULL,
    turer INTEGER NOT NULL,
    rullet TEXT NOT NULL
);

-- Døgn som ALDRI skal rulles opp, med begrunnelsen.
--
-- Dette hører hjemme i basen og ikke i koden, fordi det er en egenskap ved
-- DISSE dataene og ikke ved programmet. Et nytt oppsett skal ikke arve
-- utelatelser fra vårt. Første forsøk var en hardkodet liste i vedlikehold.py,
-- og selvtesten falt over den med en gang: den bygger syntetiske døgn
-- relativt til i dag, og et av dem traff en av datoene. Det var et varsel om
-- at plasseringen var feil, ikke om at testen var det.
--
-- `grunn` er ikke pynt. Et hull i et arkiv som aldri slettes, uten en
-- forklaring ved siden av, blir til «her mangler det data - vet ikke hvorfor»
-- lenge etter at alle som visste har glemt det.
CREATE TABLE IF NOT EXISTS dogn_utelatt (
    dato TEXT PRIMARY KEY,
    grunn TEXT NOT NULL,
    lagt_inn TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Døgngrenser
# ---------------------------------------------------------------------------
# Databasen lagrer UTC. Et døgn er lokalt. Sommertid flytter grensen to ganger
# i året, og da er ett døgn 23 timer og ett er 25 - `zoneinfo` kan reglene,
# en hardkodet +2 kan dem ikke. Samme valg som `analyse.py` gjør for rushtid.

def _dogn_grenser(dag: date) -> tuple[str, str]:
    """(fra, til) som ISO-tekst i UTC, halvåpent: fra <= t < til."""
    start = datetime(dag.year, dag.month, dag.day, tzinfo=OSLO)
    slutt = start + timedelta(days=1)
    # `+ timedelta(days=1)` på en aware datetime gir riktig veggklokke også
    # over sommertidsskiftet, fordi zoneinfo normaliserer ved konvertering.
    slutt = datetime(slutt.year, slutt.month, slutt.day, tzinfo=OSLO)
    return (
        start.astimezone(timezone.utc).isoformat(),
        slutt.astimezone(timezone.utc).isoformat(),
    )


def _i_dag_lokalt() -> date:
    return datetime.now(timezone.utc).astimezone(OSLO).date()


def _dogn_i_basen(con: sqlite3.Connection) -> tuple[date | None, date | None]:
    """Første og siste LOKALE døgn det finnes råobservasjoner for.

    Utledet av min/max på tidspunkt, ikke av en gjennomlesing: tabellen har
    en indeks på `tidspunkt`, så dette er to indeksoppslag uansett størrelse.
    Ytterpunktene konverteres til lokal dato, og da kan første døgn bli et
    døgn tidligere enn UTC-datoen tilsier. Det er poenget.
    """
    rad = con.execute(
        "SELECT MIN(tidspunkt), MAX(tidspunkt) FROM observasjoner"
    ).fetchone()
    if not rad or not rad[0]:
        return None, None

    def lokal(tekst: str) -> date:
        return datetime.fromisoformat(tekst).astimezone(OSLO).date()

    return lokal(rad[0]), lokal(rad[1])


# ---------------------------------------------------------------------------
# Rullup
# ---------------------------------------------------------------------------

def _persentil(verdier: list[float], andel: float) -> int:
    """Nærmeste-rang-persentil. Ingen interpolasjon.

    `statistics.quantiles` ville gjort dette penere, men den interpolerer
    mellom to observasjoner og finner dermed på et avvik som ingen tog hadde.
    Til en p90 over et døgn med et par hundre turer er forskjellen liten og
    forklaringen dobbelt så lang. Her er verdien alltid et tall et ekte tog
    faktisk hadde.
    """
    if not verdier:
        return 0
    sortert = sorted(verdier)
    plass = max(0, min(len(sortert) - 1, int(round(andel * len(sortert))) - 1))
    return int(round(sortert[plass]))


def _rull_ett_dogn(con: sqlite3.Connection, dag: date) -> int:
    """Regn ut og skriv radene for ett lokalt døgn. Returnerer antall rader.

    `INSERT OR REPLACE` og ikke `INSERT`: kjøres døgnet på nytt, skal
    resultatet erstattes og ikke duplisere. Primærnøkkelen er (dato, operatør,
    linje), så et døgn som har fått flere linjer siden sist får de nye lagt
    til og de gamle oppdatert.
    """
    fra, til = _dogn_grenser(dag)
    con.row_factory = sqlite3.Row
    dato = dag.isoformat()
    rader = hent_rader_mellom(con, fra, til)
    if not rader:
        _fort_i_loggen(con, dato, 0, 0)
        return 0

    # `_samle_turer` grupperer på (lokal dato, tog_id) og kan derfor gi turer
    # merket med nabodøgnet: et nattog som passerer midnatt har observasjoner
    # på begge sider av grensen. Vinduet over henter bare det ene døgnet, så
    # den halen er kort - men den finnes, og en tur som havner på feil dato
    # ville blitt skrevet inn under en dato vi ikke rullet opp, og dermed
    # aldri kommet med. Vi beholder bare turene som hører til dagen.
    turer = [t for t in _samle_turer(rader) if t.dato == dato]
    if not turer:
        _fort_i_loggen(con, dato, 0, 0)
        return 0

    grupper: dict[tuple[str, str], list] = defaultdict(list)
    for tur in turer:
        grupper[(tur.operator or "", tur.linje or "")].append(tur)

    naa = datetime.now(timezone.utc).isoformat()
    utrader = []
    for (operator, linje), egne in grupper.items():
        avvik = [t.avvik for t in egne]
        morgen = [t for t in egne if _i_vindu(t.timer, MORGENRUSH)]
        ettermiddag = [t for t in egne if _i_vindu(t.timer, ETTERMIDDAGSRUSH)]
        navn = next((t.linjenavn for t in egne if t.linjenavn), "")
        utrader.append((
            dato, operator, linje, navn,
            len(egne),
            sum(1 for t in egne if t.i_rute),
            sum(1 for t in egne if t.beregnet),
            sum(t.observasjoner for t in egne),
            int(round(statistics.median(avvik))),
            _persentil(avvik, 0.90),
            int(round(max(avvik))),
            len(morgen), sum(1 for t in morgen if t.i_rute),
            len(ettermiddag), sum(1 for t in ettermiddag if t.i_rute),
            TERSKEL_SEKUNDER, naa,
        ))

    con.executemany(
        """INSERT OR REPLACE INTO dogn
           (dato, operator, linje, linjenavn, turer, turer_i_rute,
            turer_beregnet, observasjoner, median_avvik_sek, p90_avvik_sek,
            verst_avvik_sek, turer_morgen, i_rute_morgen, turer_ettermiddag,
            i_rute_ettermiddag, terskel_sek, rullet)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        utrader,
    )
    _fort_i_loggen(con, dato, len(utrader), len(turer))
    return len(utrader)


def _fort_i_loggen(con: sqlite3.Connection, dato: str, rader: int, turer: int) -> None:
    con.execute(
        """INSERT OR REPLACE INTO dogn_rullet (dato, rader, turer, rullet)
           VALUES (?, ?, ?, ?)""",
        (dato, rader, turer, datetime.now(timezone.utc).isoformat()),
    )


def _i_vindu(timer: set[int], vindu: tuple[int, int]) -> bool:
    return any(vindu[0] <= t < vindu[1] for t in timer)


def _bryt(tekst: str, bredde: int) -> list[str]:
    """Enkel tekstbryting. `textwrap` for én bruk er å dra inn et bibliotek."""
    linjer, na = [], ""
    for ord_ in tekst.split():
        if na and len(na) + 1 + len(ord_) > bredde:
            linjer.append(na)
            na = ord_
        else:
            na = f"{na} {ord_}".strip()
    if na:
        linjer.append(na)
    return linjer


# Leseren bor i historikk.py, som eier døra inn til basen. Én utgave, så
# rullupen, operatørrangeringen og flaskehalskartet ikke kan komme til å mene
# forskjellige ting om hvilke døgn som gjelder - samme begrunnelse som
# `hent_rader_mellom` i analyse.py har for sitt delte filter.
from historikk import utelatte_dogn  # noqa: E402


def utelat_dogn(con: sqlite3.Connection, dato: str, grunn: str) -> None:
    """Merk et døgn som utelatt, og fjern det fra arkivet om det står der.

    Sletter IKKE rådata. De forsvinner av seg selv etter BEHOLD_DAGER, og til
    da er de fortsatt det beste vi har hvis noen vil ettergå avgjørelsen.
    """
    con.execute(
        "INSERT OR REPLACE INTO dogn_utelatt (dato, grunn, lagt_inn) "
        "VALUES (?, ?, ?)",
        (dato, grunn, datetime.now(timezone.utc).isoformat()),
    )
    fjernet = con.execute("DELETE FROM dogn WHERE dato = ?", (dato,)).rowcount
    log.info(
        "vedlikehold: %s utelatt fra arkivet (%d rader fjernet). Grunn: %s",
        dato, fjernet, grunn,
    )


def rull_opp(con: sqlite3.Connection, *, torrkjor: bool = False) -> dict:
    """Rull opp alle ferdige døgn som mangler i `dogn`.

    «Ferdig» betyr strengt før dagens lokale dato. Døgnet som pågår rulles
    aldri opp: gjorde vi det, ville raden beskrevet formiddagen og blitt
    stående som fasit for hele dagen, fordi neste kjøring ser at datoen
    allerede finnes og hopper over den.

    Døgn som står i `dogn_utelatt` hoppes over uansett. Se tabellen der,
    som bærer begrunnelsen sin selv.
    """
    forste, siste = _dogn_i_basen(con)
    if forste is None:
        return {"dogn": 0, "rader": 0, "fra": None, "til": None}

    i_dag = _i_dag_lokalt()
    sluttdag = min(siste, i_dag - timedelta(days=1))
    if sluttdag < forste:
        return {"dogn": 0, "rader": 0, "fra": None, "til": None}

    kjent = {rad[0] for rad in con.execute("SELECT dato FROM dogn_rullet")}
    utelatte = utelatte_dogn(con)
    # De nyeste døgnene regnes ut på nytt selv om de finnes. Se OVERLAPP_DOGN.
    tvungne = {
        (sluttdag - timedelta(days=n)).isoformat() for n in range(OVERLAPP_DOGN)
    }

    dag = forste
    skal_rulles = []
    utelatt = []
    while dag <= sluttdag:
        tekst = dag.isoformat()
        if tekst in utelatte:
            utelatt.append(tekst)
        elif tekst not in kjent or tekst in tvungne:
            skal_rulles.append(dag)
        dag += timedelta(days=1)

    if utelatt:
        log.info(
            "vedlikehold: hopper over %d døgn som står i dogn_utelatt (%s).",
            len(utelatt), ", ".join(utelatt),
        )

    if torrkjor or not skal_rulles:
        return {
            "dogn": len(skal_rulles),
            "rader": 0,
            "fra": skal_rulles[0].isoformat() if skal_rulles else None,
            "til": skal_rulles[-1].isoformat() if skal_rulles else None,
        }

    rader = 0
    for d in skal_rulles:
        rader += _rull_ett_dogn(con, d)

    log.info(
        "vedlikehold: rullet opp %d døgn (%s til %s), %d rader i 'dogn'",
        len(skal_rulles), skal_rulles[0], skal_rulles[-1], rader,
    )
    return {
        "dogn": len(skal_rulles),
        "rader": rader,
        "fra": skal_rulles[0].isoformat(),
        "til": skal_rulles[-1].isoformat(),
    }


# ---------------------------------------------------------------------------
# Rotasjon
# ---------------------------------------------------------------------------

def roter(con: sqlite3.Connection, *, torrkjor: bool = False) -> dict:
    """Slett råobservasjoner eldre enn BEHOLD_DAGER.

    Med én sperre, og den er hele forskjellen på en rotasjonsjobb og et
    datatap: **ingenting slettes med mindre rullupen har tatt igjen.** Vi
    krever at `dogn` inneholder et døgn som er nyere enn eller lik grensen vi
    sletter fram til. Feiler rullupen - en ødelagt rad, en full disk, en
    exception i `_samle_turer` - står slettingen stille i stedet for å kaste
    data ingen har arkivert.

    Sperren er også grunnen til at rullupen kjøres FØR rotasjonen i
    `vedlikehold()`, og ikke etter.
    """
    grense_dag = _i_dag_lokalt() - timedelta(days=BEHOLD_DAGER)
    grense_iso, _ = _dogn_grenser(grense_dag)

    antall = con.execute(
        "SELECT COUNT(*) FROM observasjoner WHERE tidspunkt < ?", (grense_iso,)
    ).fetchone()[0]
    if not antall:
        return {"slettet": 0, "grense": grense_dag.isoformat(), "sperret": False}

    nyeste_rullet = con.execute("SELECT MAX(dato) FROM dogn_rullet").fetchone()[0]
    if not nyeste_rullet or nyeste_rullet < grense_dag.isoformat():
        log.warning(
            "vedlikehold: %d rader er eldre enn %d dager, men rullupen står på "
            "%s. Sletter ingenting - arkivet må ta igjen først.",
            antall, BEHOLD_DAGER, nyeste_rullet or "ingenting",
        )
        return {"slettet": 0, "grense": grense_dag.isoformat(), "sperret": True}

    if torrkjor:
        return {"slettet": antall, "grense": grense_dag.isoformat(), "sperret": False}

    con.execute("DELETE FROM observasjoner WHERE tidspunkt < ?", (grense_iso,))
    log.info(
        "vedlikehold: slettet %d observasjoner eldre enn %s (%d dager)",
        antall, grense_dag.isoformat(), BEHOLD_DAGER,
    )
    return {"slettet": antall, "grense": grense_dag.isoformat(), "sperret": False}


# ---------------------------------------------------------------------------
# Plass
# ---------------------------------------------------------------------------

def _sett_inkrementell_vakuum(sti: str) -> bool:
    """Gjør databasen i stand til å frigjøre plass uten å skrives om helt.

    Standarden er `auto_vacuum=NONE`: sletting frigjør sider inne i filen,
    men filen krymper aldri. Eneste måte å få plassen tilbake er `VACUUM`,
    som skriver hele databasen på nytt - på 640 MB er det sekunder med I/O
    og like mye ledig disk som filen er stor, hver eneste natt.

    `INCREMENTAL` legger inn et sidekart som lar `PRAGMA incremental_vacuum`
    klippe av frigjorte sider i enden, billig og uten omskriving.

    Bytte av modus krever én full `VACUUM` - moden ligger i filhodet. Den
    kjøres derfor bare den ene gangen, og helst nå mens filen er liten.
    Returnerer True hvis den faktisk konverterte noe.

    Egen tilkobling med `isolation_level=None`: `VACUUM` kan ikke kjøre inne
    i en transaksjon, og Python sin sqlite3 åpner en implisitt transaksjon
    med standardinnstillingene.
    """
    con = sqlite3.connect(sti, isolation_level=None)
    try:
        if con.execute("PRAGMA auto_vacuum").fetchone()[0] != 0:
            return False
        con.execute("PRAGMA auto_vacuum = INCREMENTAL")
        con.execute("VACUUM")
        log.info("vedlikehold: historikk.db satt til auto_vacuum=INCREMENTAL")
        return True
    finally:
        con.close()


def frigjor_plass(sti: str = "") -> dict:
    """Klipp av frigjorte sider og skriv WAL-loggen tilbake til hovedfilen."""
    sti = sti or DB_PATH
    for_ = _filstorrelse(sti)

    con = sqlite3.connect(sti, isolation_level=None)
    try:
        con.execute("PRAGMA incremental_vacuum")
        # TRUNCATE og ikke PASSIVE: uten den vokser -wal-filen til
        # høyvannsmerket sitt og blir stående der. Etter en sletting av tre
        # måneder med rader er det høyvannsmerket stort.
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()

    etter = _filstorrelse(sti)
    return {"forBytes": for_, "etterBytes": etter, "friBytes": for_ - etter}


def _filstorrelse(sti: str) -> int:
    """Databasen pluss sidefilene. -wal kan alene være titalls MB."""
    total = 0
    for suffiks in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(sti + suffiks)
        except OSError:
            pass
    return total


# ---------------------------------------------------------------------------
# Hele jobben
# ---------------------------------------------------------------------------

def vedlikehold(*, torrkjor: bool = False) -> dict:
    """Rull opp, roter, frigjør plass. Synkron - kalles i en tråd fra app.py.

    Rekkefølgen er ikke tilfeldig: rullupen må ha skrevet arkivet før
    rotasjonen får lov til å slette noe. Se `roter()`.
    """
    with kobling() as con:
        con.executescript(_SCHEMA)
        rullup = rull_opp(con, torrkjor=torrkjor)
        rotasjon = roter(con, torrkjor=torrkjor)

    plass = {"forBytes": _filstorrelse(DB_PATH), "etterBytes": None, "friBytes": 0}
    if not torrkjor:
        # Konverteringen til INCREMENTAL kjøres ved FØRSTE vedlikehold, ikke
        # ved første sletting. Forskjellen er prisen: bytte av modus krever én
        # full VACUUM, og den koster like mye som databasen er stor. I dag er
        # det 15 MB og et blunk; venter vi til rotasjonen har noe å gjøre, er
        # det 450 MB og en jobb som trenger like mye ledig disk som filen.
        # Etter denne ene gangen er `_sett_inkrementell_vakuum` et pragma-
        # oppslag som svarer nei.
        _sett_inkrementell_vakuum(DB_PATH)
        if rotasjon["slettet"]:
            plass = frigjor_plass(DB_PATH)
        else:
            # Ingen sletting, ingen frie sider å klippe av. Et checkpoint per
            # døgn på en fil ingen har rørt er ren I/O.
            plass["etterBytes"] = plass["forBytes"]

    return {
        "kjort": datetime.now(timezone.utc).isoformat(),
        "torrkjor": torrkjor,
        "beholdDager": BEHOLD_DAGER,
        "rullup": rullup,
        "rotasjon": rotasjon,
        "plass": plass,
    }


def sjekk_taket(maks_dager: int) -> None:
    """Advar hvis appen tilbyr et vindu databasen ikke lenger har.

    `app.py` klemmer `dager` til 1-90 på tre endepunkter. Er BEHOLD_DAGER
    satt lavere enn det taket, svarer `?dager=90` med det som er igjen og
    kaller det 90 dager. Det er ikke en feil som gir en feilmelding; det er
    et tall som ser riktig ut og er for kort. Derfor sagt høyt ved oppstart.
    """
    if BEHOLD_DAGER < maks_dager:
        log.warning(
            "HISTORIKK_BEHOLD_DAGER=%d er lavere enn taket på 'dager' (%d). "
            "Spørringer med dager>%d vil svare med færre døgn enn de ber om, "
            "uten å si fra. Senk taket i app.py, eller hev BEHOLD_DAGER.",
            BEHOLD_DAGER, maks_dager, BEHOLD_DAGER,
        )


# ---------------------------------------------------------------------------
# Status og utskrift
# ---------------------------------------------------------------------------

def status() -> dict:
    """Hva ligger i basen nå. Leser bare, endrer ingenting."""
    with kobling(skrivbar=False) as con:
        rader = con.execute("SELECT COUNT(*) FROM observasjoner").fetchone()[0]
        forste, siste = _dogn_i_basen(con)
        har_dogn = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dogn'"
        ).fetchone()
        if har_dogn:
            arkiv = con.execute(
                "SELECT COUNT(*), COUNT(DISTINCT dato), MIN(dato), MAX(dato), "
                "COALESCE(SUM(turer), 0) FROM dogn"
            ).fetchone()
        else:
            arkiv = (0, 0, None, None, 0)

        har_utelatt = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='dogn_utelatt'"
        ).fetchone()
        if har_utelatt:
            utelatt = con.execute(
                "SELECT dato, grunn FROM dogn_utelatt ORDER BY dato"
            ).fetchall()
        else:
            utelatt = []

        modus = con.execute("PRAGMA journal_mode").fetchone()[0]
        auto = con.execute("PRAGMA auto_vacuum").fetchone()[0]

    bytes_ = _filstorrelse(DB_PATH)
    dogn_med_data = (siste - forste).days + 1 if forste else 0
    return {
        "sti": os.path.abspath(DB_PATH),
        "bytes": bytes_,
        "journalModus": modus,
        "autoVacuum": {0: "NONE", 1: "FULL", 2: "INCREMENTAL"}.get(auto, str(auto)),
        "beholdDager": BEHOLD_DAGER,
        "raa": {
            "rader": rader,
            "fraDato": forste.isoformat() if forste else None,
            "tilDato": siste.isoformat() if siste else None,
            "dogn": dogn_med_data,
            "raderPerDogn": round(rader / dogn_med_data) if dogn_med_data else 0,
        },
        "arkiv": {
            "rader": arkiv[0],
            "dogn": arkiv[1],
            "fraDato": arkiv[2],
            "tilDato": arkiv[3],
            "turer": arkiv[4],
            "utelatt": utelatt,
        },
    }


def _mb(bytes_: int) -> str:
    return f"{bytes_ / 1_048_576:.1f} MB"


def _skriv_status(s: dict) -> None:
    print(f"\nhistorikk.db  {s['sti']}")
    print(f"  Størrelse        {_mb(s['bytes'])}")
    print(f"  Journalmodus     {s['journalModus']}"
          + ("" if s["journalModus"].lower() == "wal" else "   <- ikke WAL"))
    print(f"  auto_vacuum      {s['autoVacuum']}")
    print(f"  Beholder         {s['beholdDager']} dager rådata")

    r = s["raa"]
    print(f"\nRåobservasjoner")
    if r["rader"]:
        print(f"  {r['rader']:>12,} rader".replace(",", " "))
        print(f"  {r['fraDato']} til {r['tilDato']}  ({r['dogn']} døgn)")
        print(f"  {r['raderPerDogn']:>12,} rader i døgnet".replace(",", " "))
        i_aaret = r["raderPerDogn"] * 365 * (s["bytes"] / max(r["rader"], 1))
        print(f"  Uten rotasjon:  {_mb(i_aaret)} i året")
        print(f"  Med rotasjon:   {_mb(i_aaret * s['beholdDager'] / 365)} i likevekt")
    else:
        print("  tom")

    a = s["arkiv"]
    print(f"\nArkiv (dogn) - slettes aldri")
    if a["rader"]:
        print(f"  {a['rader']:>12,} rader".replace(",", " "))
        print(f"  {a['fraDato']} til {a['tilDato']}  ({a['dogn']} døgn)")
        print(f"  {a['turer']:>12,} togturer arkivert".replace(",", " "))
    elif a.get("utelatt"):
        print("  tomt")
    else:
        print("  tomt - kjør 'python vedlikehold.py'")

    # Et hull i et arkiv som aldri slettes MÅ ha en forklaring ved siden av
    # seg. Uten den blir det til «her mangler det data - vet ikke hvorfor»
    # lenge etter at alle som visste har glemt det.
    if a.get("utelatt"):
        print(f"\n  {len(a['utelatt'])} døgn er bevisst utelatt og rulles aldri opp.")
        # Gruppert på begrunnelse: utelatelser kommer som regel i puljer fra
        # samme hendelse, og den samme teksten tre ganger er støy.
        per_grunn: dict[str, list[str]] = {}
        for dato, grunn in a["utelatt"]:
            per_grunn.setdefault(grunn, []).append(dato)
        for grunn, datoer in per_grunn.items():
            print(f"    {', '.join(datoer)}")
            for linje in _bryt(grunn, 64):
                print(f"      {linje}")
    print()


def _skriv_resultat(res: dict) -> None:
    merke = " (tørrkjøring - ingenting er endret)" if res["torrkjor"] else ""
    print(f"\nVedlikehold{merke}")

    ru = res["rullup"]
    if ru["dogn"]:
        vindu = f"{ru['fra']} til {ru['til']}"
        if res["torrkjor"]:
            print(f"  Ville rullet opp   {ru['dogn']} døgn ({vindu})")
        else:
            print(f"  Rullet opp         {ru['dogn']} døgn ({vindu})")
            print(f"                     {ru['rader']} rader i 'dogn'")
    else:
        print("  Rullup             ingenting nytt å arkivere")

    ro = res["rotasjon"]
    if ro["sperret"]:
        print(f"  Rotasjon           SPERRET - arkivet henger etter, se loggen")
    elif ro["slettet"]:
        verb = "Ville slettet" if res["torrkjor"] else "Slettet"
        print(f"  {verb:<18} {ro['slettet']} rader eldre enn {ro['grense']}")
    else:
        print(f"  Rotasjon           ingenting eldre enn {ro['grense']}")

    p = res["plass"]
    if p["friBytes"] > 0:
        print(f"  Frigjort           {_mb(p['friBytes'])} "
              f"({_mb(p['forBytes'])} -> {_mb(p['etterBytes'])})")
    print()


# ---------------------------------------------------------------------------
# Selvtest
# ---------------------------------------------------------------------------

def _selvtest() -> int:
    """Bygger en database fra bunnen, mater den med kjente rader, og sjekker
    at rullupen arkiverer det den skal og rotasjonen sletter det den skal.

    Ingen nett, ingen ekte historikk. Kjører mot en midlertidig fil som
    slettes etterpå - selvtesten skal aldri kunne røre `historikk.db`.
    """
    import tempfile

    global DB_PATH, BEHOLD_DAGER
    ekte_sti, ekte_behold = DB_PATH, BEHOLD_DAGER
    mappe = tempfile.mkdtemp(prefix="togkart-vedlikehold-")
    DB_PATH = os.path.join(mappe, "test.db")

    import historikk
    ekte_historikk_sti = historikk.DB_PATH
    historikk.DB_PATH = DB_PATH

    feil = 0

    def sjekk(navn: str, faktisk, ventet) -> None:
        nonlocal feil
        if faktisk == ventet:
            print(f"  OK    {navn}")
        else:
            print(f"  FEIL  {navn}: fikk {faktisk!r}, ventet {ventet!r}")
            feil += 1

    try:
        with kobling() as con:
            con.executescript(historikk._SCHEMA)
            con.executescript(_SCHEMA)

        i_dag = _i_dag_lokalt()

        def skriv(dag: date, tog: str, operator: str, linje: str,
                  avvik: float, time: int = 8, stale: int = 0,
                  delay_none: bool = False) -> None:
            naar = datetime(dag.year, dag.month, dag.day, time, 0, tzinfo=OSLO)
            with kobling() as con:
                con.execute(
                    """INSERT INTO observasjoner
                       (tog_id, tidspunkt, linje, tognummer, linjenavn,
                        operator, lat, lon, delay, band, delay_source,
                        position_method, computed, stale)
                       VALUES (?, ?, ?, '1', 'Testlinja', ?, 59.9, 10.7, ?,
                               'grønn', 'journey-planner', 'track', 0, ?)""",
                    (tog, naar.astimezone(timezone.utc).isoformat(), linje,
                     operator, None if delay_none else avvik, stale),
                )

        # Fire døgn: to gamle nok til å slettes, ett ferskt, og dagen i dag.
        gammel_a = i_dag - timedelta(days=120)
        gammel_b = i_dag - timedelta(days=119)
        fersk = i_dag - timedelta(days=2)

        # gammel_a: to tog i rute, ett ikke. Terskelen er 240 sekunder.
        skriv(gammel_a, "L1:101", "VYG", "L1", 60.0)
        skriv(gammel_a, "L1:102", "VYG", "L1", 120.0)
        skriv(gammel_a, "L1:103", "VYG", "L1", 900.0)
        # Én rad som ikke skal telle: stale.
        skriv(gammel_a, "L1:104", "VYG", "L1", 30.0, stale=1)
        # Én rad som ikke skal telle: uten avvik.
        skriv(gammel_a, "L1:105", "VYG", "L1", 0.0, delay_none=True)
        # En annen operatør samme døgn -> egen rad i 'dogn'.
        skriv(gammel_a, "F1:201", "FLT", "F1", 30.0)

        skriv(gammel_b, "L1:106", "VYG", "L1", 100.0)
        skriv(fersk, "L1:107", "VYG", "L1", 100.0)
        skriv(i_dag, "L1:108", "VYG", "L1", 100.0)

        print("\nvedlikehold.py --selvtest\n")
        print("Rullup")
        res = vedlikehold()
        with kobling(skrivbar=False) as con:
            con.row_factory = sqlite3.Row
            rader = con.execute("SELECT * FROM dogn ORDER BY dato, operator").fetchall()

        sjekk("fire rader i arkivet (tre døgn, to operatører)", len(rader), 4)

        vy_a = next(r for r in rader
                    if r["dato"] == gammel_a.isoformat() and r["operator"] == "VYG")
        sjekk("stale og delay=NULL holdes utenfor", vy_a["turer"], 3)
        sjekk("to av tre turer i rute", vy_a["turer_i_rute"], 2)
        sjekk("medianen er den midterste turen", vy_a["median_avvik_sek"], 120)
        sjekk("verste tur er med", vy_a["verst_avvik_sek"], 900)
        sjekk("morgenrush fanget klokka 08", vy_a["turer_morgen"], 3)
        sjekk("ettermiddagsrush tomt", vy_a["turer_ettermiddag"], 0)
        sjekk("terskelen er skrevet inn i raden",
              vy_a["terskel_sek"], TERSKEL_SEKUNDER)

        datoer = {r["dato"] for r in rader}
        sjekk("dagen i dag er IKKE rullet opp", i_dag.isoformat() in datoer, False)
        sjekk("det ferske døgnet er rullet opp", fersk.isoformat() in datoer, True)

        print("\nRotasjon")
        # Seks rader på gammel_a og én på gammel_b. Alle sju er eldre enn 90
        # dager, også de to som ikke ga en togtur: `stale` og `delay IS NULL`
        # holdes utenfor STATISTIKKEN, men de er fortsatt rader på disk, og
        # rotasjonen er en diskjobb.
        sjekk("gamle rader slettet", res["rotasjon"]["slettet"], 7)
        sjekk("rotasjonen var ikke sperret", res["rotasjon"]["sperret"], False)
        with kobling(skrivbar=False) as con:
            igjen = con.execute("SELECT COUNT(*) FROM observasjoner").fetchone()[0]
            arkiv = con.execute("SELECT COUNT(*) FROM dogn").fetchone()[0]
        sjekk("de to ferske radene står igjen", igjen, 2)
        sjekk("arkivet overlevde slettingen", arkiv, 4)

        print("\nIdempotens")
        res3 = vedlikehold()
        sjekk("andre kjøring ruller bare overlappsdøgnene",
              res3["rullup"]["dogn"] <= OVERLAPP_DOGN, True)
        sjekk("og sletter ingenting mer", res3["rotasjon"]["slettet"], 0)
        with kobling(skrivbar=False) as con:
            etter = con.execute("SELECT COUNT(*) FROM dogn").fetchone()[0]
        sjekk("ingen duplikater i arkivet", etter, 4)

        print("\nUtelatte døgn")
        # Et døgn som er utelatt skal aldri komme tilbake - heller ikke via
        # OVERLAPP_DOGN, som tvinger fram ny utregning av de nyeste døgnene
        # selv når de allerede står i `dogn_rullet`. Det var nettopp den
        # mekanismen som ville resurrektert 19.-21. august etter at de ble
        # fjernet fra arkivet 22. august.
        with kobling() as con:
            datoer = [r[0] for r in con.execute(
                "SELECT DISTINCT dato FROM dogn ORDER BY dato DESC")]
        offer = datoer[0] if datoer else None
        if offer is None:
            sjekk("hadde et døgn å utelate", False, True)
        else:
            with kobling() as con:
                for_ = con.execute("SELECT COUNT(*) FROM dogn").fetchone()[0]
                utelat_dogn(con, offer, "selvtest")
                con.commit()
                etter_utelat = con.execute("SELECT COUNT(*) FROM dogn").fetchone()[0]
            sjekk("utelatelsen fjernet døgnet fra arkivet", etter_utelat < for_, True)

            vedlikehold()
            with kobling(skrivbar=False) as con:
                kom_tilbake = con.execute(
                    "SELECT COUNT(*) FROM dogn WHERE dato = ?", (offer,)
                ).fetchone()[0]
                registrert = con.execute(
                    "SELECT grunn FROM dogn_utelatt WHERE dato = ?", (offer,)
                ).fetchone()
                raa = con.execute(
                    "SELECT COUNT(*) FROM observasjoner WHERE tidspunkt LIKE ?",
                    (f"{offer}%",),
                ).fetchone()[0]
            sjekk("og døgnet kom ikke tilbake ved neste kjøring", kom_tilbake, 0)
            sjekk("begrunnelsen står i basen", bool(registrert and registrert[0]), True)
            # Rådata røres ikke: de er fortsatt det beste grunnlaget hvis noen
            # vil ettergå avgjørelsen, og de forsvinner av seg selv.
            sjekk("rådataene for døgnet står urørt", raa > 0, True)

        # Denne står sist fordi den river ned arkivet med vilje.
        print("\nSperren")
        skriv(gammel_a, "L1:109", "VYG", "L1", 100.0)
        with kobling() as con:
            con.execute("DELETE FROM dogn")
            con.execute("DELETE FROM dogn_rullet")
            res2 = roter(con)
        sjekk("sletter ikke uten arkiv", res2["slettet"], 0)
        sjekk("og sier fra at den er sperret", res2["sperret"], True)
        with kobling(skrivbar=False) as con:
            overlevde = con.execute(
                "SELECT COUNT(*) FROM observasjoner").fetchone()[0]
        sjekk("den gamle raden står urørt", overlevde, 3)

        print("\nDøgngrenser")
        # Sommertid: 29. mars 2026 er ett 23-timers døgn i Norge.
        fra, til = _dogn_grenser(date(2026, 3, 29))
        timer = (datetime.fromisoformat(til) - datetime.fromisoformat(fra)).seconds / 3600
        sjekk("sommertidsdøgnet er 23 timer", timer, 23.0)
        fra, til = _dogn_grenser(date(2026, 10, 25))
        timer = (datetime.fromisoformat(til) - datetime.fromisoformat(fra)).total_seconds() / 3600
        sjekk("vintertidsdøgnet er 25 timer", timer, 25.0)

        print()
        if feil:
            print(f"{feil} feil.\n")
        else:
            print("Alt stemmer.\n")
        return 1 if feil else 0

    finally:
        DB_PATH, BEHOLD_DAGER = ekte_sti, ekte_behold
        historikk.DB_PATH = ekte_historikk_sti
        import shutil
        shutil.rmtree(mappe, ignore_errors=True)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    p = argparse.ArgumentParser(
        description="Rullup og rotasjon for historikk.db.",
    )
    p.add_argument("--status", action="store_true",
                   help="vis hva som ligger i basen, uten å endre noe")
    p.add_argument("--torrkjor", action="store_true",
                   help="vis hva som ville skjedd, uten å endre noe")
    p.add_argument("--selvtest", action="store_true",
                   help="test rullup og rotasjon mot en midlertidig database")
    args = p.parse_args()

    if args.selvtest:
        return _selvtest()

    if not os.path.exists(DB_PATH):
        print(f"Fant ingen database på '{DB_PATH}'. Start appen først, "
              f"eller sett HISTORIKK_DB.")
        return 1

    if args.status:
        _skriv_status(status())
        return 0

    _skriv_resultat(vedlikehold(torrkjor=args.torrkjor))
    if not args.torrkjor:
        _skriv_status(status())
    return 0


if __name__ == "__main__":
    sys.exit(main())
