"""
lagjernbanenett.py — bygg `static/jernbanenett.geojson`: hele sporet, ikke banene.

Hvorfor denne fila finnes ved siden av lagbaner.py
--------------------------------------------------
`lagbaner.py` bygger et **visningslag**: seksten navngitte baner, forenklet til
150 m, som skal se riktige ut på zoom 6. Til det formålet spiller det ingen
rolle at bitene ligger løsrevet og i vilkårlig rekkefølge — de tegnes uansett
hver for seg.

Denne fila bygger et **rutingsnett**: alt kjørbart hovedspor i Norge, delt ved
sporvekslene og med endepunktene intakte, slik at det kan gås gjennom som en
graf. Til det formålet er sammenhengen alt.

Forskjellen er ikke akademisk. `hovedbaner.geojson` mangler Hovedbanen,
Askerbanen, Follobanen, Spikkestadbanen, Roa–Hønefossbanen og Østfoldbanens
østre linje — nettopp de linjene som gjør Oslo, Akershus og Østfold til det
tetteste og vanskeligste området i kartet. Et flaskehalskart som prøvde å
legge strekningene sine på hovedbanene ville tegnet Ski–Mysen tvers over
åkeren.

Hvem bruker resultatet
----------------------
`jernbanenett.py` laster fila og ruter mellom nabostasjoner, slik at
strekningene i flaskehalskartet følger sporet i stedet for en median av
GPS-punkter. Ingenting annet i appen rører den.

Hva som kommer med, og hva som ikke gjør det
--------------------------------------------
  * `railway=rail`. Ikke `light_rail` (T-banen og Bybanen i Bergen ligger
    tett inntil jernbanen i Oslo og Bergen, og et rutesøk som får lov til å
    bruke dem finner snarveier som ingen tog kan kjøre) og ikke
    `narrow_gauge` (museumsbaner).
  * Ikke `usage=tourism|industrial|freight|crane|test|military|museum|...`.
    Godsspor og industrispor er ekte spor, men de er ikke der persontog
    kjører, og de lager blindveier og omveier i grafen.
  * Ikke `service=*` (sidespor, skiftespor, vekselforbindelser). Filtreres
    allerede bort i Overpass-spørringen.
  * `disused`, `abandoned` og `construction` er egne `railway`-verdier og
    faller derfor utenfor av seg selv — samme valg som lagbaner.py gjør.

Stasjonsbufferet
----------------
Spørringen dekker en bbox rundt Norge, og den bbox-en inneholder halve
Sverige. Vi beholder bare spor som ligger innenfor STASJONSBUFFER_M fra en
stasjon i `static/stasjoner.geojson`, altså innenfor det nettet togene i
kartet faktisk kan bevege seg i.

Målt 21. august 2026 på de 335 stasjonene i fila:

    buffer      biter    punkter (5 m)   stasjoner uten kontakt
    25 km        9580          36 687             82
    40 km        9969          38 835              0
    ingen       31366         134 345              0

25 km klipper Bergensbanen over Hardangervidda og Nordlandsbanen over
Saltfjellet, der det er langt mellom stasjonene — og et klipt nett er verre
enn et stort, fordi ruting da svarer «finner ingen vei» i stedet for feil.
40 km koster 2 000 punkter mer enn 25 km og holder alle 335 stasjoner i én
komponent. Uten buffer i det hele tatt blir fila 3,5 ganger så stor uten å
koble til en eneste stasjon til.

Kjøring
-------
    python lagjernbanenett.py               # hent, filtrer, skriv
    python lagjernbanenett.py --sjekk       # bare rapporter om fila som finnes
    python lagjernbanenett.py --selvtest    # deling og filtrering, uten nett
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

# Samme speil og samme retrylogikk som lagbaner.py — dette er det samme
# byggetrinnet mot den samme tjenesten, og to sett med retryregler mot
# Overpass ville drevet fra hverandre.
from lagbaner import NORGE_BBOX, OVERPASS_SPEIL, PAUSE_S, RUNDER, lengde_km
from sporgeometri import Punkt, avstand_m, forenkle

UT = Path("static/jernbanenett.geojson")
STASJONER = Path("static/stasjoner.geojson")

# Toleranse for forenklingen. Fem meter er langt under sporvidden og godt
# under GPS-støyen i observasjonene vi skal måle mot, men kutter likevel tre
# fjerdedeler av punktene: OSM lagrer kurver med rundt 10 m mellomrom.
#
# Ikke skru den opp for å spare plass. Hele poenget med nettet er å skille
# Follobanen fra Østfoldbanen og Askerbanen fra Drammenbanen, og de går noen
# hundre meter fra hverandre. Grov geometri her blir feil bane der.
TOLERANSE_M = 5.0

# Se modul-docstringen for målingen bak dette tallet.
STASJONSBUFFER_M = 40_000

# `usage`-verdier som er ekte spor, men ikke spor persontog kjører på.
# `None` er med vilje IKKE i lista: mange stasjonsspor står uten `usage`, og
# uten dem blir stasjonene blindveier i grafen.
DROPP_USAGE = {
    "tourism", "industrial", "freight", "crane",
    "test", "military", "portage", "spur", "museum",
}

# Koordinater rundes til seks desimaler (~11 cm). Det er finere enn OSM
# lagrer, så to ways som deler en node får identiske tall — og det er slik
# `jernbanenett.py` finner ut at de henger sammen.
DESIMALER = 6


# ---------------------------------------------------------------------------
# 1. Overpass
# ---------------------------------------------------------------------------
def bygg_query() -> str:
    """Alt kjørbart hovedspor i bbox-en, med geometri og node-ID-er.

    `[!"service"]` gjør det tunge filtreringsarbeidet allerede hos Overpass:
    sidespor, skiftespor og plattformspor bærer alle `service=*`, og de er
    flertallet av ways rundt en stasjon.

    `out geom` gir både `geometry` (koordinatene) og `nodes` (ID-ene). Vi
    trenger ID-ene for å vite hvor to spor deler en node og altså er
    forbundet — se `del_ved_kryss`.
    """
    sor, vest, nord, ost = NORGE_BBOX
    return (
        f"[out:json][timeout:300][bbox:{sor},{vest},{nord},{ost}];\n"
        '(\n  way["railway"="rail"][!"service"];\n);\n'
        "out geom;"
    )


def _klient() -> httpx.Client:
    return httpx.Client(
        timeout=300.0,
        headers={"User-Agent": "TogKart/1.0 (byggeskript, lagjernbanenett.py)"},
    )


def hent(klient: httpx.Client, query: str) -> dict:
    """Spør speilene i tur og orden. 429 og 504 er travelhet, 400 er vår feil."""
    for runde in range(RUNDER):
        for i, url in enumerate(OVERPASS_SPEIL):
            try:
                svar = klient.post(url, data={"data": query})
            except httpx.HTTPError as feil:
                print(f"    speil {i+1} svarte ikke: {feil}", file=sys.stderr)
                continue
            if svar.status_code in (429, 504):
                print(f"    speil {i+1} er opptatt ({svar.status_code})", file=sys.stderr)
                continue
            if svar.status_code >= 400:
                raise RuntimeError(f"Overpass {svar.status_code}: {svar.text[:300]}")
            return svar.json()
        pause = PAUSE_S * (runde + 1) * 5
        print(f"    alle speil opptatt, venter {pause:.0f} s "
              f"(runde {runde+1} av {RUNDER})", file=sys.stderr)
        time.sleep(pause)
    raise RuntimeError("Alle Overpass-speil er opptatt. Prøv igjen om noen minutter.")


# ---------------------------------------------------------------------------
# 2. Tolkning — rene funksjoner, dette er det selvtesten kjører
# ---------------------------------------------------------------------------
def spor_fra_svar(svar: dict) -> list[tuple[list[int], list[Punkt]]]:
    """(node-ID-er, punkter) per way, filtrert på tagger.

    Overpass gir `{"lat":..,"lon":..}`, altså motsatt rekkefølge av GeoJSON.
    Bytter du dem ikke om her, havner jernbanen i Somalia.
    """
    ut = []
    for e in svar.get("elements") or []:
        if e.get("type") != "way":
            continue
        t = e.get("tags") or {}
        if t.get("railway") != "rail" or t.get("usage") in DROPP_USAGE:
            continue
        geometri = e.get("geometry") or []
        noder = e.get("nodes") or []
        # Ways som krysser bbox-kanten kommer med hull i geometrien; da
        # stemmer ikke listene overens, og vi kan ikke stole på hvilken node
        # som hører til hvilket punkt.
        if len(geometri) < 2 or len(noder) != len(geometri):
            continue
        ut.append((noder, [(p["lon"], p["lat"]) for p in geometri]))
    return ut


class Stasjonsnaerhet:
    """«Ligger dette punktet innenfor N meter fra en stasjon?»

    Rutenett med kvartgradsceller, samme grep som `flaskehals.Stasjonsnett`.
    Uten indeks er 144 000 punkter mot 335 stasjoner 48 millioner
    avstandsregninger, og byggeskriptet bruker et kvarter på et filter.
    """

    CELLE = 0.25

    def __init__(self, punkter: list[Punkt]):
        self.rutenett: dict[tuple[int, int], list[Punkt]] = defaultdict(list)
        for lon, lat in punkter:
            self.rutenett[(int(lon / self.CELLE), int(lat / self.CELLE))].append((lon, lat))

    def innenfor(self, punkt: Punkt, meter: float) -> bool:
        lon, lat = punkt
        cx, cy = int(lon / self.CELLE), int(lat / self.CELLE)
        # Én celle er ~28 km i nord–sør. Så mange celler må vi ut for å være
        # sikre på at ingen stasjon innenfor rekkevidden blir oversett.
        rekke = int(meter / 27_500) + 1
        for dx in range(-rekke, rekke + 1):
            for dy in range(-rekke, rekke + 1):
                for s in self.rutenett.get((cx + dx, cy + dy), ()):
                    if avstand_m(punkt, s) <= meter:
                        return True
        return False


def naer_stasjon(punkter: list[Punkt], naerhet: Stasjonsnaerhet, meter: float) -> bool:
    """Er noen del av dette sporet innenfor rekkevidde?

    Vi ser på hver tiende punkt og alltid på endepunktene. Et spor på tvers av
    bufferkanten skal bli med, og en bit er sjelden så lang at ti punkter
    hopper over hele treffet — men å måle alle punktene i alle spor koster
    mer enn presisjonen er verdt i et filter som uansett har 40 km slingring.
    """
    steg = max(1, len(punkter) // 10)
    return any(
        naerhet.innenfor(p, meter)
        for p in [*punkter[::steg], punkter[-1]]
    )


def del_ved_kryss(
    spor: list[tuple[list[int], list[Punkt]]]
) -> list[list[Punkt]]:
    """Del hvert spor der det deler en node med et annet spor.

    OSM deler stort sett ways ved sporvekslene allerede, så dette flytter få
    biter — målt 21. august: 9 969 ways ble 10 200 biter. Men de få det
    gjelder er nettopp sporveksler midt i en lang way, og uten delingen ville
    grafen ikke visst at man kan bytte spor der. En manglende forbindelse i
    Oslo-området er ikke en detalj; den sender rutesøket rundt hele byen.

    Delingen er også grunnen til at forenklingen kan gjøres per bit uten
    fare: Ramer–Douglas–Peucker beholder alltid endepunktene, og etter
    delingen ER hver krysningsnode et endepunkt.
    """
    teller: Counter[int] = Counter()
    for noder, _ in spor:
        teller.update(noder)

    biter: list[list[Punkt]] = []
    for noder, punkter in spor:
        start = 0
        for i in range(1, len(noder) - 1):
            if teller[noder[i]] > 1:
                biter.append(punkter[start:i + 1])
                start = i
        biter.append(punkter[start:])
    return [b for b in biter if len(b) >= 2]


def rund(punkter: list[Punkt]) -> list[list[float]]:
    return [[round(lon, DESIMALER), round(lat, DESIMALER)] for lon, lat in punkter]


# ---------------------------------------------------------------------------
# 3. Sammenheng — den ene sjekken som betyr noe
# ---------------------------------------------------------------------------
def komponenter(biter: list[list[Punkt]]) -> list[set[tuple[float, float]]]:
    """Grupper endepunktene i sammenhengende komponenter.

    Nøkkelen er de avrundede koordinatene, ikke node-ID-ene: det er slik
    `jernbanenett.py` kommer til å koble dem sammen når den leser fila, så
    det er den sammenhengen som må måles her. Måler vi på ID-ene i stedet,
    rapporterer skriptet en sammenheng runtime ikke har.
    """
    forelder: dict[tuple, tuple] = {}

    def finn(x):
        while forelder.setdefault(x, x) != x:
            forelder[x] = forelder[forelder[x]]
            x = forelder[x]
        return x

    for bit in biter:
        a = (round(bit[0][0], DESIMALER), round(bit[0][1], DESIMALER))
        for punkt in bit[1:]:
            b = (round(punkt[0], DESIMALER), round(punkt[1], DESIMALER))
            ra, rb = finn(a), finn(b)
            if ra != rb:
                forelder[ra] = rb
            a = b

    grupper: dict[tuple, set] = defaultdict(set)
    for node in list(forelder):
        grupper[finn(node)].add(node)
    return sorted(grupper.values(), key=len, reverse=True)


def stasjonspunkter(sti: Path = STASJONER) -> dict[str, Punkt]:
    data = json.loads(sti.read_text(encoding="utf-8"))
    ut = {}
    for f in data.get("features", []):
        navn = (f["properties"].get("navn") or "").removesuffix(" stasjon")
        if navn:
            lon, lat = f["geometry"]["coordinates"]
            ut[navn] = (lon, lat)
    return ut


def rapport(biter: list[list[Punkt]]) -> int:
    """Skriv ut hva nettet er verdt. Returnerer antall ting å se på.

    Den avgjørende sjekken er den siste: hver stasjon i kartet må ha spor
    innenfor en kilometer, og det sporet må ligge i den STORE komponenten.
    En stasjon som henger i en egen liten komponent kan ikke rutes til, og
    da faller flaskehalskartet stille tilbake på medianlinja der — riktig
    oppførsel, men et hull vi vil vite om.
    """
    feil = 0
    km = sum(lengde_km(b) for b in biter)
    punkter = sum(len(b) for b in biter)
    print(f"  {len(biter):>7} biter")
    print(f"  {punkter:>7} punkter")
    print(f"  {km:>7.0f} km spor")

    komp = komponenter(biter)
    stor = komp[0]
    print(f"  {len(komp):>7} komponenter, største har {len(stor)} noder "
          f"({len(stor) / sum(len(k) for k in komp):.0%} av nettet)")

    stasjoner = stasjonspunkter()
    # Rutenett over storkomponentens noder, ellers er dette 335 x 130 000.
    naerhet = Stasjonsnaerhet(list(stor))
    utenfor = [n for n, p in stasjoner.items() if not naerhet.innenfor(p, 1000)]
    if utenfor:
        feil += 1
        print(f"\n  FEIL: {len(utenfor)} av {len(stasjoner)} stasjoner har ikke")
        print("  spor fra storkomponenten innenfor 1 km. De kan ikke rutes til:")
        for navn in sorted(utenfor)[:20]:
            print(f"    {navn}")
        if len(utenfor) > 20:
            print(f"    ... og {len(utenfor) - 20} til")
    else:
        print(f"  {len(stasjoner):>7} stasjoner, alle med spor fra storkomponenten "
              "innenfor 1 km")
    return feil


# ---------------------------------------------------------------------------
# 4. Bygg
# ---------------------------------------------------------------------------
def bygg() -> int:
    print("Henter alt hovedspor i Norge fra Overpass. Dette tar et minutt.")
    with _klient() as klient:
        svar = hent(klient, bygg_query())

    spor = spor_fra_svar(svar)
    print(f"  {len(spor)} spor etter taggfilter "
          f"({sum(len(p) for _, p in spor)} punkter)")

    naerhet = Stasjonsnaerhet(list(stasjonspunkter().values()))
    nar = [(n, p) for n, p in spor if naer_stasjon(p, naerhet, STASJONSBUFFER_M)]
    print(f"  {len(nar)} innenfor {STASJONSBUFFER_M/1000:.0f} km fra en stasjon "
          f"({len(spor) - len(nar)} forkastet, mest svensk)")

    biter = del_ved_kryss(nar)
    print(f"  {len(biter)} biter etter deling ved sporveksler")

    forenklet = [forenkle(b, TOLERANSE_M) for b in biter]
    forenklet = [b for b in forenklet if len(b) >= 2]
    for_pkt = sum(len(b) for b in biter)
    etter_pkt = sum(len(b) for b in forenklet)
    print(f"  {etter_pkt} punkter etter forenkling til {TOLERANSE_M:.0f} m "
          f"({1 - etter_pkt/for_pkt:.0%} kuttet)")

    print("\nSjekker nettet:")
    feil = rapport(forenklet)

    data = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "navn": "Jernbanenett",
                "kilde": "osm",
                "toleranse_m": TOLERANSE_M,
                "buffer_m": STASJONSBUFFER_M,
                "biter": len(forenklet),
                "km": round(sum(lengde_km(b) for b in forenklet)),
            },
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [rund(b) for b in forenklet],
            },
        }],
    }
    UT.parent.mkdir(parents=True, exist_ok=True)
    UT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                  encoding="utf-8")
    print(f"\nSkrev {UT} ({UT.stat().st_size / 1e6:.1f} MB)")
    return feil


def sjekk() -> int:
    """Rapporter om fila som allerede ligger der. Ingen nettkall."""
    if not UT.exists():
        print(f"{UT} finnes ikke. Kjør: python lagjernbanenett.py")
        return 1
    data = json.loads(UT.read_text(encoding="utf-8"))
    biter = [
        [(p[0], p[1]) for p in linje]
        for f in data["features"]
        for linje in f["geometry"]["coordinates"]
    ]
    print(f"{UT} ({UT.stat().st_size / 1e6:.1f} MB)")
    return rapport(biter)


# ---------------------------------------------------------------------------
# 5. Selvtest — uten nett
# ---------------------------------------------------------------------------
def selvtest() -> int:
    svar = {"elements": [
        {"type": "way", "tags": {"railway": "rail", "usage": "main"},
         "nodes": [1, 2, 3], "geometry": [
             {"lon": 10.0, "lat": 60.0}, {"lon": 10.1, "lat": 60.0},
             {"lon": 10.2, "lat": 60.0}]},
        # Deler node 2 med den over: her går en sporveksel.
        {"type": "way", "tags": {"railway": "rail"},
         "nodes": [4, 2], "geometry": [
             {"lon": 10.1, "lat": 60.1}, {"lon": 10.1, "lat": 60.0}]},
        {"type": "way", "tags": {"railway": "rail", "usage": "industrial"},
         "nodes": [5, 6], "geometry": [
             {"lon": 11.0, "lat": 60.0}, {"lon": 11.1, "lat": 60.0}]},
        {"type": "way", "tags": {"railway": "light_rail"},
         "nodes": [7, 8], "geometry": [
             {"lon": 10.7, "lat": 59.9}, {"lon": 10.8, "lat": 59.9}]},
        # Klippet av bbox-kanten: flere noder enn punkter.
        {"type": "way", "tags": {"railway": "rail"},
         "nodes": [9, 10, 11], "geometry": [{"lon": 12.0, "lat": 60.0}]},
    ]}

    spor = spor_fra_svar(svar)
    assert len(spor) == 2, f"taggfilteret slapp gjennom {len(spor)} spor, ventet 2"
    assert all(p[0][0] > 5 for _, p in spor), "(lon, lat) er byttet om"
    print("taggfilter    OK   industri, light_rail og klippede ways ute")

    biter = del_ved_kryss(spor)
    assert len(biter) == 3, f"delingen ga {len(biter)} biter, ventet 3"
    assert any(len(b) == 2 and b[0] == (10.0, 60.0) for b in biter), \
        "første spor ble ikke delt ved node 2"
    print("kryssdeling   OK   delt der to spor deler en node")

    komp = komponenter(biter)
    assert len(komp) == 1, f"{len(komp)} komponenter, ventet 1 sammenhengende"
    print("sammenheng    OK   de tre bitene henger sammen")

    naerhet = Stasjonsnaerhet([(10.0, 60.0)])
    assert naerhet.innenfor((10.0, 60.05), 6000), "6 km burde vært innenfor"
    assert not naerhet.innenfor((10.0, 60.5), 6000), "55 km burde vært utenfor"
    assert naerhet.innenfor((10.0, 60.5), 60_000), "rekkevidden dekker ikke nok celler"
    print("stasjonsbuffer OK  også over flere rutenettsceller")

    print("\nAlt i orden.")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Bygg static/jernbanenett.geojson")
    ap.add_argument("--sjekk", action="store_true",
                    help="rapporter om fila som finnes, uten å hente på nytt")
    ap.add_argument("--selvtest", action="store_true",
                    help="test tolkning og deling, uten nett")
    args = ap.parse_args()

    if args.selvtest:
        raise SystemExit(selvtest())
    if args.sjekk:
        raise SystemExit(sjekk())
    raise SystemExit(bygg())
