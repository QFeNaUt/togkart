"""Verifiser driftsmeldingene bak nyhetsstripa.

Kjør:  python prober/sjekk_avvik.py

`python avvik.py` viser hva stripa ville rullet nå. Denne proben spør de
spørsmålene man ikke ser svaret på ved å se på stripa:

  1. Hvilke kodrom svarer i det hele tatt? Vy publiserer under NSB og ikke
     VYG, og Flytoget og SJ Nord publiserte ingenting 20. august. Slutter NSB
     å svare - eller begynner SJN - er det her det synes først.

  2. Slår nivåreglene riktig? Fargen er utledet av alvorsgrad OG tekst, fordi
     alvorsgraden alene er «normal» for nesten alt. Proben viser hvilken regel
     som avgjorde hver melding, så en regel som har sluttet å treffe blir
     synlig i stedet for å gi alt samme farge.

  3. Hvor mye slås sammen? Feeden gjentar seg kraftig. Faller
     sammenslåingsgraden mot null, har Entur endret tekstene og dedupen
     nøkler på noe som ikke lenger er likt.

  4. Er meldingene stedfestet? En melding uten steder kan ikke klikkes fram i
     kartet. Andelen uten sted er hele kartkoblingens dekningsgrad.

  5. HVOR FERSKE ER DE? Dette er den sjekken som fanger den feilen stripa
     hadde 21. august: seks av femten meldinger var planlagt arbeid som
     startet opptil 57 DØGN fram i tid, og sju var mellom 30 og 68 timer
     gamle. Bare to av femten var fra de siste to timene. Ingenting så galt
     ut - stripa rullet like pent som før, med feil innhold.

     Derfor måler proben aldersfordelingen og sier fra når den skrider ut.
     Se `MAKS_ALDER_TIMER` og `VARSEL_TIMER` i avvik.py.

Ingenting her endrer noe. Proben leser.
"""

import asyncio
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from dotenv import load_dotenv

from avvik import (
    GRUPPE_MAKS_SAMMENDRAG,
    KODEROM,
    MAKS_ALDER_TIMER,
    MAKS_MELDINGER,
    MIN_GRUPPE,
    MIN_STREKNING_STEDER,
    VARSEL_TIMER,
    VISNINGSORDEN,
    SITUASJON_URL,
    SPORRING,
    STORT_FRA_MINUTTER,
    _MINUTTER,
    _klokke,
    _norsk,
    _RETTET,
    _STORT,
    _endepunkter,
    paafor_kart,
    til_meldinger,
)
from jernbanenett import _lengde

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _hvorfor(s: dict) -> str:
    """Hvilken regel i _niva() avgjorde denne meldingen.

    Speiler rekkefølgen i avvik._niva(). Endrer du den ene, må den andre
    følge etter - derfor står de to funksjonene bevisst kort og like.
    """
    sammendrag = _norsk(s.get("summary"))
    beskrivelse = _norsk(s.get("description"))
    tekst = f"{sammendrag} {beskrivelse}"
    alvor = s.get("severity") or ""

    if _RETTET.search(tekst):
        return "tekst: rettet"
    if alvor == "noImpact":
        return "alvor: noImpact"
    if alvor in ("severe", "verySevere"):
        return f"alvor: {alvor}"
    if _STORT.search(tekst):
        return f"tekst: {_STORT.search(tekst).group(0).lower()}"
    minutter = _MINUTTER.search(tekst)
    if minutter and int(minutter.group(1)) >= STORT_FRA_MINUTTER:
        return f"minutter: {minutter.group(1)}"
    return f"type: {s.get('reportType') or '—'}"


async def _hent(client_name: str, koderom: list[str]) -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        svar = await client.post(
            SITUASJON_URL,
            json={"query": SPORRING, "variables": {"koderom": koderom}},
            headers={"ET-Client-Name": client_name},
        )
        svar.raise_for_status()
        payload = svar.json()
    if payload.get("errors"):
        raise RuntimeError(payload["errors"][0].get("message", "?"))
    return (payload.get("data") or {}).get("situations") or []


async def hoved() -> int:
    load_dotenv()
    client_name = os.getenv("ET_CLIENT_NAME", "").strip()
    if not client_name:
        print("ET_CLIENT_NAME er ikke satt. Kopier .env.example til .env.")
        return 1

    feil = 0

    # --- 1. Kodrommene ----------------------------------------------------
    print("=" * 72)
    print("1. HVILKE KODEROM SVARER")
    print("=" * 72)

    per_kodrom: dict[str, int] = {}
    for kode in KODEROM:
        try:
            per_kodrom[kode] = len(await _hent(client_name, [kode]))
        except Exception as exc:  # noqa: BLE001
            print(f"  {kode:<5} FEIL: {exc}")
            per_kodrom[kode] = 0
    for kode, antall in per_kodrom.items():
        merke = "" if antall else "   (ingen - normalt for FLT/SJN/VYG)"
        print(f"  {kode:<5} {antall:>4} situasjoner{merke}")

    if not sum(per_kodrom.values()):
        print("\n  FEIL: ingen kodrom svarte. Stripa blir tom.")
        return 1
    if not per_kodrom.get("NSB"):
        print("\n  ADVARSEL: NSB ga null. Vy publiserer der - er noe endret?")
        feil += 1

    situasjoner = await _hent(client_name, KODEROM)

    # --- 2. Nivåreglene ---------------------------------------------------
    print()
    print("=" * 72)
    print("2. HVILKEN REGEL AVGJORDE FARGEN")
    print("=" * 72)

    meldinger, forkastet = til_meldinger(situasjoner)
    regler = Counter(_hvorfor(s) for s in situasjoner)
    for regel, antall in regler.most_common():
        print(f"  {antall:>4}  {regel}")

    if not any(r.startswith("minutter") for r in regler):
        print("\n  Merk: minuttregelen slo ikke til nå. Den leser tall ut av")
        print("  fritekst, så den er stille helt til noen skriver «30 minutter».")

    # --- 3. Sammenslåing --------------------------------------------------
    print()
    print("=" * 72)
    print("3. SAMMENSLÅING OG UTVALG")
    print("=" * 72)

    unike = len({_norsk(s.get("description")) or _norsk(s.get("summary"))
                 for s in situasjoner})
    print(f"  {len(situasjoner):>4} situasjoner fra Entur")
    print(f"  {unike:>4} unike tekster etter sammenslåing")
    print(f"  {len(meldinger):>4} i stripa (tak: {MAKS_MELDINGER})")

    # Andre passering: meldinger som deler sammendrag og er slått til én
    # linje. Se `_grupper` i avvik.py.
    gruppert = [m for m in meldinger if m["avganger"] > 1]
    if gruppert:
        print(f"\n{len(gruppert)} linjer står for flere avganger:")
        for m in gruppert:
            print(f"    {m['avganger']:>3} avganger  {m['tekst'][:52]}")

    # Sammendrag som gjentar seg men IKKE ble gruppert. Er det mange, er en
    # av vaktene i _gruppenokkel for stram - typisk lengdegrensen.
    sammendrag = Counter(
        sm for s in situasjoner
        if (sm := _norsk(s.get("summary"))) and len(sm) <= GRUPPE_MAKS_SAMMENDRAG
    )
    igjen = [
        (sm, n) for sm, n in sammendrag.items()
        if n >= MIN_GRUPPE and not any(m["tekst"] == sm for m in meldinger)
    ]
    if igjen:
        print(f"\nSammendrag som gjentar seg uten å bli gruppert:")
        for sm, n in igjen:
            print(f"    {n:>3}x  {sm[:52]}")
        print("  (normalt: de falt ut på alder, eller ligger under terskelen)")

    if situasjoner and unike == len(situasjoner):
        print("\n  ADVARSEL: ingenting ble slått sammen. Feeden pleier å")
        print("  gjenta seg - er tekstene endret slik at dedupen bommer?")
        feil += 1

    fordeling = Counter(m["niva"] for m in meldinger)
    print()
    for niva in VISNINGSORDEN:
        print(f"  {niva:<9} {fordeling.get(niva, 0):>3}")

    # --- 4. Stedfesting ---------------------------------------------------
    print()
    print("=" * 72)
    print("4. KARTKOBLING")
    print("=" * 72)

    # Geometrien er et eget ledd i avvik.py og kjøres ikke av til_meldinger.
    # Vi kaller den her, for det er nettopp den som avgjør hva kartet tegner.
    paafor_kart(meldinger)

    uten_sted = [m for m in meldinger if not m["boks"]]
    print(f"  {len(meldinger) - len(uten_sted)} av {len(meldinger)} meldinger "
          f"kan klikkes fram i kartet")
    for m in uten_sted:
        print(f"    uten sted: {m['tekst'][:60]}")

    if meldinger and len(uten_sted) == len(meldinger):
        print("\n  FEIL: ingen meldinger er stedfestet. Kartkoblingen er død.")
        feil += 1

    # Formen er den nye halvdelen av kartkoblingen, og den er verdt å telle av
    # to grunner. Faller «strekning» til null, har enten spornettet forsvunnet
    # eller tekstene endret seg slik at «mellom X og Y» ikke leses lenger -
    # og da tegner kartet ringer rundt to endestasjoner i stedet for banen
    # mellom dem, som ser helt normalt ut.
    #
    # Den andre grunnen er meldingene UTEN steder. De er ofte de verste
    # («Toget er innstilt mellom Stavanger og Oslo S» kom med null
    # stoppesteder), og for dem er teksten den eneste kilden til geometri.
    former = Counter(m["form"] or "ingenting" for m in meldinger)
    print()
    for form, antall in former.most_common():
        print(f"  {form:<12}{antall:>3}")

    fra_tekst = [
        m for m in meldinger if m["form"] == "strekning" and not m["antallSteder"]
    ]
    if fra_tekst:
        print(f"\n  {len(fra_tekst)} strekning(er) lest ut av TEKSTEN alene:")
        for m in fra_tekst:
            km = sum(
                _lengde([(p[0], p[1]) for p in linje]) for linje in m["strekninger"]
            ) / 1000
            print(f"    {km:>6.0f} km  {m['tekst'][:56]}")

    # Navneoppslaget mot faste setninger. Det som står over måler feeden slik
    # den er akkurat nå; dette måler regelen. Slutter «mellom X og Y» å bli
    # lest, blir tellingen over null en dag uten at noe feiler - og da tegner
    # kartet igjen ingenting for de meldingene som ikke har stoppesteder.
    #
    # De fire første er ekte setninger fra feeden. De to siste skal IKKE
    # treffe: «mellom klokken 10 og 12» er ikke stasjoner, og en setning uten
    # «mellom» er ikke en strekning.
    faste = [
        ("Toget er innstilt mellom Stabekk og Ski. Dette skyldes en feil.", True),
        ("... buss for tog mellom Kristiansand og Gjerstad for Sørtoget. NB:", True),
        # Oslo S og ikke Oslo: oppslaget må prøve det lengste navnet først.
        ("Ta neste tog mellom Sagdalen og Oslo S.", True),
        # Tre ord i navnet, det eneste i registeret.
        ("Sporarbeid mellom Mo i Rana og Bodø.", True),
        ("Bussen går mellom klokken 10 og 12.", False),
        ("Ta andre tog fra Skøyen og Lysaker.", False),
    ]
    bommet = [
        tekst for tekst, ventet in faste if bool(_endepunkter(tekst)) is not ventet
    ]
    if bommet:
        print(f"\n  FEIL: navneoppslaget bommet på {len(bommet)} faste setning(er):")
        for tekst in bommet:
            print(f"    {tekst[:64]}")
        feil += 1
    else:
        print(f"\n  navneoppslaget: {len(faste)} faste setninger tolket riktig")

    manglet = [
        m for m in meldinger
        if m["form"] == "steder" and m["antallSteder"] >= MIN_STREKNING_STEDER
    ]
    if manglet:
        print(f"\n  {len(manglet)} melding(er) med {MIN_STREKNING_STEDER}+ steder "
              "ble likevel IKKE en strekning.")
        print("  Én i ny og ne er greit - stasjoner som ikke henger sammen på")
        print("  ett spor. Er det mange, ruter ikke spornettet lenger:")
        for m in manglet[:5]:
            print(f"    {m['antallSteder']:>3} steder  {m['tekst'][:56]}")

    # --- 5. Ferskhet ------------------------------------------------------
    print()
    print("=" * 72)
    print("5. HVOR FERSKE ER MELDINGENE")
    print("=" * 72)
    print(f"  Grenser: eldre enn {MAKS_ALDER_TIMER} t faller ut, og en melding")
    print(f"  må ha trådt i kraft (varsel {VARSEL_TIMER} t fram).\n")

    na = datetime.now(timezone.utc)
    print("  Forkastet av portene i til_meldinger():")
    for grunn, antall in forkastet.items():
        print(f"    {grunn:<14}{antall:>4}")

    # Hva feeden HADDE å by på, uavhengig av portene. Faller antallet ferske
    # situasjoner mot null mens totalen står, er det Entur som har sluttet å
    # publisere - ikke portene som er for stramme.
    ferske = sum(
        1 for s in situasjoner
        if (k := _klokke(s)) and (na - k).total_seconds() < 3600
    )
    print(f"\n  {ferske} av {len(situasjoner)} situasjoner er under en time gamle")

    if not meldinger:
        print("\n  Stripa er tom. På en stille natt er det riktig svar - men")
        print("  står den tom midt i rushet, er en av portene for stram.")
    else:
        aldre = [
            (na - datetime.fromisoformat(m["fra"])).total_seconds() / 3600
            for m in meldinger
        ]
        print(f"\n  alder i stripa: median {statistics.median(aldre):.1f} t, "
              f"eldste {max(aldre):.1f} t, ferskeste {min(aldre)*60:.0f} min")

        # Én av dem MÅ være fersk. Er selv den yngste timer gammel, ruller
        # stripa gårsdagen - som var nettopp feilen 21. august.
        if min(aldre) > 3:
            print("\n  FEIL: selv den ferskeste meldingen er over tre timer")
            print("  gammel. Stripa viser ikke hva som skjer nå.")
            feil += 1

        # Portene skal gjøre dette umulig. Slår det til, er en av dem omgått -
        # typisk fordi `fra` ikke lenger er ferskhetsklokka.
        if max(aldre) > MAKS_ALDER_TIMER + 0.5:
            print(f"\n  FEIL: en melding er {max(aldre):.1f} t gammel, over")
            print(f"  grensen på {MAKS_ALDER_TIMER} t. Aldersporten virker ikke.")
            feil += 1

        # Ingen melding kan ha negativ alder. Skjer det, er `fra` en starttid
        # i framtiden igjen - den opprinnelige sorteringsfeilen.
        if min(aldre) < -0.1:
            print("\n  FEIL: en melding er datert i FRAMTIDEN. `fra` er ikke")
            print("  ferskhetsklokka - se _klokke() i avvik.py.")
            feil += 1

    # --- Stripa slik den blir --------------------------------------------
    print()
    print("=" * 72)
    print("6. SLIK RULLER STRIPA")
    print("=" * 72)
    for i, m in enumerate(meldinger, 1):
        linjer = ", ".join(m["linjer"]) or "—"
        stikkord = f"{m['stikkord']}: " if m["stikkord"] else ""
        alder = (na - datetime.fromisoformat(m["fra"])).total_seconds() / 60
        alder_s = f"{alder:.0f} min" if alder < 90 else f"{alder/60:.1f} t"
        print(f"  {i:>2}. [{m['niva']:<7}] {alder_s:>7} siden  "
              f"{stikkord}{m['tekst'][:56]}")
        avganger = f"{m['avganger']} avganger · " if m["avganger"] > 1 else ""
        print(f"      {linjer} · {avganger}{m['antallSteder']} steder · "
              f"{len(m['punkter'])} punkter i kartet")

    print()
    if feil:
        print(f"{feil} ting å se på.")
    else:
        print("Alt ser riktig ut.")
    return 1 if feil else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(hoved()))
