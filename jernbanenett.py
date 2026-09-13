"""
jernbanenett.py — legg en strekning på sporet i stedet for gjennom terrenget.

Problemet
---------
`flaskehals.py` tegnet strekningene sine som medianen av observerte
togposisjoner, bøttet langs korden mellom de to stasjonene. Det er en god idé
med tre feil som alle rammer hardest der linjene ligger tettest — altså i
Oslo, Akershus og Østfold:

  1. **Medianen av to parallelle spor er ingen av dem.** Passerer både et
     Askerbane-tog og et Drammenbane-tog mellom Sandvika og Lysaker, legger
     medianen streken midt imellom, ute på jordet mellom traseene. Jo tettere
     nettet er, desto oftere skjer det.
  2. **Bøttene tømmes.** Punktene projiseres på korden, og bare de som lander
     mellom 0 og 1 teller. En trasé som svinger — Romeriksporten, Follobanen,
     Østre linje — mister punktene sine i endene, og med tolv bøtter ble
     Bryn–Lillestrøm tegnet med fem punkter over 13,6 km. Det er en luftlinje
     med et lite kink på.
  3. **Median av lengdegrad og median av breddegrad hver for seg er ikke et
     punkt på sporet.** I en sving trekker de to medianene hver sin vei, og
     resultatet legger seg på innsiden av kurven.

Målt mot `static/hovedbaner.geojson` bøyde Nationaltheatret–Oslo S seg 966 m
vekk fra sporet på en strekning på 1,3 km.

Løsningen
---------
Vi ruter i stedet gjennom et ekte spornett — `static/jernbanenett.geojson`,
bygget av `lagjernbanenett.py` fra OpenStreetMap. Da er hvert punkt i streken
per definisjon et punkt på et spor.

Men et rutesøk alene løser bare feil 2 og 3. Korteste vei fra Bryn til
Lillestrøm går gjennom Romeriksporten, enten toget kjørte der eller tok
Hovedbanen om Strømmen. Feil 1 krever at søket vet hvilket spor toget FAKTISK
brukte, og det vet observasjonene.

Derfor ruter vi ikke etter korteste vei, men etter **korteste vei som følger
observasjonene**: hver sporbit koster lengden sin, ganget med 1 hvis toget ble
observert langs den og med `STRAFF` hvis ikke. En omvei gjennom feil tunnel
blir da åtte ganger så dyr som den er lang, og taper mot riktig trasé selv når
den er kortere.

Straffen er en vekt og ikke et forbud, og det er med vilje. Blir det hull i
observasjonene — logging som stanset, eller en bit spor ingen ble målt på —
finner søket fortsatt fram. Det koster, men et svar på riktig bane er bedre
enn ikke noe svar.

Hva som gjør at svaret kan avvises
----------------------------------
Ruting kan lykkes og likevel være feil. To sjekker til slutt, se `_troverdig`:
ruten kan ikke være mye lengre enn luftlinja, og togene må ha blitt målt der
den går. Faller den på en av dem, svarer modulen None, og `flaskehals.py`
faller tilbake på medianlinja. Et kart som noen ganger tegner den gamle
streken er bedre enn et som noen ganger tegner en trasé på tvers av landet.

Målt på databasen 21. august slår reserven inn på 1 av 147 strekninger:
Hvalstad-Slependen, der togene er ekspresstog i Askerbanens tunnel mens
stasjonsparet ligger på den gamle Drammenbanen. Der er det ikke rutingen som
tar feil - det er strekningen selv som er tvetydig.

To innganger, og de svarer på to ulike spørsmål
----------------------------------------------
`rute()` spør «hvor kjørte toget?», og trenger observasjoner for å svare.
Den er flaskehalskartets inngang.

`strekning()` spør «hvor går denne banestrekningen?», og har ingen
observasjoner å spørre - driftsmeldingene sier «stengt mellom Oslo S og
Skøyen», ikke hvor noen kjørte. Der løses tvetydigheten av KJEDEN i stedet:
stasjonene meldingen rammer, i rekkefølge. Korteste vei fra Oslo S til
Sagdalen går gjennom Romeriksporten, men korteste vei Oslo S - Alna - Nyland
- Grorud - Sagdalen kan bare gå om Hovedbanen. Se metoden.

Selvtest
--------
    python jernbanenett.py

Ingen database og ingen nettkall: den ruter mot spornettet med
stasjonskjeder som late observasjoner, og sjekker de åtte tingene som gikk
galt mens modulen ble skrevet. Ende-til-ende med ekte observasjoner ligger i
`prober/sjekk_flaskehals.py`.
"""

from __future__ import annotations

import heapq
import json
import logging
import os
import statistics
from collections import defaultdict

# `_projiser_pa_segment` har understrek foran fordi den var ment som intern i
# sporgeometri.py. Vi låner den likevel, av samme grunn som lagbaner.py gjør:
# den offentlige `avstand_til_strekning` kaster brøken langs segmentet, og
# festepunktene trenger nettopp den for å vite HVOR på sporbiten de sitter.
from sporgeometri import Punkt, _projiser_pa_segment, avstand_m, forenkle

log = logging.getLogger(__name__)

NETT = os.path.join("static", "jernbanenett.geojson")
STASJONER = os.path.join("static", "stasjoner.geojson")

# Hvor nær en sporbit må ha vært en observasjon for å regnes som «her kjørte
# toget». 250 m er romsligere enn GPS-feilen og strammere enn avstanden
# mellom parallelle baner: Gardermobanen ligger to–tre km fra Hovedbanen ved
# Jessheim, Askerbanen rundt en km fra Drammenbanen ved Sandvika, og
# Follobanen er en egen tunnel. Alle skilles med god margin.
#
# Doble spor på samme trasé ligger 4-5 m fra hverandre og skilles IKKE — de
# skal heller ikke skilles. Hvilket av to spor i samme trasé toget lå på er
# ikke en flaskehals, det er en sporvalgsdetalj.
KORRIDOR_M = 250.0

# Hva en sporbit uten observasjoner koster, som multiplikator på lengden.
#
# Åtte er valgt slik at det aldri lønner seg å ta feil bane for å spare vei.
# Verste tilfelle i nettet er Bryn–Lillestrøm: Romeriksporten er 13 km,
# Hovedbanen om Strømmen 16. Straffen må gjøre 13 km feil trasé dyrere enn
# 16 km riktig, altså minst 16/13 = 1,25. Åtte gir rikelig margin også der
# omveien er mye lengre enn snarveien, uten å bli så stor at et hull i
# loggingen på noen hundre meter velter hele ruten.
STRAFF = 8.0

# Hvor langt fra en stasjon vi leter etter spor å begynne på. Alle 335
# stasjoner har spor innenfor 1 km — `lagjernbanenett.py --sjekk` sjekker
# nettopp det — så 800 m er nok, og strammere enn 1 km slik at en stasjon
# uten eget spor heller feiler enn å feste seg til nabolinja.
SNAPP_M = 800.0

# Hvor mange spor en stasjon får feste seg til.
#
# Dette tallet må være større enn én, og grunnen er dobbeltspor. To spor i
# samme trasé er to atskilte linjer i OSM som bare møtes i sporvekslene, og
# sporvekslene kan ligge kilometer unna. Festet vi hver stasjon til sitt ene
# nærmeste spor, kunne Sagdalen havne på det ene og Strømmen på det andre —
# og rutesøket måtte da ut til nærmeste veksel og tilbake. Målt før fiksen:
# Sagdalen–Strømmen er 1,1 km i luftlinje og ble rutet 4,1 km, med alle
# punkter pent på skinner hele veien.
#
# Med flere festepunkter i hver ende finner søket det paret som ligger på
# samme spor, og velger selv. Tolv holder til de største stasjonene: Oslo S
# har flest spor innenfor 800 m, og der er de uansett alle forbundet.
MAKS_FESTER = 12

# Hva en meter sidelengs fra stasjonen til sporet koster, målt i meter kjørt.
#
# Uten en vekt her kan søket kjøpe seg kortere rute ved å feste stasjonen
# lenger unna: fester det Nationaltheatret 350 m vestover og Skøyen 350 m
# østover, blir strekningen 700 m kortere og regnskapet går i null. Målt før
# vekten kom inn: Nationaltheatret–Skøyen ble rutet 2,4 km der luftlinja er
# 3,1 — en strekning kortere enn luftlinja, som ikke kan stemme.
#
# Ti gjør handelen ulønnsom uten å gjøre festet til en absolutt regel: to
# spor i samme trasé ligger så nær hverandre at valget mellom dem fortsatt
# avgjøres av hvor ruten skal, som er hele poenget med MAKS_FESTER.
FESTE_VEKT = 10.0

# Taket på hvor mye lengre ruten kan være enn luftlinja mellom stasjonene.
# En ekte jernbanetrasé svinger, men den svinger ikke dobbelt så langt: målt
# på nettet ligger de krokete strekningene rundt 1,3-1,6. 2,2 slipper gjennom
# ekte omveier som Bratsbergbanens sløyfe inn til Skien, og stopper ruter som
# har tatt av på feil bane og kommet tilbake.
#
# Pluss et fast slingringsmonn: på en strekning på 600 m er en stasjonssløyfe
# på 400 m ikke en feil, den er en stasjon.
MAKS_OMVEI = 2.2
OMVEI_MONN_M = 1_500.0

# Hvor langt observasjonene i typisk fall kan ligge fra ruten før vi ikke tror
# på den. Romsligere enn KORRIDOR_M fordi dette er en vetosjekk og ikke et
# valg mellom to spor.
#
# Merk retningen: vi måler fra hver OBSERVASJON til ruten, ikke fra hvert
# rutepunkt til nærmeste observasjon. Den første versjonen målte motsatt vei,
# og straffet dermed hull i loggingen i stedet for feil trasé.
#
# Lysaker–Sandvika viste hvorfor det ikke går. De 32 passeringene der er
# ekspresstog som bare ble målt ved hver ende: mellom 10,54 og 10,62 grader
# øst — fem kilometer — finnes det ikke én observasjon. Målt den gale veien
# lå halve ruten «langt fra observasjonene» og ble vraket, selv om hver
# eneste observasjon lå på skinnene den fant. Målt riktig vei stiller et hull
# i loggingen ingen spørsmål, mens en rute på feil bane fortsatt faller.
MAKS_TYPISK_AVVIK_M = 400.0

# Ruten fortettes før vi måler observasjonene mot den, ellers ville en rett
# tunnel på to kilometer bestå av to punkter og en observasjon midt i
# tunnelen se ut til å ligge en kilometer fra sporet.
MAAL_STEG_M = 100.0

# Tak på hvor mange observasjoner vetosjekken ser på. En travel strekning har
# tusenvis, og medianen flytter seg ikke av de siste. Vi tar hver n-te, ikke
# de første: de første er alle fra samme tur.
MAKS_MAALEPUNKTER = 400

# Streken som sendes til nettleseren. Fem meter var riktig for å skille baner
# i rutingen; det er langt finere enn en piksel i kartet. 20 m halverer
# punkttallet uten synlig forskjell.
UT_TOLERANSE_M = 20.0

# Sporbiter kan være lange og nesten rette — en tunnel er to punkter og to km.
# Når vi måler hvor nær en bit lå observasjonene, legger vi inn punkter
# underveis, ellers måler vi bare endene og lar hele midten være umålt.
PROVE_STEG_M = 200.0
MAKS_PROVER = 40

# Tak på hvor mange noder A* får se på før vi gir opp. Et vanlig søk mellom
# nabostasjoner ser på noen hundre. Taket finnes for at en stasjon som ved en
# feil ikke henger sammen med nettet skal koste et øyeblikk og ikke et minutt.
MAKS_NODER = 60_000

# Taket på hvor mange etapper `strekning()` ruter for én kjede. En melding som
# rammer hele Sørlandsbanen har flere titalls stasjoner, og de siste av dem
# forteller ikke øyet noe den første halvdelen ikke allerede har sagt.
# Kjeden tynnes jevnt i stedet for å klippes, så begge endene blir med.
MAKS_ETAPPER = 40

# Hvor mange etappesvar `strekning()` husker. Se `_etappe` for hvorfor de
# bufres. Et stasjonspar i Oslo-området koster noen millisekunder å regne og
# noen kilobyte å huske; taket finnes bare for at en prosess som kjører i
# ukevis ikke skal vokse uten grense.
MAKS_BUFRETE_ETAPPER = 4_000


# ---------------------------------------------------------------------------
# Observasjonene, som en indeks
# ---------------------------------------------------------------------------
class Punktsky:
    """«Hvor nær nærmeste observasjon ligger dette punktet?»

    Rutenett med hundredels grad. Én celle er 1,1 km i nord–sør og rundt 550 m
    i øst–vest på 60 grader nord, og vi ser alltid på cellen og de åtte rundt.
    Alt innenfor 550 m finnes derfor garantert — komfortabelt over KORRIDOR_M.

    Svarene er klemt til `tak`. Vi bryr oss aldri om nøyaktig hvor langt unna
    noe er når det først er langt unna, og taket gjør at et punkt midt i
    ødemarka koster like lite som et punkt på sporet.
    """

    CELLE = 0.01

    def __init__(self, punkter: list[Punkt], tak: float = 5_000.0):
        self.tak = tak
        self.rutenett: dict[tuple[int, int], list[Punkt]] = defaultdict(list)
        for lon, lat in punkter:
            self.rutenett[(int(lon / self.CELLE), int(lat / self.CELLE))].append(
                (lon, lat)
            )

    def avstand(self, punkt: Punkt) -> float:
        cx, cy = int(punkt[0] / self.CELLE), int(punkt[1] / self.CELLE)
        beste = self.tak
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for p in self.rutenett.get((cx + dx, cy + dy), ()):
                    d = avstand_m(punkt, p)
                    if d < beste:
                        beste = d
        return beste


# ---------------------------------------------------------------------------
# Grafen
# ---------------------------------------------------------------------------
class Kant:
    """En sporbit mellom to knutepunkter, med geometrien sin intakt."""

    __slots__ = ("a", "b", "geo", "lengde", "prover")

    def __init__(self, a: int, b: int, geo: list[Punkt]):
        self.a = a
        self.b = b
        self.geo = geo
        self.lengde = sum(avstand_m(p, q) for p, q in zip(geo, geo[1:]))
        self.prover = _prover(geo, self.lengde)


def _prover(geo: list[Punkt], lengde: float) -> list[Punkt]:
    """Punkter å måle biten mot observasjonene med.

    Bitens egne hjørner, pluss innskutte punkter der den går langt uten å
    svinge. Uten dem ville en rett tunnel på to kilometer blitt vurdert på
    endepunktene alene — og endepunktene ligger ved stasjoner, der det alltid
    er observasjoner uansett hvilken trasé toget tok.
    """
    if lengde <= PROVE_STEG_M or len(geo) < 2:
        return list(geo)

    ut: list[Punkt] = [geo[0]]
    for p, q in zip(geo, geo[1:]):
        d = avstand_m(p, q)
        if d > PROVE_STEG_M:
            antall = min(int(d / PROVE_STEG_M), MAKS_PROVER)
            for i in range(1, antall):
                t = i / antall
                ut.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
        ut.append(q)
    return ut[:MAKS_PROVER * 4]


class Feste:
    """Et sted en stasjon kan henge seg på nettet.

    `seg` og `t` peker inn i sporbitens geometri: punktet ligger `t` av veien
    ut i linjestykket fra `geo[seg]` til `geo[seg+1]`. `avvik` er hvor mange
    meter det er derfra til stasjonen — prisen for å begynne akkurat her.
    """

    __slots__ = ("kant", "seg", "t", "avvik", "punkt")

    def __init__(self, kant: int, seg: int, t: float, avvik: float, punkt: Punkt):
        self.kant = kant
        self.seg = seg
        self.t = t
        self.avvik = avvik
        self.punkt = punkt


def _del_geo(geo: list[Punkt], i: int, t: float) -> tuple[Punkt, list[Punkt], list[Punkt]]:
    """Klipp en bit i to ved brøken `t` inne i segment nummer `i`.

    Returnerer (klippunktet, veien BAKOVER til bitens start, veien FRAMOVER
    til bitens slutt). Begge deler starter i klippunktet, slik at de kan
    brukes rett fram som geometri ut fra en virtuell node.
    """
    a, b = geo[i], geo[i + 1]
    q = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    bakover = [q, *reversed(geo[:i + 1])]
    framover = [q, *geo[i + 1:]]
    return q, bakover, framover


class Jernbanenett:
    """Spornettet som graf, med rutesøk som følger observasjonene.

    Noder er koordinater. `lagjernbanenett.py` runder alt til seks desimaler
    før det skrives, og to sporbiter som deler en OSM-node får derfor
    identiske tall — det er hele koblingsmekanismen, og grunnen til at
    byggeskriptet måler sammenhengen på avrundede koordinater og ikke på
    node-ID-er.
    """

    CELLE = 0.02

    def __init__(self, biter: list[list[Punkt]]):
        self.noder: list[Punkt] = []
        self.kanter: list[Kant] = []
        self.naboer: dict[int, list[int]] = defaultdict(list)
        self._etappebuffer: dict[tuple[float, float, float, float],
                                 list[Punkt] | None] = {}
        indeks: dict[Punkt, int] = {}

        def node(p: Punkt) -> int:
            i = indeks.get(p)
            if i is None:
                i = len(self.noder)
                indeks[p] = i
                self.noder.append(p)
            return i

        for bit in biter:
            if len(bit) < 2:
                continue
            a, b = node(bit[0]), node(bit[-1])
            if a == b:
                # En sløyfe tilbake til seg selv gir grafen ingenting og gjør
                # rekonstruksjonen tvetydig.
                continue
            k = len(self.kanter)
            self.kanter.append(Kant(a, b, bit))
            self.naboer[a].append(k)
            self.naboer[b].append(k)

        # Rutenett over kantene, så «hvilke spor ligger nær denne stasjonen»
        # ikke betyr å måle mot alle ti tusen.
        self.kantnett: dict[tuple[int, int], list[int]] = defaultdict(list)
        for k, kant in enumerate(self.kanter):
            celler = {
                (int(p[0] / self.CELLE), int(p[1] / self.CELLE)) for p in kant.geo
            }
            for c in celler:
                self.kantnett[c].append(k)

    # -- oppslag ----------------------------------------------------------
    def _kanter_naer(self, punkt: Punkt, meter: float) -> list[int]:
        cx, cy = int(punkt[0] / self.CELLE), int(punkt[1] / self.CELLE)
        # 0,02 grader er ~1,1 km i øst–vest på 60 nord. Én celle ut holder for
        # SNAPP_M, men vi regner rekkevidden ut i stedet for å anta den.
        rekke = int(meter / 1_000) + 1
        ut: set[int] = set()
        for dx in range(-rekke, rekke + 1):
            for dy in range(-rekke, rekke + 1):
                ut.update(self.kantnett.get((cx + dx, cy + dy), ()))
        return list(ut)

    def _fester(self, punkt: Punkt, sky: Punktsky | None) -> list[Feste]:
        """Sporene en stasjon kan tenkes å ligge på, billigste først.

        Ett feste per sporbit — det nærmeste punktet på den — og aldri lenger
        unna enn SNAPP_M. Rekkefølgen vekter avstanden med den samme straffen
        rutesøket bruker, så et spor toget ble observert på slår et spor tjue
        meter nærmere som det ikke ble observert på. Det er det som holder
        rutene fra hverandre på Oslo S, der et titalls spor og fem baner
        ligger innenfor hundre meter av stasjonspunktet.

        Hvorfor flere og ikke bare det billigste: se MAKS_FESTER.

        `sky` er None når det ikke finnes observasjoner å styre etter — se
        `strekning()`. Da er avstanden til sporet det eneste som skiller
        festene, og rekkefølgen blir den samme som om alle lå like langt fra
        en observasjon.
        """
        beste: dict[int, Feste] = {}
        for k in self._kanter_naer(punkt, SNAPP_M):
            geo = self.kanter[k].geo
            for i in range(len(geo) - 1):
                t, avvik = _projiser_pa_segment(punkt, geo[i], geo[i + 1])
                if avvik > SNAPP_M:
                    continue
                truffet = beste.get(k)
                if truffet is None or avvik < truffet.avvik:
                    a, b = geo[i], geo[i + 1]
                    q = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                    beste[k] = Feste(k, i, t, avvik, q)

        fester = list(beste.values())
        if sky is None:
            fester.sort(key=lambda f: f.avvik)
        else:
            fester.sort(
                key=lambda f: f.avvik
                * (1.0 if sky.avstand(f.punkt) <= KORRIDOR_M else STRAFF)
            )
        return fester[:MAKS_FESTER]

    # -- rutesøket --------------------------------------------------------
    def rute(
        self, fra: Punkt, til: Punkt, observasjoner: list[Punkt]
    ) -> list[Punkt] | None:
        """Sporet mellom to stasjoner, slik togene faktisk kjørte det.

        `observasjoner` er posisjonene som ble målt på denne strekningen. De
        avgjør hvilken av flere mulige traseer som velges — uten dem er dette
        bare korteste vei, og korteste vei tar feil tunnel.

        Returnerer None når nettet ikke gir et troverdig svar. Da skal
        kalleren tegne noe annet, ikke tegne dette likevel.
        """
        if not observasjoner:
            return None
        sky = Punktsky(observasjoner)

        starter = self._fester(fra, sky)
        maal = self._fester(til, sky)
        if not starter or not maal:
            return None

        rute = self._astar(starter, maal, til, sky)
        if rute is None:
            return None
        if not _troverdig(rute, fra, til, observasjoner):
            return None
        return _forenkle_ut(rute)

    # -- strekninger uten observasjoner -----------------------------------
    def strekning(self, kjede: list[Punkt]) -> list[list[Punkt]]:
        """Sporet gjennom en rekke stasjoner, uten observasjoner å styre etter.

        `rute()` sitt problem er å finne ut hvilken av flere mulige traseer
        toget FAKTISK tok, og til det trengs målinger. Her er spørsmålet et
        annet: en driftsmelding sier «stengt mellom Oslo S og Skøyen», og da
        skal kartet vise den strekningen — ikke gjette hvilket spor noen kjørte
        på. Det finnes ingen observasjoner å spørre, og det trengs ikke.

        Tvetydigheten løses av KJEDEN i stedet. Korteste vei fra Oslo S til
        Sagdalen går gjennom Romeriksporten; korteste vei fra Oslo S til Alna
        til Nyland til Grorud kan bare gå om Hovedbanen. Stasjonene mellom
        endepunktene er altså den samme opplysningen som observasjonene ga,
        bare gratis — og de følger med i `affects` på nettopp de meldingene
        som gjelder en hel strekning.

        Returnerer én linje per sammenhengende bit. Flere enn én betyr at en
        etappe falt fra: det er bedre å tegne resten med et hull i enn å la
        hele meldingen bli usynlig fordi ett stasjonspar ikke henger sammen.
        Tom liste når ingenting lot seg rute.
        """
        kjede = _tynn(kjede, MAKS_ETAPPER + 1)
        biter: list[list[Punkt]] = []
        loepende: list[Punkt] = []

        for fra, til in zip(kjede, kjede[1:]):
            etappe = self._etappe(fra, til)
            if etappe is None:
                # Hullet er ekte: her vet vi ikke hvor sporet går. Da starter
                # neste bit for seg selv i stedet for at streken hopper.
                if len(loepende) > 1:
                    biter.append(loepende)
                loepende = []
                continue
            loepende = [*loepende, *etappe[1:]] if loepende else list(etappe)

        if len(loepende) > 1:
            biter.append(loepende)
        return [_forenkle_ut(b) for b in biter]

    def _etappe(self, fra: Punkt, til: Punkt) -> list[Punkt] | None:
        """Korteste vei mellom to nabostasjoner, eller None.

        Vetoet er halve `_troverdig`: lengden mot luftlinja. Den andre halvdelen
        måler mot observasjonene, og dem har vi ikke — men lengdesjekken er
        nettopp den som fanger den feilen som kan oppstå her, at søket har vært
        innom feil bane og kommet tilbake.

        Svaret bufres på stasjonsparet. Driftsmeldingene spør om de samme
        etappene om og om igjen — Oslo S-Nationaltheatret ligger i nesten hver
        melding fra Oslo-området, og hele stripa regnes på nytt hvert minutt.
        Nettet er uforanderlig, så et svar er like gyldig i morgen.
        """
        nokkel = (round(fra[0], 5), round(fra[1], 5), round(til[0], 5), round(til[1], 5))
        if nokkel in self._etappebuffer:
            return self._etappebuffer[nokkel]

        svar = self._finn_etappe(fra, til)
        if len(self._etappebuffer) < MAKS_BUFRETE_ETAPPER:
            self._etappebuffer[nokkel] = svar
        return svar

    def _finn_etappe(self, fra: Punkt, til: Punkt) -> list[Punkt] | None:
        starter = self._fester(fra, None)
        maal = self._fester(til, None)
        if not starter or not maal:
            return None

        rute = self._astar(starter, maal, til, None)
        if rute is None:
            return None
        if _lengde(rute) > avstand_m(fra, til) * MAKS_OMVEI + OMVEI_MONN_M:
            return None
        return rute

    def _astar(
        self,
        starter: list[Feste],
        maal: list[Feste],
        til: Punkt,
        sky: Punktsky | None,
    ) -> list[Punkt] | None:
        """A* fra et sett festepunkter til et annet, med korridorstraff per bit.

        Søket starter ikke i ett punkt, men i alle festene stasjonen har —
        hvert med sin egen startkostnad for hvor langt fra sporet stasjonen
        ligger og om toget ble observert der. Det er søket selv som velger
        hvilket spor det begynner på, og det er nettopp det som gjør at to
        stasjoner på et dobbeltspor havner på det SAMME sporet.

        Heuristikken er luftlinja til stasjonen, minus SNAPP_M. Den er
        tillatelig: billigste faktor er 1,0, så ingen vei kan koste mindre enn
        den er lang, og det siste stykket inn til et festepunkt er alltid
        regnet med i kostnaden. A* finner derfor den virkelig billigste ruten
        og ikke bare en billig en.
        """
        faktor = self._faktorer(sky)

        beste_svar: tuple[float, list[Punkt]] | None = None

        def vurder(kost: float, vei: list[Punkt]) -> None:
            nonlocal beste_svar
            if beste_svar is None or kost < beste_svar[0]:
                beste_svar = (kost, vei)

        # Ligger et start- og et målfeste på SAMME sporbit, er strekningen
        # kortere enn ett ledd i grafen, og svaret er et utsnitt av den biten.
        # Uten dette ville søket gått ut til nærmeste knutepunkt og tilbake.
        mal_per_kant = {m.kant: m for m in maal}
        for s in starter:
            m = mal_per_kant.get(s.kant)
            if m is None:
                continue
            vei = _utsnitt(self.kanter[s.kant].geo, s, m)
            f = faktor(s.kant)
            vurder((_lengde(vei) + (s.avvik + m.avvik) * FESTE_VEKT) * f, vei)

        # Køen seeds med begge retninger ut fra hvert startfeste.
        kø: list[tuple[float, float, int, list[Punkt]]] = []
        for s in starter:
            kant = self.kanter[s.kant]
            f = faktor(s.kant)
            _, bak, fram = _del_geo(kant.geo, s.seg, s.t)
            for ende, vei in ((kant.a, bak), (kant.b, fram)):
                g = (s.avvik * FESTE_VEKT + _lengde(vei)) * f
                heapq.heappush(kø, (g + _h(self.noder[ende], til), g, ende, vei))

        # Målnodene er endene av bitene målfestene ligger på, hver med prisen
        # for det siste stykket inn til selve festepunktet. Deler to festebiter
        # en node, teller den billigste av dem.
        ender: dict[int, tuple[list[Punkt], float]] = {}
        for m in maal:
            kant = self.kanter[m.kant]
            f = faktor(m.kant)
            _, bak, fram = _del_geo(kant.geo, m.seg, m.t)
            for ende, vei in ((kant.a, bak), (kant.b, fram)):
                pris = (m.avvik * FESTE_VEKT + _lengde(vei)) * f
                truffet = ender.get(ende)
                if truffet is None or pris < truffet[1]:
                    ender[ende] = (list(reversed(vei)), pris)

        best: dict[int, float] = {}
        sett = 0

        while kø:
            prioritet, g, node, vei = heapq.heappop(kø)
            if beste_svar is not None and prioritet >= beste_svar[0]:
                # Ingenting igjen i køen kan bli bedre enn svaret vi har.
                break
            if g > best.get(node, float("inf")):
                continue
            sett += 1
            if sett > MAKS_NODER:
                log.debug("Rutesøk ga opp etter %d noder", sett)
                return None

            treff = ender.get(node)
            if treff is not None:
                hale, pris = treff
                vurder(g + pris, [*vei, *hale[1:]])

            for k in self.naboer[node]:
                kant = self.kanter[k]
                neste = kant.b if kant.a == node else kant.a
                ny_g = g + kant.lengde * faktor(k)
                if ny_g >= best.get(neste, float("inf")):
                    continue
                best[neste] = ny_g
                geo = kant.geo if kant.a == node else list(reversed(kant.geo))
                heapq.heappush(
                    kø,
                    (ny_g + _h(self.noder[neste], til), ny_g, neste, [*vei, *geo[1:]]),
                )

        return beste_svar[1] if beste_svar else None

    def _faktorer(self, sky: Punktsky | None):
        """Kostnadsmultiplikator per sporbit, regnet ut første gang den spørres.

        Medianen over bitens prøvepunkter, ikke minimum: en bit som så vidt
        streifer en observasjon i den ene enden har ikke båret toget. To
        parallelle spor som møtes i en sporveksel deler alltid det siste
        stykket, og med minimum ville begge sett like riktige ut.

        Uten observasjoner (`sky is None`) koster hver bit lengden sin, og
        søket blir korteste vei. Merk at det IKKE er det samme som å gi alle
        bitene straffen: en jevn faktor på åtte gjør heuristikken åtte ganger
        for svak, og A* faller tilbake til Dijkstra over hele landet.
        """
        if sky is None:
            return lambda k: 1.0

        buffer: dict[int, float] = {}

        def faktor(k: int) -> float:
            f = buffer.get(k)
            if f is None:
                prover = self.kanter[k].prover
                typisk = statistics.median(sky.avstand(p) for p in prover)
                f = 1.0 if typisk <= KORRIDOR_M else STRAFF
                buffer[k] = f
            return f

        return faktor


# ---------------------------------------------------------------------------
# Småting rutesøket bruker
# ---------------------------------------------------------------------------
def _h(node: Punkt, til: Punkt) -> float:
    """Underestimat av hva som gjenstår fra `node`.

    Minus SNAPP_M fordi ruten ikke skal fram til stasjonspunktet, men til et
    festepunkt som kan ligge inntil SNAPP_M unna det. Uten fratrekket kunne
    heuristikken overvurdere, og A* ville sluttet å garantere billigste rute.
    """
    return max(0.0, avstand_m(node, til) - SNAPP_M)


def _lengde(geo: list[Punkt]) -> float:
    return sum(avstand_m(p, q) for p, q in zip(geo, geo[1:]))


def _utsnitt(geo: list[Punkt], fra: Feste, til: Feste) -> list[Punkt]:
    """Stykket av én sporbit mellom to festepunkter på den."""
    (i, ti), (j, tj) = (fra.seg, fra.t), (til.seg, til.t)
    snu = (i, ti) > (j, tj)
    if snu:
        (i, ti), (j, tj) = (j, tj), (i, ti)

    def punkt(seg: int, t: float) -> Punkt:
        a, b = geo[seg], geo[seg + 1]
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    ut = [punkt(i, ti), *geo[i + 1:j + 1], punkt(j, tj)]
    return list(reversed(ut)) if snu else ut


def _fortett(geo: list[Punkt], steg: float) -> list[Punkt]:
    """Skyt inn punkter der linjen går langt uten å svinge."""
    ut: list[Punkt] = [geo[0]]
    for p, q in zip(geo, geo[1:]):
        d = avstand_m(p, q)
        if d > steg:
            for i in range(1, int(d / steg) + 1):
                t = i / (int(d / steg) + 1)
                ut.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
        ut.append(q)
    return ut


def _troverdig(
    rute: list[Punkt], fra: Punkt, til: Punkt, observasjoner: list[Punkt]
) -> bool:
    """To vetoer mot en rute som gikk galt uten å feile.

    Lengden: en trasé svinger, men den svinger ikke MAKS_OMVEI ganger
    luftlinja. Slår denne ut, har søket som regel vært innom feil bane.

    Nærheten: togene skal ha blitt målt der ruten går. Vi måler fra hver
    observasjon TIL ruten - se MAKS_TYPISK_AVVIK_M for hvorfor retningen er
    hele forskjellen - og bruker medianen, slik at et enkelt punkt med dårlig
    GPS ikke velter et riktig svar.
    """
    luft = avstand_m(fra, til)
    if _lengde(rute) > luft * MAKS_OMVEI + OMVEI_MONN_M:
        return False

    langs_ruten = Punktsky(_fortett(rute, MAAL_STEG_M))
    steg = max(1, len(observasjoner) // MAKS_MAALEPUNKTER)
    typisk = statistics.median(
        langs_ruten.avstand(p) for p in observasjoner[::steg]
    )
    return typisk <= MAKS_TYPISK_AVVIK_M


def _forenkle_ut(rute: list[Punkt]) -> list[Punkt]:
    return forenkle(rute, UT_TOLERANSE_M)


def _tynn(kjede: list[Punkt], tak: int) -> list[Punkt]:
    """Kort ned en kjede til `tak` punkter uten å miste endene.

    Klipping ville flyttet den ene enden av strekningen innover i landet -
    en melding om Oslo S-Stavanger ville endt i Kristiansand. Vi hopper over
    stasjoner i midten i stedet. Traseen blir den samme; det eneste vi mister
    er styringen gjennom de mellomste, og der det er tvil om trasé står
    stasjonene uansett tett.
    """
    if len(kjede) <= tak or tak < 2:
        return kjede
    steg = (len(kjede) - 1) / (tak - 1)
    return [kjede[round(i * steg)] for i in range(tak)]


# ---------------------------------------------------------------------------
# Innlasting — én gang per prosess
# ---------------------------------------------------------------------------
_nett: Jernbanenett | None = None
_forsokt = False
_stasjoner_buffer: dict[str, Punkt] | None = None


def nett() -> Jernbanenett | None:
    """Spornettet, lastet ved første bruk.

    Returnerer None hvis fila mangler eller er ødelagt, og prøver ikke igjen.
    `static/jernbanenett.geojson` er en bygget fil som ikke alle klonene av
    prosjektet trenger å ha; mangler den, skal flaskehalskartet fortsatt
    virke, bare med den gamle geometrien.
    """
    global _nett, _forsokt
    if _forsokt:
        return _nett
    _forsokt = True
    try:
        with open(NETT, encoding="utf-8") as fil:
            data = json.load(fil)
        biter = [
            [(p[0], p[1]) for p in linje]
            for f in data["features"]
            for linje in f["geometry"]["coordinates"]
        ]
        _nett = Jernbanenett(biter)
        log.info(
            "Spornett lastet: %d biter, %d noder", len(_nett.kanter), len(_nett.noder)
        )
    except FileNotFoundError:
        log.info("%s finnes ikke - flaskehalskartet bruker medianlinjer. "
                 "Bygg den med: python lagjernbanenett.py", NETT)
    except Exception as exc:  # noqa: BLE001 - en ødelagt fil skal ikke velte kartet
        log.warning("Kunne ikke lese %s: %s", NETT, exc)
    return _nett


def rute(fra: Punkt, til: Punkt, observasjoner: list[Punkt]) -> list[Punkt] | None:
    """Kortveien inn: sporet mellom to punkter, eller None."""
    n = nett()
    return None if n is None else n.rute(fra, til, observasjoner)


def strekning(kjede: list[Punkt]) -> list[list[Punkt]]:
    """Kortveien inn: sporet gjennom en kjede av stasjoner, eller tom liste."""
    n = nett()
    return [] if n is None or len(kjede) < 2 else n.strekning(kjede)


def stasjoner() -> dict[str, Punkt]:
    """Stasjonspunktene fra `static/stasjoner.geojson`, lest én gang.

    Nøkkelen er navnet uten «stasjon» bakpå - det er formen resten av
    prosjektet bruker, og den formen driftsmeldingene skriver i teksten sin.
    Tom dict hvis fila mangler; da faller kalleren tilbake på det den har.
    """
    global _stasjoner_buffer
    if _stasjoner_buffer is not None:
        return _stasjoner_buffer

    ut: dict[str, Punkt] = {}
    try:
        with open(STASJONER, encoding="utf-8") as fil:
            data = json.load(fil)
        for f in data["features"]:
            navn = (f["properties"].get("navn") or "").removesuffix(" stasjon")
            if navn:
                lon, lat = f["geometry"]["coordinates"]
                ut[navn] = (lon, lat)
    except FileNotFoundError:
        log.info("%s finnes ikke. Bygg den med: python lagstasjoner.py", STASJONER)
    except Exception as exc:  # noqa: BLE001 - en ødelagt fil skal ikke velte kartet
        log.warning("Kunne ikke lese %s: %s", STASJONER, exc)

    _stasjoner_buffer = ut
    return ut


# ---------------------------------------------------------------------------
# Selvtest
# ---------------------------------------------------------------------------


def _korridor(punkter: list[Punkt], steg: float = 100.0) -> list[Punkt]:
    """Late observasjoner: en tett kjede av punkter gjennom en rekke steder.

    Brukes i selvtesten til å si «toget kjørte HER» uten å ha en database.
    Mellom to nabostasjoner ligger sporet nær nok luftlinja til at en kjede
    gjennom stasjonene peker ut riktig bane - som er akkurat det korridoren
    skal brukes til.
    """
    ut: list[Punkt] = []
    for a, b in zip(punkter, punkter[1:]):
        antall = max(1, int(avstand_m(a, b) / steg))
        for i in range(antall):
            t = i / antall
            ut.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    ut.append(punkter[-1])
    return ut


def _selvtest() -> int:
    """Tester rutingen mot spornettet. Ingen database, ingen nettkall.

    Sjekkene er valgt etter feilene som faktisk oppsto mens modulen ble
    skrevet, og hver av dem ville fanget sin. Ende-til-ende-målingen med ekte
    observasjoner hører hjemme i `prober/sjekk_flaskehals.py`.
    """
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    n = nett()
    if n is None:
        print("Fant ikke spornettet. Kjør: python lagjernbanenett.py")
        return 1
    print(f"spornett     {len(n.kanter)} biter, {len(n.noder)} noder")

    st = stasjoner()
    feil = 0

    # --- 1. Alle stasjoner finner spor -----------------------------------
    # `lagjernbanenett.py --sjekk` sier at alle har spor innenfor en
    # kilometer. Her sjekker vi at de også finner det gjennom SNAPP_M og
    # kantrutenettet, som er to andre tall og en annen indeks.
    tom = Punktsky([])
    uten = [navn for navn, p in st.items() if not n._fester(p, tom)]
    if uten:
        print(f"feste        FEIL  {len(uten)} stasjoner uten spor innen "
              f"{SNAPP_M:.0f} m: {', '.join(sorted(uten)[:6])}")
        feil += 1
    else:
        print(f"feste        OK    alle {len(st)} stasjoner finner spor")

    # --- 2. Korridoren velger bane ---------------------------------------
    # Bryn og Lillestrøm henger sammen på to måter: Gardermobanen gjennom
    # Romeriksporten (rett, kort) og Hovedbanen om Strømmen (rundt, lang).
    # Korteste vei tar alltid tunnelen. Klarer ikke korridoren å overstyre
    # det, er hele STRAFF-mekanikken uvirksom - og det var nettopp den feilen
    # medianlinja hadde på en annen måte.
    bryn, lstrom = st["Bryn"], st["Lillestrøm"]
    via_hovedbanen = [
        st[navn] for navn in (
            "Bryn", "Alna", "Nyland", "Grorud", "Haugenstua", "Høybråten",
            "Lørenskog", "Hanaborg", "Fjellhamar", "Strømmen", "Sagdalen",
            "Lillestrøm",
        )
    ]
    tunnel = n.rute(bryn, lstrom, _korridor([bryn, lstrom]))
    om_strommen = n.rute(bryn, lstrom, _korridor(via_hovedbanen))
    if tunnel is None or om_strommen is None:
        print("korridor     FEIL  fant ikke rute Bryn–Lillestrøm")
        feil += 1
    else:
        kort, lang = _lengde(tunnel) / 1000, _lengde(om_strommen) / 1000
        # Grorud er prøvesteinen, ikke Strømmen. Strømmen ligger nesten
        # oppå luftlinja Bryn-Lillestrøm og er derfor 50 m fra BEGGE rutene -
        # den skiller dem ikke. Grorud ligger nord for korden, midt på
        # Hovedbanens sløyfe, og over en kilometer fra tunnelen.
        naer_hovedbanen = Punktsky(_fortett(om_strommen, MAAL_STEG_M)).avstand(
            st["Grorud"])
        naer_tunnel = Punktsky(_fortett(tunnel, MAAL_STEG_M)).avstand(st["Grorud"])
        if lang < kort + 1.5 or naer_hovedbanen > 300 or naer_tunnel < 1000:
            print(f"korridor     FEIL  tunnel {kort:.1f} km, om Strømmen "
                  f"{lang:.1f} km, Grorud {naer_hovedbanen:.0f} m / "
                  f"{naer_tunnel:.0f} m fra rutene")
            feil += 1
        else:
            print(f"korridor     OK    {kort:.1f} km gjennom Romeriksporten, "
                  f"{lang:.1f} km om Strømmen og Grorud")

    # --- 3. Dobbeltspor gir ikke omvei -----------------------------------
    # Sagdalen og Strømmen er 1,1 km fra hverandre på et dobbeltspor. Fester
    # de seg til hvert sitt av de to sporene, må ruten ut til nærmeste
    # sporveksel og tilbake - målt til 4,1 km før MAKS_FESTER kom inn.
    par = [("Sagdalen", "Strømmen"), ("Høn", "Vakås"), ("Hvalstad", "Vakås")]
    verst = 0.0
    verst_navn = ""
    for a, b in par:
        r = n.rute(st[a], st[b], _korridor([st[a], st[b]]))
        if r is None:
            print(f"dobbeltspor  FEIL  ingen rute {a}–{b}")
            feil += 1
            continue
        forhold = _lengde(r) / avstand_m(st[a], st[b])
        if forhold > verst:
            verst, verst_navn = forhold, f"{a}–{b}"
    if verst > 1.6:
        print(f"dobbeltspor  FEIL  {verst_navn} rutet {verst:.1f}x luftlinja - "
              "ser ut som en tur til nærmeste sporveksel og tilbake")
        feil += 1
    elif verst:
        print(f"dobbeltspor  OK    verste omvei {verst:.2f}x ({verst_navn})")

    # --- 4. Festet flytter ikke endepunktene -----------------------------
    # Uten FESTE_VEKT kunne søket kjøpe seg kortere rute ved å feste
    # stasjonene lenger fra sporet. Resultatet var strekninger KORTERE enn
    # luftlinja, som ingen jernbane kan være.
    korte = []
    for a, b in (("Nationaltheatret", "Skøyen"), ("Oslo S", "Nationaltheatret"),
                 ("Lillestrøm", "Strømmen"), ("Drammen", "Brakerøya")):
        r = n.rute(st[a], st[b], _korridor([st[a], st[b]]))
        if r is None:
            continue
        forhold = _lengde(r) / avstand_m(st[a], st[b])
        if forhold < 0.95:
            korte.append(f"{a}–{b} {forhold:.2f}x")
    if korte:
        print(f"endepunkter  FEIL  kortere enn luftlinja: {', '.join(korte)}")
        feil += 1
    else:
        print("endepunkter  OK    ingen strekning kortere enn luftlinja")

    # --- 5. Vetoene virker -----------------------------------------------
    # En korridor som ligger et helt annet sted skal gi None, ikke en pen rute
    # gjennom Oslo. Uten dette ville en feil i observasjonene blitt til en
    # selvsikker strek på feil bane.
    langt_unna = _korridor([(5.33, 60.39), (5.35, 60.40)])  # Bergen sentrum
    if n.rute(st["Oslo S"], st["Lillestrøm"], langt_unna) is not None:
        print("veto         FEIL  godtok en rute observasjonene ikke støtter")
        feil += 1
    elif n.rute(st["Oslo S"], st["Lillestrøm"], []) is not None:
        print("veto         FEIL  rutet uten observasjoner i det hele tatt")
        feil += 1
    else:
        print("veto         OK    avviser ruter observasjonene ikke støtter")

    # --- 6. Strekninger uten observasjoner -------------------------------
    # `strekning()` er den andre inngangen: driftsmeldingene har ingen målte
    # posisjoner, bare en rekke stasjoner. Tre ting må stemme.

    # a) Kjeden må velge bane der korteste vei ville tatt feil. Dette er den
    #    samme prøvesteinen som sjekk 2, uten en eneste observasjon.
    kjede = n.strekning(via_hovedbanen)
    rett_fram = n.strekning([bryn, lstrom])
    if not kjede or not rett_fram:
        print("strekning    FEIL  fant ikke strekningen Bryn–Lillestrøm")
        feil += 1
    else:
        via = Punktsky(_fortett(kjede[0], MAAL_STEG_M)).avstand(st["Grorud"])
        uten = Punktsky(_fortett(rett_fram[0], MAAL_STEG_M)).avstand(st["Grorud"])
        if via > 300 or uten < 1000:
            print(f"strekning    FEIL  Grorud er {via:.0f} m fra kjeden og "
                  f"{uten:.0f} m fra korteste vei - kjeden styrer ikke")
            feil += 1
        else:
            print(f"strekning    OK    kjeden gir Hovedbanen ({_lengde(kjede[0])/1000:.1f} km), "
                  f"korteste vei tunnelen ({_lengde(rett_fram[0])/1000:.1f} km)")

    # b) Rekkefølgen må tåle en lang kjede. Sørlandsbanen fra Oslo S er den
    #    lengste meldingen som faktisk forekommer, og den skal komme ut som
    #    ÉN sammenhengende bit - flere biter betyr at en etappe falt fra.
    sor = [
        st[navn] for navn in (
            "Oslo S", "Nationaltheatret", "Skøyen", "Lysaker", "Sandvika",
            "Asker", "Drammen", "Kongsberg", "Nordagutu", "Bø", "Nelaug",
            "Vegårshei", "Gjerstad",
        )
        if navn in st
    ]
    biter = n.strekning(sor)
    if len(biter) != 1:
        print(f"lang kjede   FEIL  Oslo S–Gjerstad kom ut i {len(biter)} biter")
        feil += 1
    else:
        print(f"lang kjede   OK    Oslo S–Gjerstad i én bit, "
              f"{_lengde(biter[0])/1000:.0f} km")

    # c) Tynningen må beholde begge endene. Ble kjeden klippet i stedet, endte
    #    en melding om Oslo S–Stavanger midtveis inne i landet.
    lang = _tynn(sor, 4)
    if len(lang) != 4 or lang[0] != sor[0] or lang[-1] != sor[-1]:
        print("tynning      FEIL  mistet en ende av kjeden")
        feil += 1
    else:
        print(f"tynning      OK    {len(sor)} stasjoner til {len(lang)}, "
              "begge endene beholdt")

    print()
    if feil:
        print(f"{feil} ting å se på.")
    else:
        print("Rutingen ser riktig ut.")
    return 1 if feil else 0


if __name__ == "__main__":
    raise SystemExit(_selvtest())
