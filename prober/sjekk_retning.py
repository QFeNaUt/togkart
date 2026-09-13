"""Verifiser kjøreretningen — pila som peker dit toget skal.

Kjør:  python prober/sjekk_retning.py       (krever uvicorn på 8000)

En pil som peker feil vei er den verste slags feil i dette kartet: den ser
helt riktig ut. Alt annet — en manglende prikk, en tom stripe, et tall som
ikke stemmer — kan man se at er galt. En trekant som peker sørover mens toget
kjører nordover ser nøyaktig ut som en trekant som peker riktig.

Derfor krysspeiler denne proben kildene mot hverandre i stedet for å stole på
noen av dem. Retningen har tre kilder med fallende nøyaktighet:

    vehicle-positions   operatørens eget tall. Bare Flytoget publiserer det.
    bevegelse           målt mellom to hentinger. Krever at toget kjører.
    rutetabell          rett linje mot neste stopp. Virker når toget står.
    (sporgeometri)      tangenten til sporet. Bare SJ, som ikke har GPS.

Ingen av dem kan bevise seg selv. Men to uavhengige kilder som er enige om at
toget kjører nordøstover, tar neppe feil på samme måte — og der de er uenige,
er det verdt å vite hvilken som pleier å ta feil.

Proben henter to ganger med et opphold imellom, slik at den kan måle den
faktiske bevegelsen selv og holde alle tre opp mot den.

Ingenting her endrer noe. Proben leser.
"""

import json
import os
import sys
import time
import urllib.request
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sporgeometri import avstand_m, retning_grader

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

API = "http://127.0.0.1:8000/api/trains"

# Hvor lenge vi venter mellom de to hentingene. Må være lenger enn cachen i
# app.py (10 s), ellers får vi det samme svaret to ganger og måler null
# bevegelse for alle tog. Og lenger enn Entur bruker på å oppdatere en
# posisjon — målt til rundt 30 sekunder for Vy.
OPPHOLD_S = 45

# Under denne avstanden er retningen mellom to målinger mest GPS-støy.
MIN_METER = 60.0

# Hvor mye to kilder får sprike før vi kaller dem uenige. En pil er ti piksler
# bred; tjue grader er under en halv pikselbredde på spissen og kan ingen se.
# Over 90 grader er det ikke lenger unøyaktighet — da peker de hver sin vei.
ENIGE_GRADER = 20.0
MOTSATT_GRADER = 90.0


def _avvik(a: float, b: float) -> float:
    """Minste vinkel mellom to kompassretninger, 0-180."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _hent() -> list[dict]:
    with urllib.request.urlopen(API, timeout=60) as svar:
        return json.load(svar)["geojson"]["features"]


def _operator(p: dict) -> str:
    if p.get("computed"):
        return "SJN"
    return (p.get("journeyRef") or p.get("id") or "?").split(":")[0]


def hoved() -> int:
    try:
        forst = _hent()
    except Exception as exc:  # noqa: BLE001
        print(f"Fikk ikke kontakt med {API}\n  {exc}")
        print("\nStart serveren først:  uvicorn app:app --reload")
        return 1

    print(f"Henter to ganger med {OPPHOLD_S} sekunders opphold ...")
    print(f"  henting 1: {len(forst)} tog")
    time.sleep(OPPHOLD_S)
    sist = _hent()
    print(f"  henting 2: {len(sist)} tog\n")

    feil = 0
    a = {f["properties"].get("id"): f for f in forst}

    # --- 1. Dekning ------------------------------------------------------
    print("=" * 72)
    print("1. HVOR MANGE TOG HAR EN RETNING")
    print("=" * 72)

    per_op: dict = defaultdict(lambda: [0, 0])
    kilder: Counter = Counter()
    for f in sist:
        p = f["properties"]
        op = _operator(p)
        per_op[op][0] += 1
        if p.get("heading") is not None:
            per_op[op][1] += 1
            kilder[p.get("headingSource") or "sporgeometri"] += 1

    med = sum(v[1] for v in per_op.values())
    print(f"  {med} av {len(sist)} tog ({100 * med / max(1, len(sist)):.0f} %)\n")
    for op, (tot, h) in sorted(per_op.items()):
        print(f"  {op:<5} {h:>3} av {tot:>3}   ({100 * h / tot:>3.0f} %)")
    print()
    for kilde, antall in kilders(kilder):
        print(f"  {kilde:<18} {antall:>3}")

    if not med:
        print("\n  FEIL: ingen tog har retning. Pilene er borte fra kartet.")
        return 1

    # --- 2. Krysspeiling mot faktisk bevegelse ---------------------------
    print()
    print("=" * 72)
    print("2. PEKER PILA DIT TOGET FAKTISK KJØRTE")
    print("=" * 72)
    print(f"  Sammenligner oppgitt retning med den vi måler selv mellom de to")
    print(f"  hentingene. Bare tog som flyttet seg over {MIN_METER:.0f} m telles.\n")

    per_kilde: dict = defaultdict(list)
    verstinger: list = []

    for f in sist:
        p = f["properties"]
        tid = p.get("id")
        if tid not in a or p.get("heading") is None:
            continue

        fra = tuple(a[tid]["geometry"]["coordinates"])
        til = tuple(f["geometry"]["coordinates"])
        if avstand_m(fra, til) < MIN_METER:
            continue

        faktisk = retning_grader(fra, til)
        if faktisk is None:
            continue

        kilde = p.get("headingSource") or "sporgeometri"
        avvik = _avvik(float(p["heading"]), faktisk)
        per_kilde[kilde].append(avvik)
        verstinger.append((avvik, p, faktisk))

    if not per_kilde:
        print("  Ingen tog flyttet seg langt nok til å måles. Kjør på dagtid.")
    else:
        print(f"  {'kilde':<18}{'tog':>5}{'median':>9}{'verste':>9}   enige  motsatt")
        print("  " + "-" * 62)
        for kilde, avvikene in sorted(per_kilde.items()):
            avvikene.sort()
            median = avvikene[len(avvikene) // 2]
            enige = sum(1 for x in avvikene if x <= ENIGE_GRADER)
            motsatt = sum(1 for x in avvikene if x >= MOTSATT_GRADER)
            print(f"  {kilde:<18}{len(avvikene):>5}{median:>8.0f}°{max(avvikene):>8.0f}°"
                  f"{enige:>8}{motsatt:>9}")
            # `sporgeometri` telles ikke som feil her — se forklaringen under.
            if motsatt and kilde != "sporgeometri":
                feil += 1

        alle = [x for v in per_kilde.values() for x in v]
        alle.sort()
        print(f"\n  Samlet: median {alle[len(alle) // 2]:.0f}°, "
              f"{sum(1 for x in alle if x <= ENIGE_GRADER)} av {len(alle)} enige.")

        print("\n  Merk at `bevegelse` måler mot seg selv: både pila og fasiten")
        print("  kommer av bevegelse mellom to hentinger. At den viser 0° sier")
        print("  at regnestykket er konsistent, ikke at det er riktig. Det er")
        print("  `rutetabell` og `vehicle-positions` som er ekte krysspeiling.")

        sporfeil = [
            (av, p, f) for av, p, f in verstinger
            if av >= MOTSATT_GRADER and (p.get("headingSource") or "sporgeometri") == "sporgeometri"
        ]
        if sporfeil:
            print(f"\n  {len(sporfeil)} SJ-tog ser ut til å peke motsatt vei. Det er")
            print("  nesten alltid FASITEN som tar feil, ikke pila: SJ-posisjoner")
            print("  er beregnet av rutetabellen, og når Journey Planner skyver en")
            print("  forventet avgangstid senere, GLIR TOGET BAKOVER langs sporet")
            print("  til neste henting. Målt 20. august: F6 tog 51 flyttet seg")
            print("  169 m bakover mens neste stopp sto uendret. Pila følger")
            print("  sporets tangent og er upåvirket. Se om det samme toget viser")
            print("  seg igjen over flere kjøringer før du mistenker tangenten.")
            for avvik, p, faktisk in sorted(sporfeil, reverse=True)[:5]:
                print(f"    {p.get('line', ''):<6}{p.get('trainNumber', ''):<7}"
                      f"pil {float(p['heading']):>5.0f}°  «fasit» {faktisk:>5.0f}°")

        ekte = [
            (av, p, f) for av, p, f in verstinger
            if av >= MOTSATT_GRADER and (p.get("headingSource") or "sporgeometri") != "sporgeometri"
        ]
        if ekte:
            print(f"\n  ADVARSEL: {len(ekte)} tog med MÅLT posisjon har en pil som")
            print(f"  peker over {MOTSATT_GRADER:.0f}° feil. Her kan ikke fasiten gli bakover —")
            print("  posisjonen er målt av toget selv. Dette er en ekte feil:")
            for avvik, p, faktisk in sorted(ekte, reverse=True)[:8]:
                print(f"    {p.get('line', ''):<6}{p.get('trainNumber', ''):<7}"
                      f"oppgitt {float(p['heading']):>5.0f}°  faktisk {faktisk:>5.0f}°"
                      f"   ({p.get('headingSource')})")

    # --- 3. Kildene mot hverandre ----------------------------------------
    print()
    print("=" * 72)
    print("3. ER RUTETABELLEN TIL Å STOLE PÅ")
    print("=" * 72)
    print("  Rutetabellen er den groveste kilden — en rett linje mellom to")
    print("  stasjoner. Den er også den eneste som virker når toget står")
    print("  stille, så det er verdt å vite hvor mye den bommer når vi kan")
    print("  måle fasiten.\n")

    rute = sorted(per_kilde.get("rutetabell", []))
    bevegelse = sorted(per_kilde.get("bevegelse", []))
    if rute and bevegelse:
        print(f"  rutetabell  median {rute[len(rute) // 2]:>3.0f}°  ({len(rute)} tog)")
        print(f"  bevegelse   median {bevegelse[len(bevegelse) // 2]:>3.0f}°  "
              f"({len(bevegelse)} tog)")
        if rute[len(rute) // 2] > 45:
            print("\n  MERK: rutetabellen bommer mye. Den peker mot neste stopp i")
            print("  luftlinje, så på en bane som svinger kraftig er det ventet —")
            print("  men over 45° i median betyr at pila ofte peker synlig feil.")
            feil += 1
    else:
        print("  For få tog med begge kilder til å sammenligne nå.")

    # --- 4. Spøkelsestog --------------------------------------------------
    print()
    print("=" * 72)
    print("4. TOG UTEN RETNING")
    print("=" * 72)

    uten = [f for f in sist if f["properties"].get("heading") is None]
    spokelser = sum(1 for f in uten if f["properties"].get("stale"))
    print(f"  {len(uten)} tog uten pil, hvorav {spokelser} er spøkelsestog")
    print("  (uten fersk posisjon — de har ingen retning å ha).\n")

    # Tre grunner til å mangle pil, og bare den siste er en feil.
    staaende = 0
    nye = 0
    bevegde = []
    for f in uten:
        tid = f["properties"].get("id")
        if tid not in a:
            nye += 1          # kom inn i feeden mellom de to hentingene
            continue
        fra = tuple(a[tid]["geometry"]["coordinates"])
        til = tuple(f["geometry"]["coordinates"])
        if avstand_m(fra, til) < MIN_METER:
            staaende += 1     # sto stille — ingen bevegelse å måle
        else:
            bevegde.append((f["properties"], avstand_m(fra, til)))

    print(f"  {staaende:>3} sto praktisk talt stille mellom hentingene")
    print(f"  {nye:>3} kom inn i feeden underveis (ingen forrige posisjon)")
    print(f"  {len(bevegde):>3} flyttet seg likevel uten å få retning")

    if bevegde:
        print("\n  MERK: disse skulle hatt en pil. Bevegelsen alene er nok.")
        for p, d in sorted(bevegde, key=lambda x: -x[1])[:6]:
            print(f"    {p.get('line', ''):<6}{p.get('trainNumber', ''):<7}"
                  f"flyttet {d:>6.0f} m   stale={p.get('stale')}")
        feil += 1

    print()
    if feil:
        print(f"{feil} ting å se på.")
    else:
        print("Pilene peker riktig vei.")
    return 1 if feil else 0


def kilders(teller: Counter) -> list:
    """Kildene i rekkefølge etter nøyaktighet, ikke etter antall."""
    orden = ["vehicle-positions", "sporgeometri", "bevegelse", "rutetabell"]
    return [(k, teller[k]) for k in orden if teller.get(k)]


if __name__ == "__main__":
    raise SystemExit(hoved())
