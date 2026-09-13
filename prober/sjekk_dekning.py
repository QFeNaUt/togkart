"""Hvor mange tog Entur ikke har posisjon for.

Kjør:  python prober/sjekk_dekning.py

Bakgrunn
--------
21. august ble kartet sammenliknet med togkart.banenor.no, som tegner fra Bane
NORs eget signalanlegg og dermed ser hvert tog på sporet uansett hva
operatøren publiserer. Stikkprøve på 24 tog gav 14 treff. Saken er lukket -
se «Tog som manglet i kartet» i docs/undersokelser.md.

Tre årsaker, og bare denne proben kan måle den tredje løpende:

  1. Gods kommer aldri i Entur. Ikke noe å måle.
  2. SJ Norge publiserer ingen GPS. Vi regner posisjonen selv i `sjnord.py`.
  3. **Vy publiserer GPS for bare noen av sine egne turer.** RE10 311 og 316
     lå i Journey Planner med `realtime: true` og sanntidsavvik, men fantes
     ikke i Vehicle Positions. De var usynlige i kartet vårt mens Bane NOR
     tegnet dem. Fra 21. august regnes de ut på samme måte som SJ-togene, og
     denne proben er målingen av hvor mye det tettet.

Hva proben gjør
---------------
Den spør Vehicle Positions om alt som ruller, og Journey Planner om hvilke
turer som er underveis forbi et knippe knutepunkter. Turer som Journey Planner
kjenner MED sanntid, men som Vehicle Positions ikke har posisjon for, er
hullet.

Fra 21. august tegner kartet dem: `sjnord.py` regner posisjonen fra rutetid og
sporgeometri for enhver tur uten målt posisjon, ikke bare for SJ. Proben måler
derfor to ting nå, og de må holdes fra hverandre:

  * **hullet** - turer Vehicle Positions ikke har posisjon for.
  * **resten** - turer vi heller ikke klarer å regne ut. Det er de som ikke er
    underveis akkurat nå (helt normalt: hullet inneholder turer som gikk for
    timer siden) og de som mangler brukbare stoppetider.

Det første tallet skal IKKE gå ned - det er Enturs dekning og ikke vår. Det
andre er vårt.

Uten Bane NOR som fasit er begge undertall: en tur som ingen av de to
Entur-kildene kjenner, kan ikke telles her.

Ingenting her endrer noe. Proben leser.
"""

import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from dotenv import load_dotenv

import entur
import sjnord

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

VP_URL = "https://api.entur.io/realtime/v2/vehicles/graphql"
JP_URL = "https://api.entur.io/journey-planner/v3/graphql"

# Knutepunkter å måle forbi. Valgt for å dekke korridorene der hullet er
# størst, og få nok til at proben er rask. Dovrebanen er med tre ganger fordi
# det var der funnet ble gjort.
#
# NAVN, ikke ID-er. Første utgave skrev ID-ene rett inn og fikk to av seks
# feil: Oslo S og Drammen var byttet om, og Lillestrøm pekte på et sted som
# ikke finnes. Feilen var stille - `stopPlace` svarer villig for enhver gyldig
# ID, og Lillestrøm rapporterte null tog uten at noe så galt ut. ID-ene slås
# derfor opp i `static/stasjoner.geojson`, som er bygget av lagstasjoner.py og
# er den samme fasiten resten av kartet bruker.
KNUTEPUNKTER = [
    "Moelv", "Hamar", "Lillehammer", "Oslo S", "Drammen", "Lillestrøm",
    "Asker", "Ski",
]

STASJONER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "static", "stasjoner.geojson",
)


def _stasjons_id() -> dict[str, str]:
    """Navn -> NSR-ID, fra fila kartet allerede bruker."""
    with open(STASJONER, encoding="utf-8") as fil:
        data = json.load(fil)
    ut = {}
    for f in data["features"]:
        navn = (f["properties"].get("navn") or "").removesuffix(" stasjon")
        if navn and f["properties"].get("id"):
            ut[navn] = f["properties"]["id"]
    return ut

# Hvor langt tilbake og fram vi ser etter avganger. Et tog som gikk fra
# knutepunktet for under en time siden er som regel fortsatt underveis.
TILBAKE_MIN = 60
FRAM_MIN = 20

# Under dette antallet turer sier proben ingenting om andelen. En andel uten
# nevner er ikke en måling: to av to er 100 %, og om natta er det alt som går.
MINST_UTVALG = 20

VP_SPORRING = """
{
  vehicles(mode: RAIL) {
    vehicleId
    line { publicCode }
    serviceJourney { id }
    datedServiceJourney { id }
    codespace { codespaceId }
  }
}
"""

JP_SPORRING = """
query($id: String!, $tid: DateTime!, $vindu: Int!) {
  stopPlace(id: $id) {
    name
    estimatedCalls(startTime: $tid, timeRange: $vindu,
                   numberOfDepartures: 60, arrivalDeparture: both) {
      realtime
      expectedDepartureTime
      serviceJourney {
        id
        line { publicCode transportMode }
        operator { id }
      }
    }
  }
}
"""


async def _post(klient: httpx.AsyncClient, url: str, sporring: str, variabler=None):
    svar = await klient.post(
        url, json={"query": sporring, "variables": variabler or {}}
    )
    svar.raise_for_status()
    payload = svar.json()
    if payload.get("errors"):
        raise RuntimeError(payload["errors"][0].get("message", "?"))
    return payload["data"]


def _turnokkel(sj_id: str) -> str:
    """ServiceJourney-ID uten datodelen, slik VP og JP kan sammenliknes.

    VP gir `serviceJourney.id` på formen `VYG:ServiceJourney:314-LHM_444167-R`,
    JP gir det samme. Nøkkelen er derfor hele ID-en - men vi klipper vekk
    kodrommet foran, fordi de to kildene av og til bruker ulikt prefiks for
    samme selskap (se punkt 1 i avvik.py om NSB mot VYG).
    """
    return sj_id.rsplit(":", 1)[-1]


async def hoved() -> int:
    load_dotenv()
    navn = os.getenv("ET_CLIENT_NAME", "").strip()
    if not navn:
        print("ET_CLIENT_NAME er ikke satt. Kopier .env.example til .env.")
        return 1

    feil = 0
    na = datetime.now(timezone.utc)

    async with httpx.AsyncClient(
        timeout=30.0, headers={"ET-Client-Name": navn}
    ) as klient:
        # --- 1. Hva Vehicle Positions har ---------------------------------
        print("=" * 72)
        print("1. HVA VEHICLE POSITIONS HAR")
        print("=" * 72)

        vp = (await _post(klient, VP_URL, VP_SPORRING))["vehicles"]

        # Nøklene i to former, nøyaktig som app.py bygger dem. Tur-ID-en er
        # den presise, men den holder ikke alene: for noen Vy-tog svarer
        # Vehicle Positions med en DatedServiceJourney-ID der Journey Planner
        # gir en ServiceJourney-ID, og de to er ulike ID-rom for samme tog.
        # Måler man bare den ene, ser fem tog med GPS ut som et hull.
        vp_turer: set[str] = set()
        for kjoretoy in vp:
            for felt in ("serviceJourney", "datedServiceJourney"):
                sj_id = (kjoretoy.get(felt) or {}).get("id")
                if sj_id:
                    vp_turer.add(_turnokkel(sj_id))
            nummer = entur._train_number(kjoretoy)
            if nummer:
                vp_turer.add(sjnord.tognokkel(
                    (kjoretoy.get("line") or {}).get("publicCode"), nummer))

        datert = sum(
            1 for v in vp
            if "DatedServiceJourney" in ((v.get("serviceJourney") or {}).get("id") or "")
        )
        kodrom = Counter((v.get("codespace") or {}).get("codespaceId") for v in vp)
        linjer = Counter((v.get("line") or {}).get("publicCode") for v in vp)

        print(f"  {len(vp)} kjøretøy på skinner")
        print(f"  kodrom: {dict(kodrom)}")
        print(f"  {len(vp_turer)} nøkler (tur-ID-er og linje:tognummer)")
        if datert:
            print(f"  {datert} av dem svarer med DatedServiceJourney-ID der")
            print("  Journey Planner gir ServiceJourney-ID. Derfor to nøkkelformer.")

        if "SJN" in kodrom:
            print("\n  MERK: SJN er begynt å publisere posisjoner. Da kan")
            print("  beregningen i sjnord.py trolig slås av for SJ.")
        if len(kodrom) < 3:
            print(f"\n  ADVARSEL: bare {len(kodrom)} kodrom svarer. Normalt er")
            print("  det tre: VYG, FLT og GOA.")
            feil += 1

        # --- 2. Hva Journey Planner vet om ------------------------------
        print()
        print("=" * 72)
        print("2. TURER UNDERVEIS FORBI KNUTEPUNKTENE")
        print("=" * 72)

        fra = (na - timedelta(minutes=TILBAKE_MIN)).isoformat()
        vindu = (TILBAKE_MIN + FRAM_MIN) * 60

        ider = _stasjons_id()
        ukjente = [n for n in KNUTEPUNKTER if n not in ider]
        if ukjente:
            print(f"  FEIL: ukjent(e) stasjon(er) i KNUTEPUNKTER: {ukjente}")
            print("  Navnene må stemme med static/stasjoner.geojson.")
            feil += 1

        jp_turer: dict[str, dict] = {}
        for stedsnavn in KNUTEPUNKTER:
            sted_id = ider.get(stedsnavn)
            if not sted_id:
                continue
            data = await _post(
                klient, JP_URL, JP_SPORRING,
                {"id": sted_id, "tid": fra, "vindu": vindu},
            )
            sp = data.get("stopPlace")
            if not sp:
                print(f"  {stedsnavn:<14} ukjent stoppested")
                continue
            treff = 0
            for kall in sp["estimatedCalls"]:
                sj = kall.get("serviceJourney") or {}
                if not sj.get("id"):
                    continue
                # Uten sanntid er turen bare en rutetabellinje. Den sier ikke
                # at toget faktisk ruller nå.
                if not kall.get("realtime"):
                    continue
                linje = sj.get("line") or {}
                # Stasjonene betjenes av buss også - Ruter, Brakar og
                # Innlandstrafikk lå i svaret med linjene 100, 256 og B60.
                # Dette er et togkart, og et hull i bussdekningen er ikke
                # vårt hull.
                if linje.get("transportMode") != "rail":
                    continue
                jp_turer[_turnokkel(sj["id"])] = {
                    "id": sj["id"],
                    "linje": linje.get("publicCode"),
                    "operator": (sj.get("operator") or {}).get("id"),
                    "sted": stedsnavn,
                }
                treff += 1
            print(f"  {stedsnavn:<14} {treff:>3} sanntidsturer på skinner i vinduet")

        print(f"\n  {len(jp_turer)} unike turer med sanntid til sammen")

        # --- 3. Hullet ----------------------------------------------------
        print()
        print("=" * 72)
        print("3. TURER MED SANNTID, UTEN POSISJON")
        print("=" * 72)
        print("  Journey Planner vet at toget ruller og hvor forsinket det er.")
        print("  Vehicle Positions har ingen posisjon. Dette er togene som")
        print("  mangler i kartet - og som kunne vært beregnet.\n")

        mangler = {
            k: v for k, v in jp_turer.items()
            if k not in vp_turer
            and sjnord.tognokkel(v["linje"], sjnord._journey_number(v["id"]))
            not in vp_turer
        }
        per_linje = Counter(v["linje"] for v in mangler.values())
        per_operator = Counter(v["operator"] for v in mangler.values())

        if not jp_turer:
            print("  Ingen turer å måle mot. Kjører det tog nå?")
            return feil

        andel = len(mangler) / len(jp_turer)
        print(f"  {len(mangler)} av {len(jp_turer)} turer mangler posisjon "
              f"({andel:.0%})")
        print(f"  per operatør: {dict(per_operator)}")
        print(f"  per linje:    {dict(sorted(per_linje.items(), key=lambda t: str(t[0])))}")

        # Terskelen er satt godt over det som ble målt 21. august (40 % med
        # bussene luket bort), slik at proben sier fra når det blir vesentlig
        # verre - ikke hver gang den kjøres. Hullet er stort allerede; det
        # denne vakten skal fange er at det VOKSER.
        #
        # Merk at dette IKKE er en måling av kartet vårt. Det er Enturs
        # dekning, og den blir ikke bedre av at vi regner ut posisjoner.
        #
        # MINST_UTVALG er der fordi en andel uten nevner ikke er en måling.
        # Kjørt 02:10 natt til 22. august fant proben to turer i vinduet, og
        # begge manglet posisjon: «100 % mangler posisjon», FEIL, og ingenting
        # galt. Om natta går det knapt tog forbi knutepunktene på Østlandet.
        # Samme felle som lærdom 3 - en prosent av to er ikke et funn.
        if len(jp_turer) < MINST_UTVALG:
            print(f"\n  For få turer ({len(jp_turer)}) til å si noe om andelen.")
            print(f"  Vakten krever minst {MINST_UTVALG}. Kjør på dagtid.")
        elif andel > 0.60:
            print("\n  FEIL: over 60 % av turene mangler posisjon. Enten har")
            print("  en operatør sluttet å publisere, eller så er noe galt med")
            print("  sammenlikningen av ServiceJourney-ID-er.")
            feil += 1

        # --- 4. Hvor mye av hullet tegner vi? -----------------------------
        print()
        print("=" * 72)
        print("4. HVOR MYE AV HULLET KARTET FAKTISK TEGNER")
        print("=" * 72)
        print("  Samme maskineri som app.py bruker, men med proben sine egne")
        print("  knutepunkter som fasit - så dette er ikke en sirkelslutning.\n")

        # Nøklene app.py sender inn: rå tur-ID OG «linje:tognummer». Begge
        # trengs, fordi Vehicle Positions svarer med DatedServiceJourney-ID for
        # noen Vy-tog der Journey Planner gir ServiceJourney-ID.
        har_gps: set[str] = set()
        for kjoretoy in vp:
            for felt in ("serviceJourney", "datedServiceJourney"):
                sj_id = (kjoretoy.get(felt) or {}).get("id")
                if sj_id:
                    har_gps.add(sj_id)
            nummer = entur._train_number(kjoretoy)
            if nummer:
                har_gps.add(sjnord.tognokkel(
                    (kjoretoy.get("line") or {}).get("publicCode"), nummer))

        journeys = await sjnord.fetch_journeys(navn, har_gps=har_gps)
        beregnet = sjnord.positions(journeys, hopp_over=har_gps)
        tegnet = {
            _turnokkel(f["properties"]["id"]): f["properties"] for f in beregnet
        }
        # Punkt 5 trenger de hele ID-ene, ikke turnøklene.
        tegnet_full = {f["properties"]["id"] for f in beregnet}

        traff = {k: v for k, v in mangler.items() if k in tegnet}
        bom = {k: v for k, v in mangler.items() if k not in tegnet}

        print(f"  {len(traff)} av {len(mangler)} turer i hullet tegnes nå "
              f"({len(traff) / max(len(mangler), 1):.0%})")
        grunner = Counter(tegnet[k].get("computedReason") for k in traff)
        print(f"  grunn: {dict(grunner)}")
        spor = sum(1 for k in traff if tegnet[k].get("positionMethod") == "track")
        print(f"  {spor} av {len(traff)} ligger på sporet, resten i luftlinje")

        # Hvorfor de øvrige ikke tegnes. Uten denne oppdelingen er «33 turer
        # tegnes ikke» et tall man ikke kan gjøre noe med: det store flertallet
        # er turer som ble ferdige for lengst, og det er ikke et hull i kartet.
        # Den siste kategorien er den eneste som er vår feil.
        aarsaker: Counter = Counter()
        uforklart: list[str] = []
        for nokkel, verdi in bom.items():
            forberedt = sjnord._FORBEREDT.get(verdi["id"])
            if forberedt is None:
                # sjnord bruker sine egne knutepunkter. Ser de ikke turen i det
                # hele tatt, er det dekningen i knutepunktlista som er saken.
                aarsaker["ikke sett av knutepunktene i sjnord.py"] += 1
                continue
            tidslinje = forberedt.get("timeline") or []
            if len(tidslinje) < 2:
                aarsaker["ingen brukbare stoppetider"] += 1
            elif tidslinje[-1]["at"] < na:
                aarsaker["turen er ferdig"] += 1
            elif tidslinje[0]["at"] > na:
                aarsaker["turen har ikke startet"] += 1
            else:
                aarsaker["underveis, men ingen posisjon"] += 1
                uforklart.append(f'{verdi["linje"]} {verdi["id"][:34]}')

        if bom:
            print(f"\n  {len(bom)} turer tegnes fortsatt ikke:")
            for grunn, antall in aarsaker.most_common():
                print(f"    {antall:>3}  {grunn}")

        # Vakten her er vår og ikke Enturs. De tre første årsakene er ventet -
        # et hull målt over en time inneholder turer som for lengst er kjørt
        # ferdig. Den fjerde er ikke: en tur som er underveis SKAL få en
        # posisjon, og at den ikke gjør det er det eneste som skiller «ingen
        # posisjon fordi toget ikke kjører» fra «ingen posisjon fordi noe er
        # ødelagt».
        if uforklart:
            print(f"\n  FEIL: {len(uforklart)} turer er underveis nå, men fikk")
            print("  ingen beregnet posisjon. Det skal ikke skje.")
            for rad in uforklart[:8]:
                print(f"    {rad}")
            feil += 1

        # --- 5. Dekning per operatør, over sjnord sine EGNE knutepunkter ----
        #
        # Punkt 4 måler mot proben sine åtte knutepunkter på Østlandet, og det
        # er med vilje: det gjør sjekken uavhengig. Prisen er at SJ nesten
        # ikke er representert - Nordlandsbanen og Rørosbanen går ikke forbi
        # Ski. Denne delen ser på ALT sjnord hentet, delt på kodeområde, og
        # den koster ingen nye kall: både turene og posisjonene ligger klare
        # fra punkt 4.
        #
        # Den finnes fordi SJ-dekningen en gang var 12 av 16 uten at noe
        # målte det. Se «SJ-dekningen: 12 av 16» i docs/undersokelser.md.
        print()
        print("=" * 72)
        print("5. AV ALT SJNORD HENTET - HVA BLE TEGNET, PER OPERATØR")
        print("=" * 72)

        tegnet_ider = set(tegnet_full)
        per_kode: dict[str, Counter] = defaultdict(Counter)
        underveis_uten: list[str] = []

        for tur_id in journeys:
            kode = tur_id.split(":", 1)[0]
            if tur_id in tegnet_ider:
                per_kode[kode]["tegnet"] += 1
                continue
            forberedt = sjnord._FORBEREDT.get(tur_id) or {}
            tidslinje = forberedt.get("timeline") or []
            if len(tidslinje) < 2:
                per_kode[kode]["ingen stoppetider"] += 1
            elif tidslinje[-1]["at"] < na:
                per_kode[kode]["ferdig"] += 1
            elif tidslinje[0]["at"] > na:
                per_kode[kode]["ikke startet"] += 1
            else:
                per_kode[kode]["UNDERVEIS UTEN POSISJON"] += 1
                underveis_uten.append(f"{kode} {tur_id[:44]}")

        print(f"  {'kode':<6}{'hentet':>8}{'tegnet':>8}{'ferdig':>8}"
              f"{'ikke st.':>10}{'u/tider':>9}{'UTEN POS':>10}")
        for kode in sorted(per_kode):
            c = per_kode[kode]
            print(f"  {kode:<6}{sum(c.values()):>8}{c['tegnet']:>8}"
                  f"{c['ferdig']:>8}{c['ikke startet']:>10}"
                  f"{c['ingen stoppetider']:>9}{c['UNDERVEIS UTEN POSISJON']:>10}")

        if "SJN" not in per_kode:
            print("\n  MERK: ingen SJN-turer i utvalget. Om natta er det normalt -")
            print("  SJ kjører få tog. Står det slik midt på dagen, ser ikke")
            print("  knutepunktlista de nordlige banene lenger.")

        # Samme vakt som i punkt 4, men per operatør: en tur som er underveis
        # SKAL få en posisjon. Det var nettopp den linja som manglet da
        # SJ-dekningen var 12 av 16 - ingen målte den, så ingen så det.
        if underveis_uten:
            print(f"\n  FEIL: {len(underveis_uten)} turer er underveis, men fikk")
            print("  ingen posisjon:")
            for rad in underveis_uten[:8]:
                print(f"    {rad}")
            feil += 1

        if len(mangler) >= MINST_UTVALG and not traff:
            print("\n  FEIL: ingen av turene i hullet ble tegnet. Kjører")
            print("  beregningen i det hele tatt? Se `python sjnord.py`.")
            feil += 1

    print()
    if feil:
        print(f"{feil} ting å se på.")
    else:
        print("Dekningen er som forventet. Se docs/undersokelser.md for",
              "hva tallene betydde da hullet ble tettet.")
    return 1 if feil else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(hoved()))
