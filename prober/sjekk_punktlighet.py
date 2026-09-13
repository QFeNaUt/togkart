"""sjekk_punktlighet - har Journey Planner sanntid for Vy, Flytoget og Go-Ahead?

Problem 1 står fast fordi Vehicle Positions ikke bærer noe å holde `delay` opp
mot: `speed`, `vehicleStatus`, `monitoredCall` og `destinationRef` er tomme for
samtlige tog. Se `sjekk.py spokelser`.

Men `serviceJourney.id` ligger på hvert kjøretøy, og Journey Planner v3 kjenner
den samme turen. Denne proben tar tog fra den ene kilden og slår dem opp i den
andre, med nøyaktig den lesingen `sjnord.py` allerede gjør for SJ - inkludert
`_last_measured`, som leser bakover fra siste passerte stopp fordi framtidige
stopp står på rutetiden selv når de er merket «updated».

Den svarer på to spørsmål, og det andre er det viktigste:

  1. Er `delay` og Journey Planner enige om hvor forsinket toget er?
  2. Er toget i det hele tatt underveis? Journey Planner vet når en tur er
     over. Melder Vehicle Positions 90 minutters forsinkelse på en tur som
     endte for en time siden, er spøkelseshypotesen bekreftet - ikke lenger
     antatt.

Proben velger de ti mest forsinkede togene pluss ti tilfeldige. De forsinkede
fordi det er dem hele spørsmålet handler om, de tilfeldige fordi en kilde som
bare stemmer for verstingene ikke er en kilde å bygge på.

Kjør fra prosjektroten. Uvicorn trenger ikke å kjøre:

    python prober/sjekk_punktlighet.py

Den henter geometri-fritt: `pointsOnLink` slås av før spørringene bygges, så
tjue turer koster kilobyte i stedet for megabyte. Vi skal måle tid her, ikke
tegne noe.


## Hvorfor begge kjøredatoer hentes for hver tur

Skrevet om 19. august etter at proben konkluderte feil klokka 02:09:

    turer spurt for 2026-08-19................. 16
    stopp totalt............................... 251
       realtimeState = scheduled      251
    INGEN stopp har sanntid. Da er heller ikke denne veien åpen

Konklusjonen var feil. Togene som lå i feeden klokka to om natta gikk før
midnatt og tilhører kjøredato 18.08. Proben spurte om 19.08, fikk svar - og
svaret var morgendagens instans, som naturligvis har rutetid på hvert eneste
stopp fordi den ikke har kjørt ennå. Dagen før målte samme probe 263 av 263
stopp med ekte sanntid og ikke ett `scheduled`. En fullstendig omvending til
100 % `scheduled` er signaturen til feil instans, ikke til en stille feed.

Den gamle koden falt tilbake til gårsdagen bare når turen ikke ble funnet i
det hele tatt. Det er for sent: turen *blir* funnet på dagens dato, den er
bare feil døgn. Derfor hentes nå begge datoene for hver tur, og instansen
velges etter hvilken av dem som omslutter nå-tidspunktet - se `velg_instans`.

Det koster én ekstra spørring per gruppe på tjue. Til gjengjeld er det den
samme feilen som problem 3, og den skal ikke måtte oppdages en tredje gang.
"""

import random
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sjnord  # noqa: E402
from entur import ENTUR_URL, QUERY, _line_code, _train_number  # noqa: E402
from sjnord import JOURNEY_URL, _last_measured, _measured_delay, _timeline, velg_instans  # noqa: E402
from prober.felles import klientnavn, sporr  # noqa: E402

load_dotenv()

ANTALL_VERSTE = 10
ANTALL_TILFELDIGE = 10


def ask(url: str, sporring: str, variabler: dict | None = None) -> dict:
    """Denne probens tretti sekunder, ellers `prober.felles.sporr`.

    `EnturFeil` arver `RuntimeError`, som er det bunnen av denne fila alt
    fanger — så feilhåndteringen nederst er uendret.
    """
    return sporr(url, sporring, variabler, timeout=30.0)


def velg_tog(vehicles: list[dict]) -> list[dict]:
    """De ti mest forsinkede pluss ti tilfeldige av resten."""
    brukbare = [
        v for v in vehicles
        if v.get("delay") is not None and (v.get("serviceJourney") or {}).get("id")
    ]
    etter_delay = sorted(brukbare, key=lambda v: -v["delay"])
    verste = etter_delay[:ANTALL_VERSTE]

    resten = etter_delay[ANTALL_VERSTE:]
    tilfeldige = random.sample(resten, min(ANTALL_TILFELDIGE, len(resten)))
    return verste + tilfeldige


def hent_turer(tur_ider: list[str], dato: str) -> tuple[dict[str, dict], set[str]]:
    """Slå opp turene i Journey Planner for én kjøredato.

    Gjenbruker `sjnord._query_turer`, som bygger aliasspørringen (t0, t1, ...)
    og sender ID-ene som variabler. Geometrien er slått av i hovedløpet - vi
    trenger stoppetider, ikke polylinjer.

    Returnerer `(svar, uten_svar)`. Skillet er ikke pedanteri: `serviceJourney`
    som `null` betyr at Entur ikke kjenner ID-en, mens et tur-objekt med tom
    `estimatedCalls` betyr at turen finnes men ikke kjører den datoen. Det er
    nøyaktig de to forklaringene løs tråd 2 ba om å få skilt fra hverandre.
    """
    svar: dict[str, dict] = {}
    uten_svar: set[str] = set()
    steg = sjnord.TURER_PER_SPORRING

    for start in range(0, len(tur_ider), steg):
        gruppe = tur_ider[start:start + steg]
        sporring = sjnord._query_turer(len(gruppe))
        variabler = {"dato": dato}
        variabler.update({f"id{i}": tur_id for i, tur_id in enumerate(gruppe)})

        data = ask(JOURNEY_URL, sporring, variabler)
        for i, tur_id in enumerate(gruppe):
            tur = data.get(f"t{i}")
            if tur:
                svar[tur_id] = tur
            else:
                uten_svar.add(tur_id)
    return svar, uten_svar




def hovedlop() -> None:
    print("sjekk_punktlighet - har Journey Planner sanntid for GPS-operatørene?\n")
    if not klientnavn():
        print("   ADVARSEL: ET_CLIENT_NAME er ikke satt.\n")

    # Sporgeometrien er tung og irrelevant her. Bryteren leses inne i
    # sjnord._geometri() når spørringen bygges, så det holder å sette den.
    sjnord._GEOMETRI_I_SPORRING = False

    # -- 1. Vehicle Positions ----------------------------------------------
    print("1. Vehicle Positions")
    vehicles = ask(ENTUR_URL, QUERY).get("vehicles") or []
    valgte = velg_tog(vehicles)
    med_id = sum(
        1 for v in vehicles
        if v.get("delay") is not None and (v.get("serviceJourney") or {}).get("id")
    )
    print(f"   tog i feeden................................ {len(vehicles)}")
    print(f"   med delay og serviceJourney-ID.............. {med_id}")
    print(f"   valgt til sammenligning..................... {len(valgte)}"
          f"  ({ANTALL_VERSTE} verste + {ANTALL_TILFELDIGE} tilfeldige)")

    if not valgte:
        print("\n   Ingen tog å sammenligne. Avbryter.")
        return

    per_id = {(v["serviceJourney"] or {})["id"]: v for v in valgte}
    if len(per_id) < len(valgte):
        # Dobbeltsett deler tur-ID. Da er det fortsatt én tur å slå opp, men
        # to kjøretøy - og ordboken beholder bare det siste. Uproblematisk
        # for tidsmålingen, men tallet skal ikke se ut som et tap.
        print(f"   unike turer å slå opp....................... {len(per_id)}"
              f"  ({len(valgte) - len(per_id)} kjøretøy deler tur-ID)")

    # -- 2. Journey Planner -------------------------------------------------
    na = datetime.now().astimezone()
    i_dag = na.date().isoformat()
    i_gar = (na - timedelta(days=1)).date().isoformat()
    datoer = [i_dag, i_gar]

    print("\n2. Journey Planner")
    print("   Begge kjøredatoer hentes for hver tur, og instansen velges etter")
    print("   hvilken av dem som omslutter nå. Å spørre om i dag og godta det")
    print("   første svaret gir morgendagens rutetabell om natta.")

    per_dato: dict[str, dict[str, dict]] = {}
    uten_svar: dict[str, set[str]] = {}
    for dato in datoer:
        svar, mangler = hent_turer(list(per_id), dato)
        per_dato[dato] = svar
        uten_svar[dato] = mangler
        med_stopp = sum(1 for t in svar.values() if t.get("estimatedCalls"))
        print(f"   {dato}: {len(svar):>2} av {len(per_id)} kjent, "
              f"{med_stopp:>2} med stoppetider")

    # Løs tråd 2: hvorfor svarer ikke alle turene? To ulike årsaker.
    ukjent_begge = uten_svar[i_dag] & uten_svar[i_gar]
    kjent_uten_stopp = {
        tur_id for tur_id in per_id
        if tur_id not in ukjent_begge
        and not any((per_dato[d].get(tur_id) or {}).get("estimatedCalls")
                    for d in datoer)
    }
    if ukjent_begge or kjent_uten_stopp:
        print()
        if ukjent_begge:
            print(f"   ukjent ID på begge datoer................... {len(ukjent_begge)}"
                  "   (serviceJourney = null)")
        if kjent_uten_stopp:
            print(f"   kjent, men uten stopp noen av dagene........ "
                  f"{len(kjent_uten_stopp)}   (kjører ikke da)")

    if not any(per_dato[d] for d in datoer):
        print("\n   Journey Planner kjenner ingen av turene. Da er ID-formatet")
        print("   ikke felles mellom de to API-ene, og hele koblingen faller.")
        return

    # -- 3. Hvilken instans er toget vi ser? --------------------------------
    valg: dict[str, dict] = {}
    for tur_id in per_id:
        kandidater: dict[str, list] = {}
        for dato in datoer:
            tur = per_dato[dato].get(tur_id)
            if not tur:
                continue
            timeline = _timeline(tur.get("estimatedCalls") or [])
            if timeline:
                kandidater[dato] = timeline
        dato, timeline, status = velg_instans(kandidater, na)
        valg[tur_id] = {"dato": dato, "timeline": timeline, "status": status}

    print("\n3. Hvilken instans traff?")
    fordeling = Counter(v["dato"] for v in valg.values() if v["dato"])
    for dato in datoer:
        print(f"   valgt kjøredato {dato}.............. {fordeling.get(dato, 0)}")
    statuser = Counter(v["status"] for v in valg.values())
    for status in ("underveis", "FERDIG", "ikke startet", "ingen tider"):
        if statuser.get(status):
            print(f"   {status:<14}............................. {statuser[status]}")

    if statuser.get("ikke startet") and fordeling.get(i_gar):
        print("\n   Blandingen er poenget: noen tog tilhører i går og noen i dag.")
        print("   Én fast dato ville tatt feil på halvparten.")

    # -- 4. Finnes sanntid i det hele tatt? --------------------------------
    print("\n4. Har Journey Planner sanntid for de valgte instansene?")
    print("   Bare den valgte instansen telles. Telles begge datoene, drukner")
    print("   målingene i morgendagens rutetabell - som er nøyaktig feilen")
    print("   19. august klokka 02.\n")

    tilstander: Counter = Counter()
    stopp_totalt = 0
    stopp_maalt = 0

    for v in valg.values():
        for stopp in v["timeline"]:
            stopp_totalt += 1
            tilstander[stopp["state"] or "(tom)"] += 1
            if _measured_delay(stopp) is not None:
                stopp_maalt += 1

    print(f"   stopp totalt................................ {stopp_totalt}")
    for tilstand, antall in tilstander.most_common():
        print(f"      realtimeState = {tilstand:<14} {antall}")
    print(f"   stopp med ekte måling....................... {stopp_maalt}")

    if not stopp_maalt:
        # Vakten. Uten den melder proben «veien er stengt» hver eneste natt.
        # Et skript som kan konkludere fra fravær, kommer til å gjøre det -
        # dette er tredje gang i dette prosjektet, og de to første ligger i
        # OKT-18-AUGUST.md.
        kjorende = [v for v in valg.values()
                    if v["status"] in ("underveis", "FERDIG")]
        ikke_kjort = [v for v in valg.values() if v["status"] == "ikke startet"]

        if not kjorende:
            print(f"\n   INGEN av de {len(valg)} valgte instansene har startet ennå")
            print("   - og en tur som ikke har kjørt har rutetid på hvert stopp.")
            print("   Dette er feil instans, ikke fravær av sanntid. Proben kan")
            print("   ikke konkludere om Journey Planner herfra.")
            print("\n   Sannsynlig årsak: turene i feeden tilhører en kjøredato")
            print("   som ikke ble hentet, eller Entur svarer med neste instans")
            print("   uansett dato. Kjør `python prober/sjekk_dato.py` - er den")
            print("   forskjøvet +24 t der også, er det samme rot som problem 3.")
            return

        print(f"\n   INGEN stopp har sanntid, og {len(kjorende)} av instansene")
        print("   ER underveis eller ferdige. Da er denne veien faktisk stengt,")
        print("   og `delay` fra Vehicle Positions er alt vi har. Se lærdom 5:")
        print("   forventet lik planlagt er fravær av måling, ikke en nullmåling.")
        if ikke_kjort:
            print(f"\n   ({len(ikke_kjort)} instanser som ikke har startet er holdt")
            print("   utenfor - de kan ikke telle for eller imot.)")
        return

    # -- 5. Sammenligning ---------------------------------------------------
    print("\n5. Vehicle Positions mot Journey Planner")
    print("   linje  tog     VP delay   JP delay     avvik  kjøredato   status")
    print("   " + "-" * 74)

    rader = []
    for tur_id, vehicle in per_id.items():
        v = valg[tur_id]
        timeline = v["timeline"]
        rad = {
            "linje": _line_code(vehicle.get("line")),
            "tog": _train_number(vehicle) or "—",
            "kode": tur_id.split(":", 1)[0],
            "vp": vehicle["delay"],
            "jp": None,
            "dato": v["dato"] or "—",
            "status": v["status"],
        }

        # En instans som ikke har startet har ingenting å lese bakover fra.
        # `_last_measured` ville returnert None uansett, men å hoppe over den
        # eksplisitt gjør det umulig å forveksle «ikke målt» med «målt til 0».
        if v["status"] in ("underveis", "FERDIG"):
            index = len(timeline) - 1
            for i in range(len(timeline) - 1):
                if timeline[i]["at"] <= na <= timeline[i + 1]["at"]:
                    index = i
                    break
            rad["jp"] = _last_measured(timeline, index)

        rader.append(rad)

    for rad in sorted(rader, key=lambda r: -(r["vp"] or 0)):
        jp = f"{rad['jp'] / 60:>7.1f} m" if rad["jp"] is not None else "      —"
        avvik = (
            f"{(rad['vp'] - rad['jp']) / 60:>7.1f} m"
            if rad["jp"] is not None else "      —"
        )
        dato_kort = rad["dato"][5:] if rad["dato"] != "—" else "—"
        print(f"   {rad['linje']:<6} {rad['tog']:<6} {rad['vp'] / 60:>8.1f} m "
              f"{jp} {avvik}  {dato_kort:<10} {rad['status']}")

    # -- 6. Konklusjon ------------------------------------------------------
    print("\n6. Konklusjon")

    maalte = [r for r in rader if r["jp"] is not None]
    for rad in maalte:
        rad["enig"] = abs(rad["vp"] - rad["jp"]) <= 60

    uten_maling = [r for r in rader if r["jp"] is None]
    ikke_startet = [r for r in uten_maling if r["status"] == "ikke startet"]

    print(f"   sammenlignbare.............................. {len(maalte)}")
    print(f"   uten måling fra Journey Planner............. {len(uten_maling)}")
    if ikke_startet:
        print(f"      av dem: instans ikke startet............. {len(ikke_startet)}"
              "   (ikke et hull i JP)")

    if not maalte:
        print("\n   Ingen tur ga en måling. Se raden over før du konkluderer:")
        print("   er alle instansene ustartede, er det datoen som er feil.")
        return

    # Krysstabellen er hele poenget. Å telle hvor mange tog som er FERDIGE
    # svarer ikke på noe: et ferdig tog der begge kildene sier 17,4 minutter
    # er ikke et spøkelse, det er et tog som faktisk var forsinket og kom
    # fram. Spørsmålet er om UENIGHETEN følger tilstanden.
    print("\n   Følger uenigheten om turen er over?")
    print("   status           enige   uenige")
    print("   " + "-" * 34)
    for status in ("underveis", "FERDIG"):
        gruppe = [r for r in maalte if r["status"] == status]
        if not gruppe:
            continue
        enige_n = sum(1 for r in gruppe if r["enig"])
        print(f"   {status:<14} {enige_n:>7}   {len(gruppe) - enige_n:>6}")

    print("\n   Uenighet per kodespak:")
    for kode in sorted({r["kode"] for r in maalte}):
        gruppe = [r for r in maalte if r["kode"] == kode]
        u = [r for r in gruppe if not r["enig"]]
        verst = max((abs(r["vp"] - r["jp"]) for r in u), default=0)
        hale = f", verst {verst / 60:.1f} min" if u else ""
        print(f"      {kode:<5} {len(u)} av {len(gruppe)} uenige{hale}")

    ferdige = [r for r in maalte if r["status"] == "FERDIG"]
    ferdige_oppblast = [r for r in ferdige if not r["enig"] and r["vp"] > r["jp"]]

    print()
    if ferdige and len(ferdige_oppblast) == len(ferdige):
        print("   SAMTLIGE ferdige tog melder høyere forsinkelse enn Journey")
        print("   Planner, og ingen av de underveis gjør det. Det er")
        print("   spøkelseshypotesen: `delay` vokser mot en rutetid toget")
        print("   aldri skal innfri. Fiksen hører hjemme ved siden av")
        print("   STALE_AFTER_SECONDS i entur.py.")
    elif ferdige:
        print(f"   {len(ferdige_oppblast)} av {len(ferdige)} ferdige tog er blåst opp.")
        print("   «Turen er over» forklarer altså ikke uenigheten alene. Se")
        print("   kodespak-tabellen: er uenigheten samlet hos én operatør, er")
        print("   det operatøren og ikke tilstanden som er forklaringen - og")
        print("   da er det den observasjonen som skal i issuet.")
    else:
        print("   Ingen av togene er ferdige med turen sin. Det er forventet")
        print("   midt på dagen. Kjør igjen mellom 22 og 24.")

    if any(not r["enig"] for r in maalte):
        print("\n   Der kildene er uenige, er Journey Planner den vi kan")
        print("   forsvare: den måler planlagt mot forventet på et navngitt")
        print("   stopp. `delay` er et tall uten oppgitt referanse.")


if __name__ == "__main__":
    try:
        hovedlop()
    except RuntimeError as feil:
        print(f"\nEntur avviste spørringen: {feil}")
    except httpx.HTTPError as feil:
        print(f"\nNettverksfeil: {feil}")
