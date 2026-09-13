"""Verifiser at /api/health er noe en overvåker kan stole på.

Kjør:  python prober/sjekk_helse.py

Verken nett eller database. Cachen i `app` settes for hånd, og `fetch_trains`
byttes ut med en som nekter å svare — så alle fire tilstandene kan tvinges
fram uten at Entur er involvert.

En helsesjekk har tre måter å være ubrukelig på, og endepunktet hadde alle tre
til 23. august:

  1. **Den koster det den måler.** `CACHE_TTL` er 10 sekunder. En monitor på
     ett minutt bommet derfor på cachen HVER gang, og en bom henter Vehicle
     Positions, spør Journey Planner, henter rutedata for togene uten GPS og
     skriver til historikk.db. Overvåkingen ville vært den tyngste trafikken
     appen hadde. Sjekk 1 og 2 måler at `HELSE_TTL` skiller de to.

  2. **Den svarer 200 uansett.** `"ok": false` lå i kroppen, og statuskoden var
     200 samme hvor galt det sto til. Det virker for en overvåker som er satt
     opp til å lese kroppen, og er blindt for alle andre — `curl -f`, en
     systemd-sjekk, en uptime-tjeneste med standardinnstillinger. Sjekk 3 og 4
     krever 503.

  3. **Den sier ikke hvor gammelt svaret er.** «12 tog» uten alder kan være fra
     nå eller fra i går. Sjekk 4 krever at alderen overlever en mislykket
     henting i stedet for å nullstilles.

Sjekk 5 er invarianten som holder punkt 1 i sjakk over tid:
`HELSE_TTL` må være vesentlig større enn `CACHE_TTL`. Settes de like, er vi
tilbake til én Entur-henting per helsesjekk, og ingenting vil si fra.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from fastapi.testclient import TestClient

import app as appmodul
from app import CACHE_TTL, HELSE_TTL, app

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FEIL = 0


def si(ok, tittel, detalj=""):
    global FEIL
    if not ok:
        FEIL += 1
    print(f"{tittel:<38} {'OK  ' if ok else 'FEIL'} {detalj}")


klient = TestClient(app)

# Teller hvor mange ganger noen prøvde å hente fra Entur. Det er hele poenget
# med sjekk 1: en helsesjekk som ikke øker dette, koster ingenting.
FORSOK = {"n": 0}


async def nekter(*_a, **_k):
    FORSOK["n"] += 1
    raise httpx.ConnectError("proben later som Entur er nede")


appmodul.fetch_trains = nekter


def sett_cache(alder_sekunder, antall=42):
    """Legg en snapshot i cachen med en gitt alder."""
    appmodul._cache["payload"] = {"geojson": {}, "meta": {"count": antall}}
    appmodul._cache["at"] = time.monotonic() - alder_sekunder


def tom_cache():
    appmodul._cache["payload"] = None
    appmodul._cache["at"] = 0.0


print("\n1. Fersk cache koster ingen Entur-henting\n")

sett_cache(alder_sekunder=30)
FORSOK["n"] = 0
svar = klient.get("/api/health")
kropp = svar.json()

si(svar.status_code == 200, "200 når alt står bra", f"fikk {svar.status_code}")
si(kropp["ok"] is True, "ok: true", str(kropp["ok"]))
si(FORSOK["n"] == 0, "ingen henting utløst", f"{FORSOK['n']} forsøk")
si(kropp["trains"] == 42, "tallet kommer fra cachen", str(kropp["trains"]))
si(abs(kropp["alderSekunder"] - 30) <= 1, "alderSekunder er ekte",
   f"{kropp['alderSekunder']} s")


print("\n2. Gammel cache utløser én henting\n")

sett_cache(alder_sekunder=HELSE_TTL + 60)
FORSOK["n"] = 0
svar = klient.get("/api/health")

si(FORSOK["n"] == 1, "hentingen ble forsøkt", f"{FORSOK['n']} forsøk")


print("\n3. Feilet henting gir 503, ikke 200\n")

kropp = svar.json()
si(svar.status_code == 503, "503 når Entur ikke svarer", f"fikk {svar.status_code}")
si(kropp["ok"] is False, "ok: false", str(kropp["ok"]))
si(bool(kropp["error"]), "error er fylt ut", repr(kropp["error"]))


print("\n4. Alderen overlever en mislykket henting\n")

# Payloaden fra før står igjen - det er riktig, vi HAR fortsatt de tallene -
# men alderen må fortelle at de er gamle, ikke nullstilles av forsøket.
si(kropp["trains"] == 42, "gamle tall beholdes", str(kropp["trains"]))
si(kropp["alderSekunder"] >= HELSE_TTL, "alderen er den ekte",
   f"{kropp['alderSekunder']} s, ikke 0")

tom_cache()
FORSOK["n"] = 0
svar = klient.get("/api/health")
kropp = svar.json()
si(svar.status_code == 503, "503 før første henting", f"fikk {svar.status_code}")
si(kropp["trains"] is None, "trains er null, ikke 0",
   repr(kropp["trains"]) + " — 0 tog og «vet ikke» er ikke det samme")
si(kropp["alderSekunder"] is None, "alder er null når det ikke finnes tall",
   repr(kropp["alderSekunder"]))


print("\n5. Feltene runbooken leser finnes\n")

# docs/feilsoking.md sender folk hit for tre spørsmål. Forsvinner feltene,
# blir runbooken en løgn.
strupe = kropp.get("strupe") or {}
for felt in ("aktiv", "bakCloudflare", "enturKvote"):
    si(felt in strupe, f"strupe.{felt}", "" if felt in strupe else "borte")
si("igjen" in (strupe.get("enturKvote") or {}), "strupe.enturKvote.igjen",
   "tallet som sier om kvoten brukes opp")

# Bakgrunnsjobben, av samme grunn som strupen: en jobb som har stoppet ser
# nøyaktig ut som en som virker, helt til noen leter etter hull i historikken
# en uke senere. `sisteOkSekunderSiden` vesentlig over `intervallSekunder`
# betyr at den står.
poller = kropp.get("poller") or {}
for felt in ("aktiv", "intervallSekunder", "runder", "feil",
             "sisteOkSekunderSiden"):
    si(felt in poller, f"poller.{felt}", "" if felt in poller else "borte")


print("\n6. Invarianten som holder sjekk 1 i live\n")

si(HELSE_TTL >= CACHE_TTL * 10, "HELSE_TTL >> CACHE_TTL",
   f"{HELSE_TTL:g} s mot {CACHE_TTL:g} s — settes de like, er hver "
   f"helsesjekk en Entur-henting igjen")


print()
if FEIL:
    print(f"{FEIL} feil.")
    sys.exit(1)
print("Alt grønt.")
