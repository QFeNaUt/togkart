"""Verifiser varmekartet over flaskehalser.

Kjør:  python prober/sjekk_flaskehals.py       (uten nett, leser historikk.db)

Varmekartet er den analysen i dette prosjektet som lettest kan være grundig
feil uten at noe ser galt ut. Den bygger på tre ledd som hver kan svikte
stille:

  1. **Stasjonstilordningen.** Hver observasjon får sin nærmeste stasjon. Går
     det galt, havner tapt tid på feil strekning - og en strekning med feil
     navn ser like troverdig ut som en med riktig.
  2. **Passeringsfiltrene.** Hull i loggingen kan få to observasjoner timer
     fra hverandre til å se ut som én sammenhengende kjøring. Første kjøring
     ga «Marnardal-Stavanger» som verste flaskehals - to stasjoner 25 mil fra
     hverandre.
  3. **Geometrien.** Streken rutes gjennom `static/jernbanenett.geojson`, med
     observasjonene som pekepinn på hvilken trasé toget brukte. Går det galt,
     havner streken på nabobanen - og det er faktisk det enkleste stedet å SE
     at noe er galt.

Proben måler alle tre. Den viktigste enkeltsjekken er punkt 2 under: **ligger
hvert eneste tegnede punkt på en skinne?** Ruting gjennom et spornett kan
ikke svare noe annet - så et punkt utenfor sporet betyr at strekningen falt
tilbake på medianlinja, og da vil vi vite hvor mange og hvilke.

Ingenting her endrer noe. Proben leser.
"""

import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jernbanenett
from flaskehals import MAKS_STASJONSAVSTAND_M, flaskehalser
from sporgeometri import avstand_til_strekning

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _forste(par: tuple) -> float:
    """Sorteringsnøkkel: bare tallet.

    Uten den sammenligner Python neste ledd i tuppelen når to tall er like -
    og der ligger en dict, som ikke kan sammenlignes med en annen dict. Det
    er ikke en teoretisk fare: to strekninger med samme medianverdi er helt
    vanlig når verdiene er hele sekunder.
    """
    return par[0]


def _bøy(koordinater: list[list[float]]) -> float:
    """Hvor langt streken bøyer seg vekk fra luftlinja mellom endepunktene."""
    a, b = tuple(koordinater[0]), tuple(koordinater[-1])
    verst = 0.0
    for p in koordinater[1:-1]:
        verst = max(verst, avstand_til_strekning(tuple(p), a, b))
    return verst


class Sporsok:
    """«Hvor langt er det herfra til nærmeste skinne?»

    Rutenett over alle linjestykkene i spornettet. Uten indeks er 3 000
    tegnede punkter mot 39 000 sporpunkter et par hundre millioner
    avstandsregninger, og proben blir noe man ikke gidder å kjøre.

    Cellen er 0,02 grader, altså rundt 1,1 km i øst-vest på 60 nord. Vi ser på
    cellen og de åtte rundt, så alt innenfor en kilometer finnes - og et punkt
    som ligger mer enn en kilometer fra nærmeste skinne har vi uansett svaret
    på.
    """

    CELLE = 0.02
    TAK = 5_000.0

    def __init__(self, nett):
        self.stykker: dict[tuple[int, int], list[tuple]] = defaultdict(list)
        for kant in nett.kanter:
            for a, b in zip(kant.geo, kant.geo[1:]):
                celle = (int(a[0] / self.CELLE), int(a[1] / self.CELLE))
                self.stykker[celle].append((a, b))

    def avstand(self, punkt: tuple[float, float]) -> float:
        cx, cy = int(punkt[0] / self.CELLE), int(punkt[1] / self.CELLE)
        beste = self.TAK
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for a, b in self.stykker.get((cx + dx, cy + dy), ()):
                    d = avstand_til_strekning(punkt, a, b)
                    if d < beste:
                        beste = d
        return beste


def hoved() -> int:
    feil = 0
    data = flaskehalser(dager=7)
    features = data["features"]
    m = data["meta"]

    # --- 1. Utvalget -----------------------------------------------------
    print("=" * 72)
    print("1. HVA SOM KOM MED")
    print("=" * 72)
    print(f"  {m['observasjoner']:>6} observasjoner lest fra historikk.db")
    print(f"  {m['strekningerFunnet']:>6} strekninger funnet")
    print(f"  {m['strekninger']:>6} vist (minst {m['minPasseringer']} passeringer)")
    print(f"  {m['forkastet']:>6} passeringer forkastet av filtrene")

    if not features:
        print("\n  Ingen strekninger. Har historikk.db samlet data over et døgn?")
        print("  Det er ikke nødvendigvis en feil på en fersk database.")
        return 0

    andel = m["forkastet"] / max(1, m["forkastet"] + sum(
        f["properties"]["passeringer"] for f in features))
    print(f"\n  Forkastet andel: {andel:.0%}")
    if andel > 0.6:
        print("  ADVARSEL: over 60 % forkastes. Enten er loggingen full av")
        print("  hull, eller så er et av filtrene for strengt.")
        feil += 1

    # --- 2. Ligger strekene på skinner -----------------------------------
    print()
    print("=" * 72)
    print("2. LIGGER STREKENE PÅ SPORET")
    print("=" * 72)
    print("  Hvert punkt i en rutet strek er hentet fra spornettet, så det kan")
    print("  ikke ligge noe annet sted enn på en skinne. Måler vi noe annet,")
    print("  er det ikke geometrien som er skjev - det er rutingen som ikke")
    print("  ble brukt.\n")

    rutet = [f for f in features if f["properties"].get("geometri") == "spor"]
    reserve = [f for f in features if f["properties"].get("geometri") != "spor"]
    print(f"  {len(rutet)} av {len(features)} strekninger rutet gjennom spornettet")

    nett = jernbanenett.nett()
    if nett is None:
        print("\n  FEIL: fant ikke static/jernbanenett.geojson. Hele")
        print("  flaskehalskartet tegner medianlinjer, som var geometrien før")
        print("  21. august. Bygg nettet med: python lagjernbanenett.py")
        feil += 1
    elif rutet:
        sok = Sporsok(nett)
        verste = 0.0
        verst_p = None
        for f in rutet:
            for punkt in f["geometry"]["coordinates"]:
                d = sok.avstand(tuple(punkt))
                if d > verste:
                    verste, verst_p = d, f["properties"]
        print(f"  verste punkt utenfor sporet: {verste:.0f} m", end="")
        print(f" ({verst_p['fra']} – {verst_p['til']})" if verst_p else "")
        # Forenklingen til nettleseren fjerner punkter, den flytter dem ikke,
        # så alt over noen få meter er en reell feil og ikke avrunding.
        if verste > 25:
            print("\n  FEIL: et rutet punkt ligger langt fra nærmeste skinne.")
            print("  Enten er spornettet og geometrien ute av synk, eller så")
            print("  har forenklingen i jernbanenett.UT_TOLERANSE_M blitt grov.")
            feil += 1

    if reserve:
        print(f"\n  {len(reserve)} strekninger falt tilbake på medianlinje:")
        for f in reserve[:8]:
            p = f["properties"]
            print(f"    {p['fra']} – {p['til']} ({p['passeringer']} passeringer)")
        if len(reserve) > 8:
            print(f"    ... og {len(reserve) - 8} til")
        print("  Reserven er ikke en feil i seg selv - den slår inn der togene")
        print("  ble målt på en annen bane enn stasjonsparet ligger på. Men er")
        print("  det mange, er det rutingen som ikke treffer.")
        if len(reserve) > len(features) * 0.25:
            print(f"\n  FEIL: over en fjerdedel bruker reserven.")
            print(f"  Sjekk KORRIDOR_M ({jernbanenett.KORRIDOR_M:.0f} m), "
                  f"SNAPP_M ({jernbanenett.SNAPP_M:.0f} m) og")
            print(f"  MAKS_STASJONSAVSTAND_M ({MAKS_STASJONSAVSTAND_M} m).")
            feil += 1

    # --- 3. Bøyer strekene seg ------------------------------------------
    print()
    print("=" * 72)
    print("3. BØYER STREKENE SEG SOM SPOR")
    print("=" * 72)
    print("  En trasé svinger. Er alt praktisk talt rett, tegner vi luftlinjer")
    print("  og har mistet hele poenget.\n")

    boyer = sorted(
        ((_bøy(f["geometry"]["coordinates"]), f["properties"]) for f in features),
        key=_forste,
        reverse=True,
    )
    rette = sum(1 for b, _ in boyer if b < 20)
    print(f"  median bøy: {statistics.median(b for b, _ in boyer):.0f} m")
    print(f"  praktisk talt rette (< 20 m): {rette} av {len(boyer)}")
    print("\n  Mest krokete strekninger:")
    for b, p in boyer[:5]:
        print(f"    {p['fra'][:18] + ' – ' + p['til'][:18]:<40}{b:>7.0f} m "
              f"på {p['km']:.1f} km")

    if rette == len(boyer):
        print("\n  FEIL: ingen strek bøyer seg. Geometrien er luftlinjer.")
        feil += 1

    # --- 4. Er stasjonene naboer -----------------------------------------
    print()
    print("=" * 72)
    print("4. ER STASJONSPARENE NABOER")
    print("=" * 72)
    print("  `km` måles nå LANGS streken, ikke i luftlinje. For rutede")
    print("  strekninger er det kjørelengden.\n")

    lengder = sorted(
        ((f["properties"]["km"], f["properties"]) for f in features),
        key=_forste,
    )
    print(f"  korteste: {lengder[0][0]:.1f} km "
          f"({lengder[0][1]['fra']} – {lengder[0][1]['til']})")
    print(f"  median:   {statistics.median(k for k, _ in lengder):.1f} km")
    print(f"  lengste:  {lengder[-1][0]:.1f} km "
          f"({lengder[-1][1]['fra']} – {lengder[-1][1]['til']})")

    lange = [p for k, p in lengder if k > 60]
    if lange:
        print(f"\n  MERK: {len(lange)} strekninger over 60 km. Ekte nabostasjoner")
        print("  er sjelden så langt fra hverandre utenfor Nordlandsbanen:")
        for p in lange[:4]:
            print(f"    {p['fra']} – {p['til']}: {p['km']} km")

    # --- 5. Tallene ------------------------------------------------------
    print()
    print("=" * 72)
    print("5. HVA KARTET VISER")
    print("=" * 72)

    sekunder = sorted(f["properties"]["sekunder"] for f in features)
    taper = sum(1 for s in sekunder if s > 0)
    print(f"  {taper} strekninger der togene taper tid, "
          f"{len(sekunder) - taper} der de tar inn")
    print(f"  spenn: {sekunder[0]:+d} s til {sekunder[-1]:+d} s, "
          f"median {statistics.median(sekunder):+.0f} s")

    if sekunder[0] == sekunder[-1]:
        print("\n  FEIL: alle strekninger har samme verdi. Kartet blir ensfarget.")
        feil += 1

    verst = max(features, key=lambda f: f["properties"]["sekunder"])["properties"]
    print(f"\n  Verst: {verst['fra']} – {verst['til']}")
    print(f"    {verst['sekunder'] / 60:+.1f} min typisk over "
          f"{verst['passeringer']} passeringer, verste {verst['verste'] / 60:+.1f} min")
    for r in verst["retninger"]:
        print(f"    {r['retning']:<34}{r['sekunder'] / 60:+6.1f} min "
              f"({r['passeringer']} passeringer)")

    # Retningsforskjell er et ekte signal og verdt å se etter: en flaskehals
    # rammer sjelden begge veier likt.
    skjeve = []
    for f in features:
        r = f["properties"]["retninger"]
        if len(r) == 2 and min(x["passeringer"] for x in r) >= 3:
            skjeve.append((abs(r[0]["sekunder"] - r[1]["sekunder"]), f["properties"]))
    if skjeve:
        skjeve.sort(key=_forste, reverse=True)
        print("\n  Størst forskjell mellom retningene:")
        for d, p in skjeve[:4]:
            a, b = p["retninger"]
            print(f"    {p['fra']} – {p['til']}: "
                  f"{a['sekunder']:+d} s mot {b['sekunder']:+d} s")

    print()
    if feil:
        print(f"{feil} ting å se på.")
    else:
        print("Varmekartet ser riktig ut.")
    return 1 if feil else 0


if __name__ == "__main__":
    raise SystemExit(hoved())
