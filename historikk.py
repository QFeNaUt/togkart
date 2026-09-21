"""Historikk - logger hver tog-observasjon til en lokal SQLite-database.

Ett tog logges bare når posisjonen har flyttet seg mer enn TERSKEL_M meter,
eller forsinkelsen har endret seg mer enn TERSKEL_SEKUNDER sekunder, siden
forrige logging av akkurat det toget. Uten den sperren ville hver eneste
oppdatering skrevet en rad per tog på kartet - beregnede SJ-posisjoner
flytter seg litt ved hver forespørsel, siden de regnes ut på nytt fra
klokka. Med 15 sekunders oppdatering og ~100 tog i drift blir det fort
700 000 rader i døgnet skrevet for ingenting; med sperren skrives det bare
når noe faktisk endret seg.

Skriving skjer i en egen tråd (asyncio.to_thread), slik at SQLite-filens I/O
aldri blokkerer event-loopen som samtidig skal svare på /api/trains.

Den tråden er også grunnen til at `kobling()` finnes. Skrivetråden og
lesetrådene (`analyse.py`, `flaskehals.py`, `vedlikehold.py`) deler én fil, og
i SQLite sin standardmodus - `journal_mode=delete` - tar en skriver en lås som
sperrer alle lesere mens den holder på. Under last er utfallet «database is
locked», og feilen kommer i den enden som leser: /api/statistikk svarer 503
mens historikken skrives. Se `kobling()` for hva som er skrudd på i stedet.
"""

import asyncio
import contextlib
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone

from sporgeometri import avstand_m

log = logging.getLogger("togkart")

# Leses ved IMPORT, ikke ved bruk, og det er en felle verdt å kjenne.
# `app.py` kaller load_dotenv() før alt annet, og systemd setter
# EnvironmentFile i tillegg, så serveren treffer alltid riktig fil. Et verktøy
# startet for hånd gjør ingen av delene. Se `les_env()`.
DB_PATH = os.getenv("HISTORIKK_DB", "historikk.db")

# Hvor lenge en tilkobling venter på at en annen skal slippe låsen før den gir
# opp med "database is locked". Standarden i SQLite er NULL sekunder - den
# feiler med en gang - og det er den innstillingen, ikke lastet, som gjorde
# feilen uunngåelig. Fem sekunder er rikelig: den lengste skrivingen appen
# gjør er en `executemany` med rundt hundre rader.
LAAS_TIMEOUT_SEK = float(os.getenv("HISTORIKK_LAAS_TIMEOUT", "5.0"))

# Under denne avstanden regnes toget som stillestående. GPS-støy og den
# kontinuerlige fremdriften i SJ-interpolasjonen skal ikke fylle databasen.
TERSKEL_M = 50.0

# Under denne forskjellen regnes forsinkelsen som uendret. `delay` kommer fra
# Entur i SEKUNDER, samme enhet som resten av appen lagrer den i (se
# entur.py). 30 sekunder er godt under presisjonen kartet viser (tidelen av
# et minutt), men over den støyen VP og JP kan ha mellom to hentinger.
TERSKEL_SEKUNDER = 30.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observasjoner (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tog_id TEXT NOT NULL,
    tidspunkt TEXT NOT NULL,
    linje TEXT,
    tognummer TEXT,
    linjenavn TEXT,
    operator TEXT,
    lat REAL,
    lon REAL,
    delay REAL,
    band TEXT,
    delay_source TEXT,
    position_method TEXT,
    computed INTEGER NOT NULL,
    stale INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observasjoner_tog
    ON observasjoner(tog_id, tidspunkt);
-- Analysene i analyse.py leser alltid et tidsvindu bakover og grupperer på
-- operatør eller linje. Uten denne indeksen blir hver spørring en full
-- gjennomlesing av tabellen, og den vokser med ~25 000 rader i døgnet.
CREATE INDEX IF NOT EXISTS idx_observasjoner_tid
    ON observasjoner(tidspunkt);
CREATE INDEX IF NOT EXISTS idx_observasjoner_linje
    ON observasjoner(linje);
"""

# Indekser på kolonner som kom til etter at tabellen fantes. De kan ikke stå i
# _SCHEMA: det skriptet kjører FØR `_migrer()`, og på en gammel database
# eksisterer ikke kolonnen ennå - CREATE INDEX ville feilet med "no such
# column" og tatt hele oppstarten med seg.
_SENERE_INDEKSER = """
-- `_etterfyll()` spør ved hver oppstart om det finnes rader uten operatør.
-- Uten denne indeksen er det spørsmålet en full gjennomlesing hver gang, også
-- den dagen svaret er nei - og tabellen vokser med rundt 25 000 rader i
-- døgnet. Med den er det et indeksoppslag.
CREATE INDEX IF NOT EXISTS idx_observasjoner_operator
    ON observasjoner(operator);
"""

# Kolonner lagt til etter at databasen kom i drift. SQLite har ingen
# "ADD COLUMN IF NOT EXISTS", så vi spør PRAGMA-en hva som allerede finnes.
# Rekkefølgen spiller ingen rolle - ALTER TABLE legger dem bakerst uansett,
# og all lesing skjer på navn.
_TILLEGGSKOLONNER = {
    "linjenavn": "TEXT",
    "operator": "TEXT",
}

# Siste loggede (lat, lon, delay, sett) per tog. Holdt i minnet, ikke slått opp
# i databasen ved hver henting - vi husker forrige verdi selv i stedet for å
# spørre etter den. Nullstilles ved omstart av serveren, så første snapshot
# etter en omstart logger alle aktive tog på nytt. Det er riktig oppførsel:
# databasen skal vite at prosessen startet på nytt, ikke late som om den
# kjente togenes historie fra før.
#
# `sett` er `time.monotonic()` fra sist toget var med i et snapshot, og den
# kom til 14. september. Til da ble ingenting fjernet fra denne dicten, og det
# gikk bra så lenge prosessen ble startet om stadig vekk under utvikling. Med
# en bakgrunnsjobb som poller døgnet rundt er det en lekkasje: rundt 1 200 nye
# tog-ID-er i døgnet som aldri slippes.
_siste: dict[str, tuple[float, float, float | None, float]] = {}

# Hvor lenge et tog huskes etter at det sist var i et snapshot.
#
# En time er rundhåndet med vilje. Kostnaden ved å glemme for tidlig er
# nøyaktig én ekstra rad - toget dukker opp som ukjent og logges på nytt - og
# et fjerntog kan ha lange GPS-hull uten å være ferdig. Kostnaden ved å glemme
# for sent er noen kilobyte.
GLEMSEL_SEKUNDER = float(os.getenv("HISTORIKK_GLEMSEL_SEKUNDER", "3600"))

# Ryddingen går gjennom hele dicten, så den gjøres sjelden og ikke ved hvert
# snapshot.
RYDDE_INTERVALL = 600.0
_sist_ryddet = 0.0


def _rydd_siste(naa: float) -> int:
    """Slipp tog vi ikke har sett på en stund. Returnerer antall fjernet."""
    global _sist_ryddet
    if naa - _sist_ryddet < RYDDE_INTERVALL:
        return 0
    _sist_ryddet = naa

    doede = [t for t, v in _siste.items() if naa - v[3] > GLEMSEL_SEKUNDER]
    for tog_id in doede:
        del _siste[tog_id]
    if doede:
        log.debug("Glemte %d tog som ikke er sett på %.0f minutter (%d igjen)",
                  len(doede), GLEMSEL_SEKUNDER / 60, len(_siste))
    return len(doede)


# Alle NeTEx-ID-er fra Entur starter med kodeområdet til den som eide dataene:
# "VYG:ServiceJourney:...", "SJN:ServiceJourney:...". Det er den eneste
# operatørmerkingen som følger med helt ut i /api/trains-payloaden, og den er
# stabil på tvers av de to kildene - Vehicle Positions og Journey Planner
# bruker samme kodeområder.
KODESPAKE_BEREGNET = "SJN"

# Grensen for slutningen «beregnet posisjon betyr SJ». Den holdt så lenge
# sjnord.py bare regnet for operatører uten GPS i det hele tatt. Fra kvelden
# 21. august beregnes også turer fra Vy, Flytoget og Go-Ahead som mangler
# posisjon, og da er kodeområdet noe som må leses av tur-ID-en - ikke utledes
# av at raden er beregnet.
#
# Grensen står på starten av den dagen og ikke på klokkeslettet endringen kom.
# Det er den trygge retningen: rader skrevet i dag rører etterfyllingen ikke,
# og de trenger den heller ikke - `_operator()` leser kodeområdet av tur-ID-en
# ved skriving, så en beregnet rad er aldri uten operatør til å begynne med.
# Se `_etterfyll`.
BEREGNET_BARE_SJN_FOR = "2026-08-21"


def les_env() -> None:
    """Last `.env` og utled `DB_PATH` på nytt. For verktøy kjørt fra kommandolinja.

    `DB_PATH` bindes ved import, og de fire verktøyene som leser historikken -
    `vedlikehold.py`, `analyse.py`, `flaskehals.py` og
    `prober/sjekk_historikk.py` - importerer den før noen har lest `.env`. De
    tok derfor standardverdien `historikk.db` i prosjektroten i stedet for
    `HISTORIKK_DB`, og rapporterte trygt om en tom fil uten å si fra at det var
    det de gjorde. Målt på serveren 21. september: `vedlikehold.py` meldte
    `dogn: 0` mot en base som i virkeligheten hadde 708 576 rader.

    Kalles fra `__main__`, ikke ved import: en modul brukt som bibliotek skal
    ikke ha bivirkninger, og `app.py` har sin egen `load_dotenv()`.

    **Kallerne må hente verdien tilbake etterpå** - `DB_PATH =
    historikk.DB_PATH` - fordi `from historikk import DB_PATH` lager en kopi
    som ikke følger med når denne endrer originalen. Det er nettopp den kopien
    som var feilen.
    """
    global DB_PATH
    from dotenv import load_dotenv

    load_dotenv()
    DB_PATH = os.getenv("HISTORIKK_DB", "historikk.db")


# ---------------------------------------------------------------------------
# Tilkobling
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def kobling(sti: str = "", *, skrivbar: bool = True):
    """Én tilkobling til historikken, med de innstillingene alle skal ha.

    ALLE som åpner historikk.db skal gå gjennom denne - `analyse.py`,
    `flaskehals.py`, `vedlikehold.py` og skrivetråden her. Det er ikke en
    ryddesak; det er hele poenget. Tre av de fire innstillingene under er
    per TILKOBLING og ikke per fil, så en leser som åpner databasen selv får
    standardverdiene igjen uansett hva noen andre satte.

    Fire ting settes:

    - **`journal_mode=WAL`.** Den ene som er per FIL og overlever omstart:
      SQLite skriver moden inn i filhodet. Den er likevel satt her, ikke i
      en engangsmigrasjon, fordi en ny database - en fersk container, en
      backup lagt tilbake - skal få den uten at noen husket å kjøre noe.
      Med WAL skriver skriveren til en sidelogg mens leserne fortsetter å
      lese den gamle filen. Lesere blokkerer ikke skriveren, og skriveren
      blokkerer ikke leserne. Det er akkurat den samtidigheten appen har:
      én skrivetråd fra /api/trains, flere lesetråder fra /api/statistikk.

    - **`busy_timeout`.** Én skriver om gangen er fortsatt regelen, også med
      WAL - vedlikeholdsjobben og snapshot-skrivingen kan kollidere. Uten en
      timeout gir SQLite opp umiddelbart; med den venter den.

    - **`synchronous=NORMAL`.** Standarden `FULL` tvinger en fsync per
      transaksjon. Med WAL er NORMAL trygt mot programkrasj og mot at
      appen dør - det som kan gå tapt er de siste transaksjonene ved
      STRØMBRUDD på maskinen. For posisjonslogging er det en akseptabel
      pris: taper vi de siste sekundene med togprikker, mangler det noen
      rader i en median. Databasen blir ikke korrupt.

    - **`foreign_keys`.** Ingen fremmednøkler i skjemaet i dag, men det er
      gratis å ha på, og default-av er en felle den dagen noen legger til en.

    `skrivbar=False` åpner filen skrivebeskyttet gjennom en URI. Da kan en
    analyse ikke ved et uhell endre noe, og - viktigere - den kan ikke ta en
    skrivelås ved et uhell heller. WAL-modus kan ikke settes på en
    read-only-tilkobling, så den hoppes over der; filen har allerede moden.
    """
    sti = sti or DB_PATH

    if skrivbar:
        con = sqlite3.connect(sti, timeout=LAAS_TIMEOUT_SEK)
    else:
        # `uri=True` med mode=ro. Krever at filen finnes; det gjør den, for
        # `_init()` har kjørt ved import.
        con = sqlite3.connect(
            f"file:{sti}?mode=ro", uri=True, timeout=LAAS_TIMEOUT_SEK
        )

    try:
        con.execute(f"PRAGMA busy_timeout = {int(LAAS_TIMEOUT_SEK * 1000)}")
        if skrivbar:
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("PRAGMA synchronous = NORMAL")
            con.execute("PRAGMA foreign_keys = ON")
        yield con
        if skrivbar:
            con.commit()
    except BaseException:
        if skrivbar:
            con.rollback()
        raise
    finally:
        # `with sqlite3.connect(...)` - som denne modulen brukte før - avslutter
        # transaksjonen, men LUKKER IKKE tilkoblingen. I en tråd som kalles
        # hvert tiende sekund er det en filhåndtakslekkasje som venter på en
        # lang nok oppetid. Her lukkes den.
        con.close()


def _init() -> None:
    with kobling() as con:
        con.executescript(_SCHEMA)
        _migrer(con)
        con.executescript(_SENERE_INDEKSER)
        _etterfyll(con)

        modus = con.execute("PRAGMA journal_mode").fetchone()[0]
        if str(modus).lower() != "wal":
            # Eneste kjente grunn: databasefilen ligger på et nettverksdrev
            # (SMB, NFS) der WAL trenger delt minne som filsystemet ikke gir.
            # Da faller SQLite tilbake til `delete` uten å feile - stille, og
            # med «database is locked» som symptom en uke senere. Derfor sagt
            # høyt.
            log.warning(
                "historikk.db kjører med journal_mode=%s, ikke WAL. Ligger "
                "filen på et nettverksdrev? Da vil lesere og skrivere sperre "
                "for hverandre under last.",
                modus,
            )


def _migrer(con: sqlite3.Connection) -> None:
    """Legg til kolonner som kom etter at databasen allerede var i drift."""
    finnes = {rad[1] for rad in con.execute("PRAGMA table_info(observasjoner)")}
    for navn, type_ in _TILLEGGSKOLONNER.items():
        if navn not in finnes:
            con.execute(f"ALTER TABLE observasjoner ADD COLUMN {navn} {type_}")
            log.info("historikk.db: la til kolonnen '%s'", navn)


def _etterfyll(con: sqlite3.Connection) -> None:
    """Gi eldre rader operatør og linjenavn, uten å gjette.

    Radene skrevet før kolonnene fantes vet bare hvilken linje de tilhørte.
    Operatøren og strekningsnavnet må derfor utledes, og det finnes to måter
    å gjøre det på uten å finne på noe:

    1. `computed = 1` betyr at posisjonen ble regnet ut i sjnord.py. Fram til
       21. august slapp bare SJN inn dit, så for de radene er kodeområdet ikke
       en gjetning - det er hvordan raden ble til. Fra og med den kvelden
       beregnes også turer fra operatører som normalt har GPS, og da holder
       slutningen ikke lenger. Derfor datogrensa: den gjør regelen sann for nøyaktig de
       radene den ble skrevet for. Uten den ville et beregnet Vy-tog fått
       «SJN» stemplet på seg av en kommentar som en gang var riktig.
    2. For resten lar vi databasen lære av seg selv: har en linje fått
       operatør eller navn på en nyere rad, gjelder de samme verdiene for de
       eldre radene på den linja.

    Slutning 2 forutsetter at en linje kjøres av én operatør under ett navn.
    Det stemmer i Norge - linjene er tildelt i trafikkpakker - men bytter en
    pakke eier, får gamle rader den nye eierens kode. Derfor gjelder
    etterfyllingen bare rader som MANGLER verdien; en rad som fikk
    kodeområdet sitt ved skrivetidspunktet blir aldri rørt.

    Alternativet var en håndskrevet tabell over hvem som kjører hvilken
    linje. Den ville vært en påstand vi måtte vedlikeholde; dette er en
    observasjon appen allerede har gjort.

    Kjører ved hver oppstart. Første gang gjør den jobben, senere treffer
    WHERE-en ingenting.
    """
    con.execute(
        """UPDATE observasjoner SET operator = ?
           WHERE (operator IS NULL OR operator = '')
             AND computed = 1
             AND tidspunkt < ?""",
        (KODESPAKE_BEREGNET, BEREGNET_BARE_SJN_FOR),
    )

    for kolonne in ("operator", "linjenavn"):
        mangler = f"({kolonne} IS NULL OR {kolonne} = '')"
        telling = f"SELECT COUNT(*) FROM observasjoner WHERE {mangler}"

        før = con.execute(telling).fetchone()[0]
        if not før:
            continue

        # rowcount duger ikke som fasit her: UPDATE-en treffer alle radene som
        # mangler verdien, også de der underspørringen ikke fant noen å arve
        # fra og setter NULL på nytt. Skulle vi logget rowcount, ville
        # oppstarten meldt at den fylte 7 607 rader og etterlatt 7 607 tomme.
        # Vi teller derfor før og etter og rapporterer differansen.
        con.execute(
            f"""UPDATE observasjoner SET {kolonne} = (
                   SELECT kjent.{kolonne} FROM observasjoner AS kjent
                   WHERE kjent.linje = observasjoner.linje
                     AND kjent.{kolonne} IS NOT NULL AND kjent.{kolonne} <> ''
                   LIMIT 1
               )
               WHERE {mangler} AND linje <> ''"""
        )
        etter = con.execute(telling).fetchone()[0]

        if før != etter:
            log.info(
                "historikk.db: fylte '%s' på %d eldre rader (%d står igjen)",
                kolonne, før - etter, etter,
            )


_init()


def utelatte_dogn(con: sqlite3.Connection) -> set[str]:
    """Lokale datoer som er erklært utroverdige og ikke skal brukes til noe.

    Tabellen fylles av `vedlikehold.utelat_dogn()` og bærer begrunnelsen sin
    selv. Leseren bor HER og ikke i vedlikehold.py fordi utelatelsen ikke bare
    gjelder arkivet: et døgn vi ikke stoler på skal ikke telle i
    operatørrangeringen eller i flaskehalskartet heller. Sto den bare i
    rullupen, ville et forkastet døgn likevel farget kartet i nitti dager til
    - helt til rådataene roterte ut av seg selv.

    Tom mengde for en base som ikke har tabellen ennå.
    """
    finnes = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='dogn_utelatt'"
    ).fetchone()
    if not finnes:
        return set()
    return {rad[0] for rad in con.execute("SELECT dato FROM dogn_utelatt")}


def _operator(properties: dict) -> str:
    """Kodeområdet ("VYG", "FLT", "GOA", "SJN"), eller tom streng.

    `journeyRef` først: den er alltid en ren tur-ID. `id` er reserven, og den
    er tur-ID-en for de beregnede SJ-togene - men kjøretøy-ID for de målte, og
    for dobbeltsett er den to kjøretøy-ID-er limt sammen med kolon ("73-04:
    73-16"). Derfor kravet om at leddet foran kolonet er rene bokstaver: det
    skiller et kodeområde fra et settnummer.
    """
    for felt in ("journeyRef", "id"):
        verdi = str(properties.get(felt) or "")
        hode = verdi.split(":", 1)[0] if ":" in verdi else ""
        if hode.isalpha():
            return hode.upper()
    return ""


def _tog_id(properties: dict) -> str:
    """Samme nøkkel som `_nokkel()` i app.py, flatet til én tekststreng.

    (linje, tognummer) er stabilt over hele driftsdagen, på tvers av VP og
    JP. Faller tognummer bort - skjer for enkelte spøkelser og feilregistrerte
    turer - brukes kjøretøy-/tur-ID-en i stedet, så raden ikke går tapt.
    """
    line = str(properties.get("line") or "").rsplit(":", 1)[-1]
    number = str(properties.get("trainNumber") or "")
    return f"{line}:{number}" if number else str(properties.get("id") or "")


def _skal_logges(tog_id: str, lat: float, lon: float, delay: float | None) -> bool:
    forrige = _siste.get(tog_id)
    if forrige is None:
        return True

    forrige_lat, forrige_lon, forrige_delay, _sett = forrige
    if avstand_m((forrige_lon, forrige_lat), (lon, lat)) > TERSKEL_M:
        return True

    if (delay is None) != (forrige_delay is None):
        return True
    if delay is not None and forrige_delay is not None:
        if abs(delay - forrige_delay) > TERSKEL_SEKUNDER:
            return True

    return False


def _skriv(rader: list[tuple]) -> None:
    with kobling() as con:
        con.executemany(
            """INSERT INTO observasjoner
               (tog_id, tidspunkt, linje, tognummer, linjenavn, operator,
                lat, lon, delay, band, delay_source, position_method,
                computed, stale)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rader,
        )


async def logg_snapshot(features: list[dict]) -> int:
    """Logg endrede tog fra én ferdigbygget /api/trains-payload.

    Kalles fra app.py etter at et snapshot er satt sammen. Feiler skrivingen,
    logges det som en advarsel - historikk er et tillegg, ikke noe kartet skal
    stoppe opp for. Returnerer antall rader skrevet, mest til bruk i tester.
    """
    now = datetime.now(timezone.utc).isoformat()
    naa = time.monotonic()
    rader = []

    for feature in features:
        p = feature["properties"]
        lon, lat = feature["geometry"]["coordinates"]
        tog_id = _tog_id(p)
        delay = p.get("delay")

        if not _skal_logges(tog_id, lat, lon, delay):
            # Toget er der, men har ikke flyttet seg nok til å bli en rad.
            # Tidsstempelet skal likevel oppdateres: «sett» betyr at toget var
            # med i et snapshot, ikke at det ble logget. Uten dette ville et
            # tog som står stille på en perrong i en time blitt glemt av
            # `_rydd_siste` og logget på nytt idet det rullet videre.
            forrige = _siste.get(tog_id)
            if forrige is not None:
                _siste[tog_id] = (forrige[0], forrige[1], forrige[2], naa)
            continue

        _siste[tog_id] = (lat, lon, delay, naa)
        rader.append((
            tog_id, now, p.get("line") or "", p.get("trainNumber") or "",
            p.get("lineName") or "", _operator(p),
            lat, lon, delay, p.get("band"), p.get("delaySource"),
            p.get("positionMethod"), 1 if p.get("computed") else 0,
            1 if p.get("stale") else 0,
        ))

    # Før den tidlige returen: i en stille periode skrives ingen rader, og da
    # ville ryddingen aldri kommet til.
    _rydd_siste(naa)

    if not rader:
        return 0

    try:
        await asyncio.to_thread(_skriv, rader)
    except Exception as exc:  # noqa: BLE001 - aldri velte kartet for dette
        log.warning("Kunne ikke skrive historikk: %s", exc)
        return 0

    return len(rader)


# ---------------------------------------------------------------------------
# Selvtest
# ---------------------------------------------------------------------------
# Bare minnelogikken: hva som skal logges, og hva som skal glemmes. Ingenting
# her rører databasen, så den kan kjøres i CI uten en `historikk.db`.
#
#     python historikk.py --selvtest


def _selvtest() -> int:
    global _siste, _sist_ryddet
    feil = 0

    def sjekk(hva: str, faktisk, ventet):
        nonlocal feil
        ok = faktisk == ventet
        if not ok:
            feil += 1
        print(f"  {'OK  ' if ok else 'FEIL'}  {hva}"
              + ("" if ok else f"   ventet {ventet!r}, fikk {faktisk!r}"))

    def nullstill():
        global _siste, _sist_ryddet
        _siste = {}
        _sist_ryddet = 0.0

    print("1. Hva som logges")
    nullstill()
    sjekk("et ukjent tog logges", _skal_logges("T1", 59.91, 10.75, 0.0), True)
    _siste["T1"] = (59.91, 10.75, 0.0, 1000.0)
    sjekk("samme sted, samme avvik: nei",
          _skal_logges("T1", 59.91, 10.75, 0.0), False)
    # 50 m er terskelen. 0,001 grader breddegrad er ca. 111 m.
    sjekk("flyttet over 50 m: ja",
          _skal_logges("T1", 59.911, 10.75, 0.0), True)
    sjekk("avvik endret over 30 s: ja",
          _skal_logges("T1", 59.91, 10.75, 45.0), True)
    sjekk("avvik endret under 30 s: nei",
          _skal_logges("T1", 59.91, 10.75, 20.0), False)
    sjekk("avvik ble borte: ja",
          _skal_logges("T1", 59.91, 10.75, None), True)

    print("\n2. Hva som glemmes")
    nullstill()
    naa = 100_000.0
    _siste["ferskt"] = (59.9, 10.7, 0.0, naa - 60)
    _siste["gammelt"] = (59.9, 10.7, 0.0, naa - GLEMSEL_SEKUNDER - 1)
    fjernet = _rydd_siste(naa)
    sjekk("ett tog glemt", fjernet, 1)
    sjekk("det ferske står igjen", "ferskt" in _siste, True)
    sjekk("det gamle er borte", "gammelt" in _siste, False)

    print("\n3. Ryddingen går ikke ved hvert snapshot")
    nullstill()
    _siste["gammelt"] = (59.9, 10.7, 0.0, 0.0)
    _rydd_siste(100_000.0)          # setter _sist_ryddet
    _siste["gammelt2"] = (59.9, 10.7, 0.0, 0.0)
    sjekk("for tidlig: ingenting fjernet",
          _rydd_siste(100_000.0 + RYDDE_INTERVALL - 1), 0)
    sjekk("etter intervallet: fjernet",
          _rydd_siste(100_000.0 + RYDDE_INTERVALL + 1), 1)

    print("\n4. Invarianten bak glemselen")
    nullstill()
    # En glemt tog-ID skal logges på nytt neste gang den dukker opp. Det er
    # kostnaden ved å glemme, og den skal være nøyaktig én rad.
    _siste["T2"] = (59.9, 10.7, 0.0, 0.0)
    _rydd_siste(GLEMSEL_SEKUNDER + RYDDE_INTERVALL + 1)
    sjekk("glemt tog logges på nytt", _skal_logges("T2", 59.9, 10.7, 0.0), True)

    nullstill()
    print()
    if feil:
        print(f"{feil} feil.")
        return 1
    print("Alt grønt.")
    return 0


if __name__ == "__main__":
    import sys
    if "--selvtest" in sys.argv:
        sys.exit(_selvtest())
    print(__doc__)
