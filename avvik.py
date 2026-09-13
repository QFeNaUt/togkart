"""Driftsmeldinger (SIRI-SX) fra Entur - kilden til nyhetsstripa.

Alt som har med avviksmeldinger å gjøre bor her, på samme måte som entur.py
eier Vehicle Positions og sjnord.py eier Journey Planner. app.py får ferdige
en-linjere tilbake og vet ingenting om GraphQL eller SIRI.

Kilden er `situations` i Journey Planner v3 - Entur sitt JSON-vindu mot
SIRI-SX. Vi henter ikke XML-en direkte: GraphQL gir samme innhold uten at vi
må skrive en XML-parser, og `codespaces`-argumentet gjør filtreringen på
serversiden.


Fire ting som er målt mot den ekte feeden 20. august, og som er verdt å vite
før du endrer noe her:

  1. VY PUBLISERER UNDER `NSB`, IKKE `VYG`. Kodrommet `VYG` - det samme som
     kjøretøyene og linjene kommer under i Vehicle Positions - ga NULL
     situasjoner. `NSB` ga 45, alle med Vy-linjer (L1, L2, R14, R21, RE20).
     Spør du bare om VYG, blir stripa tom og alt ser ut til å virke.

  2. BARE `NSB` OG `GOA` SVARER I DAG. FLT, SJN og BNR ga null hver. Vi spør
     likevel om alle fem: det koster ingenting, og den dagen SJ Nord begynner
     å publisere, dukker meldingene opp av seg selv. `prober/sjekk_avvik.py`
     skriver ut antallet per kodrom, så du ser når det skjer.

  3. `severity` KAN IKKE BÆRE FARGEN ALENE. Av 56 meldinger var 45 «normal»,
     10 «noImpact» og én «severe». En innstilling og en heis ute av drift har
     altså samme alvorlighetsgrad i feeden. Derfor leser `_niva()` teksten i
     tillegg - se kommentaren der.

  4. SAMMENDRAGET ER FOR KORT TIL Å STÅ ALENE. `summary` er «Toget står»,
     «Forsinket», «Ta neste tog». Det er `description` som sier «Toget står på
     Sørumsand» - og det er den som ruller forbi. Sammendraget blir stikkordet
     foran.

  5. FEEDEN ER IKKE EN NYHETSSTRØM, OG DET MÅ FILTRERINGEN VITE. Målt
     21. august inneholdt de 50 situasjonene alt fra et tog som sto akkurat
     nå til et venterom som hadde vært stengt i 143 døgn og vedlikehold som
     starter om 57 døgn. En melding ligger i feeden fra den varsles til den
     er over, og «over» er ofte ikke definert: 16 av 50 hadde ingen `endTime`.

     Stripa skal si hva som skjer NÅ. Tre porter i `til_meldinger` gjør den
     jobben - se MAKS_ALDER_TIMER, VARSEL_TIMER og `_klokke`.

  6. GJENTAKELSEN HAR TO FORMER, OG BARE DEN ENE ER EKSAKT. Samme tekst
     ordrett er den lette: den slås sammen på teksten. Den andre er tekster
     som sier nesten det samme - «3 vogner i stedet for 6», «4 i stedet for
     8», «5 i stedet for 10» - og som alle bærer sammendraget «Færre vogner».
     De fylte seks av ti plasser i stripa 21. august.

     Derfor to passeringer: `_lik_tekst` og så `_gruppenokkel`. Sammendraget
     er operatørens egen merkelapp og en langt bedre nøkkel enn å normalisere
     bort tallene.
"""

import asyncio
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx

from sporgeometri import avstand_m, forenkle

log = logging.getLogger(__name__)

SITUASJON_URL = "https://api.entur.io/journey-planner/v3/graphql"

# Kodrommene som kan tenkes å publisere togavvik. Se punkt 1 og 2 i modul-
# dokumentasjonen: NSB er Vy, ikke NSB. RUT, SKY og de andre fylkeskodrommene
# er utelatt med vilje - de gir buss, trikk og t-bane, og dette er et togkart.
KODEROM = ["NSB", "GOA", "VYG", "FLT", "SJN"]

# Hvor mange meldinger stripa får. Én melding vises i seks sekunder, så 15 er
# halvannet minutt rundt løypa - lenge nok til at ingen står og venter på at
# den de så skal komme tilbake, kort nok til at de viktige ikke drukner.
MAKS_MELDINGER = 15

# Hvor lenge en melding får bli stående i stripa etter at den sist ble sagt
# noe om. Se `_klokke` for hvilken tid som måles.
#
# Målt på feeden 21. august, uten denne grensen: sju av de femten meldingene i
# stripa var mellom 30 og 68 timer gamle. «Ta andre tog fra Skøyen og Lysaker»
# hadde rullet i tre døgn. Ingenting fjernet dem, fordi det eneste som utløp
# en melding var `endTime` - og 16 av 50 situasjoner har ingen `endTime` i det
# hele tatt. «Venterom stengt grunnet hærverk» hadde da vært aktiv i 143 døgn.
#
# Tolv timer dekker et driftsdøgnskifte uten å dra med gårsdagen. Prisen er en
# kortere stripe: samme feed ga 15 meldinger før og 9 etter. Det er meningen -
# stripa skal si hva som skjer nå, ikke fylle femten plasser.
MAKS_ALDER_TIMER = 12

# Hvor lenge FØR en melding trer i kraft den får stå i stripa.
#
# Null, altså bare det som pågår nå. Uten denne grensen sto seks av femten
# plasser til planlagt arbeid som startet mellom 18 timer og 57 DØGN fram i
# tid - og de lå øverst, fordi sorteringen den gang brukte starttiden som
# ferskhetsmål og et tidspunkt i oktober er «ferskere» enn ett i dag.
#
# Vedlikehold som allerede pågår kommer fortsatt med; det er `startTime` i
# fortiden og `endTime` i framtiden, som enhver annen løpende melding. Vil du
# varsle om arbeid som starter i natt, er det denne du skrur opp.
VARSEL_TIMER = 0

# Når flere meldinger deler sammendrag, hvor mange som skal til før de blir
# én linje. To meldinger er ikke en klump som må ryddes - de får stå med hver
# sin tekst. Tre er et mønster.
MIN_GRUPPE = 3

# Lengste sammendrag som regnes som en MERKELAPP og ikke en setning. Bare
# merkelapper brukes som gruppenøkkel - se `_gruppenokkel`.
#
# Målt 21. august: «Færre vogner», «Ta andre tog» og «Ta neste tog» er alle 12
# tegn og gjentas 15, 9 og 3 ganger. «Sørtoget: Bane NOR utfører
# vedlikeholdsarbeid.» er 46 og gjentas 7 ganger - men der ligger innholdet i
# beskrivelsen (hvilke stasjoner, buss for tog), så den skal ikke grupperes
# bort. 40 skiller de to gruppene med god margin i begge retninger.
GRUPPE_MAKS_SAMMENDRAG = 40

# Hvor mange stasjoner én melding får tegne ring rundt i kartet. Vedlikeholds-
# meldingene fra Go-Ahead rammer hele Sørlandsbanen; ringer rundt hver eneste
# stasjon der ville dekket kartet i stedet for å peke på det. Kameraet rammer
# uansett inn alle stedene - taket gjelder bare prikkene.
MAKS_PUNKTER = 60

# Hvor mange stasjoner som skal til før meldingen tegnes som en STREKNING og
# ikke som ringer. Se «Hva meldingen peker på i kartet» for resonnementet.
#
# Tre, og grunnen er en telling på feeden 21. august. «Ta andre tog fra Skøyen
# og Lysaker» rammer to stasjoner, og det er to steder å gå av - ikke en
# strekning. «Ta andre tog fra Skøyen, Lysaker og Stabekk» rammer tre på rad
# langs Drammenbanen, og da er det nettopp strekningen som er poenget.
MIN_STREKNING_STEDER = 3

# Hvor grov streken sendes ut. `jernbanenett` forenkler allerede til 20 m,
# som er finere enn en piksel før man er zoomet inn på en bydel. Oslo S-
# Stavanger var 1375 punkter ved 20 m og er 300 ved 80 - og stripa kan ha
# femten meldinger som alle skal ligge i det samme svaret.
STREKNING_TOLERANSE_M = 80.0

# Nivåene etter ALVOR. Brukes når to like meldinger slås sammen og den
# strengeste skal vinne. Må matche NYHET_NIVA i app.js.
NIVAER = ["stort", "middels", "rettet", "info"]

# Nivåene etter NYHETSVERDI - rekkefølgen de vises i. Ikke den samme, og det
# er hele poenget: «rettet» er mildt målt i alvor, men det er den meldingen
# folk venter på. Sto den etter «middels», ble den aldri vist - proben målte
# fem røde og ti gule som fylte hele taket på 15, mens de to grønne om at
# Gardermoen kjørte normalt igjen falt utenfor. Da forsvant hele den grønne
# fargen fra stripa i praksis, uten at noe så ut til å være galt.
VISNINGSORDEN = ["stort", "rettet", "middels", "info"]

SPORRING = """
query($koderom: [String]) {
  situations(codespaces: $koderom) {
    situationNumber
    reportType
    severity
    creationTime
    versionedAtTime
    validityPeriod { startTime endTime }
    summary { language value }
    description { language value }
    affects {
      __typename
      ... on AffectedLine {
        line { publicCode transportMode }
      }
      ... on AffectedStopPlace {
        stopPlace { id name latitude longitude }
      }
      ... on AffectedStopPlaceOnLine {
        stopPlace { id name latitude longitude }
        line { publicCode transportMode }
      }
      ... on AffectedServiceJourney {
        serviceJourney { line { publicCode transportMode } }
      }
      ... on AffectedStopPlaceOnServiceJourney {
        stopPlace { id name latitude longitude }
        serviceJourney { line { publicCode transportMode } }
      }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Nivå: hvilken farge en melding får i stripa
# ---------------------------------------------------------------------------
# Rekkefølgen på testene i _niva() er ikke tilfeldig, og to av dem er
# feilrettinger mot ekte data:
#
#   `rettet` FØRST. «Åpnet: Sørumsand» og «Normal hastighet: Gardermoen»
#   kommer som reportType general med severity normal, og ville ellers falt
#   ned i info-bøtta sammen med heisene. En melding om at feilen er borte er
#   det mest verdifulle stripa kan si, og skal ha sin egen farge.
#
#   `noImpact` FØR nøkkelordene. Uten den rekkefølgen ble alle åtte
#   «Bane NOR utfører vedlikeholdsarbeid»-meldingene røde: beskrivelsen deres
#   inneholder både «stengt» og «buss for tog». Men det er PLANLAGT arbeid,
#   varslet uker i forveien, og operatøren har selv merket det noImpact.
#   Planlagt stengning er ikke en nyhet om at noe er galt nå.
_RETTET = re.compile(
    r"(normal (hastighet|trafikk|drift|fart)|feilen er rettet|er rettet"
    r"|gjenoppt|åpnet igjen|åpen for togtrafikk igjen|åpnet:|som normalt)",
    re.IGNORECASE,
)

# Nøkkelordene som gjør en melding rød. Merk hvor smale de er: «stengt» alene
# ville tatt med «Venterom stengt grunnet hærverk», som er en låst dør og ikke
# en stengt bane. Derfor «stengt for togtrafikk» og «Stengt:» - Vy sin faste
# overskriftsform - i stedet for det løse ordet.
_STORT = re.compile(
    r"(innstilt|innstill|buss for tog|stengt for togtrafikk|stengt:"
    r"|ikke tog|evakuer|personpåkjørsel|sporfeil)",
    re.IGNORECASE,
)

# «Toget kjørte 40 minutter forsinket fra Sørumsand.» Minuttallet står i
# beskrivelsesteksten, ikke i noe eget felt - SIRI-SX har ingen plass til det.
# Derfor må det leses ut av setningen, og derfor er terskelen sårbar for at
# operatørene endrer formulering. Første halvtime kvelden 20. august hadde
# ingen av 56 meldinger et minuttall; den neste hadde ett. Proben teller hvor
# ofte regelen slår til, så du ser om den slutter å treffe.
_MINUTTER = re.compile(r"(\d{1,3})\s*(?:min\b|minutt)", re.IGNORECASE)
STORT_FRA_MINUTTER = 15


def _niva(sammendrag: str, beskrivelse: str, alvor: str, type_: str) -> str:
    """Rød, gul, grønn eller hvit. Se kommentaren over for rekkefølgen."""
    tekst = f"{sammendrag} {beskrivelse}"

    if _RETTET.search(tekst):
        return "rettet"
    if alvor == "noImpact":
        return "info"
    if alvor in ("severe", "verySevere"):
        return "stort"
    if _STORT.search(tekst):
        return "stort"

    minutter = _MINUTTER.search(tekst)
    if minutter and int(minutter.group(1)) >= STORT_FRA_MINUTTER:
        return "stort"

    return "middels" if type_ == "incident" else "info"


# ---------------------------------------------------------------------------
# Tekst
# ---------------------------------------------------------------------------
_MELLOMROM = re.compile(r"\s+")

# Den ene meldingen med severity «severe» i feeden 20. august lød: «Toget er
# innstilt mellom Oslo S og Stavanger på grunn av .» - årsaken manglet, men
# innledningen til den sto igjen. Det er Go-Ahead sitt hull, ikke vårt, men
# stripa skal ikke rulle en halv setning forbi. Vi klipper den hengende
# bindingen og lar punktumet stå.
_HENGENDE = re.compile(
    r"\s*(på grunn av|grunnet|som følge av|skyldes)\s*\.\s*$", re.IGNORECASE
)

# «Lillestrøm stasjon» -> «Lillestrøm». Stripa er trang, og ordet «stasjon»
# sier ingenting man ikke visste i et togkart.
_STASJONSORD = re.compile(r"\s+stasjon$", re.IGNORECASE)

_ORD = re.compile(r"\w{4,}", re.UNICODE)


def _stikkord(sammendrag: str, tekst: str) -> str:
    """Sammendraget, men bare når det sier noe teksten ikke sier.

    De fleste sammendragene er teksten i kortform: «Stengt: Fredrikstad» foran
    «Fredrikstad er stengt for togtrafikk». Å vise begge er å bruke halve
    stripa på å si det samme to ganger.

    Men noen bærer et ord teksten mangler, og det er nettopp det ordet man
    trenger: «Sørtoget: Bane NOR utfører vedlikeholdsarbeid» - beskrivelsen
    alene sier ikke hvilken bane det gjelder. Regelen blir da: behold
    sammendraget hvis minst ett meningsbærende ord i det ikke finnes i
    teksten. Korte ord (på, er, til) telles ikke - de finnes overalt.
    """
    if not sammendrag:
        return ""
    ord_i_tekst = set(_ORD.findall(tekst.casefold()))
    nytt = [o for o in _ORD.findall(sammendrag.casefold()) if o not in ord_i_tekst]
    return sammendrag if nytt else ""


def _rydd(tekst: str) -> str:
    tekst = _MELLOMROM.sub(" ", tekst).strip()
    return _HENGENDE.sub(".", tekst)


def _norsk(tekster: list[dict] | None) -> str:
    """Den norske utgaven av en MultilingualString-liste, ellers den første.

    Entur sender som regel både «no» og «en». Vi vil ha norsk, men en melding
    som bare finnes på engelsk er bedre enn ingen melding.
    """
    if not tekster:
        return ""
    for t in tekster:
        if t.get("language") == "no" and (t.get("value") or "").strip():
            return _rydd(t["value"])
    for t in tekster:
        if (t.get("value") or "").strip():
            return _rydd(t["value"])
    return ""


def _tid(iso: str | None) -> datetime | None:
    """ISO-streng til tidssone-bevisst tid.

    Entur sender alltid med sone i dag - alle 50 situasjonene 21. august hadde
    `+02:00`. Men en naiv verdi ville krasjet HELE stripa: sammenligningen mot
    `na` kaster TypeError når bare den ene siden har sone, og den kastes inne i
    løkka som bygger meldingene. Én rar tidsstreng fra Entur ville altså tatt
    ned endepunktet, ikke bare den ene meldingen. Mangler sonen, leser vi den
    som UTC.
    """
    if not iso:
        return None
    try:
        tid = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return tid if tid.tzinfo else tid.replace(tzinfo=timezone.utc)


def _klokke(s: dict) -> datetime | None:
    """Når meldingen sist ble sagt noe om. Stripas ferskhetsmål.

    `versionedAtTime` først, og det er en rettelse: sorteringen brukte før
    `validityPeriod.startTime`, som kan ligge i FRAMTIDEN. Et planlagt arbeid
    som starter om 57 døgn fikk da det største tidsstempelet i hele feeden og
    la seg øverst i stripa som den ferskeste nyheten. Målt 21. august sto seks
    slike på rad, sortert med det fjerneste først.

    `versionedAtTime` kan ikke ligge i framtiden - null av 50 gjorde det - og
    den fanger i tillegg at operatøren har oppdatert en løpende melding: elleve
    av 50 var oppdatert etter at de ble laget, én av dem over tre døgn etterpå.

    Rekkefølgen er fallende presisjon: sist oppdatert, ellers da den ble laget,
    ellers da den trådte i kraft.
    """
    return (
        _tid(s.get("versionedAtTime"))
        or _tid(s.get("creationTime"))
        or _tid((s.get("validityPeriod") or {}).get("startTime"))
    )


# ---------------------------------------------------------------------------
# Det meldingen rammer
# ---------------------------------------------------------------------------
def _rammer(affects: list[dict]) -> tuple[list[str], list[dict]]:
    """Plukk ut linjekoder og stoppesteder fra affects-lista.

    Lista kan være enorm: én Go-Ahead-melding om vedlikehold på Sørlandsbanen
    hadde over 2000 AffectedServiceJourney-oppføringer, én per avgang i
    perioden. Vi bryr oss bare om hvilke LINJER og hvilke STEDER det gjelder,
    så begge samles i sett - da blir 2000 oppføringer til «F5» og en håndfull
    stasjoner.

    Bare tog telles med. Kodrommene er allerede togselskaper, men et selskap
    som legger inn en bussmelding skal ikke havne i et togkart.
    """
    linjer: dict[str, None] = {}
    steder: dict[str, dict] = {}

    for a in affects:
        linje = a.get("line") or (a.get("serviceJourney") or {}).get("line") or {}
        kode = linje.get("publicCode")
        if kode and linje.get("transportMode") in (None, "rail"):
            linjer.setdefault(str(kode), None)

        sted = a.get("stopPlace") or {}
        sid = sted.get("id")
        lat, lon = sted.get("latitude"), sted.get("longitude")
        if sid and lat is not None and lon is not None and sid not in steder:
            steder[sid] = {
                "id": sid,
                "navn": _STASJONSORD.sub("", str(sted.get("name") or "")),
                "lon": lon,
                "lat": lat,
            }

    return sorted(linjer), list(steder.values())


def _boks(steder: list[dict]) -> list[float] | None:
    """Omsluttende rektangel [vest, sør, øst, nord] - det kartet skal fly til.

    None når meldingen ikke er stedfestet. Da tegner ikke frontend noe
    klikkmål, og det er riktig: en melding uten sted skal ikke sende kartet
    til et tilfeldig punkt.
    """
    if not steder:
        return None
    lons = [s["lon"] for s in steder]
    lats = [s["lat"] for s in steder]
    return [min(lons), min(lats), max(lons), max(lats)]


# ---------------------------------------------------------------------------
# Hva meldingen peker på i kartet
# ---------------------------------------------------------------------------
# En driftsmelding rammer én av to ting, og de skal tegnes ulikt:
#
#   BESTEMTE STASJONER. «Heisen på Sandvika til spor 3 og 4 er ute av drift.»
#   «Ta andre tog fra Skøyen og Lysaker.» Her er stedet hele opplysningen, og
#   en ring rundt hvert av dem sier akkurat det.
#
#   EN HEL STREKNING. «Bane NOR utfører vedlikeholdsarbeid ... buss for tog
#   mellom Kristiansand og Gjerstad.» Ringer rundt endepunktene sier at det er
#   noe galt to steder. Det er det ikke - det er banen mellom dem som er
#   stengt, og det er den som skal lyse.
#
# Skillet leses av to ting, i denne rekkefølgen:
#
#   1. STASJONSKJEDEN. Rammer meldingen MIN_STREKNING_STEDER eller flere
#      stasjoner, er de nesten alltid en sammenhengende rekke langs en bane -
#      «Ta neste tog mellom Sagdalen og Oslo S» kom 21. august med alle tolv
#      stasjonene på Hovedbanen mellom dem. Da er kjeden både beviset for at
#      det er en strekning OG oppskriften på hvor den går.
#
#   2. TEKSTEN. De hardeste meldingene har INGEN steder i det hele tatt:
#      «Toget er innstilt mellom Stabekk og Ski» kom samme dag med bare
#      linjekoden L2 og null stoppesteder. Da leses endepunktene ut av
#      setningen og slås opp i stasjonsregisteret. Uten dette leddet er de
#      alvorligste meldingene i stripa nettopp de som ikke kan klikkes fram.
#
# Geometrien kommer fra `jernbanenett.strekning()`, altså ekte spor og ikke en
# strek gjennom terrenget. Se den for hvorfor kjeden er nok til å velge riktig
# trasé uten at vi har observert et eneste tog.

# «mellom X og Y», med begge navnene rundt seg. Vi tar med det som følger
# etter og lar oppslaget klippe: «buss for tog mellom Kristiansand og Gjerstad
# for Sørtoget» skal gi Gjerstad, ikke «Gjerstad for Sørtoget».
_MELLOM = re.compile(r"\bmellom\s+([^.,;:!?]{2,60}?)\s+og\s+([^.,;:!?]{2,60})", re.I)

# Hvor mange ord et stasjonsnavn kan bestå av. «Oslo lufthavn», «Eidsvoll
# verk» og «Oslo S» er to; «Mo i Rana» er den eneste på tre i hele registeret.
# Trengs fordi oppslaget prøver det LENGSTE navnet først - ellers ville
# «mellom Sagdalen og Oslo S» gitt Oslo, som er en annen stasjon.
MAKS_NAVNEORD = 3

_TEGN = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)

_navn: dict[str, tuple[float, float]] | None = None


def _navneindeks() -> dict[str, tuple[float, float]]:
    """Stasjonsnavn i småbokstaver -> koordinat.

    Hentet fra `jernbanenett` og ikke fra en egen kopi: det er den samme fila,
    og et navneoppslag som svarer noe annet enn det rutingen fester seg til
    ville gitt en strek som begynner et annet sted enn navnet sier.
    """
    global _navn
    if _navn is None:
        import jernbanenett

        _navn = {navn.casefold(): p for navn, p in jernbanenett.stasjoner().items()}
    return _navn


def _stasjon_sist(bit: str) -> tuple[float, float] | None:
    """Stasjonen som avslutter «... mellom NN». Lengste treff vinner."""
    ord_ = [_TEGN.sub("", o) for o in bit.split()]
    indeks = _navneindeks()
    for n in range(min(MAKS_NAVNEORD, len(ord_)), 0, -1):
        truffet = indeks.get(" ".join(ord_[-n:]).casefold())
        if truffet:
            return truffet
    return None


def _stasjon_forst(bit: str) -> tuple[float, float] | None:
    """Stasjonen som innleder «... og NN ...». Lengste treff vinner."""
    ord_ = [_TEGN.sub("", o) for o in bit.split()]
    indeks = _navneindeks()
    for n in range(min(MAKS_NAVNEORD, len(ord_)), 0, -1):
        truffet = indeks.get(" ".join(ord_[:n]).casefold())
        if truffet:
            return truffet
    return None


def _endepunkter(tekst: str) -> list[tuple[float, float]]:
    """Stasjonsparet en «mellom X og Y»-setning navngir, eller tom liste.

    Finner den ikke begge navnene i registeret, gir den opp helt. Ett
    endepunkt er ikke en strekning, og å tegne fra det ene til gjetningen om
    det andre er verre enn å tegne ingenting.
    """
    for treff in _MELLOM.finditer(tekst):
        a = _stasjon_sist(treff.group(1))
        b = _stasjon_forst(treff.group(2))
        if a and b and a != b:
            return [a, b]
    return []


def _kjede(punkter: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Stasjonene i den rekkefølgen de ligger langs banen.

    `affects` kommer uordnet. «Ta neste tog» ga 21. august Lysaker,
    Vevelstad, Myrvoll, Oslo S, Nationaltheatret, Nordstrand ... for en L2 som
    kjører Stabekk-Ski. Rutes de i den rekkefølgen, sikksakker streken fram og
    tilbake gjennom Oslo seksten ganger.

    Metoden er nærmeste nabo fra den ene enden av kjeden, og den er eksakt så
    lenge nabostasjoner ligger nærmere hverandre enn stasjoner som ikke er
    naboer - som er akkurat hva en jernbanelinje er. Å projisere på korden i
    stedet ville brutt sammen på hver bane som svinger.

    Enden finnes ved å ta paret som ligger lengst fra hverandre. Det er
    kvadratisk, men MAKS_PUNKTER er 60.
    """
    if len(punkter) < 3:
        return list(punkter)

    start = punkter[0]
    lengst = -1.0
    for i, a in enumerate(punkter):
        for b in punkter[i + 1:]:
            d = avstand_m(a, b)
            if d > lengst:
                lengst, start = d, a

    igjen = [p for p in punkter if p != start]
    ut = [start]
    while igjen:
        naermest = min(igjen, key=lambda p: avstand_m(ut[-1], p))
        igjen.remove(naermest)
        ut.append(naermest)
    return ut


def _boks_om(linjer: list[list[list[float]]]) -> list[float] | None:
    """Omsluttende rektangel om streken, på samme form som `_boks`."""
    if not linjer:
        return None
    lons = [p[0] for linje in linjer for p in linje]
    lats = [p[1] for linje in linjer for p in linje]
    return [min(lons), min(lats), max(lons), max(lats)]


def paafor_kart(meldinger: list[dict]) -> None:
    """Fyll ut `form`, `strekninger` og `punkter` - det kartet skal tegne.

    Skilt fra `til_meldinger` fordi den er ren tekstbehandling og denne leser
    to bygde filer og kjører et rutesøk. Proben kan da måle filtreringen uten
    å ha spornettet liggende, og app.py kan legge dette leddet i en tråd.

    Endrer meldingene på plass. Går noe galt her - mangler spornettet, er
    stasjonsfila ikke bygget - blir `form` stående som «steder», og stripa
    virker som den gjorde før strekningene fantes.
    """
    import jernbanenett

    for m in meldinger:
        m["form"] = "steder" if m["punkter"] else ""
        m["strekninger"] = []

        kjede = _kjede([(p[0], p[1]) for p in m["punkter"]])
        if len(kjede) < MIN_STREKNING_STEDER:
            kjede = _endepunkter(f"{m['stikkord']} {m['tekst']}")
        if len(kjede) < 2:
            continue

        try:
            biter = jernbanenett.strekning(kjede)
        except Exception as exc:  # noqa: BLE001 - en strek skal aldri velte stripa
            log.warning("Kunne ikke rute strekningen for en driftsmelding: %s", exc)
            continue
        if not biter:
            continue

        linjer = [
            [[round(x, 5), round(y, 5)] for x, y in forenkle(b, STREKNING_TOLERANSE_M)]
            for b in biter
        ]
        m["form"] = "strekning"
        m["strekninger"] = linjer
        # Ringene ville sagt det streken allerede sier, og på en strekning med
        # tjue stasjoner ville de dekket den.
        m["punkter"] = []
        # Kameraet skal ramme inn det som faktisk BLE tegnet. Falt en etappe
        # fra, eller kom kjeden fra teksten og ikke fra stedene, er boksen om
        # stasjonene en annen boks enn streken.
        m["boks"] = _boks_om(linjer) or m["boks"]


# ---------------------------------------------------------------------------
# Sammenstilling
# ---------------------------------------------------------------------------
def _flett(inn_i: dict, fra: dict) -> None:
    """Legg `fra` inn i `inn_i`. Strengeste nivå, ferskeste tid, alt av
    linjer og steder, og summen av avganger."""
    if NIVAER.index(fra["niva"]) < NIVAER.index(inn_i["niva"]):
        inn_i["niva"] = fra["niva"]
    if fra["_sortert"] > inn_i["_sortert"]:
        inn_i["_sortert"] = fra["_sortert"]
        inn_i["fra"] = fra["fra"]

    inn_i["linjer"] = sorted(set(inn_i["linjer"]) | set(fra["linjer"]))
    inn_i["avganger"] += fra["avganger"]

    kjente = {s["id"] for s in inn_i["_steder"]}
    inn_i["_steder"].extend(s for s in fra["_steder"] if s["id"] not in kjente)


def _slaa_sammen(meldinger: list[dict], nokkel) -> list[dict]:
    """Slå sammen meldinger som `nokkel` gir samme verdi for.

    `nokkel(m)` som gir None betyr «la denne stå alene». Rekkefølgen er den
    inngangen hadde: den første i hver gruppe blir stående der den lå, og
    resten flettes inn i den.
    """
    samlet: dict[str, dict] = {}
    ut: list[dict] = []

    for m in meldinger:
        n = nokkel(m)
        if n is None:
            ut.append(m)
            continue
        finnes = samlet.get(n)
        if finnes is None:
            samlet[n] = m
            ut.append(m)
            continue
        _flett(finnes, m)

    return ut


def _lik_tekst(m: dict) -> str:
    """Første passering: eksakt samme tekst.

    Feeden gjentar seg kraftig: 56 situasjoner ga 35 ulike beskrivelser. «Ta
    neste tog mellom Stabekk og Skøyen» kommer én gang per berørt linje.
    Stripa skal si det én gang, med alle linjene og alle stedene samlet.

    Nøkkelen er teksten, ikke situationNumber: det er nettopp fordi numrene er
    forskjellige at duplikatene finnes.
    """
    return m["tekst"].casefold()


def _gruppenokkel(m: dict) -> str | None:
    """Andre passering: samme sammendrag, samme nivå.

    Eksakt tekst er ikke nok. Målt 21. august hadde femten situasjoner
    sammendraget «Færre vogner», men tre ulike beskrivelser - «3 vogner i
    stedet for 6», «4 i stedet for 8», «5 i stedet for 10». Første passering
    ga da tre linjer i stripa som sa nesten det samme, og de tok seks av ti
    plasser. Ni situasjoner delte «Ta andre tog» på samme vis.

    Sammendraget er operatørens egen merkelapp og en bedre nøkkel enn å
    normalisere bort tallene: den er stabil, den er skrevet for å gruppere,
    og den ryker ikke hvis noen skriver «fire» i stedet for «4».

    To vakter mot å slå sammen for mye:

      * **Nivået er med i nøkkelen.** En innstilling og en heis skal ikke
        havne på samme linje selv om sammendraget skulle være likt.
      * **Bare korte sammendrag.** Over GRUPPE_MAKS_SAMMENDRAG er det en
        setning og ikke en merkelapp - «Sørtoget: Bane NOR utfører
        vedlikeholdsarbeid.» er 46 tegn, og der er det beskrivelsen som
        bærer innholdet (hvilke stasjoner, buss for tog). Den skal stå
        for seg.

    None betyr «la stå alene». Terskelen MIN_GRUPPE håndheves i `_grupper`,
    som teller før den slår sammen.
    """
    sammendrag = (m["_sammendrag"] or "").strip()
    if not sammendrag or len(sammendrag) > GRUPPE_MAKS_SAMMENDRAG:
        return None
    return f"{m['niva']}|{sammendrag.casefold()}"


def _grupper(meldinger: list[dict]) -> list[dict]:
    """Slå sammen på sammendrag, men bare der det faktisk er en klump.

    To krav, og de måler to forskjellige ting:

      * **Minst to ULIKE tekster.** Er det bare én, er teksten allerede
        dekkende, og å bytte den mot sammendraget ville kastet bort
        informasjon: «Ta neste tog mellom Stabekk og Skøyen» sier hvor, mens
        «Ta neste tog» ikke gjør det. Én tekst som dekker mange avganger
        trenger ingen ny tekst - den trenger bare tallet i undertittelen.
      * **Minst MIN_GRUPPE AVGANGER til sammen.** Terskelen teller avganger og
        ikke tekster, fordi det er avgangene som gjør det til et mønster.
        Målt 21. august sto «Færre vogner» igjen som to tekster etter første
        passering - «3 i stedet for 6» og «4 i stedet for 8» - men de dekket
        2 og 8 avganger. Telte vi tekster, ville terskelen på tre aldri slått
        til, og ti avganger hadde fortsatt tatt to av ti plasser i stripa.

    Der begge kravene er oppfylt, blir sammendraget hele teksten og antallet
    avganger flytter ut i undertittelen.
    """
    tekster: Counter[str] = Counter()
    avganger: Counter[str] = Counter()
    for m in meldinger:
        n = _gruppenokkel(m)
        if n is not None:
            tekster[n] += 1
            avganger[n] += m["avganger"]

    klump = {
        n for n in tekster
        if tekster[n] >= 2 and avganger[n] >= MIN_GRUPPE
    }

    # Hvilke meldinger som ER gruppehoder noteres UNDER sammenslåingen, ikke
    # ved å regne nøkkelen på nytt etterpå. Grunnen er at `_flett` kan endre
    # `niva` til det strengeste i gruppen - og nivået er en del av nøkkelen.
    # Regnet vi den på nytt, ville en gruppe som fikk hevet nivå ikke lenger
    # kjenne seg igjen, og teksten ville blitt stående som den første
    # medlemmets i stedet for sammendraget.
    hoder: list[dict] = []

    def nokkel(m: dict) -> str | None:
        n = _gruppenokkel(m)
        if n not in klump:
            return None
        hoder.append(m)
        return n

    ut = _slaa_sammen(meldinger, nokkel)

    # `hoder` inneholder alle medlemmene, men bare hodene ble stående i `ut`.
    staaende = {id(m) for m in ut}
    for m in hoder:
        if id(m) in staaende:
            # Sammendraget er nå hele meldingen. Stikkordet ville gjentatt
            # det, og beskrivelsen gjaldt bare én av avgangene.
            m["tekst"] = m["_sammendrag"].strip()
            m["stikkord"] = ""
    return ut


def til_meldinger(
    situasjoner: list[dict], na: datetime | None = None
) -> tuple[list[dict], dict[str, int]]:
    """SIRI-SX-svaret som en ferdig sortert liste, og hva som ble luket bort.

    Skilt fra hentingen så den kan testes uten nett - proben mater den med
    lagrede svar.

    Returnerer `(meldinger, forkastet)`. Telleren er ikke pynt: den er den
    eneste måten å se at et filter har begynt å spise for mye. Slutter Entur å
    sette `endTime`, eller endrer de hvordan `versionedAtTime` fylles, viser
    det seg som et tall som vokser - ikke som en stripe som stille blir kort.
    """
    na = na or datetime.now(timezone.utc)
    raa: list[dict] = []
    forkastet = {"utenTekst": 0, "utgatt": 0, "ikkeStartet": 0, "forGammel": 0}

    for s in situasjoner:
        sammendrag = _norsk(s.get("summary"))
        beskrivelse = _norsk(s.get("description"))

        # Beskrivelsen er en-linjeren; mangler den, må sammendraget bære
        # meldingen alene. Har vi ingen av delene, har vi ikke en nyhet.
        tekst = beskrivelse or sammendrag
        if not tekst:
            forkastet["utenTekst"] += 1
            continue

        # Tre porter, og de spør om tre ulike ting. En melding må være over,
        # i gang, og fortsatt aktuell - i den rekkefølgen.
        periode = s.get("validityPeriod") or {}
        start = _tid(periode.get("startTime"))
        slutt = _tid(periode.get("endTime"))

        # 1. Er den over? Entur lar meldinger ligge en stund etter at de er
        #    ferdige.
        if slutt and slutt < na:
            forkastet["utgatt"] += 1
            continue

        # 2. Har den begynt? Planlagt arbeid ligger i feeden fra det blir
        #    varslet, ofte måneder i forveien. Se VARSEL_TIMER.
        if start and start > na + timedelta(hours=VARSEL_TIMER):
            forkastet["ikkeStartet"] += 1
            continue

        # 3. Er den fortsatt fersk? Se MAKS_ALDER_TIMER. Denne porten er den
        #    eneste som fanger meldingene uten `endTime`, og de er en tredjedel
        #    av feeden.
        klokke = _klokke(s) or start or na
        if na - klokke > timedelta(hours=MAKS_ALDER_TIMER):
            forkastet["forGammel"] += 1
            continue

        linjer, steder = _rammer(s.get("affects") or [])

        raa.append(
            {
                "id": s.get("situationNumber") or "",
                "stikkord": _stikkord(sammendrag, tekst),
                "tekst": tekst,
                "niva": _niva(
                    sammendrag,
                    beskrivelse,
                    s.get("severity") or "",
                    s.get("reportType") or "",
                ),
                "linjer": linjer,
                "steder": [],          # fylles etter sammenslåing
                "antallSteder": 0,     # likeså
                "boks": None,
                # Hver situasjon er én avgang: alle «Færre vogner» hadde
                # nøyaktig ett AffectedServiceJourney 21. august. Tallet
                # summeres når meldinger slås sammen, og er det stripa viser
                # som «6 avganger».
                "avganger": 1,
                # `fra` er ferskhetsklokka, ikke starttiden. Det er den
                # frontend regner alderen fra, og den skal svare på «når ble
                # dette sagt», ikke «når begynte det».
                "fra": klokke.isoformat(timespec="seconds"),
                "_steder": steder,
                "_sortert": klokke,
                "_sammendrag": sammendrag,
            }
        )

    # To passeringer, og de fanger to ulike former for gjentakelse.
    meldinger = _slaa_sammen(raa, _lik_tekst)
    meldinger = _grupper(meldinger)

    # Viktigst først, ferskest først innenfor hvert nivå. En innstilling fra i
    # går skal ikke ligge over en som kom for fem minutter siden.
    meldinger.sort(
        key=lambda m: (VISNINGSORDEN.index(m["niva"]), -m["_sortert"].timestamp())
    )
    meldinger = meldinger[:MAKS_MELDINGER]

    for m in meldinger:
        steder = m.pop("_steder")
        m.pop("_sortert")
        m.pop("_sammendrag")
        # Boksen regnes av ALLE stedene, ikke av de vi sender ut. Klikker man
        # på en melding som rammer 40 stasjoner, skal kartet ramme inn alle 40
        # - også de vi ikke har plass til å tegne prikk på.
        m["boks"] = _boks(steder)
        m["antallSteder"] = len(steder)
        # Navnene er til teksten under meldingen, punktene til ringene i
        # kartet. Begge har tak: fire navn er alt det er plass til på én linje,
        # og over MAKS_PUNKTER ringer er kartet dekket uansett.
        m["steder"] = [s["navn"] for s in steder[:4] if s["navn"]]
        m["punkter"] = [[s["lon"], s["lat"]] for s in steder[:MAKS_PUNKTER]]
        # Hva kartet skal tegne avgjøres av `paafor_kart`, som er et eget
        # ledd fordi den leser bygde filer og ruter. Feltene står her med
        # verdien «bare ringer», så et svar uten det leddet er gyldig.
        m["form"] = "steder" if m["punkter"] else ""
        m["strekninger"] = []

    return meldinger, forkastet


async def hent_avvik(
    client_name: str, koderom: list[str] | None = None, timeout: float = 20.0
) -> dict:
    """Hent driftsmeldingene og returner {"meldinger": [...], "hentet": iso, ...}.

    Kaster httpx.HTTPError ved nettverksfeil og RuntimeError hvis GraphQL
    avviser spørringen - app.py bestemmer hva som skjer da.
    """
    headers = {"ET-Client-Name": client_name}
    variabler = {"koderom": koderom or KODEROM}

    async with httpx.AsyncClient(timeout=timeout) as client:
        svar = await client.post(
            SITUASJON_URL,
            json={"query": SPORRING, "variables": variabler},
            headers=headers,
        )
        svar.raise_for_status()
        payload = svar.json()

    # GraphQL svarer HTTP 200 selv når spørringen er ugyldig. Samme felle som
    # i entur.py, samme vakt.
    if payload.get("errors"):
        beskjed = "; ".join(e.get("message", "?") for e in payload["errors"])
        raise RuntimeError(f"Entur avviste avviksspørringen: {beskjed}")

    situasjoner = (payload.get("data") or {}).get("situations") or []
    na = datetime.now(timezone.utc)
    meldinger, forkastet = til_meldinger(situasjoner, na)

    # Rutesøket er det eneste her som regner i stedet for å vente på nett, og
    # det leser to filer fra disk første gang. En egen tråd, av samme grunn som
    # historikkanalysene ligger i en: hendelsesløkka står ikke i kø bak den.
    # Svarene bufres i `jernbanenett`, så andre gang er dette ingenting.
    await asyncio.to_thread(paafor_kart, meldinger)

    fordeling = {n: sum(1 for m in meldinger if m["niva"] == n) for n in NIVAER}
    aldre = [
        (na - datetime.fromisoformat(m["fra"])).total_seconds() / 3600
        for m in meldinger
    ]
    log.info(
        "Driftsmeldinger: %d situasjoner -> %d i stripa (%s), eldste %.1f t "
        "(forkastet: %s)",
        len(situasjoner),
        len(meldinger),
        ", ".join(f"{n}={a}" for n, a in fordeling.items() if a) or "ingen",
        max(aldre) if aldre else 0.0,
        ", ".join(f"{k}={v}" for k, v in forkastet.items() if v) or "ingenting",
    )

    return {
        "meldinger": meldinger,
        "antall": len(meldinger),
        "situasjoner": len(situasjoner),
        "fordeling": fordeling,
        # Hva som ble luket bort og hvorfor. Ligger i svaret slik at det kan
        # leses av uten å kjøre proben - se `til_meldinger`.
        "forkastet": forkastet,
        "eldsteTimer": round(max(aldre), 1) if aldre else 0.0,
        "maksAlderTimer": MAKS_ALDER_TIMER,
        "hentet": na.isoformat(timespec="seconds"),
    }


if __name__ == "__main__":
    # Kjør:  python avvik.py
    # Viser hva stripa ville rullet akkurat nå. Verifiseringen - antall per
    # kodrom, nivåfordeling, hvilke regler som slo til - bor i
    # prober/sjekk_avvik.py.
    import os
    import sys

    from dotenv import load_dotenv

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    data = asyncio.run(hent_avvik(os.getenv("ET_CLIENT_NAME", "").strip()))

    if not data["meldinger"]:
        print("\nIngen driftsmeldinger å vise nå.")
        raise SystemExit

    na = datetime.now(timezone.utc)
    print()
    for m in data["meldinger"]:
        sted = f"{m['antallSteder']} steder" if m["antallSteder"] else "uten sted"
        linjer = ", ".join(m["linjer"]) or "—"
        stikkord = f"{m['stikkord']}: " if m["stikkord"] else ""
        alder = (na - datetime.fromisoformat(m["fra"])).total_seconds() / 60
        # Hva kartet får: en strekning på skinner, ringer rundt stasjoner,
        # eller ingenting å klikke fram.
        if m["form"] == "strekning":
            punkter = sum(len(linje) for linje in m["strekninger"])
            kart = f"strekning ({len(m['strekninger'])} bit, {punkter} pkt)"
        elif m["form"] == "steder":
            kart = f"{len(m['punkter'])} ringer"
        else:
            kart = "ikke i kartet"
        print(f"[{m['niva']:<7}] {stikkord}{m['tekst']}")
        print(f"{'':>10} {linjer} · {sted} · {kart} · "
              f"{f'{alder:.0f} min siden' if alder < 90 else f'{alder/60:.1f} t siden'}")

    print()
    print(f"Eldste melding: {data['eldsteTimer']} t "
          f"(grense {data['maksAlderTimer']} t). "
          f"Forkastet: {data['forkastet']}")
