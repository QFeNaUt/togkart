"""Punktlighet fra Journey Planner for GPS-operatørene.

`delay` fra Vehicle Positions er systematisk feil for en del tog (problem 1):
den bærer ikke noe å holde tallet opp mot - `speed`, `monitoredCall` og resten
er tomme. Journey Planner måler derimot planlagt mot forventet tid på et
navngitt stopp, og den koblingen virker for alle tre GPS-operatørene:
`serviceJourney.id` fra feeden er den samme turen Journey Planner kjenner.

Denne modulen gjør for Vy, Flytoget og Go-Ahead nøyaktig det `sjnord.py`
allerede gjør for SJ - leser sanntid bakover fra siste passerte stopp - men
uten å beregne posisjon: togene har GPS, det er bare forsinkelsen vi henter.
Alt tungt gjenbrukes fra `sjnord.py`, så det finnes én utgave av «slik spør vi
Journey Planner om et togs forsinkelse».

To ting som er lært dyrt og bakt inn her:

  * Begge kjøredatoer hentes for hvert tog. Spør vi bare om i dag og godtar
    første svar, får et tog som startet før midnatt morgendagens rutetabell -
    +24 t, samme felle som problem 3. `velg_instans` velger instansen som
    omslutter nå.
  * Forsinkelsen leses BAKOVER fra siste passerte stopp. Framtidige stopp står
    på rutetiden selv når de er merket «updated», så leser vi framover får vi
    alltid null. Se `_last_measured` i sjnord.py.

Har ikke Journey Planner et tall for toget - ukjent ID, kjører ikke, eller
ingen måling ennå - returneres `delay: None`, og da beholder `app.py`
VP-delay. Kilden merkes på featuren, samme tanke som `positionMethod`.
"""

import asyncio
import logging
from collections import Counter
from datetime import datetime, timedelta

import httpx

from sporgeometri import retning_grader
from materiell import service_type
from sjnord import (
    JOURNEY_URL,
    TURER_PER_SPORRING,
    _last_measured,
    _post,
    _query_turer,
    _timeline,
    velg_instans,
)

log = logging.getLogger(__name__)


async def _spor(client, headers: dict, sporring: str, variabler: dict) -> dict:
    """Post til Journey Planner og pakk ut `data`.

    Gjenbruker sjnord._post for selve kallet, men sjekker `errors` her:
    GraphQL svarer HTTP 200 selv når spørringen er feil.
    """
    payload = await _post(client, headers, sporring, variabler)
    if payload.get("errors"):
        beskjed = "; ".join(e.get("message", "?") for e in payload["errors"])
        raise RuntimeError(beskjed)
    return payload.get("data") or {}


def _strekk_na(timeline: list[dict], na: datetime) -> int:
    """Indeksen til stoppet toget sist passerte."""
    for i in range(len(timeline) - 1):
        if timeline[i]["at"] <= na <= timeline[i + 1]["at"]:
            return i
    return len(timeline) - 1


def _delay_na(timeline: list[dict], na: datetime) -> float | None:
    """Forsinkelsen akkurat nå: finn strekket toget er på, les bakover.

    For et ferdig tog (na etter siste stopp) treffer ingen intervaller, og vi
    leser fra siste stopp - forsinkelsen toget kom fram med.
    """
    return _last_measured(timeline, _strekk_na(timeline, na))


def _retning_na(timeline: list[dict], na: datetime) -> float | None:
    """Hvilken vei toget skal, etter rutetabellen: fra forrige mot neste stopp.

    Dette er reserveløsningen for pila i kartet, og den finnes for å dekke
    nøyaktig ett tilfelle som bevegelsesmålingen ikke kan dekke: TOGET SOM STÅR
    STILLE. En retning utledet av bevegelse krever bevegelse; et tog på en
    perrong har ingen, og mistet dermed pila si akkurat der flest ser etter
    den. Rutetabellen vet hvor toget skal videre selv når det står.

    Grovere enn de to andre kildene - en rett linje mellom to stasjoner, ikke
    tangenten til sporet - så den brukes bare når ingen av dem svarer.
    Merkingen `headingSource: rutetabell` gjør forskjellen målbar.
    """
    index = _strekk_na(timeline, na)
    if index >= len(timeline) - 1:
        # Toget er framme. Da er det ingen neste strekning å peke mot.
        return None
    a, b = timeline[index], timeline[index + 1]
    return retning_grader((a["lon"], a["lat"]), (b["lon"], b["lat"]))


async def hent_forsinkelser(
    client_name: str,
    journey_ids: list[str],
    now: datetime | None = None,
    timeout: float = 25.0,
) -> dict[str, dict]:
    """Slå opp forsinkelsen for hver serviceJourney-ID i Journey Planner.

    Returnerer `{journey_id: {"delay": sekunder|None, "status": str,
    "dato": str|None, "retning": grader|None, "slutt": datetime|None}}`.
    `delay` er None når toget ikke har en måling vi kan forsvare - da faller
    app.py tilbake på VP-delay. `slutt` er ankomsten til siste stopp, og er
    det app.py trenger for å slutte å tegne tog som har kjørt ferdig.

    `now` kan settes i tester for å fryse tiden.
    """
    # Dobbeltsett deler serviceJourney-ID. Slå opp hver tur én gang; rekkefølgen
    # beholdes så aliasene t0, t1 ... er forutsigbare.
    journey_ids = list(dict.fromkeys(journey_ids))
    if not journey_ids:
        return {}

    na = (now or datetime.now()).astimezone()
    datoer = [na.date().isoformat(), (na - timedelta(days=1)).date().isoformat()]
    headers = {"ET-Client-Name": client_name}

    # kjøredato -> {journey_id -> tur}
    per_dato: dict[str, dict[str, dict]] = {d: {} for d in datoer}

    async with httpx.AsyncClient(timeout=timeout) as client:

        async def hent(dato: str, bit: list[str]) -> tuple[str, list[str], dict]:
            sporring = _query_turer(len(bit), med_geometri=False)
            variabler = {"dato": dato}
            variabler.update({f"id{i}": jid for i, jid in enumerate(bit)})
            data = await _spor(client, headers, sporring, variabler)
            return dato, bit, data

        # Alle datoer og grupper parallelt: for ~90 tog blir det ti korte kall,
        # og de trenger ikke vente på hverandre. En feilet gruppe (return_
        # exceptions) skal ikke rive med seg resten.
        oppgaver = [
            hent(dato, journey_ids[start:start + TURER_PER_SPORRING])
            for dato in datoer
            for start in range(0, len(journey_ids), TURER_PER_SPORRING)
        ]
        for res in await asyncio.gather(*oppgaver, return_exceptions=True):
            if isinstance(res, Exception):
                log.warning("Journey Planner-gruppe feilet: %s", res)
                continue
            dato, bit, data = res
            for i, jid in enumerate(bit):
                tur = data.get(f"t{i}")
                if tur:
                    per_dato[dato][jid] = tur

    if not any(per_dato[d] for d in datoer):
        # Ingenting kom gjennom. Kast, så app.py beholder forrige cache i stedet
        # for å overskrive den med bare None-er - da ville hele flåten falt til
        # VP-delay ved en forbigående nettverksfeil.
        raise RuntimeError("Journey Planner ga ingen svar for noen tur")

    resultat: dict[str, dict] = {}
    statuser: Counter = Counter()
    for jid in journey_ids:
        kandidater: dict[str, list] = {}
        for dato in datoer:
            tur = per_dato[dato].get(jid)
            if not tur:
                continue
            timeline = _timeline(tur.get("estimatedCalls") or [])
            if timeline:
                kandidater[dato] = timeline

        dato, timeline, status = velg_instans(kandidater, na)
        underveis = status in ("underveis", "FERDIG")
        delay = _delay_na(timeline, na) if underveis else None
        # Retningen hentes bare for tog som faktisk er ute og kjører. Et tog
        # som ikke har startet ennå har ingen retning å vise.
        retning = _retning_na(timeline, na) if underveis else None
        resultat[jid] = {
            "delay": delay,
            "status": status,
            "dato": dato,
            "retning": retning,
            # Trafikktype fra Entur: lokaltog, regiontog, fjerntog, nattog.
            # Ikke materiell, men målt - og for Vy, som ikke publiserer
            # settnummer noe sted, er det det eneste vi har om selve toget.
            # Feltet ligger på TUREN; på linja er det `unknown` for alle Vys
            # 24 linjer. Se `service_type()` i materiell.py.
            "trafikktype": service_type(
                (per_dato.get(dato, {}).get(jid) or {}).get("transportSubmode")
                if dato else None
            ),
            # Når turen er over, målt på ANKOMST til siste stopp - se
            # `_timeline` i sjnord.py. app.py bruker den til å slutte å tegne
            # tog som har kjørt ferdig og står hensatt. Statusen alene holder
            # ikke: et tog som nettopp ankom skal stå litt til, ellers blinker
            # ankomster ut mens man ser på dem.
            "slutt": timeline[-1]["at"] if timeline else None,
        }
        statuser[status] += 1

    log.info(
        "Journey Planner: %d av %d tog fikk forsinkelse (%s)",
        sum(1 for r in resultat.values() if r["delay"] is not None),
        len(journey_ids),
        ", ".join(f"{s}={n}" for s, n in sorted(statuser.items())),
    )
    return resultat


if __name__ == "__main__":
    # Kjør:  python punktlighet.py
    # Viser hva modulen ville matet kartet: for hvert GPS-tog, forsinkelsen fra
    # Journey Planner ved siden av den fra Vehicle Positions. Dette er IKKE
    # verifiseringen - den bor i prober/sjekk_punktlighet.py med utvalg og
    # krysstabell. Dette er bare «gir funksjonen fornuftige tall nå?».
    import os

    from dotenv import load_dotenv

    from entur import ENTUR_URL, QUERY, _line_code, _train_number

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    client_name = os.getenv("ET_CLIENT_NAME", "").strip()

    async def _demo() -> tuple[dict, dict]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                ENTUR_URL, json={"query": QUERY},
                headers={"ET-Client-Name": client_name},
            )
            response.raise_for_status()
            vehicles = (response.json().get("data") or {}).get("vehicles") or []

        per_ref: dict[str, dict] = {}
        for vehicle in vehicles:
            ref = (vehicle.get("serviceJourney") or {}).get("id")
            if ref and vehicle.get("delay") is not None:
                per_ref.setdefault(ref, vehicle)

        forsinkelser = await hent_forsinkelser(client_name, list(per_ref))
        return per_ref, forsinkelser

    per_ref, forsinkelser = asyncio.run(_demo())

    if not per_ref:
        print("\nIngen GPS-tog med serviceJourney-ID i feeden nå.")
        raise SystemExit

    print(f"\n{'linje':<7}{'tog':<8}{'VP':>9}{'JP':>9}   status   kilde")
    print("-" * 62)
    valgt_jp = 0
    for ref, vehicle in sorted(per_ref.items(), key=lambda kv: -(kv[1]["delay"] or 0)):
        r = forsinkelser.get(ref) or {"delay": None, "status": "—"}
        vp = f"{vehicle['delay'] / 60:>6.1f} m"
        if r["delay"] is not None:
            jp = f"{r['delay'] / 60:>6.1f} m"
            kilde = "journey-planner"
            valgt_jp += 1
        else:
            jp = "     —"
            kilde = "vehicle-positions"
        print(
            f"{_line_code(vehicle.get('line')):<7}"
            f"{_train_number(vehicle) or '—':<8}{vp:>9}{jp:>9}   "
            f"{r['status']:<12} {kilde}"
        )

    print(f"\n{valgt_jp} av {len(per_ref)} tog ville fått forsinkelsen sin fra "
          f"Journey Planner. Resten beholder VP-delay.")
