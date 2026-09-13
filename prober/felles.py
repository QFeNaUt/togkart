"""Det verktøyene og probene gjør likt: spørre Entur og tro på svaret.

Importeres som `from prober.felles import ...` — også fra probene selv, som
legger prosjektroten på `sys.path` først. Én stavemåte, uansett hvor du står.

Fram til 24. august fantes denne funksjonen i fem utgaver: `ask()` i
`nordtog.py`, `sanntidsjekk.py` og `sjekk_punktlighet.py`, `be_om()` i
`sjekk_dato.py` og `ask_entur()` i `sjekk.py`. De var ikke helt like, og det
er nettopp poenget med å ha dem fem steder: forskjellene var utilsiktede.

**Den ene som betydde noe: `be_om()` sjekket ikke `errors` i det hele tatt.**
Den ga hele nyttelasten videre, og førstekallet i `sjekk_dato.py` gjorde
`(payload.get("data") or {})` på den. En avvist spørring ble altså til et tomt
felt-sett, og proben konkluderte «date MANGLER» — et målt svar på et spørsmål
den aldri fikk stille. Det er den slags feil hele dette prosjektet handler om,
og den satt i koden som skulle avdekke den.

GRAPHQL SVARER 200 PÅ EN AVVIST SPØRRING. Det er hele grunnen til at denne
filen finnes. `raise_for_status()` er ikke nok; feilene ligger i `errors` i en
kropp med statuskode 200, og en kode som bare ser på statuskoden vil tro at
den fikk data.

TIDSAVBRUDDENE ER IKKE SAMLET, MED VILJE. De fem sto på 15, 25, 30, 30 og 25
sekunder, og forskjellene er reelle: `sjekk.py` stiller små spørringer mot
Vehicle Positions, mens `sanntidsjekk.py` ber Journey Planner nøste seg
gjennom en avgangstavle. Kallerne oppgir sin egen; standarden her er bare det
de fleste hadde.
"""

import os
import sys
from pathlib import Path

# Prosjektroten på sys.path. Modulen importeres normalt som `prober.felles`
# fra et sted der roten alt er der - men den kjøres også direkte
# (`python prober/felles.py --selvtest`), og da er det `prober/` som ligger
# på stien og ikke roten. Uten denne finner ikke importene under seg selv.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

# Hentet fra appen og ikke skrevet på nytt. De to sto i fire kopier på tvers
# av probene før 24. august, og en URL som finnes fire steder er fire steder å
# glemme å endre den dagen Entur flytter et endepunkt.
from entur import ENTUR_URL as VEHICLES_URL
from sjnord import JOURNEY_URL

# Geokoderen har ingen tilsvarende ett-sted i appen: `app.py` og `lagbaner.py`
# har hver sin kopi. Å samle DE to er en annen jobb enn denne saken, så her
# står den én gang til - men nå med en lapp om hvor de andre er.
GEOCODER_URL = "https://api.entur.io/geocoder/v3/autocomplete"

STANDARD_TIMEOUT = 25.0


class EnturFeil(RuntimeError):
    """Entur avviste spørringen. Meldingen er det `errors` sa.

    Arver `RuntimeError` fordi det er det `sjekk_punktlighet.py` alt fanget i
    bunnen av fila si, og fordi en probe som ikke fanger den skal stoppe — et
    tomt svar er verre enn ingen.

    `meldinger` er listen slik den kom, ikke bare den sammenslåtte teksten.
    Verktøyene formaterer den ulikt - `sjekk.py` setter én per linje med
    innrykk - og en liste kan formateres, en ferdig streng kan ikke tas fra
    hverandre igjen.
    """

    def __init__(self, meldinger):
        self.meldinger = list(meldinger)
        super().__init__("; ".join(self.meldinger))


def klientnavn() -> str:
    """`ET_CLIENT_NAME` fra miljøet. Tom streng hvis den ikke er satt.

    Leses ved hvert kall og ikke ved import: en probe som kaller
    `load_dotenv()` etter at denne modulen ble importert, skal likevel få
    navnet sitt med.
    """
    return os.getenv("ET_CLIENT_NAME", "").strip()


def headere() -> dict:
    return {"ET-Client-Name": klientnavn()}


def _data(nyttelast: dict) -> dict:
    """Skrell ut `data`, og kast hvis Entur klaget."""
    if nyttelast.get("errors"):
        raise EnturFeil(e.get("message", "?") for e in nyttelast["errors"])
    return nyttelast.get("data") or {}


def sporr(
    url: str,
    sporring: str,
    variabler: dict | None = None,
    timeout: float = STANDARD_TIMEOUT,
) -> dict:
    """Send en GraphQL-spørring og få `data` tilbake. Synkron.

    Kaster `EnturFeil` om Entur avviste den, og `httpx.HTTPError` om det ikke
    ble noe svar i det hele tatt. De to er forskjellige ting og skal ikke slås
    sammen: den første betyr at spørringen er feil, den andre at nettet er det.
    """
    svar = httpx.post(
        url,
        json={"query": sporring, "variables": variabler or {}},
        headers=headere(),
        timeout=timeout,
    )
    svar.raise_for_status()
    return _data(svar.json())


async def sporr_async(
    client: httpx.AsyncClient,
    url: str,
    sporring: str,
    variabler: dict | None = None,
) -> dict:
    """Samme, på en klient kalleren eier.

    Klienten kommer utenfra fordi den eneste som bruker denne kjører mange
    spørringer i parallell gjennom `asyncio.gather`, og da skal de dele
    tilkoblingspulje. Tidsavbruddet hører til klienten, ikke til kallet.
    """
    svar = await client.post(
        url,
        json={"query": sporring, "variables": variabler or {}},
        headers=headere(),
    )
    svar.raise_for_status()
    return _data(svar.json())


def hent_json(
    url: str,
    params: dict | None = None,
    timeout: float = STANDARD_TIMEOUT,
) -> dict:
    """Et vanlig GET-kall mot et Entur-endepunkt som ikke er GraphQL.

    Finnes for geokoderen, som `nordtog.py` slår opp stasjonsnavn i. Uten den
    måtte den fila importert `httpx` for ett kall, og da er «ett
    HTTP-bibliotek» igjen noe man husker i stedet for noe strukturen holder.
    """
    svar = httpx.get(url, params=params or {}, headers=headere(), timeout=timeout)
    svar.raise_for_status()
    return svar.json()


# ---------------------------------------------------------------------------
# Selvtest
# ---------------------------------------------------------------------------
# Uten nett. Vokter det ene denne modulen finnes for: at en kropp med
# statuskode 200 og `errors` i seg ikke slipper gjennom som data.
#
#     python prober/felles.py --selvtest

def _selvtest() -> int:
    feil = 0

    def si(ok, tittel, detalj=""):
        nonlocal feil
        if not ok:
            feil += 1
        print(f"{tittel:<38} {'OK  ' if ok else 'FEIL'} {detalj}")

    # Det normale: data kommer ut, errors finnes ikke.
    si(_data({"data": {"a": 1}}) == {"a": 1}, "data slippes gjennom")

    # Entur svarte 200 og avviste spørringen. Dette er hele poenget.
    try:
        _data({"data": None, "errors": [{"message": "Feltet finnes ikke"}]})
        si(False, "errors kaster EnturFeil", "kom gjennom uten kast")
    except EnturFeil as f:
        si(f.meldinger == ["Feltet finnes ikke"], "errors kaster EnturFeil",
           str(f.meldinger))

    # `data` OG `errors` samtidig er lovlig GraphQL - delvis svar. Da er det
    # errors som gjelder: et halvt svar er ikke et svar man kan måle på.
    try:
        _data({"data": {"a": 1}, "errors": [{"message": "delvis"}]})
        si(False, "delvis svar kaster også", "data vant over errors")
    except EnturFeil:
        si(True, "delvis svar kaster også", "errors vinner over data")

    # Flere meldinger skal overleve som liste. sjekk.py setter én per linje.
    try:
        _data({"errors": [{"message": "en"}, {"message": "to"}]})
    except EnturFeil as f:
        si(f.meldinger == ["en", "to"] and str(f) == "en; to",
           "meldinger beholdes som liste", f"{f.meldinger} / «{f}»")

    # En melding uten `message` skal ikke velte formateringen.
    try:
        _data({"errors": [{}]})
    except EnturFeil as f:
        si(f.meldinger == ["?"], "melding uten tekst blir «?»", str(f.meldinger))

    # `data: null` uten errors er tomt, ikke et kast.
    si(_data({"data": None}) == {}, "data: null uten errors gir {}")

    print()
    if feil:
        print(f"{feil} feil.")
        return 1
    print("Alt grønt.")
    return 0


if __name__ == "__main__":
    import sys

    if "--selvtest" in sys.argv:
        raise SystemExit(_selvtest())
    print(__doc__)
