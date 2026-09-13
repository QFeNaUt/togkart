"""
lagbaner.py — bygg `static/hovedbaner.geojson` fra OpenStreetMap eller Entur.

Hvorfor dette skriptet finnes
-----------------------------
Jernbanen du ser i kartet i dag kommer fra bakgrunnskartets vektorfliser
(`rail-network` i app.js). De flisene kan vi ikke styre: vi kan ikke be dem
beholde Dovrebanen og skjule sidesporene, fordi `transportation`-laget bare
har `class`/`subclass` — ingen banenavn å filtrere på. Og langt ute i zoom
ligger det som regel ingen jernbane i flisene i det hele tatt.

Løsningen er å eie geometrien selv for de banene som alltid skal være synlige.

To kilder, med ulike styrker
----------------------------
**OpenStreetMap (standard).** Banene ligger der som navngitt infrastruktur.
Vi henter alle jernbanelinjer som heter «Dovrebanen» og tegner dem. Ingen
geokoding, ingen reisesøk, ingen skjøting — til et visningslag spiller det
ingen rolle om linjen kommer som femti biter, for de tegnes uansett hver for
seg. Kilden beskriver *sporet*, og oppdateres når nye baner åpner.

**Entur (reserve).** Reisesøk mellom to endestasjoner, med traseen som
Google-kodet polylinje i `pointsOnLink` — samme felt `sjnord.py` bruker for å
legge SJ-togene på sporet. Kilden beskriver *det et tog faktisk kjører*, og
er derfor den eneste som kan svare på «Trønderbanen», som er et togtilbud og
ikke en bane. Prisen er at du får én bestemt rute, ikke hele korridoren.

Hver bane merkes med `kilde` i GeoJSON-en, av samme grunn som posisjonene
merkes `positionMethod` i `sjnord.py`: du skal kunne se på dataene hvilken av
dem du ser på.

Dette er et **byggeskript**, ikke en del av appen. Kjør det når du vil,
sjekk resultatet inn i git, og la appen servere en statisk fil. Ingen
API-kall i drift.

Kjøring
-------
    python lagbaner.py                     # OSM, med Entur som reserve
    python lagbaner.py --kilde osm         # bare OSM, ingen reserve
    python lagbaner.py --kilde entur       # bare Entur, som før
    python lagbaner.py --bane Jærbanen
    python lagbaner.py --selvtest          # tester forenkling og OSM-tolkning, uten nett
    python lagbaner.py --enum StreetMode   # lovlige verdier for en type i Entur-skjemaet
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, time as klokkeslett, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Vi låner dekoderen og avstandsmatematikken fra modulen som allerede finnes.
# `_projiser_pa_segment` har understrek foran fordi den var ment som intern i
# sporgeometri.py. Alternativet var å skrive projeksjonen på nytt her, og to
# kopier av den samme matematikken er verre enn ett lån på tvers av moduler.
from sporgeometri import (
    Punkt,
    _projiser_pa_segment,
    avstand_m,
    decode_polyline,
    forenkle,
)

load_dotenv()

JOURNEY_URL = "https://api.entur.io/journey-planner/v3/graphql"
GEOCODER_URL = "https://api.entur.io/geocoder/v3/autocomplete"
# Flere speil med samme data. Hovedserveren er den travleste; når den svarer
# 504 eller 429, er det som regel nok å spørre en annen.
# Begge har hele planeten. Regionale instanser (overpass.osm.ch har bare
# Sveits) må IKKE inn her: de svarer 200 OK med null treff, og da ser «serveren
# har ikke disse dataene» ut som «OSM kjenner ikke dette banenavnet». Et
# vellykket svar fra feil database er verre enn en feilmelding.
OVERPASS_SPEIL = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Antall runder gjennom speilene før vi gir opp. 504 er som regel forbigående.
RUNDER = 3

# Sør, vest, nord, øst. Dekker fastlands-Norge med god margin, men ikke
# Svalbard — der går det ingen persontog.
NORGE_BBOX = (57.0, 4.0, 72.0, 32.0)

# Overpass er en gratistjeneste med delt kapasitet. Vent litt mellom kallene,
# ellers får du 429 halvveis gjennom lista.
PAUSE_S = 2.0

UT = Path("static/hovedbaner.geojson")

# Toleranse for forenklingen. Ved zoom 6 er én piksel omtrent 1,2 km i Norge,
# så 150 m er godt under det øyet kan se — men det kutter rundt 95 % av
# punktene. Vil du bruke laget helt inn på zoom 12, sett den lavere.
TOLERANSE_M = 150.0

# OSM gir banen som mange biter. Sidespor og stumper under denne lengden er
# støy på zoom 6 — men sett den lavt nok til at ekte korte biter overlever.
MIN_BIT_M = 300.0

# Hvilke `railway`-verdier vi regner som kjørbart spor. `disused`, `abandoned`
# og `construction` faller utenfor med vilje: nedlagte traseer bærer ofte
# fortsatt banenavnet i OSM, og de skal ikke inn i et sanntidskart.
SPORTYPER = {"rail", "light_rail", "narrow_gauge"}

# `rang` følger prioriteringslista di. Den legges på hver bane slik at du
# senere kan gi de tre-fire viktigste en egen bredde eller farge uten å
# hente data på nytt.
# `osm` er navnene å slå opp i OpenStreetMap. Flere navn per bane der
# infrastrukturen har delt seg: Askerbanen er en egen trasé ved siden av
# Drammenbanen, og begge er del av korridoren du vil vise.
#
# `osm: None` betyr at banen ikke finnes som navngitt infrastruktur.
# Trønderbanen er et togtilbud — sporet under heter Dovrebanen nordover og
# Nordlandsbanen sørover. Den må hentes fra Entur, som kjenner tilbudet.
#
# `km` er banens omtrentlige lengde fra åpne kilder. Den brukes ikke som
# fasit, men som plausibilitetssjekk: kommer OSM tilbake med 1 km der vi
# venter 75, har vi truffet en gate som heter det samme, ikke jernbanen.
# Uten den sjekken havnet en 23-punkters stump inn som «Jærbanen».
#
# Merk at tallet må være banens *faktiske* lengde, ikke et tall fra en
# overskrift. Bratsbergbanen sto med 74 km i et halvt år og fikk en komplett
# OSM-henting på 47 km til å se ut som en halvfeilet en — se problem 7. En
# målestokk som er feil, gjør riktige data mistenkelige.
#
# To ting til om `km`, begge lært av samme feil:
#
#   1. Står det flere navn i `osm`, skal `km` være *summen* av dem. Vi måler
#      det vi henter, ikke det første navnet i lista. Drammenbanen og
#      Gardermobanen sto begge med bare det ene navnets lengde.
#   2. `km` er rutelengde, mens OSM gir sporlengde. På dobbeltspor er hvert
#      spor sitt eget way, så en helt riktig henting kan gi nær det dobbelte.
#      Derfor er 1,5-grensen under bare en MERK og ikke en forkasting.
#
# `spor` er antall parallelle spor korridoren har, og brukes bare til å heve
# MERK-taket. Standard 1. Uten den ga Drammenbanen MERK ved hver eneste
# bygging, og et varsel som alltid står på, leses ikke — se lærdom 13. Den
# hever bare taket; gulvet på 50 % som utløser Entur-reserven måles fortsatt
# mot ren rutelengde, så en halvt manglende bane fanges like godt som før.
#
# `min_bit` overstyrer MIN_BIT_M for én bane. Standarden på 300 m antar at
# korte biter er sidespor, og det er feil på baner OSM deler opp i hver bru
# og hver tunnel: der er de korte bitene ledd i hovedsporet. Signaturen er
# målbar — hever du terskelen og hullene i banen vokser, kuttet du kjeden,
# ikke støyen. Bratsbergbanen og Raumabanen er begge slike.
#
# `fra`/`til` brukes bare av Entur-veien.
BANER = [
    # `km` gjaldt lenge bare det første navnet i `osm`, ikke det vi faktisk
    # henter. Begge disse to henter to baner, og begge sto derfor med en
    # målestokk som var altfor liten - samme feil som Bratsbergbanen, bare
    # motsatt vei. Rettet 19. august, se problem 7.
    #
    # Drammenbanen: 53 km var traseen før 1973, om Spikkestad. Lieråsen-
    # tunnelen kortet den til 42 (Jernbanedirektoratet: 41,66 km Oslo S-
    # Drammen). Askerbanen er 17 km egne spor Lysaker-Asker. 42 + 17 = 59.
    # `spor` 2: begge er dobbeltsporet hele veien, og Lysaker-Asker har
    # dermed fire spor til sammen. Målt 1,8 spor per korridor-km.
    {"rang": 1, "navn": "Drammenbanen", "km": 59, "spor": 2,
     "osm": ["Drammenbanen", "Askerbanen"],
     "fra": "Oslo S", "til": "Drammen stasjon"},

    # Gardermobanen 64 km + Hovedbanen 68 km. To selvstendige baner Oslo-
    # Eidsvoll, ikke ett spor: Gardermobanen om Romeriksporten, Hovedbanen
    # om Strømmen og Jessheim. 64 alene ga 278 % og evig MERK.
    #
    # OSM navngir bare Lillestrøm-Eidsvoll som «Gardermobanen» - Romeriksporten
    # inn til Oslo bærer ikke navnet. Korridoren blir likevel tegnet, fordi
    # Hovedbanen dekker Oslo-Lillestrøm i dagen ved siden av. Derfor står `km`
    # på banenes offisielle lengder og ikke på de 119 vi faktisk får: fasiten
    # skal være uavhengig av målingen, ellers slutter den å kunne dømme den.
    {"rang": 2, "navn": "Gardermobanen", "km": 132, "spor": 2,
     "osm": ["Gardermobanen", "Hovedbanen"],
     "fra": "Oslo S", "til": "Eidsvoll stasjon"},
    {"rang": 3, "navn": "Jærbanen", "km": 75, "osm": ["Jærbanen"],
     "fra": "Stavanger stasjon", "til": "Egersund stasjon"},
    {"rang": 4, "navn": "Østfoldbanen", "km": 170, "osm": ["Østfoldbanen"],
     "fra": "Oslo S", "til": "Halden stasjon"},
    {"rang": 5, "navn": "Vestfoldbanen", "km": 148, "osm": ["Vestfoldbanen"],
     "fra": "Drammen stasjon", "til": "Skien stasjon"},
    {"rang": 6, "navn": "Bergensbanen", "km": 371, "osm": ["Bergensbanen", "Vossebanen"],
     "fra": "Oslo S", "til": "Bergen stasjon"},

    # Randsfjordbanen er ikke en bibane, den er et hull i Bergensbanen.
    # Offisielt starter Bergensbanen på Hønefoss, men hvert eneste Oslo-Bergen-
    # tog kjører Drammen-Hokksund-Hønefoss for å komme dit: Drammenbanen til
    # Drammen, Sørlandsbanen til Hokksund, så disse 54 kilometerne. Uten dem
    # stopper korridoren i Hokksund og starter igjen på Hønefoss, og på zoom 6
    # ser Bergensbanen ut til å begynne midt inne i landet.
    #
    # Samme `rang` som Bergensbanen, ikke 14. Rang er prioriteringslista, og
    # gir du senere de fem-seks viktigste egen bredde eller farge, må denne
    # følge med — ellers går korridoren i stykker på nytt, bare visuelt i
    # stedet for geometrisk.
    #
    # 54 km er Bane NORs egen definisjon av banen slik den er i dag. Historisk
    # gikk den Drammen-Randsfjord (87-90 km), men Drammen-Hokksund tilfalt
    # Sørlandsbanen, og Hønefoss-Randsfjord ble revet i 1984. Kommer OSM
    # tilbake med 80+ km, er nedlagt trasé tagget `rail` i stedet for
    # `abandoned` — da slår MERK-linjen ut, og da er det verdt å se etter.
    #
    # Entur-reserven går fra Drammen, ikke Hokksund. Lokaltrafikken
    # Hokksund-Hønefoss ble nedlagt i 2004, og bare noen av fjerntogene
    # stopper på Hokksund — et reisesøk derfra kan gi buss. Alle
    # Bergensbanens tog stopper på Drammen. Prisen er 17 km overlapp med
    # Sørlandsbanen, som er den samme bevisste overlappen som Jærbanen har.
    {"rang": 6, "navn": "Randsfjordbanen", "km": 54, "osm": ["Randsfjordbanen"],
     "fra": "Drammen stasjon", "til": "Hønefoss stasjon"},
    {"rang": 7, "navn": "Trønderbanen", "km": 145, "osm": None,
     "fra": "Melhus skysstasjon", "til": "Steinkjer stasjon"},
    {"rang": 8, "navn": "Gjøvikbanen", "km": 123, "osm": ["Gjøvikbanen"],
     "fra": "Oslo S", "til": "Gjøvik stasjon"},
    {"rang": 9, "navn": "Kongsvingerbanen", "km": 115, "osm": ["Kongsvingerbanen"],
     "fra": "Lillestrøm stasjon", "til": "Kongsvinger stasjon"},
    {"rang": 10, "navn": "Dovrebanen", "km": 485, "osm": ["Dovrebanen"],
     "fra": "Oslo S", "til": "Trondheim S"},
    {"rang": 11, "navn": "Nordlandsbanen", "km": 729, "osm": ["Nordlandsbanen"],
     "fra": "Trondheim S", "til": "Bodø stasjon"},

    # Sørlandsbanen går Drammen-Stavanger og inneholder hele Jærbanen. De
    # overlapper med vilje: Jærbanen er sitt eget pendlermarked og skal kunne
    # framheves for seg, mens Sørlandsbanen er korridoren den ligger i.
    {"rang": 12, "navn": "Sørlandsbanen", "km": 545, "osm": ["Sørlandsbanen"],
     "fra": "Drammen stasjon", "til": "Stavanger stasjon"},

    # Bratsbergbanen er broen Skien-Nordagutu. Uten den henger Vestfoldbanen
    # og Sørlandsbanen i løse luften i forhold til hverandre, og strekningen
    # Skien-Stavanger går ikke i ett i kartet. Det går lokaltog her, men ingen
    # gjennomgående tog - den er med for sammenhengen, ikke for trafikken.
    # Ta den ut hvis laget skal være rene pendlerkorridorer.
    #
    # `km` sto på 74 og var feil. Målt 19. august: OSM-relasjonen dekker
    # Porsgrunn-Skien-Nordagutu sammenhengende på 47,4 km, og Enturs egen
    # ruting av samme strekning sier 44,0 km. To uavhengige kilder på ~45 km
    # mot en målestokk på 74 er grunnen til at hentingen så vidt klarte
    # 50 %-terskelen og dermed aldri utløste Entur-reserven - den så halvfeilet
    # ut mens den var komplett. Se problem 7.
    #
    # `min_bit` 0: banen er kort og OSM deler den i 177 biter. Ved 300 m
    # forsvinner 19 % og verste hull vokser fra 10 m til 946 m - beviset på
    # at bitene er hovedspor mellom bruer, ikke sidespor.
    #
    # Entur-reserven går Porsgrunn-Nordagutu, ikke Skien-Notodden som før.
    # Notodden ligger på den andre siden av Nordagutu og er ikke Bratsbergbanen;
    # den gamle reserven tegnet 54 km av noe annet enn banen den het.
    {"rang": 13, "navn": "Bratsbergbanen", "km": 47, "osm": ["Bratsbergbanen"],
     "min_bit": 0, "fra": "Porsgrunn stasjon", "til": "Nordagutu stasjon"},

    # Rørosbanen er den andre veien til Trondheim: Hamar-Elverum-Røros-Støren,
    # der den møter Dovrebanen og følger den siste stykket inn. Uten den er
    # hele Østerdalen og Nord-Østerdal tomt for jernbane i kartet, og det ser
    # ut som om det bare går ett spor nordover.
    #
    # `rang` 14 er ankomstrekkefølge, ikke viktighet. Banen har daglige
    # persontog (SJ Nord, Hamar-Røros-Trondheim) og bærer altså mer trafikk
    # enn Bratsbergbanen på 13, som er med for sammenhengen alene. Skal rang
    # en dag styre bredde eller farge, hører denne over den — men å omnummerere
    # tretten baner for å si det, ville flyttet feilen til dokumentasjonen.
    #
    # 384 km er Bane NORs lengde for Hamar-Støren. Kommer OSM tilbake med
    # under halvparten, er det Elverum-Støren som mangler navngiving, ikke
    # banen som er kort — se Bratsbergbanen i problem 7 for samme feilmodus.
    {"rang": 14, "navn": "Rørosbanen", "km": 384, "osm": ["Rørosbanen"],
     "fra": "Hamar stasjon", "til": "Støren stasjon"},

    # Raumabanen er sidegrenen vestover fra Dovrebanen: Dombås-Bjorli-
    # Åndalsnes, ned Romsdalen til fjorden. Uten den stopper jernbanen i
    # kartet ved Dombås, og hele Møre-siden ser ut til å være uten spor.
    #
    # OSM har den komplett — 114,8 km målt mot 114 ventet, hele veien til
    # Åndalsnes. Ingen grunn til å røre Entur-reserven her.
    #
    # `min_bit` 0 av samme grunn som Bratsbergbanen, og målt like tydelig:
    # OSM deler banen i 414 biter, og ved 300 m forsvinner 17 % mens verste
    # hull vokser fra 91 m til 832 m. Raumabanen går gjennom Romsdalen med
    # bru og tunnel på rekke, og det er nettopp der oppdelingen blir tett.
    {"rang": 15, "navn": "Raumabanen", "km": 114, "osm": ["Raumabanen"],
     "min_bit": 0, "fra": "Dombås stasjon", "til": "Åndalsnes stasjon"},
]


# ---------------------------------------------------------------------------
# 1. Lengde
# ---------------------------------------------------------------------------
# `forenkle` bodde her til 21. august. Den flyttet til sporgeometri.py da
# `jernbanenett.py` fikk bruk for den i drift — se docstringen der. Vi
# importerer den fortsatt inn i dette navnerommet, så selvtesten under
# tester den samme funksjonen resten av prosjektet bruker.

def lengde_km(punkter: list[Punkt]) -> float:
    return sum(avstand_m(a, b) for a, b in zip(punkter, punkter[1:])) / 1000


# ---------------------------------------------------------------------------
# 2. OpenStreetMap via Overpass
# ---------------------------------------------------------------------------

def _osm_klient() -> httpx.Client:
    """Overpass ber om at klienter identifiserer seg. Timeouten er høy fordi
    tjenesten køer forespørsler når den er travel."""
    return httpx.Client(
        timeout=180.0,
        headers={"User-Agent": "TogKart/1.0 (byggeskript, lagbaner.py)"},
    )


def bygg_overpass_query(navn: list[str]) -> str:
    """
    Overpass-spørring som henter alle jernbanelinjer med gitte navn.

    Vi spør etter to ting i union:

      - `way[railway][name=...]` — enkeltstrekk som bærer banenavnet direkte.
        Dette er den vanligste taggingen i Norge.
      - `relation[type=route][name=...]` — ruterelasjonen, hvis den finnes.
        `out geom` tar med medlemsgeometrien, så vi slipper å slå opp noder.

    Å spørre om begge er billigere enn å gjette hvilken som finnes. Duplikater
    rydder vi bort på way-ID i tolkningen.

    Avgrensningen er en **bbox**, ikke et `area`. Begge holder søket i Norge —
    banenavn er ikke globalt unike — men `area["ISO3166-1"="NO"]` tvinger
    Overpass til å hente Norges landgeometri og skjære alt mot den. Det er dyrt
    nok til å gi 504 Gateway Timeout på en travel dag. En bbox er fire tall.
    """
    deler = []
    for n in navn:
        # Anførselstegn i et banenavn ville brutt spørringen. Ingen norske
        # baner har det, men billig å utelukke.
        rent = n.replace('"', "")
        deler.append(f'  way["railway"]["name"="{rent}"];')
        deler.append(f'  relation["type"="route"]["name"="{rent}"];')

    kropp = "\n".join(deler)
    sor, vest, nord, ost = NORGE_BBOX
    return (
        f"[out:json][timeout:180][bbox:{sor},{vest},{nord},{ost}];\n"
        f"(\n{kropp}\n);\n"
        "out geom;"
    )


def linjer_fra_osm_svar(svar: dict) -> list[list[Punkt]]:
    """
    Plukker ut linjegeometri fra et Overpass-svar.

    Ren funksjon uten nett — det er den som testes i selvtesten. Returnerer én
    punktliste per `way`, i (lon, lat) som resten av prosjektet.

    To ting som ikke er åpenbare:

      - Et `way` kan komme både direkte og som medlem av relasjonen. Vi
        holder styr på ID-ene og tar hver bit én gang.
      - Overpass gir geometri som `{"lat": .., "lon": ..}`-ordbøker, altså
        motsatt rekkefølge av det vi bruker. Bytter du dem ikke om her,
        havner hele jernbanenettet i Somalia.
    """
    sett: set[int] = set()
    linjer: list[list[Punkt]] = []

    def legg_til(element: dict) -> None:
        if element.get("type") != "way":
            return
        way_id = element.get("id")
        if way_id in sett:
            return
        tagger = element.get("tags") or {}
        # Medlemmer av en relasjon kommer uten tagger. Da stoler vi på at
        # relasjonen vet hva den inneholder.
        if tagger and tagger.get("railway") not in SPORTYPER:
            return
        geometri = element.get("geometry") or []
        punkter = [
            (node["lon"], node["lat"])
            for node in geometri
            if node.get("lon") is not None and node.get("lat") is not None
        ]
        if len(punkter) < 2:
            return
        sett.add(way_id)
        linjer.append(punkter)

    for element in svar.get("elements") or []:
        if element.get("type") == "relation":
            for medlem in element.get("members") or []:
                legg_til(medlem)
        else:
            legg_til(element)

    return linjer


def hent_fra_osm(
    klient: httpx.Client, navn: list[str], min_bit_m: float = MIN_BIT_M
) -> list[list[Punkt]]:
    """Henter banen som en liste med linjebiter."""
    query = bygg_overpass_query(navn)

    # 429 (for mange kall) og 504 (tok for lang tid) er begge tilstander hos
    # serveren, ikke feil i spørringen. Da er neste speil riktig svar. En 400
    # derimot betyr at spørringen er gal, og da hjelper det ikke å spørre noen
    # andre — vi gir opp med en gang og viser hva serveren sa.
    svar = None
    for runde in range(RUNDER):
        for i, url in enumerate(OVERPASS_SPEIL):
            try:
                svar = klient.post(url, data={"data": query})
            except httpx.HTTPError as feil:
                print(f"    speil {i+1} svarte ikke: {feil}", file=sys.stderr)
                svar = None
                continue
            if svar.status_code in (429, 504):
                print(f"    speil {i+1} er opptatt ({svar.status_code})",
                      file=sys.stderr)
                svar = None
                continue
            if svar.status_code >= 400:
                raise RuntimeError(f"Overpass {svar.status_code}: {svar.text[:300]}")
            break
        if svar is not None:
            break
        # Voksende pause. Er alle speil travle, hjelper det ikke å mase.
        pause = PAUSE_S * (runde + 1) * 5
        print(f"    alle speil opptatt, venter {pause:.0f} s "
              f"(runde {runde+1} av {RUNDER})", file=sys.stderr)
        time.sleep(pause)

    if svar is None:
        raise RuntimeError("Alle Overpass-speil er opptatt. Prøv igjen om noen minutter.")

    linjer = linjer_fra_osm_svar(svar.json())

    # Sidespor, plattformspor og vekselstumper. OSM deler opp hver `way` ved
    # sporveksler, bruer og planoverganger, så en bane på 500 km blir naturlig
    # til nesten tusen biter — antallet som droppes sier derfor ingenting.
    #
    # Det som betyr noe er hvor mange kilometer som forsvinner, og om de lå i
    # hovedsporet eller ved siden av det. Sammenlign summen med banens
    # offisielle lengde: stemmer den, var bitene parallelle spor.
    lange, korte = [], []
    for linje in linjer:
        (lange if lengde_km(linje) * 1000 >= min_bit_m else korte).append(linje)

    if korte:
        tapt = sum(lengde_km(linje) for linje in korte)
        beholdt = sum(lengde_km(linje) for linje in lange)
        lengste = max(lengde_km(linje) for linje in korte) * 1000
        print(f"    droppet {len(korte)} biter under {min_bit_m:.0f} m: "
              f"{tapt:.1f} km ({tapt/(tapt+beholdt):.1%}), "
              f"lengste {lengste:.0f} m")
    return lange


# ---------------------------------------------------------------------------
# 3. Entur — reisesøk, for banene OSM ikke navngir
# ---------------------------------------------------------------------------

def _klient() -> httpx.Client:
    navn = os.getenv("ET_CLIENT_NAME", "").strip()
    if not navn:
        print("ADVARSEL: ET_CLIENT_NAME er ikke satt i .env", file=sys.stderr)
    return httpx.Client(timeout=30.0, headers={"ET-Client-Name": navn})


def finn_stoppested(klient: httpx.Client, navn: str) -> tuple[str, str]:
    """Slår opp navn -> NSR-ID. Nøyaktig samme parametre som /api/search i app.py."""
    svar = klient.get(
        GEOCODER_URL,
        params={
            "q": navn,              # ikke "text" — Entur avviser Pelias-navnet
            "layers": "stopPlace",
            "stopPlaceTypes": "railStation",
            "countries": "NO",
            "limit": 1,
            "lang": "no",
        },
    )
    if svar.status_code >= 400:
        # Geocoderen forklarer som regel hva den ikke likte. Uten dette
        # ser alle feil like ut, og du gjetter på parameternavn.
        raise RuntimeError(f"Geocoder {svar.status_code}: {svar.text[:200]}")
    treff = svar.json().get("features") or []
    if not treff:
        raise LookupError(f"Fant ingen jernbanestasjon som het «{navn}»")

    p = treff[0]["properties"]
    # Entur legger navnet i names.default, ikke i properties.name.
    navnene = p.get("names") or {}
    return p["id"], navnene.get("default") or navn


# `pointsOnLink` ligger på hvert ben i reisen. Vi ber om reisen mellom to
# endestasjoner og skjøter sammen geometrien til togbenene.
QUERY_REISE = """
query($fra: String!, $til: String!, $tid: DateTime!) {
  trip(
    from: {place: $fra}
    to: {place: $til}
    dateTime: $tid
    numTripPatterns: 5
    # Nordlandsbanen har to avganger i døgnet. Uten et vidt søkevindu svarer
    # Entur "ingen forbindelse" selv om toget går om seks timer.
    searchWindow: 1440
    modes: {
      accessMode: null
      egressMode: null
      directMode: null
      transportModes: [{transportMode: rail}]
    }
  ) {
    tripPatterns {
      legs {
        mode
        distance
        fromPlace { name }
        toPlace { name }
        pointsOnLink { points }
      }
    }
  }
}
"""


def hent_trase(klient: httpx.Client, fra_id: str, til_id: str, tid: str) -> list[Punkt]:
    """
    Henter traseen mellom to stasjoner som en punktliste.

    Vi ber om fem reiseforslag og tar det med færrest ben. Et forslag med ett
    ben er en direkteforbindelse, altså nøyaktig den sammenhengende traseen vi
    er ute etter. Må vi bytte tog underveis (Trønderbanen kan gjøre det på
    Trondheim S), skjøtes benene sammen — de følger samme korridor uansett.
    """
    svar = klient.post(
        JOURNEY_URL,
        json={"query": QUERY_REISE, "variables": {"fra": fra_id, "til": til_id, "tid": tid}},
    )
    svar.raise_for_status()
    kropp = svar.json()
    if kropp.get("errors"):
        raise RuntimeError(kropp["errors"][0].get("message", "ukjent GraphQL-feil"))

    forslag = (kropp["data"]["trip"] or {}).get("tripPatterns") or []
    togforslag = [
        f for f in forslag
        if any(b["mode"] == "rail" for b in f["legs"])
    ]
    if not togforslag:
        raise LookupError("Ingen togforbindelse på dette tidspunktet")

    beste = min(togforslag, key=lambda f: len([b for b in f["legs"] if b["mode"] == "rail"]))

    punkter: list[Punkt] = []
    oppgitt_m = 0.0
    for ben in beste["legs"]:
        if ben["mode"] != "rail":
            continue
        # Enturs egen distanse for benet. Fasit å måle dekodingen mot.
        oppgitt_m += ben.get("distance") or 0.0
        print(f"    ben: {ben['fromPlace']['name']} -> {ben['toPlace']['name']} "
              f"({(ben.get('distance') or 0)/1000:.1f} km)")

        kodet = (ben.get("pointsOnLink") or {}).get("points")
        if not kodet:
            print(f"    ADVARSEL: benet manglet geometri — traseen blir ufullstendig",
                  file=sys.stderr)
            continue
        bit = decode_polyline(kodet)
        # Skjøt: dropp første punkt hvis det er en gjentakelse av forrige ben.
        if punkter and bit and avstand_m(punkter[-1], bit[0]) < 50:
            bit = bit[1:]
        punkter.extend(bit)

    if len(punkter) < 2:
        raise LookupError("Fikk ingen sporgeometri fra Entur")

    # Avviker de to tallene mer enn noen prosent, har vi mistet et stykke spor.
    # En kortere dekodet linje enn Entur oppgir er alltid tap, aldri støy.
    dekodet_km = lengde_km(punkter)
    if oppgitt_m > 0:
        avvik = abs(dekodet_km - oppgitt_m / 1000) / (oppgitt_m / 1000)
        merke = "OK" if avvik < 0.03 else "AVVIK"
        print(f"    {merke}: Entur oppgir {oppgitt_m/1000:.1f} km, "
              f"dekodet {dekodet_km:.1f} km ({avvik:.1%})")

    return punkter


# ---------------------------------------------------------------------------
# 4. Spør skjemaet i stedet for å gjette
# ---------------------------------------------------------------------------

def vis_enum(navn: str) -> None:
    """
    Skriver ut lovlige verdier for en enum i Entur-skjemaet.

    Samme grep som `__type`-spørringen i `sjekk.py skjema`: når en verdi avvises,
    er det raskere å be om fasiten enn å prøve seg fram. `accessMode: none`
    ble avvist fordi `StreetMode` bare inneholder faktiske framkomstmåter —
    «ingen» sier man med `null`, ikke med en enum-verdi.

        python lagbaner.py --enum StreetMode
        python lagbaner.py --enum TransportMode
    """
    query = "query($n: String!) { __type(name: $n) { kind enumValues { name } } }"
    with _klient() as klient:
        svar = klient.post(JOURNEY_URL, json={"query": query, "variables": {"n": navn}})
        svar.raise_for_status()
        kropp = svar.json()
        if kropp.get("errors"):
            print(f"GraphQL-feil: {kropp['errors'][0].get('message')}", file=sys.stderr)
            return

        t = (kropp.get("data") or {}).get("__type")
        if not t:
            print(f"Skjemaet har ingen type som heter «{navn}»", file=sys.stderr)
            return
        if t["kind"] != "ENUM":
            # Nyttig i seg selv: da er det et input-objekt, og feltene ligger
            # under inputFields, ikke enumValues.
            print(f"«{navn}» er {t['kind']}, ikke en enum — ingen verdiliste")
            return

        verdier = [v["name"] for v in t["enumValues"]]
        print(f"{navn} ({len(verdier)} verdier):")
        print("  " + ", ".join(verdier))


# ---------------------------------------------------------------------------
# 5. Bygg filen
# ---------------------------------------------------------------------------

def hent_bane(
    osm_klient: httpx.Client,
    entur_klient: httpx.Client,
    bane: dict,
    kilde: str,
    tid_iso: str,
    min_bit_m: float | None = None,
) -> tuple[list[list[Punkt]], str]:
    """
    Henter én bane. Returnerer (linjebiter, hvilken kilde som ble brukt).

    `kilde="auto"` prøver OSM først og faller tilbake til Entur. Rekkefølgen er
    ikke tilfeldig: OSM beskriver sporet, Entur beskriver én rute langs det.
    Er infrastrukturen navngitt, er den det riktigere svaret.

    `min_bit_m=None` betyr «bruk banens egen `min_bit`, ellers MIN_BIT_M».
    Et tall her er en overstyring fra kommandolinjen og slår begge — det er
    diagnoseflagget, og da skal det gjelde det du peker på.
    """
    if min_bit_m is None:
        min_bit_m = bane.get("min_bit", MIN_BIT_M)
    if kilde in ("auto", "osm") and bane.get("osm"):
        try:
            linjer = hent_fra_osm(osm_klient, bane["osm"], min_bit_m)
            fikk_km = sum(lengde_km(linje) for linje in linjer)
            venter_km = bane.get("km", 0)

            if not linjer:
                print(f"    ingen treff i OSM på {', '.join(bane['osm'])}",
                      file=sys.stderr)
            elif venter_km and fikk_km < venter_km * 0.5:
                # For kort til å være banen. Enten fant vi noe annet med samme
                # navn, eller så er bare en del av banen navngitt i OSM.
                print(f"    OSM ga {fikk_km:.0f} km, venter ~{venter_km} km "
                      f"— for lite, bruker Entur i stedet", file=sys.stderr)
            else:
                # Nedre grense måles mot rutelengden, øvre mot sporlengden.
                # OSM gir ett way per spor, så en riktig hentet dobbeltsporet
                # bane gir nær det dobbelte av `km` uten at noe er galt.
                taket = venter_km * bane.get("spor", 1) * 1.5
                if venter_km and fikk_km > taket:
                    # Ikke grunn til å forkaste: sidespor teller også med.
                    # Men du skal vite om det.
                    print(f"    MERK: OSM ga {fikk_km:.0f} km mot ventet "
                          f"~{venter_km} km", file=sys.stderr)
                return linjer, "osm"
        except Exception as feil:  # noqa: BLE001
            print(f"    OSM feilet: {feil}", file=sys.stderr)
        if kilde == "osm":
            raise LookupError("ingen geometri fra OSM, og reserve er slått av")

    if kilde == "osm":
        raise LookupError("banen har ingen OSM-navn definert")

    # Entur-veien: geokod endestasjonene, be om en reise, dekod polylinjen.
    fra_id, fra_navn = finn_stoppested(entur_klient, bane["fra"])
    til_id, til_navn = finn_stoppested(entur_klient, bane["til"])
    print(f"    Entur: {fra_navn} -> {til_navn}")
    return [hent_trase(entur_klient, fra_id, til_id, tid_iso)], "entur"


def bygg(bare: str | None = None, kilde: str = "auto",
         min_bit_m: float | None = None) -> None:
    # Reisesøk midt på formiddagen gir treff selv om du kjører skriptet
    # klokka to om natta. Samme lærdom som problem 3 i docs/undersokelser.md:
    # hvilken dato og hvilket klokkeslett du spør om, avgjør hva du får.
    tid = datetime.combine(datetime.now().date(), klokkeslett(9, 0))
    if tid < datetime.now() - timedelta(hours=12):
        tid += timedelta(days=1)
    tid_iso = tid.astimezone().isoformat()

    features = []
    telling = {"osm": 0, "entur": 0}
    manglet: list[str] = []

    with _osm_klient() as osm_klient, _klient() as entur_klient:
        forste = True
        for bane in BANER:
            if bare and bane["navn"].lower() != bare.lower():
                continue
            if not forste:
                time.sleep(PAUSE_S)
            forste = False

            print(f"{bane['navn']:<18}", end=" ", flush=True)
            try:
                linjer, brukt = hent_bane(
                    osm_klient, entur_klient, bane, kilde, tid_iso, min_bit_m
                )
            except Exception as feil:  # noqa: BLE001 - én bane skal ikke velte resten
                print(f"\n    HOPPET OVER: {feil}", file=sys.stderr)
                manglet.append(bane["navn"])
                continue

            print(f"({brukt})")
            telling[brukt] += 1

            enkle = [forenkle(linje, TOLERANSE_M) for linje in linjer]
            enkle = [linje for linje in enkle if len(linje) >= 2]
            for_punkter = sum(len(linje) for linje in linjer)
            etter_punkter = sum(len(linje) for linje in enkle)
            km = sum(lengde_km(linje) for linje in enkle)

            print(f"    {len(enkle)} linjebiter, {for_punkter} punkter -> "
                  f"{etter_punkter} ({etter_punkter/max(for_punkter,1):.1%}), {km:.0f} km")

            features.append({
                "type": "Feature",
                "properties": {
                    "navn": bane["navn"],
                    "rang": bane["rang"],
                    # Samme tanke som positionMethod i sjnord.py: du skal se
                    # på dataene hvordan de ble til.
                    "kilde": brukt,
                    "km": round(km, 1),
                },
                "geometry": {
                    # Alltid MultiLineString, også når det bare er én bit.
                    # Én geometritype gjennom hele filen betyr én kodesti i
                    # frontend og ingen spesialtilfeller.
                    "type": "MultiLineString",
                    "coordinates": [
                        [[round(lon, 5), round(lat, 5)] for lon, lat in linje]
                        for linje in enkle
                    ],
                },
            })

    if not features:
        print("Ingen baner hentet — skriver ikke fil.", file=sys.stderr)
        raise SystemExit(1)

    # Rang 1 skal tegnes øverst. MapLibre tegner i den rekkefølgen features
    # ligger i kilden, så den viktigste banen må ligge sist.
    features.sort(key=lambda f: -f["properties"]["rang"])

    UT.parent.mkdir(parents=True, exist_ok=True)
    UT.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    print(f"\nSkrev {UT} — {len(features)} baner "
          f"({telling['osm']} fra OSM, {telling['entur']} fra Entur), "
          f"{UT.stat().st_size/1024:.0f} kB")

    # Filen skrives alltid på nytt fra bunnen, så en bane som feilet er ikke
    # bare fraværende fra denne kjøringen — den er borte fra kartet. Det skal
    # stå sist, der du faktisk leser det, ikke midt i en vegg av utskrift.
    if manglet:
        print(f"\nADVARSEL: {len(manglet)} bane(r) mangler i filen: "
              f"{', '.join(manglet)}", file=sys.stderr)
        print("Kjør hver av dem med --bane for å se hvorfor. Husk at en ny "
              "full kjøring må til for å få dem inn igjen.", file=sys.stderr)


# ---------------------------------------------------------------------------
# 6. Selvtest
# ---------------------------------------------------------------------------

def selvtest() -> None:
    # En rett linje med støy under toleransen skal kollapse til to punkter.
    rett = [(10.0 + i * 0.01, 60.0) for i in range(200)]
    assert len(forenkle(rett, 150.0)) == 2, "rett linje skal bli to punkter"

    # Et knekkpunkt på flere kilometer må overleve.
    knekk = [(10.0, 60.0), (10.5, 60.0), (10.5, 60.5)]
    assert len(forenkle(knekk, 150.0)) == 3, "skarp sving skal beholdes"

    # Realistisk sving: 34 m mellom punktene, som ekte pointsOnLink.
    import math
    bue = [(10.0 + 0.3 * math.sin(i / 500), 60.0 + i * 0.0003) for i in range(2000)]
    enkel = forenkle(bue, TOLERANSE_M)
    avvik = max(
        min(_projiser_pa_segment(p, a, b)[1] for a, b in zip(enkel, enkel[1:]))
        for p in bue
    )
    assert avvik <= TOLERANSE_M, f"forenklet linje ligger {avvik:.0f} m fra originalen"
    assert len(enkel) < len(bue) / 10, f"kuttet bare til {len(enkel)} av {len(bue)}"

    print(f"forenkling   OK   {len(bue)} -> {len(enkel)} punkter, "
          f"største avvik {avvik:.0f} m (toleranse {TOLERANSE_M:.0f})")
    print(f"lengde       OK   {lengde_km(bue):.1f} km -> {lengde_km(enkel):.1f} km")

    _test_osm_tolkning()
    _test_overpass_query()
    _test_min_bit()
    _test_maalestokk()
    _test_banelista()


def _test_osm_tolkning() -> None:
    """Et påstått Overpass-svar med alle fellene i, uten nett."""
    svar = {"elements": [
        # Vanlig navngitt strekk.
        {"type": "way", "id": 1, "tags": {"railway": "rail", "name": "Testbanen"},
         "geometry": [{"lat": 60.0, "lon": 10.0}, {"lat": 60.1, "lon": 10.1}]},
        # Samme way igjen, som medlem av relasjonen. Skal ikke telles to ganger.
        {"type": "relation", "id": 99, "members": [
            {"type": "way", "id": 1,
             "geometry": [{"lat": 60.0, "lon": 10.0}, {"lat": 60.1, "lon": 10.1}]},
            # Medlem uten tagger — relasjonen vet hva den inneholder.
            {"type": "way", "id": 2,
             "geometry": [{"lat": 60.1, "lon": 10.1}, {"lat": 60.2, "lon": 10.2}]},
            # Stoppested som relasjonsmedlem. Ikke en linje.
            {"type": "node", "id": 3, "lat": 60.05, "lon": 10.05},
        ]},
        # Nedlagt trasé som fortsatt bærer navnet. Skal ut.
        {"type": "way", "id": 4, "tags": {"railway": "abandoned", "name": "Testbanen"},
         "geometry": [{"lat": 61.0, "lon": 11.0}, {"lat": 61.1, "lon": 11.1}]},
        # Way med bare ett punkt. Ingen linje å tegne.
        {"type": "way", "id": 5, "tags": {"railway": "rail"},
         "geometry": [{"lat": 62.0, "lon": 12.0}]},
    ]}

    linjer = linjer_fra_osm_svar(svar)
    assert len(linjer) == 2, f"ventet 2 linjer, fikk {len(linjer)}"

    # Den mest kostbare feilen i hele prosjektet: byttet lat og lon.
    for linje in linjer:
        for lon, lat in linje:
            assert 4 < lon < 32 and 57 < lat < 72, f"({lon}, {lat}) ligger ikke i Norge"

    print(f"osm-tolkning OK   {len(linjer)} linjer, duplikat og nedlagt spor luket bort")


def _test_min_bit() -> None:
    """Hvilken terskel gjelder — banens egen, standarden, eller flagget?

    Rekkefølgen er den eneste som gir mening: uten flagg skal en bane som har
    sagt fra om at 300 m kutter hovedsporet, få viljen sin. Med flagg skal
    flagget vinne, ellers kan du ikke måle hva filteret koster på nettopp den
    banen — som er hele grunnen til at flagget finnes.
    """
    med = {"navn": "Test", "osm": ["Test"], "min_bit": 0}
    uten = {"navn": "Test", "osm": ["Test"]}

    def gjeldende(bane: dict, flagg: float | None) -> float:
        return flagg if flagg is not None else bane.get("min_bit", MIN_BIT_M)

    assert gjeldende(uten, None) == MIN_BIT_M, "uten alt: standarden"
    assert gjeldende(med, None) == 0, "banens egen skal slå standarden"
    assert gjeldende(med, 250) == 250, "flagget skal slå banens egen"
    assert gjeldende(uten, 250) == 250, "flagget skal slå standarden"
    # Fellen: `or` i stedet for `is not None` ville gjort --min-bit 0 til
    # standarden igjen, og nettopp 0 er verdien man diagnostiserer med.
    assert gjeldende(uten, 0) == 0, "--min-bit 0 må overleve"

    print("min-bit      OK   bane slår standard, flagg slår bane, 0 overlever")


def _test_maalestokk() -> None:
    """Gulvet mot rutelengde, taket mot sporlengde.

    Feilen dette vokter, er den som holdt Drammenbanen på 174 % og
    Gardermobanen på 278 % i månedsvis: taket målte sporlengde mot rutelengde
    og ga MERK for alltid. `spor` skal heve taket uten å røre gulvet — flytter
    den gulvet også, slutter en halvt manglende bane å utløse Entur-reserven,
    og da har vi byttet et støyende varsel mot et stumt.
    """
    def gulv(bane: dict, fikk: float) -> bool:
        return fikk < bane["km"] * 0.5

    def tak(bane: dict, fikk: float) -> bool:
        return fikk > bane["km"] * bane.get("spor", 1) * 1.5

    enkelt = {"km": 100}
    dobbelt = {"km": 100, "spor": 2}

    assert tak(enkelt, 160), "enkeltspor: 160 km mot ventet 100 skal merkes"
    assert not tak(dobbelt, 160), "dobbeltspor: 160 km er under to spor"
    assert tak(dobbelt, 310), "dobbeltspor: 310 km er for mye uansett"

    # Gulvet skal ikke bevege seg av `spor`. Ellers ville en dobbeltsporet bane
    # måtte mangle 75 % før reserven slo inn, i stedet for 50 %.
    assert gulv(enkelt, 49) and gulv(dobbelt, 49), "gulvet er felles"
    assert not gulv(dobbelt, 51), "51 av 100 er ikke halvt manglende"

    print("målestokk    OK   spor hever taket, gulvet står i rutelengde")


def _test_banelista() -> None:
    """Lista selv: unike navn, og en `km` det går an å måle mot."""
    navn = [b["navn"] for b in BANER]
    assert len(set(navn)) == len(navn), "to baner med samme navn"
    for b in BANER:
        assert b.get("km", 0) > 0, f"{b['navn']} mangler km å måle mot"
        assert b.get("osm") or b.get("fra"), f"{b['navn']} har ingen kilde"
        if not b.get("osm"):
            assert b.get("fra") and b.get("til"), f"{b['navn']} mangler fra/til"
        assert b.get("spor", 1) >= 1, f"{b['navn']} har spor under 1"
    print(f"banelista    OK   {len(BANER)} baner, unike navn, alle med målestokk")


def _test_overpass_query() -> None:
    query = bygg_overpass_query(["Drammenbanen", "Askerbanen"])
    assert query.count("Drammenbanen") == 2, "både way og relation per navn"
    assert "bbox:57.0,4.0,72.0,32.0" in query, "søket må avgrenses til Norge"
    assert "area" not in query, "area-oppslag er for dyrt — bruk bbox"
    assert "out geom;" in query, "uten out geom får vi bare ID-er"
    # Anførselstegn i selve spørringen er riktig — de rammer inn taggverdiene.
    # Det som ikke skal slippe gjennom, er anførselstegn fra navnet.
    assert 'Rar"bane' not in bygg_overpass_query(['Rar"bane']), "navnet må vaskes"
    print("overpass     OK   bbox-avgrenset, ber om geometri, navn vaskes")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selvtest", action="store_true", help="test forenklingen, uten nett")
    ap.add_argument("--bane", help="hent bare én bane, for eksempel Jærbanen")
    ap.add_argument("--enum", help="vis lovlige verdier for en type, f.eks. StreetMode")
    ap.add_argument(
        "--kilde", choices=["auto", "osm", "entur"], default="auto",
        help="auto: OSM med Entur som reserve (standard)",
    )
    ap.add_argument(
        "--min-bit", type=float, default=None, metavar="METER",
        help=f"korteste OSM-bit som beholdes. Uten flagget bruker hver bane "
             f"sin egen min_bit, ellers {MIN_BIT_M:.0f}. Sett 0 for å se hva "
             "filteret faktisk koster",
    )
    args = ap.parse_args()

    if args.selvtest:
        selvtest()
    elif args.enum:
        vis_enum(args.enum)
    else:
        bygg(args.bane, args.kilde, args.min_bit)
