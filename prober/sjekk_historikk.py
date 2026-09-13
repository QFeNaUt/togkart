"""Verifiser at historikken tåler å stå i drift i månedsvis.

Kjør:  python prober/sjekk_historikk.py     (uten nett, leser historikk.db)

To ting kan gjøre at appen slutter å virke etter noen måneder på M720Q-en, og
ingen av dem gir en feilmelding den dagen de oppstår:

  1. **Låsing.** En skrivetråd og flere lesetråder deler én SQLite-fil. I
     standardmodus (`journal_mode=delete`) sperrer en skriver alle lesere
     mens den holder på, og en leser som møter låsen feiler UMIDDELBART -
     `busy_timeout` er null som standard. Utfallet er «database is locked»,
     og det kommer i den enden som leser: /api/statistikk svarer 503 mens
     historikken skrives.
  2. **Vekst.** 20 000 rader i døgnet uten sletting er 1,7 GB i året. Lenge
     før disken tar slutt blir `/api/statistikk` treg, og den lesingen kan
     hvem som helst be om.

Proben sjekker at begge er håndtert, og - viktigst - at rotasjonen aldri kan
slette noe arkivet ikke har tatt vare på. Den siste sjekken er den som
skiller en rotasjonsjobb fra et datatap.

Sjekk 5 er den eneste her som gjør noe: den åpner en skriver og en leser
samtidig og ber dem jobbe. Alt annet leser.
"""

import os
import sqlite3
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vedlikehold
from analyse import OSLO, TERSKEL_SEKUNDER
from historikk import DB_PATH, LAAS_TIMEOUT_SEK, kobling

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FEIL = 0


def melding(ok: bool | None, tekst: str, detalj: str = "") -> None:
    """`ok=None` er en observasjon, ikke en dom - noen ting kan ikke feile,
    bare være verdt å vite."""
    global FEIL
    merke = {True: "OK  ", False: "FEIL", None: "    "}[ok]
    print(f"  {merke}  {tekst}")
    if detalj:
        for linje in detalj.split("\n"):
            print(f"        {linje}")
    if ok is False:
        FEIL += 1


def _mb(bytes_: int) -> str:
    return f"{bytes_ / 1_048_576:.1f} MB"


# ---------------------------------------------------------------------------

def sjekk_1_wal() -> None:
    """Innstillingene som avgjør om lesere og skrivere kan dele filen."""
    print("\n1. Journalmodus og låsing")

    with kobling(skrivbar=False) as con:
        modus = str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        timeout = con.execute("PRAGMA busy_timeout").fetchone()[0]

    melding(
        modus == "wal",
        f"journal_mode = {modus}",
        "" if modus == "wal" else
        "I `delete` sperrer en skriver alle lesere. Ligger databasen på et\n"
        "nettverksdrev? Da faller SQLite stille tilbake hit.",
    )
    melding(
        timeout >= 1000,
        f"busy_timeout = {timeout} ms på en LESETILKOBLING",
        "" if timeout >= 1000 else
        "Null betyr at en leser gir opp i samme øyeblikk den møter en lås.\n"
        "Dette settes per TILKOBLING, ikke per fil - derfor må alle gå\n"
        "gjennom historikk.kobling().",
    )

    with kobling() as con:
        sync = con.execute("PRAGMA synchronous").fetchone()[0]
    melding(
        sync in (1, 2),
        f"synchronous = {sync} (1 = NORMAL, 2 = FULL)",
        "NORMAL er trygt sammen med WAL og sparer en fsync per transaksjon."
        if sync == 1 else "",
    )


def sjekk_2_vekst() -> None:
    """Hvor fort filen vokser, og hvor den lander med rotasjon."""
    print("\n2. Vekst og tak")

    s = vedlikehold.status()
    raa = s["raa"]

    melding(None, f"Filen er {_mb(s['bytes'])}")

    if not raa["rader"]:
        melding(None, "Ingen råobservasjoner ennå - start appen og vent litt.")
        return

    per_dogn = raa["raderPerDogn"]
    bytes_per_rad = s["bytes"] / raa["rader"]
    i_aaret = per_dogn * 365 * bytes_per_rad
    i_likevekt = i_aaret * s["beholdDager"] / 365

    melding(
        None,
        f"{raa['rader']:,} rader over {raa['dogn']} døgn"
        .replace(",", " "),
        f"{per_dogn:,} rader i døgnet, {bytes_per_rad:.0f} byte per rad"
        .replace(",", " "),
    )
    melding(
        None,
        f"Uten rotasjon: {_mb(i_aaret)} i året",
        f"Med rotasjon på {s['beholdDager']} dager: {_mb(i_likevekt)} i likevekt",
    )

    melding(
        s["autoVacuum"] == "INCREMENTAL",
        f"auto_vacuum = {s['autoVacuum']}",
        "" if s["autoVacuum"] == "INCREMENTAL" else
        "Med NONE frigjør sletting sider inne i filen, men filen krymper\n"
        "aldri. Kjør `python vedlikehold.py` - den konverterer én gang.",
    )

    melding(
        vedlikehold.BEHOLD_DAGER >= 90,
        f"Beholder {vedlikehold.BEHOLD_DAGER} dager, taket på `dager` er 90",
        "" if vedlikehold.BEHOLD_DAGER >= 90 else
        "Spørringer med dager > BEHOLD_DAGER svarer med færre døgn enn de\n"
        "ber om, uten å si fra. Senk MAKS_DAGER i app.py, eller hev denne.",
    )


def sjekk_3_arkiv() -> None:
    """Er hvert ferdig døgn arkivert, og henger arkivet sammen?"""
    print("\n3. Arkivet")

    with kobling(skrivbar=False) as con:
        finnes = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dogn'"
        ).fetchone()
        if not finnes:
            melding(False, "Tabellen `dogn` finnes ikke",
                    "Kjør `python vedlikehold.py` én gang.")
            return

        con.row_factory = sqlite3.Row
        rullet = [r[0] for r in con.execute(
            "SELECT dato FROM dogn_rullet ORDER BY dato")]
        raa_forste, raa_siste = vedlikehold._dogn_i_basen(con)
        arkiv = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(turer), 0), COALESCE(SUM(turer_i_rute), 0) "
            "FROM dogn"
        ).fetchone()

    if not rullet:
        melding(None, "Ingenting arkivert ennå.",
                "Normalt på en fersk database - rullupen tar bare FERDIGE døgn,\n"
                "så det første kommer etter midnatt.")
        return

    melding(None, f"{arkiv[0]} rader i `dogn`, {arkiv[1]} togturer arkivert")
    melding(
        None,
        f"Arkivet dekker {rullet[0]} til {rullet[-1]} ({len(rullet)} døgn)",
        f"Andel i rute over hele perioden: {arkiv[2] / arkiv[1]:.1%}"
        if arkiv[1] else "",
    )

    # Hull i loggen: en dato mellom første og siste som aldri ble behandlet.
    # Et hull er ikke nødvendigvis galt - serveren kan ha stått - men det er
    # verdt å se, for et døgn som ALDRI rulles opp er et døgn rotasjonen
    # senere sletter uten arkiv.
    d = date.fromisoformat(rullet[0])
    slutt = date.fromisoformat(rullet[-1])
    kjent = set(rullet)
    hull = []
    while d <= slutt:
        if d.isoformat() not in kjent:
            hull.append(d.isoformat())
        d += timedelta(days=1)

    melding(
        not hull,
        f"{len(hull)} hull i rullup-loggen",
        ", ".join(hull[:8]) + (" ..." if len(hull) > 8 else "") if hull else "",
    )

    # Ferdige døgn som ennå ikke er rullet opp.
    i_dag = datetime.now(timezone.utc).astimezone(OSLO).date()
    ventende = []
    if raa_forste:
        d = max(raa_forste, date.fromisoformat(rullet[-1]) + timedelta(days=1))
        while d < i_dag:
            if d.isoformat() not in kjent:
                ventende.append(d.isoformat())
            d += timedelta(days=1)

    melding(
        not ventende,
        f"{len(ventende)} ferdige døgn venter på rullup",
        ", ".join(ventende[:8]) if ventende else
        "Dagens døgn rulles med vilje ikke - det er ikke ferdig.",
    )


def sjekk_4_sperren() -> None:
    """Rotasjonen skal aldri kunne slette et døgn arkivet ikke kjenner.

    Dette er den viktigste sjekken i fila. Alt annet her handler om ytelse;
    denne handler om data som ikke kan gjenskapes.
    """
    print("\n4. Rotasjonssperren")

    grense_dag = (datetime.now(timezone.utc).astimezone(OSLO).date()
                  - timedelta(days=vedlikehold.BEHOLD_DAGER))
    grense_iso, _ = vedlikehold._dogn_grenser(grense_dag)

    with kobling(skrivbar=False) as con:
        gamle = con.execute(
            "SELECT COUNT(*) FROM observasjoner WHERE tidspunkt < ?", (grense_iso,)
        ).fetchone()[0]
        nyeste = con.execute("SELECT MAX(dato) FROM dogn_rullet").fetchone()
        nyeste = nyeste[0] if nyeste else None

    melding(
        None,
        f"Grensen går ved {grense_dag}, {gamle} rader er eldre",
    )

    if not gamle:
        melding(True, "Ingenting å slette - sperren er ikke i spill ennå")
        return

    trygt = bool(nyeste) and nyeste >= grense_dag.isoformat()
    melding(
        trygt,
        f"Arkivet står på {nyeste or 'ingenting'}",
        "Rotasjonen vil slette, og arkivet har tatt igjen." if trygt else
        "Rotasjonen NEKTER å slette til arkivet har tatt igjen. Det er\n"
        "riktig oppførsel, men det betyr at rullupen henger etter - kjør\n"
        "`python vedlikehold.py` og se etter feil i loggen.",
    )
    # Sperren er ikke en feil uansett hvilken vei den slår: den beskytter.
    # Vi teller den derfor ikke som FEIL når den holder igjen.
    global FEIL
    if not trygt:
        FEIL -= 1


def sjekk_5_samtidighet() -> None:
    """Skriv og les samtidig, og se om noen møter «database is locked».

    Den eneste sjekken her som endrer noe. Den skriver til en EGEN tabell -
    `sjekk_samtidighet` - og slipper den etterpå, slik at `observasjoner`
    aldri får en rad som ikke kom fra et ekte tog. En prøvebelastning som
    forurenser målingene den skal beskytte, er ikke en prøve.

    Denne lasten er verifisert å kunne feile, og det er poenget med å ha
    den. Målt 21. august på en kopi av den ekte databasen, samme fire
    tråder og samme fyrti skrivinger:

        journal_mode=delete, busy_timeout=0   ->  "database is locked"
        journal_mode=wal,    busy_timeout=5s  ->  40 rader, 0,6 s, ingen feil

    Uten den første linja ville denne sjekken vært et grønt merke som ikke
    kunne bli rødt.
    """
    print("\n5. Skriving og lesing samtidig")

    if not os.path.exists(DB_PATH):
        melding(None, "Ingen database - hopper over")
        return

    RUNDER = 40
    feilet: list[str] = []
    ferdig = threading.Event()

    def skriver() -> None:
        try:
            for n in range(RUNDER):
                with kobling() as con:
                    con.execute(
                        "INSERT INTO sjekk_samtidighet (n, t) VALUES (?, ?)",
                        (n, datetime.now(timezone.utc).isoformat()),
                    )
        except Exception as exc:  # noqa: BLE001
            feilet.append(f"skriver: {exc}")
        finally:
            ferdig.set()

    def leser() -> None:
        try:
            while not ferdig.is_set():
                with kobling(skrivbar=False) as con:
                    con.execute(
                        "SELECT COUNT(*) FROM observasjoner "
                        "WHERE tidspunkt > ?",
                        ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),),
                    ).fetchone()
        except Exception as exc:  # noqa: BLE001
            feilet.append(f"leser: {exc}")

    with kobling() as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS sjekk_samtidighet (n INTEGER, t TEXT)"
        )

    start = time.monotonic()
    traader = [threading.Thread(target=skriver)] + [
        threading.Thread(target=leser) for _ in range(3)
    ]
    for t in traader:
        t.start()
    for t in traader:
        t.join(timeout=60)
    brukt = time.monotonic() - start

    with kobling() as con:
        skrevet = con.execute("SELECT COUNT(*) FROM sjekk_samtidighet").fetchone()[0]
        con.execute("DROP TABLE sjekk_samtidighet")

    melding(
        not feilet,
        f"{RUNDER} skrivinger og tre lesetråder samtidig på {brukt:.1f} s",
        "\n".join(feilet) if feilet else
        f"{skrevet} rader skrevet, ingen møtte en lås "
        f"(timeout er {LAAS_TIMEOUT_SEK:.0f} s)",
    )

    with kobling(skrivbar=False) as con:
        rest = con.execute(
            "SELECT name FROM sqlite_master WHERE name='sjekk_samtidighet'"
        ).fetchone()
    melding(rest is None, "Prøvetabellen er ryddet bort")


def main() -> int:
    print("\nSjekk av historikk.db - låsing, vekst og arkiv")
    print(f"  {os.path.abspath(DB_PATH)}")

    if not os.path.exists(DB_PATH):
        print("\nFant ingen database. Start appen først, eller sett HISTORIKK_DB.\n")
        return 1

    sjekk_1_wal()
    sjekk_2_vekst()
    sjekk_3_arkiv()
    sjekk_4_sperren()
    sjekk_5_samtidighet()

    print()
    if FEIL:
        print(f"{FEIL} funn som bør ses på.\n")
        return 1
    print("Historikken tåler å stå.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
