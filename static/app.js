/* TogKart — frontend.
 *
 * Kartet bygges ÉN gang. Hver oppdatering bytter bare ut dataene i
 * GeoJSON-kilden med setData(), så prikkene flytter seg uten at kartet blinker.
 */

const CONFIG = {
  refreshMs: 15000,

  /* Stromkart bruker ingen bakgrunnsfliser — prissoner er polygoner på mørk
   * flate. Det går ikke her: en togprikk uten kystlinje og jernbane rundt seg
   * er uleselig. Dette er den mørkeste gratis-stilen uten API-nøkkel, og den
   * ligger nær --bg. Vil du enda nærmere stromkart-følelsen: bygg en egen stil
   * med bare kystlinje og jernbane. */
  mapStyle: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",

  center: [12.0, 62.5],
  zoom: 4.1,
};

/* Båndene, med grensene sine i sekunder. `fra` og `til` er halvåpne: et bånd
 * dekker fra og med `fra` til, men ikke med, `til`. null betyr «ingen grense
 * den veien».
 *
 * Tallene sto tidligere spredt i tre utgaver — i STOPS over, i en if-kjede i
 * bandFarge(), og i teksten under tegnforklaringen. Nå står de her, og de tre
 * andre leser dem herfra. Grensene må fortsatt matche delay_band() i
 * entur.py; det er den ene duplikatet som ikke kan fjernes, siden backend
 * fargelegger `band` selv.
 *
 * `hjelp` er det tooltipen viser og forklarer det terskelen ikke sier: hva
 * ordet betyr, ikke bare hvilke sekunder det dekker. */
const BANDS = [
  {
    id: "i_rute", label: "I rute", varName: "--c-low",
    fra: null, til: 240,
    hjelp: "Tog som går før rutetid teller også som i rute. Grensen på fire " +
           "minutter følger den norske punktlighetsdefinisjonen for lokaltog.",
  },
  {
    id: "grense", label: "På grensen", varName: "--c-mid",
    fra: 240, til: 360,
    hjelp: "Over lokaltogsgrensen, men innenfor de 5:59 som regnes som i " +
           "rute for fjerntog. Hvilken av de to som gjelder, avhenger av " +
           "togtypen — kartet bruker den strengeste for alle.",
  },
  {
    id: "forsinket", label: "Forsinket", varName: "--c-high",
    fra: 360, til: 900,
    hjelp: "Forsinket etter enhver definisjon, uansett togtype.",
  },
  {
    id: "mye", label: "Mye forsinket", varName: "--c-vhigh",
    fra: 900, til: null,
    hjelp: "Et kvarter eller mer. Her begynner forsinkelsen å forplante seg " +
           "til andre tog på samme spor.",
  },
  {
    id: "ukjent", label: "Ukjent", varName: "--c-unknown",
    fra: null, til: null,
    hjelp: "Toget rapporterer posisjon, men ingen forsinkelse. Grå og ikke " +
           "grønn, fordi «vet ikke» ikke er det samme som «i rute».",
  },
];

/* Fargestoppene til den kontinuerlige gradienten på kartprikkene. Utledet av
 * BANDS: hvert bånd bidrar med sin nedre grense, og «i rute» starter på null.
 *
 * Fôret også .legend-bar til 20. august. Den gradientstripa ble borte da
 * tegnforklaringen ga plass til nyhetsstripa; kartprikkene bruker den ennå.
 *
 * Sto tidligere som en egen tabell med tallene skrevet en gang til, over en
 * kommentar som sa «endrer du det ene, endre det andre». Det er den slags
 * avtale som holder helt til noen glemmer den. */
const STOPS = BANDS
  .filter((b) => b.id !== "ukjent")
  .map((b) => ({ seconds: b.fra ?? 0, varName: b.varName }));

/* Intervallet et bånd dekker, som tekst: «4:00–5:59». Utledet av tallene i
 * BANDS i stedet for skrevet ved siden av dem — en tekst som sier 5:59 mens
 * koden sier 360 er verre enn ingen tekst.
 *
 * Øvre grense vises som `til - 1` sekund, altså siste verdi som faktisk
 * havner i båndet. Skrev vi «4:00–6:00» ville 6:00 stått i to bånd. */
function bandIntervall(band) {
  if (band.fra === null && band.til === null) return "ingen data";
  if (band.fra === null) return `under ${mmss(band.til)}`;
  if (band.til === null) return `${mmss(band.fra)} og mer`;
  return `${mmss(band.fra)}–${mmss(band.til - 1)}`;
}

const EMPTY = { type: "FeatureCollection", features: [] };

// Siste datasett fra backend. Togsøk leter her i stedet for å spørre serveren
// - dataene ligger allerede i nettleseren, og treff blir umiddelbare.
let latest = EMPTY;

// Tur-ID-en til toget hvis rute er tegnet nå, eller null. Brukes til å rydde
// bort ruta hvis det valgte toget forsvinner fra feeden (se refresh()).
let valgtTurId = null;

/* --- Farger ---------------------------------------------------------------
 * MapLibre kan ikke lese CSS-variabler, så vi henter dem ut i JS. Det er
 * dette som gjør theme.css til eneste sted du trenger å bytte farge. */

const rootStyle = getComputedStyle(document.documentElement);
const token = (name) => rootStyle.getPropertyValue(name).trim() || "#6e7681";

/* Datafargen som KLASSE, ikke som style-attributt.
 *
 * Fargen på et tall, en prikk eller en strek kommer fra dataene: hvilket
 * punktlighetsbånd toget er i, hvor mye tid en strekning tar. Fram til 23.
 * august ble den skrevet rett inn i markupen mens den ble bygget -
 * `style="color:${token(...)}"`, sju steder - og de sju var hele grunnen til
 * at CSP-en måtte si `style-src 'unsafe-inline'`. Et style-attributt satt fra
 * en streng er, sett fra nettleseren, ikke til å skille fra et en angriper
 * fikk plantet der.
 *
 * Klassen setter én variabel, `--f`, og komponenten i app.css bestemmer selv
 * om den skal bli tekstfarge eller flatefarge. Navnet UTLEDES av
 * variabelnavnet, så det finnes ingen tabell her som kan komme ut av takt med
 * theme.css: legger du til en farge der, får den en klasse av seg selv - så
 * lenge du husker de seks linjene i app.css. Det er én lenke i kjeden, ikke
 * to.
 *
 * Merk at CSSOM-veien - `element.style.width = ...` - IKKE rammes av
 * style-src. Den brukes fortsatt der verdien er kontinuerlig og en klasse
 * ikke gir mening: bredden på historikkstrekene. */
const fargeklasse = (varName) => `f-${varName.replace(/^--/, "")}`;

/* --- Escaping --------------------------------------------------------------
 * ALT som kommer utenfra og skal inn i innerHTML eller setHTML skal gjennom
 * denne. Linjenavn, stasjonsnavn, tognumre, materielltekster og
 * søketreff kommer fra Entur, ikke fra oss - og et API-svar er data, ikke
 * markup.
 *
 * Uten den var dette nok til å kjøre vilkårlig JS i nettleseren til alle som
 * hadde kartet oppe:
 *
 *     lineName: '<img src=x onerror="...">'
 *
 * Entur er en kilde vi stoler på, og det har aldri skjedd. Men tilliten er
 * ikke poenget: den dagen et feltnavn endrer betydning, eller noen får skrevet
 * i et stoppestedsregister, skal ikke kartet være det som kjører koden. Testet
 * 20. august - se «Full testrunde» i docs/undersokelser.md.
 *
 * Attributter siteres alltid med " i denne fila, så &quot; er med. `esc` tåler
 * null og tall og gir tom streng for null/undefined, slik at kallerne slipper
 * å sjekke først. */
const HTML_TEGN = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function esc(verdi) {
  if (verdi === null || verdi === undefined) return "";
  return String(verdi).replace(/[&<>"']/g, (t) => HTML_TEGN[t]);
}

/* Kontinuerlig farge på forsinkelse, med grå for tog uten data.
 * `case` kortslutter, så interpolate kjører aldri på et null-felt. */
function colorByDelay() {
  const interpolate = ["interpolate", ["linear"], ["to-number", ["get", "delay"], 0]];
  STOPS.forEach((s) => interpolate.push(s.seconds, token(s.varName)));

  return [
    "case",
    ["==", ["get", "band"], "ukjent"], token("--c-unknown"),
    interpolate,
  ];
}

/* --- Varmekart over flaskehalser ------------------------------------------
 * Strekninger mellom nabostasjoner, farget etter hvor mye tid togene taper på
 * å kjøre dem. Slås av og på med bryteren i #info.
 *
 * Skalaen er DIVERGERENDE og ikke ensrettet som prikkenes, fordi størrelsen er
 * en annen: prikkene viser forsinkelse, som har et gulv på null, mens dette
 * viser ENDRING i forsinkelse — og den kan gå begge veier. En strekning der
 * togene tar inn tid er et ekte og interessant funn, ikke bare fravær av et
 * problem, og den trenger sin egen farge.
 *
 * Blått til det, og ikke grønt: grønt betyr «i rute» på prikkene, og de to
 * lagene ligger i samme kart. Den samme fargen kan ikke bety to ting.
 *
 * Tallene: medianene i databasen 20. august lå mellom −48 og +48 sekunder, så
 * taket på 90 gjør at de verste strekningene blir tydelig røde uten at alt
 * annet klumper seg i toppen av skalaen. */

const FH_SKALA = [
  { sekunder: -60, varName: "--accent" },
  { sekunder: 0, varName: "--c-unknown" },
  { sekunder: 30, varName: "--c-mid" },
  { sekunder: 60, varName: "--c-high" },
  { sekunder: 90, varName: "--c-vhigh" },
];

const FH_REFRESH_MS = 10 * 60 * 1000;

let flaskehalserPaa = false;
let fhHentet = false;

function fhFarge() {
  const uttrykk = ["interpolate", ["linear"], ["get", "sekunder"]];
  FH_SKALA.forEach((s) => uttrykk.push(s.sekunder, token(s.varName)));
  return uttrykk;
}

function leggTilFlaskehalslag() {
  map.addSource("flaskehalser", { type: "geojson", data: EMPTY });

  // Glød under streken, samme visuelle språk som spor- og korridorlagene.
  map.addLayer({
    id: "flaskehals-glod",
    type: "line",
    source: "flaskehalser",
    layout: { "line-cap": "round", "line-join": "round", visibility: "none" },
    paint: {
      "line-color": fhFarge(),
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 8, 12, 22],
      "line-opacity": 0.22,
      "line-blur": 3,
    },
  });

  map.addLayer({
    id: "flaskehals-strek",
    type: "line",
    source: "flaskehalser",
    layout: { "line-cap": "round", "line-join": "round", visibility: "none" },
    paint: {
      "line-color": fhFarge(),
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 3, 12, 8],
      "line-opacity": 0.9,
    },
  });

  map.on("click", "flaskehals-strek", visFlaskehalsPopup);
  map.on("mouseenter", "flaskehals-strek", () => {
    if (flaskehalserPaa) map.getCanvas().style.cursor = "pointer";
  });
  map.on("mouseleave", "flaskehals-strek", () => (map.getCanvas().style.cursor = ""));
}

function fhMinutter(sekunder) {
  const tegn = sekunder > 0 ? "+" : sekunder < 0 ? "−" : "";
  const abs = Math.abs(sekunder);
  if (abs < 60) return `${tegn}${abs} s`;
  return `${tegn}${(abs / 60).toFixed(1)} min`;
}

function visFlaskehalsPopup(event) {
  const p = event.features[0].properties;

  /* MapLibre serialiserer alt som ikke er tall eller streng til JSON når
   * kilden leses inn, så både retningslista og linjelista kommer tilbake som
   * strenger. `retninger` ble tolket tilbake fra første dag; `linjer` ble det
   * ikke, og popupen viste derfor `["R12", "R13", "R14"]` med klammer og
   * anførselstegn. Samme behandling til begge. */
  const tolk = (verdi) => {
    if (Array.isArray(verdi)) return verdi;
    if (typeof verdi !== "string") return [];
    try {
      const tolket = JSON.parse(verdi);
      return Array.isArray(tolket) ? tolket : [];
    } catch (error) {
      // Ikke JSON. En enkelt verdi er fortsatt en liste på ett element.
      return verdi ? [verdi] : [];
    }
  };

  const retninger = tolk(p.retninger);

  const sekunder = Number(p.sekunder);
  const rader = (retninger || []).map(
    (r) =>
      `${esc(r.retning)}: <strong>${fhMinutter(r.sekunder)}</strong>` +
      ` <span class="fh-antall">${r.passeringer} passeringer</span>`
  );

  rader.push(
    `Verste enkeltpassering ${fhMinutter(Number(p.verste))}`,
    `${p.passeringer} passeringer · ${p.km} km`
  );
  const linjer = tolk(p.linjer);
  if (linjer.length) rader.push(`Linjer: ${esc(linjer.join(", "))}`);

  // Streken følger normalt sporet, rutet gjennom jernbanenettet. Klarte ikke
  // rutingen å svare — typisk der togene ble målt på en parallell bane enn den
  // stasjonsparet ligger på — er streken en median av observerte posisjoner i
  // stedet, og da skal det stå her. En reserve som ikke kan skilles fra
  // hovedveien blir usynlig når den slår inn oftere enn den skal.
  if (p.geometri === "median") {
    rader.push(
      `<span class="fh-antall">Strek anslått fra togposisjoner, ikke rutet på spor</span>`
    );
  }

  new maplibregl.Popup({ offset: 12 })
    .setLngLat(event.lngLat)
    .setHTML(
      `<div class="pop-line">${esc(p.fra)} – ${esc(p.til)}</div>
       <div class="pop-delay ${fargeklasse(fhVarFor(sekunder))}">
         ${sekunder >= 0 ? "Taper" : "Tar inn"} ${fhMinutter(sekunder)} typisk
       </div>
       <div class="pop-meta">${rader.join("<br>")}</div>`
    )
    .addTo(map);
}

/* Fargen på tallet i popupen. Velger NÆRMESTE fargestopp, mens laget i kartet
 * interpolerer jevnt mellom dem — så streken og teksten kan ha litt ulik
 * nyanse for verdier midt mellom to stopp.
 *
 * Det er et bevisst valg og ikke en forglemmelse: å regne ut MapLibre sin
 * interpolasjon en gang til i JS ville vært den samme fargen skrevet to
 * steder, og teksten står uansett ikke inntil streken. Trenger de å stemme
 * eksakt en dag, er det interpolasjonen som skal flyttes hit — ikke skalaen
 * som skal dupliseres. */
function fhVarFor(sekunder) {
  const s = FH_SKALA;
  if (sekunder <= s[0].sekunder) return s[0].varName;
  if (sekunder >= s[s.length - 1].sekunder) return s[s.length - 1].varName;
  for (let i = 0; i < s.length - 1; i++) {
    if (sekunder <= s[i + 1].sekunder) {
      return sekunder - s[i].sekunder < s[i + 1].sekunder - sekunder
        ? s[i].varName
        : s[i + 1].varName;
    }
  }
  return "--c-unknown";
}

async function hentFlaskehalser() {
  const fot = document.getElementById("flaskehals-fot");
  try {
    const svar = await fetch("/api/flaskehalser", { cache: "no-store" });
    const data = await svar.json();
    if (!svar.ok) throw new Error(data.error || "utilgjengelig");

    map.getSource("flaskehalser").setData(data);
    fhHentet = true;

    const m = data.meta || {};
    if (!data.features.length) {
      fot.textContent =
        "Ingen strekninger med nok passeringer ennå. Historikken må samle " +
        "data over noen døgn før kartet har noe å vise.";
      return;
    }

    const verst = data.features.reduce(
      (a, b) => (b.properties.sekunder > a.properties.sekunder ? b : a)
    ).properties;
    fot.innerHTML =
      `${m.strekninger} strekninger fra ${m.dager} døgn. ` +
      `Verst: <strong>${esc(verst.fra)}–${esc(verst.til)}</strong>, ` +
      `${fhMinutter(verst.sekunder)} typisk over ${verst.passeringer} passeringer.`;
  } catch (error) {
    // Varmekartet er et tillegg. Feiler det, skal kartet stå som før.
    fot.textContent = "Fikk ikke hentet flaskehalsdata.";
  }
}

function settFlaskehalser(paa) {
  flaskehalserPaa = paa;
  const synlig = paa ? "visible" : "none";
  ["flaskehals-glod", "flaskehals-strek"].forEach((lag) => {
    if (map.getLayer(lag)) map.setLayoutProperty(lag, "visibility", synlig);
  });
  document.getElementById("flaskehals-panel").hidden = !paa;

  // Hentes først når noen faktisk slår laget på. De fleste besøkende ser
  // aldri på det, og da er det ingen grunn til å regne ut medianer for hele
  // nettet ved hver sidelasting.
  if (paa && !fhHentet) hentFlaskehalser();
}

/* --- Kjøreretning ---------------------------------------------------------
 * En liten trekant på kanten av prikken, som peker dit toget kjører.
 *
 * TRE VALG SOM HENGER SAMMEN, og som er verdt å lese før du endrer tallene:
 *
 * 1. SDF-BILDE OG IKKE FERDIGFARGEDE PIKSLER. Prikkene bruker en KONTINUERLIG
 *    gradient over forsinkelsen (`colorByDelay`), ikke fem faste farger. Et
 *    ferdigfarget bilde måtte enten valgt ett av båndene — og da hadde pila
 *    hatt en annen nyanse enn prikken den sitter på — eller regnet ut den
 *    samme gradienten en gang til i JS, ved siden av den MapLibre allerede
 *    regner ut. Et SDF-bilde fargelegges av `icon-color`, og da kan pila få
 *    NØYAKTIG samme uttrykk som prikken. Én kilde til fargen.
 *
 * 2. AVSTANDEN ER BAKT INN I BILDET, ikke satt med `icon-offset`. Bildet er
 *    høyt og for det meste gjennomsiktig; trekanten sitter helt oppe.
 *    Rotasjonen skjer om bildets midtpunkt, så trekanten går i bane rundt
 *    prikken av seg selv. Alternativet — `icon-offset` sammen med
 *    `icon-rotate` — gjør det samme, men avhenger av om offseten ganges med
 *    `icon-size` eller ikke, og det er en detalj som har byttet betydning
 *    mellom versjoner. Gjennomsiktige piksler oppfører seg likt overalt.
 *
 * 3. BILDET SKALERES I TAKT MED PRIKKEN. `icon-size` går 0,4 → 1,0 over de
 *    samme zoomtrinnene som `circle-radius` går 3,2 → 8. Begge er faktor 2,5,
 *    så klaringen mellom prikkekant og trekant er den samme på alle zoomnivå.
 *    Endrer du radiusen på prikken, må PIL.radius følge etter. */

const PIL = {
  trekant: 10,      // trekantens bredde og høyde i CSS-piksler
  marg: 1,          // luft rundt trekanten, så SDF-kanten ikke klippes
  luft: 1.5,        // klaring mellom prikkens kant og trekantens bakkant
  radius: 8,        // circle-radius på zoom 10 — må matche trains-dot
  ratio: 2,         // tegnes i dobbel oppløsning
  spredning: 4,     // hvor mange piksler SDF-en bruker på å gå fra inne til ute
  navn: "tog-pil",
};

// Avstanden fra bildets midtpunkt til trekantens spiss. Bildet må være dobbelt
// så høyt, slik at midtpunktet havner på prikkens senter.
PIL.forkant = PIL.radius + PIL.luft + PIL.trekant + PIL.marg;

function _avstandTilSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const lengde2 = dx * dx + dy * dy;
  const t = lengde2 === 0
    ? 0
    : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lengde2));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/* Positiv utenfor, negativ innenfor. Fortegnet avgjøres av kryssproduktene:
 * ligger punktet på samme side av alle tre kantene, er det inni. */
function _fortegnetAvstand(px, py, hjorner) {
  let naermest = Infinity;
  let positiv = false;
  let negativ = false;

  for (let i = 0; i < 3; i++) {
    const [ax, ay] = hjorner[i];
    const [bx, by] = hjorner[(i + 1) % 3];
    naermest = Math.min(naermest, _avstandTilSegment(px, py, ax, ay, bx, by));
    const kryss = (bx - ax) * (py - ay) - (by - ay) * (px - ax);
    if (kryss > 0) positiv = true;
    if (kryss < 0) negativ = true;
  }

  return positiv && negativ ? naermest : -naermest;
}

/* Bygger SDF-en. MapLibre leser bare alfakanalen: 128 er kanten, over er
 * innenfor, under er utenfor. RGB må likevel fylles — de ignoreres, men en
 * uinitialisert buffer er ikke gjennomsiktig, den er tilfeldig. */
function lagPilbilde() {
  const bredde = PIL.trekant + 2 * PIL.marg;
  const hoyde = 2 * PIL.forkant;
  const w = Math.round(bredde * PIL.ratio);
  const h = Math.round(hoyde * PIL.ratio);

  // Trekanten står øverst i bildet, med spissen opp. Bildet er tegnet med
  // nord opp; `icon-rotate` snur det til kjøreretningen.
  const hjorner = [
    [bredde / 2, PIL.marg],
    [bredde - PIL.marg, PIL.marg + PIL.trekant],
    [PIL.marg, PIL.marg + PIL.trekant],
  ];

  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      // Midt i pikselen, ikke i hjørnet — ellers blir trekanten en halv
      // piksel forskjøvet opp og til venstre.
      const avstand = _fortegnetAvstand(
        (x + 0.5) / PIL.ratio, (y + 0.5) / PIL.ratio, hjorner
      );
      const i = (y * w + x) * 4;
      data[i] = 255;
      data[i + 1] = 255;
      data[i + 2] = 255;
      data[i + 3] = Math.max(
        0, Math.min(255, Math.round(128 - avstand * (128 / PIL.spredning)))
      );
    }
  }

  return { width: w, height: h, data };
}

/* --- Klyngedannelse -------------------------------------------------------
 * Over dette zoomnivået er hvert tog sin egen prikk. Under slås tette grupper
 * sammen. 7 er valgt fordi linjeetikettene (`trains-label`) kommer på 7,5 —
 * da er alt individuelt i det øyeblikket etikettene begynner å bety noe. */
const KLYNGE_MAKS_ZOOM = 7;

/* MapLibre legger `point_count` bare på klynger. Disse to filtrene skiller
 * dem fra enkelttog, og MÅ stå på hvert eneste lag som leser «trains» —
 * ellers tegner togprikklaget en prikk oppå klyngen med klyngens (tomme)
 * egenskaper, og etikettlaget prøver å skrive en linjekode som ikke finnes. */
const ER_KLYNGE = ["has", "point_count"];
const ALENE = ["!", ["has", "point_count"]];

/* Grunnfilteret for linjeetikettene: tog uten linjekode skal ikke få en tom
 * etikett. Stod tidligere nede ved søket, som er det eneste stedet det
 * settes på nytt - men laget opprettes lenge før, så det måtte hit opp. */
const LABEL_BASE = ["!=", ["get", "line"], ""];

/* Fargen på en klynge: andelen MÅLTE tog i den som ikke er i rute.
 *
 * Skalaen er den samme fire fargene som prikkene bruker, men aksen er en
 * annen — andel i stedet for sekunder. En klynge der ingen er forsinket er
 * grønn, en der halvparten er det er oransje. Terskelen på 0,6 til rødt er
 * satt der fordi en klynge med over 60 % avvik ikke lenger er «litt trøbbel»,
 * det er en strekning som har brutt sammen.
 *
 * «Målte» er ordet som betyr noe her. Fram til 23. august delte uttrykket på
 * `point_count`, altså på ALLE togene i klyngen — spøkelsene med. Det er
 * nøyaktig den feilen ringdiagrammet hadde 22. august: teller og nevner kom
 * fra hver sin populasjon. Et spøkelsestog har et avvik som vokser mot en
 * rutetid toget aldri innfrir, og resten av appen nekter å telle det. Da kan
 * ikke klyngefargen gjøre det heller.
 *
 * Er alt i klyngen spøkelser, finnes det ingen andel å vise. Grå er fargen
 * kartet ellers bruker til «vi vet ikke» — den samme `--c-unknown` som prikker
 * uten forsinkelsesdata får. Uten dette hadde en klynge med fem tapte tog og
 * null målte blitt grønn: null avvik av null, altså «alt i rute». */
function klyngeFarge() {
  const malte = ["-", ["get", "point_count"], ["get", "spokelser"]];
  return [
    "case",
    ["<=", malte, 0], token("--c-unknown"),
    [
      "interpolate", ["linear"],
      ["/", ["get", "avvik"], ["max", malte, 1]],
      0, token("--c-low"),
      0.2, token("--c-mid"),
      0.4, token("--c-high"),
      0.6, token("--c-vhigh"),
    ],
  ];
}

/* Farge på den valgte ruta (ruteopptegning ved klikk). Egen variabel, fordi
 * den må skille seg fra to ting på én gang: cyan-sporet (--rail) og
 * punktlighetsgradienten på prikkene. Hvitt gjør begge deler og leser som
 * «dette er markeringen», ikke som enda en datafarge. Legg gjerne --rute i
 * theme.css for å styre den derfra; uten den brukes en nesten hvit tone. */
const RUTE_FARGE = rootStyle.getPropertyValue("--rute").trim() || "#f2f6ff";

/* --- Jernbanenettet -------------------------------------------------------
 * Sporene ligger allerede i bakgrunnskartets vektorfliser — de er bare tegnet
 * nesten usynlig. Vi henter ingen nye data; vi legger på et eget lag som
 * filtrerer ut jernbane og gir den farge.
 *
 * Kilde- og lagnavn følger OpenMapTiles-skjemaet. Skulle bakgrunnskartet bruke
 * noe annet, sier console-utskriften under fra. */

/* Bakgrunnskartet er tegnet for et kart der veiene ER innholdet. Her er de
 * bakgrunn, og togene er innholdet. Men «bakgrunn» er ikke én ting, og det
 * var feilen i den første utgaven: den dempet ALT som traff ett regexp — både
 * motorveier og landegrenser — til samme 0,22.
 *
 * Landegrensa mot Sverige er ikke støy. Den er den viktigste geografiske
 * referansen på et kart over Norge, og på zoom 4 er den nesten det eneste som
 * sier hvor du er. Den ble like svak som en tunnelstrek i Bergen.
 *
 * Derfor tre grupper med hver sin behandling, og ikke én:
 *
 *   STØY      veier, tunneler, broer. Dempes hardt — de har ingenting å
 *             fortelle om tog, og de er mange.
 *   GEOGRAFI  lande-, fylkes- og kommunegrenser. LØFTES i stedet: de gir
 *             gratis kontekst og konkurrerer ikke med togfargene, siden de
 *             er grå streker og ikke fargede prikker.
 *   STEDSNAVN byer og tettsteder. Bakgrunnsstilen gir dem en mørk glorie på
 *             #222 mot en bakgrunn på #0d1117 — nesten ingen kontrast. De
 *             løftes med sterkere glorie og lysere tekst.
 *
 * Tallene under er de eneste stedene å skru. Ingenting annet henger på dem. */

const BAKGRUNN = {
  stoy: /road|highway|bridge|tunnel|street|motorway|roadname/i,
  geografi: /boundary|admin/i,
  stedsnavn: /^place_/i,
};

function justerBakgrunn() {
  for (const layer of map.getStyle().layers) {
    const id = layer.id;
    try {
      if (BAKGRUNN.geografi.test(id)) {
        // Grensene er grå streker uten metning. De kan tåle mye mer enn
        // veiene uten å ta oppmerksomhet fra en farget prikk.
        if (layer.type === "line") map.setPaintProperty(id, "line-opacity", 0.5);
        else if (layer.type === "symbol") map.setPaintProperty(id, "text-opacity", 0.6);
      } else if (BAKGRUNN.stoy.test(id)) {
        // Litt hardere enn før (0,22 → 0,16). Veiene ble mindre nødvendige da
        // grensene og stedsnavnene begynte å bære geografien i stedet.
        if (layer.type === "line") map.setPaintProperty(id, "line-opacity", 0.16);
        else if (layer.type === "symbol") map.setPaintProperty(id, "text-opacity", 0.4);
      } else if (BAKGRUNN.stedsnavn.test(id) && layer.type === "symbol") {
        // Glorien gjør mer for lesbarheten enn tekstfargen: den skiller
        // bokstavene fra det som ligger under, og under her ligger både
        // cyan spor og fargede prikker.
        map.setPaintProperty(id, "text-opacity", 0.92);
        map.setPaintProperty(id, "text-halo-color", "rgba(5, 8, 12, 0.9)");
        map.setPaintProperty(id, "text-halo-width", 1.6);
      }
    } catch (error) {
      // Laget hadde ikke den egenskapen. Ikke noe problem — gå videre.
    }
  }
}

function addRailNetwork() {
  const style = map.getStyle();

  // Samme tankegang som __type-spørringen i diagnose.py: spør hva som finnes
  // i stedet for å gjette. Åpne konsollen (F12) for å se resultatet.
  console.log("Kilder i bakgrunnskartet:", Object.keys(style.sources));
  console.log(
    "Eksisterende jernbanelag:",
    style.layers.filter((l) => /rail|transit/i.test(l.id)).map((l) => l.id)
  );

  const vectorSource = Object.keys(style.sources).find(
    (name) => style.sources[name].type === "vector"
  );

  if (!vectorSource) {
    console.warn("Fant ingen vektorkilde — hopper over jernbanenettet.");
    return;
  }

  const railFilter = ["==", ["get", "class"], "rail"];

  // To lag: en bred, nesten gjennomsiktig glød under en tynn, skarp strek.
  // Det er det som gjør at en 1-pikselslinje leses som lysende cyan i stedet
  // for som en hard tråd.
  map.addLayer({
    id: "rail-glow",
    type: "line",
    source: vectorSource,
    "source-layer": "transportation",
    // Under zoom 8 inneholder flisene uansett ingen jernbane. Der overtar
    // hovedbanelaget under, som vi eier selv.
    minzoom: 8,
    filter: railFilter,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": token("--rail"),
      "line-width": ["interpolate", ["linear"], ["zoom"], 4, 2, 10, 6, 14, 11],
      // Svakere glød når man er langt ute, ellers klumper nettet seg sammen.
      "line-opacity": ["interpolate", ["linear"], ["zoom"], 4, 0.04, 8, 0.07],
      "line-blur": 3,
    },
  });

  map.addLayer({
    id: "rail-network",
    type: "line",
    source: vectorSource,
    "source-layer": "transportation",
    // Under zoom 8 inneholder flisene uansett ingen jernbane. Der overtar
    // hovedbanelaget under, som vi eier selv.
    minzoom: 8,
    filter: railFilter,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": token("--rail"),
      // Bredere og sterkere langt ute enn før. En linje på 0,7 piksler ved
      // halv opasitet forsvinner rett og slett i skjermens pikselraster.
      "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1.1, 7, 1.4, 14, 2.4],
      /* Dempet fra 0,95/0,75 til 0,55/0,42 den 21. august. Cyan er den eneste
       * mettede fargen på kartet utenom togene, og på zoom 10+ er sporet
       * overalt der togene er - det er jo derfor de er der. Full styrke gjorde
       * at nettet konkurrerte med prikkene om den samme oppmerksomheten, og
       * sporet vinner på ren flate.
       *
       * Nedre grense er ikke smak: under rundt 0,35 forsvinner en linje på
       * 1,1 px i pikselrasteret, og da er laget borte i praksis. Se den
       * gamle kommentaren over - den feilen er gjort før. */
      "line-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.55, 9, 0.42],
    },
  });

  // Måler: hvor mange jernbanesegmenter finnes ved gjeldende zoom?
  // Zoom ut ett hakk om gangen og les tallet i konsollen. Går det til 0,
  // er det flisene som ikke inneholder jernbane lenger - ikke stylingen.
  map.on("zoomend", () => {
    const found = map.querySourceFeatures(vectorSource, {
      sourceLayer: "transportation",
      filter: railFilter,
    }).length;
    console.log(
      `zoom ${map.getZoom().toFixed(1)} — jernbanesegmenter i visningen: ${found}`
    );
  });
}

/* --- Hovedbanene ----------------------------------------------------------
 * Banene vi eier geometrien til selv: static/hovedbaner.geojson, bygget med
 * lagbaner.py fra OpenStreetMap, med Entur som reserve.
 *
 * Dette laget finnes fordi bakgrunnskartet ikke kan gjøre jobben. Flisene har
 * `class` og `subclass`, men ingen banenavn — det finnes ikke noe felt å
 * filtrere «behold Dovrebanen» mot. Og under zoom 8 er de tomme for jernbane
 * uansett, så ingen styling kunne vist noe der.
 *
 * Terskelen er valgt: hele Norge på en skjerm 1440 piksler høy tilsvarer
 * zoom 6,01. Laget har derfor ingen minzoom — kartet åpner på 4,1, og der
 * skal banene være.
 *
 * Geometrien er MultiLineString. OSM gir en bane som hundrevis av biter, og
 * MapLibre tegner dem som ett objekt uansett. */

function addMainLines() {
  map.addSource("hovedbaner", { type: "geojson", data: "hovedbaner.geojson" });

  /* Laget skifter rolle med zoomen, og det er hele poenget.
   *
   * Under zoom 8 har bakgrunnskartets fliser ingen jernbane. Der ER
   * hovedbanene kartet, og de tegnes som en tynn, skarp strek.
   *
   * Fra zoom 8 kommer `rail-network` inn med uforenklet sporgeometri fra
   * flisene. Da tegner vi to linjer på samme spor, i samme farge — og de er
   * ikke like. RDP-toleransen i lagbaner.py er 150 meter, som på 59,4°N
   * (Hamar-høyde) blir:
   *
   *     zoom  8  →  0,96 px    usynlig
   *     zoom  9  →  1,9  px    så vidt
   *     zoom 10  →  3,85 px    synlig som dobbel strek
   *     zoom 11  →  7,7  px    åpenbart feil
   *     zoom 12  →  15,4  px
   *
   * Den skarpe streken slutter derfor å påstå at den er sporet, og tones ut
   * før avviket når fire piksler. Gløden blir igjen — bredere og mer uskarp —
   * og merker hvilken korridor sporet tilhører. Det er noe flisene ikke kan:
   * de har `class` og `subclass`, men ikke banenavn.
   *
   * Uskarpheten er ikke pynt. En diffus linje har ingen kant å sammenligne
   * mot, så de 150 meterne leses som bredde i stedet for som feil.
   *
   * Endrer du toleransen i lagbaner.py, er det tabellen over som må regnes om
   * og stoppene under som må flyttes. Ingenting annet henger på tallet. */

  map.addLayer({
    id: "hovedbane-glow",
    type: "line",
    source: "hovedbaner",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": token("--rail"),
      "line-width": [
        "interpolate", ["linear"], ["zoom"],
        4, 3, 8, 7, 10, 12, 12, 18,
      ],
      "line-blur": [
        "interpolate", ["linear"], ["zoom"],
        4, 3, 8, 3, 10, 5, 12, 9,
      ],
      /* Halv bredde pluss uskarphet må dekke avviket, ellers stikker det ekte
       * sporet ut av gløden. Med tallene over: 14,5 px rekkevidde mot 7,7 px
       * avvik ved zoom 11, 18 mot 15,4 ved zoom 12.
       *
       * Opasiteten er lav med vilje, og grunnen er ikke smak. Korridorer som
       * deler spor overlapper bevisst i geometrien, og MapLibre blander dem:
       * N lag med opasitet a gir 1-(1-a)^N. Rundt Oslo S møtes samtlige, og
       * 0,20 ble til 0,67 - en cyan tåke der gløden dessuten sier minst,
       * siden «dette er en hovedkorridor» er sant om alt du ser. 0,12 gir
       * 0,47 på det samme stedet og er nesten umerkelig svakere ute på
       * åpne strekninger, der laget faktisk har noe å fortelle.
       *
       * Blir det fortsatt for varmt i knutepunktene, er dette det ene tallet
       * å skru på. Bredde og uskarphet henger på geometrien, ikke på øyet. */
      "line-opacity": [
        "interpolate", ["linear"], ["zoom"],
        4, 0.1, 8, 0.12, 10, 0.15, 11.5, 0.12, 12.5, 0,
      ],
    },
  });

  map.addLayer({
    id: "hovedbane",
    type: "line",
    source: "hovedbaner",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": token("--rail"),
      "line-width": [
        "interpolate", ["linear"], ["zoom"],
        4, 1.2, 8, 1.6, 10, 1.8,
      ],
      /* Faller raskt gjennom vinduet der avviket ennå er under to piksler,
       * og er borte før det blir fire. Tones den ut langsommere, får du en
       * blek dobbeltstrek i stedet for en dobbeltstrek — ikke en forbedring.
       *
       * Dempet 21. august: dette ER kartet under zoom 8, så det kan ikke
       * dempes like hardt som `rail-network` — men 0,95 gjorde nettet til
       * hovedsaken på oversiktskartet, der det bare skal si «her går det
       * skinner». Utgangspunktet er nå 0,6.
       *
       * Merk at zoomstoppene under 7 mangler med vilje: uten et stopp der
       * holder MapLibre den første verdien konstant nedover, så zoom 4 får
       * samme 0,6. */
      "line-opacity": [
        "interpolate", ["linear"], ["zoom"],
        7, 0.6, 8.5, 0.55, 9.5, 0.2, 10.2, 0,
      ],
    },
  });

  /* Mangler filen, gir MapLibre en 404 i nettverksfanen og et tomt lag - men
   * ingen feilmelding du legger merke til. Denne sier fra i konsollen.
   *
   * Vi teller UNIKE navn, ikke features. MapLibre deler GeoJSON-en i fliser,
   * og en bane som krysser fire fliser ligger der fire ganger - så
   * querySourceFeatures().length varierer med zoom og utsnitt og betyr
   * ingenting. Rå telling ga 17 ved ett nivå og 35 ved et annet, mens
   * antallet baner sto stille på 13 hele tiden.
   *
   * Navnene skrives ut fordi det er den eneste utskriften som avslører at en
   * bane MANGLER. Et tall som er litt for lavt ser ut som et tall. */
  map.on("sourcedata", function once(event) {
    if (event.sourceId !== "hovedbaner" || !event.isSourceLoaded) return;

    const navn = [
      ...new Set(
        map.querySourceFeatures("hovedbaner").map((f) => f.properties.navn)
      ),
    ].sort();

    if (navn.length) {
      console.log(`hovedbaner: ${navn.length} baner lastet —`, navn.join(", "));
    } else {
      console.warn(
        "hovedbaner.geojson er tom eller mangler. Ligger den i static/, " +
        "ved siden av app.js? Bygg den med: python lagbaner.py"
      );
    }
    map.off("sourcedata", once);
  });
}

/* --- Stasjonene -----------------------------------------------------------
 * static/stasjoner.geojson, bygget med lagstasjoner.py fra Enturs
 * stoppestedsregister: samtlige stasjoner der ett av selskapene kartet viser
 * tog for har en avgang. 335 stykker.
 *
 * Hvorfor egne prikker når bakgrunnskartet allerede skriver bynavn: en
 * by-etikett står der rådhuset ligger, en stasjon står der sporet går. På
 * Bergensbanen er det halvannen kilometer mellom de to. Skal du lese hvor et
 * tog er i forhold til stasjonen det kjører mot, må det være stasjonen som er
 * tegnet.
 *
 * Symbolet er ringen fra togkart.banenor.no, oversatt til mørkt tema. Deres
 * standardsymbol er en fylt skive i sporfargen med et hull i kartbakgrunnens
 * farge — målt i docs/bilder/ikon_stasjon.png: 13 piksler ytre diameter, 5 pikslers hull,
 * altså en ring omtrent halvannen gang så tykk som hullet er bredt.
 *
 * Oversettelsen er derfor ikke «blå ring» men «ring i sporfargen, hull i
 * bakgrunnsfargen»: --rail utenpå, --bg inni. Bytter du tema, følger
 * stasjonene med av seg selv. Hadde hullet vært hardkodet hvitt, ville
 * stasjonene lyst som hull i kartet i stedet for å ligge på sporet. */

/* Zoomnivåene stasjonslagene slår inn på. ALT som styrer når en stasjon
 * dukker opp står her — det er ett sted å skru, ikke fire lag å lete gjennom.
 *
 * Delingen går på `viktighet` i stasjoner.geojson: 1 er de fem
 * landsdelshovedstedene, 2 er de andre 330. Uten den delingen måtte man velge
 * mellom en cyan tåke på zoom 4 og et kart uten Oslo S.
 *
 *   alle      ← DENNE er den å justere. Blir kartet for tett, hev den til 13
 *               eller 14; vil du se stasjonene tidligere, senk den til 10.
 *   alleNavn  navnene kommer med prikkene. Settes den lavere enn `alle`,
 *               står etiketter uten prikk under seg.
 *
 * MapLibre skjuler selv etiketter som kolliderer, så tettheten i Oslo ordner
 * seg uten at vi teller piksler. */
const STASJON_ZOOM = {
  viktige: 4,
  viktigeNavn: 7,
  alle: 12,
  alleNavn: 12,
};

/* Ringen. Ett lag, ikke to stablede sirkler: `circle-color` er hullet,
 * `circle-stroke-color` er godset rundt, og til sammen ER det ringen. To
 * sirkler ville gitt samme bilde og to sett tall å holde i takt.
 *
 * Forholdet ring:hull ligger rundt 1,3 gjennom hele zoomområdet, litt tynnere
 * enn Bane NORs 1,6, fordi de tegner på lyst kart der en tynn ring har mer å
 * bryte mot. Ytre diameter går fra 7 piksler ved zoom 4 til 17 ved zoom 16.
 *
 * Rampen går til 16 og ikke til 12, fordi flertallet av stasjonene først
 * finnes fra zoom 12. Stoppet den der, ville de 330 stått med sin minste
 * størrelse gjennom hele det zoomområdet der de er det eneste man ser på. */
const STASJON_MALING = {
  "circle-color": token("--bg"),
  "circle-radius": [
    "interpolate", ["linear"], ["zoom"],
    4, 1.5, 8, 2.2, 12, 3.4, 16, 4.6,
  ],
  "circle-stroke-color": token("--rail"),
  "circle-stroke-width": [
    "interpolate", ["linear"], ["zoom"],
    4, 2, 8, 2.8, 12, 4.2, 16, 5.4,
  ],
};

/* Navnet i sporfargen, ikke i --text. Det skiller stasjonsetiketter fra
 * toglinjene (L1, R11, F4) med én gang: cyan er infrastruktur, hvitt er noe
 * som beveger seg. Samme logikk som at --rail aldri får en verdi fra
 * punktlighetsgradienten. */
const STASJON_TEKST = {
  layout: {
    "text-field": ["get", "navn"],
    "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
    "text-size": 11,
    "text-offset": [0, 1.1],
    "text-anchor": "top",
  },
  paint: {
    "text-color": token("--rail"),
    "text-halo-color": "rgba(0,0,0,0.85)",
    "text-halo-width": 1.4,
  },
};

// Oversiktsstasjonene mot resten. Filtrene er hverandres motsetning, så ingen
// stasjon tegnes to ganger — det ville gitt dobbelt strek på de fem og en
// etikett oppå seg selv.
const ER_VIKTIG = ["==", ["get", "viktighet"], 1];
const ER_VANLIG = ["!=", ["get", "viktighet"], 1];

function addStations() {
  map.addSource("stasjoner", { type: "geojson", data: "stasjoner.geojson" });

  map.addLayer({
    id: "stasjon",
    type: "circle",
    source: "stasjoner",
    minzoom: STASJON_ZOOM.alle,
    filter: ER_VANLIG,
    paint: STASJON_MALING,
  });

  map.addLayer({
    id: "stasjon-viktig",
    type: "circle",
    source: "stasjoner",
    minzoom: STASJON_ZOOM.viktige,
    filter: ER_VIKTIG,
    paint: STASJON_MALING,
  });

  map.addLayer({
    id: "stasjon-navn",
    type: "symbol",
    source: "stasjoner",
    minzoom: STASJON_ZOOM.alleNavn,
    filter: ER_VANLIG,
    ...STASJON_TEKST,
  });

  map.addLayer({
    id: "stasjon-navn-viktig",
    type: "symbol",
    source: "stasjoner",
    minzoom: STASJON_ZOOM.viktigeNavn,
    filter: ER_VIKTIG,
    // Oversiktsstasjonene skal vinne kollisjonen mot de andre etikettene.
    // Uten dette kan «Oslo S» bli borte for «Grorud stasjon» i Oslo-klyngen,
    // og da er det tilfeldig hvilket navn som overlever.
    layout: { ...STASJON_TEKST.layout, "symbol-sort-key": 0 },
    paint: STASJON_TEKST.paint,
  });

  /* Mangler filen, gir MapLibre en 404 i nettverksfanen og et tomt lag, uten
   * noen feilmelding du legger merke til. Samme vakt som på hovedbanene, og
   * den finnes av samme grunn: et lag som ikke er der, ser ut som et lag uten
   * data.
   *
   * Med fem stasjoner ble alle navnene skrevet ut. Med 335 er en liste over
   * dem støy — nå skrives antallet, delingen som styrer zoom, og de fem
   * oversiktsstasjonene ved navn. Faller en av dem ut, er det den feilen som
   * gjør kartet tomt på zoom 4, og da vil du se det i konsollen. */
  map.on("sourcedata", function once(event) {
    if (event.sourceId !== "stasjoner" || !event.isSourceLoaded) return;

    const alle = map.querySourceFeatures("stasjoner");
    if (!alle.length) {
      console.warn(
        "stasjoner.geojson er tom eller mangler. Ligger den i static/, " +
        "ved siden av app.js? Bygg den med: python lagstasjoner.py"
      );
      map.off("sourcedata", once);
      return;
    }

    const unike = new Map(alle.map((f) => [f.properties.id, f.properties]));
    const viktige = [...unike.values()]
      .filter((p) => p.viktighet === 1)
      .map((p) => p.navn)
      .sort();

    console.log(
      `stasjoner: ${unike.size} lastet — ${viktige.length} fra zoom ` +
      `${STASJON_ZOOM.viktige} (${viktige.join(", ")}), resten fra zoom ` +
      `${STASJON_ZOOM.alle}`
    );

    if (viktige.length === 0) {
      console.warn(
        "Ingen stasjoner med viktighet 1 — kartet står uten stasjoner til " +
        `zoom ${STASJON_ZOOM.alle}. Kjør lagstasjoner.py på nytt.`
      );
    }
    map.off("sourcedata", once);
  });
}

/* --- Kart ----------------------------------------------------------------- */

const map = new maplibregl.Map({
  container: "map",
  style: CONFIG.mapStyle,
  center: CONFIG.center,
  zoom: CONFIG.zoom,
  attributionControl: { compact: true },
});

map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

map.on("load", () => {
  // Rekkefølgen er tegnerekkefølgen: bakgrunn, så banene, så togprikkene.
  //
  // addMainLines() FØR addRailNetwork(): korridorgløden skal ligge under det
  // detaljerte sporet, ikke over det. Bytter du om, tegnes den forenklede
  // geometrien oppå den nøyaktige, og gløden dekker sporet den skal merke.
  //
  // addStations() SIST av de tre: stasjonene er punkter på sporet og skal
  // ligge oppå begge sporlagene. Legges de før, forsvinner ringen under
  // rail-network der sporet er bredest.
  justerBakgrunn();
  addMainLines();
  addRailNetwork();

  /* Varmekartet ligger MELLOM sporet og stasjonene. Over sporet fordi det er
   * strekningene det beskriver, og de skal kunne leses; under stasjonene
   * fordi strekene er tjue piksler brede på full zoom og ville begravd hver
   * eneste stasjonsprikk på strekningen de gjelder. Laget starter skjult og
   * slås på med bryteren i #info. */
  leggTilFlaskehalslag();

  addStations();

  /* Valgt rute: traseen til toget man klikker på. Kilde og lag legges til HER -
   * etter jernbanenettet, før togprikkene - så ruta havner i lagstabelen mellom
   * sporet og togene: over skinnene, under prikkene.
   *
   * At kilden er sin egen er hele grunnen til at ruta overlever oppdateringen:
   * refresh() hvert 15. sekund kaller bare setData() på "trains". "valgt-rute"
   * røres ikke, så streken blir stående til man klikker et annet tog eller på
   * tom kartflate. */
  map.addSource("valgt-rute", { type: "geojson", data: EMPTY });

  // Glød + skarp strek, samme visuelle språk som spor- og korridorlagene.
  map.addLayer({
    id: "valgt-rute-glod",
    type: "line",
    source: "valgt-rute",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": RUTE_FARGE,
      "line-width": ["interpolate", ["linear"], ["zoom"], 4, 4, 10, 10, 14, 16],
      "line-opacity": 0.22,
      "line-blur": 4,
    },
  });
  map.addLayer({
    id: "valgt-rute-strek",
    type: "line",
    source: "valgt-rute",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": RUTE_FARGE,
      "line-width": ["interpolate", ["linear"], ["zoom"], 4, 1.5, 10, 3, 14, 4.5],
      "line-opacity": 0.9,
    },
  });

  /* Avviksringene legges her, mellom ruta og togprikkene: de skal ligge over
   * sporet og under togene. Et tog som står midt i en stengt strekning skal
   * ikke gjemmes av ringen som forklarer hvorfor det står. */
  leggTilAvvikslag();

  /* Klyngedannelse. Under KLYNGE_MAKS_ZOOM slås tette grupper sammen til én
   * prikk med et tall i; over den er hvert tog sitt eget punkt, nøyaktig som
   * før.
   *
   * `clusterProperties` er det som gjør klyngen til mer enn en teller.
   * MapLibre kan akkumulere et uttrykk over punktene i klyngen, så vi teller
   * hvor mange av dem som IKKE er i rute. Fargen kommer av den andelen.
   *
   * Hvorfor andel og ikke verste tog: på zoom 4 har en klynge over Oslo tjue
   * tog, og det er nesten alltid ett av dem som er kraftig forsinket. Farget
   * etter verstemann ville hver eneste klynge i landet vært rød hele tida -
   * maksimal alarm, null informasjon. Andelen skiller derimot en normal
   * ettermiddag fra en der halve Østlandet står. */
  map.addSource("trains", {
    type: "geojson",
    data: EMPTY,
    cluster: true,
    clusterMaxZoom: KLYNGE_MAKS_ZOOM,
    clusterRadius: 42,
    /* Formen her er [operator, uttrykk] - TO ledd, ikke tre. MapLibre leser
     * `const [operator, mapExpression] = clusterProperties[key]` og bygger
     * selv reduksjonen `[operator, ["accumulated"], ["get", key]]`.
     *
     * Skriver man den lange formen selv - `["+", ["accumulated"], <uttrykk>]`
     * - blir `["accumulated"]` tolket som kartleggingsuttrykket, og
     * `<uttrykk>` faller på gulvet. Da teller hver klynge null avvik og alt
     * blir grønt, uten at noe feiler. */
    clusterProperties: {
      // Spøkelsestog holdes utenfor telleren, ikke bare utenfor nevneren.
      // Båndet deres er regnet av en `delay` som bare vokser: toget sluttet å
      // sende, rutetida gikk videre, og differansen er ikke en forsinkelse -
      // den er et mål på hvor lenge vi har vært uten kontakt.
      avvik: ["+", ["case",
        ["get", "stale"], 0,
        ["match", ["get", "band"], ["grense", "forsinket", "mye"], 1, 0],
      ]],
      // ... og telles for seg, så klyngen kan si fra om dem. En klynge på 20
      // ser ellers ut som 20 målte tog.
      spokelser: ["+", ["case", ["get", "stale"], 1, 0]],
    },
  });

  /* Klyngen: én prikk, farget etter andelen tog som ikke er i rute, med
   * antallet skrevet oppå. Legges FØR de individuelle togene, slik at en
   * enslig prikk som ligger inntil en klynge tegnes over den. */
  map.addLayer({
    id: "trains-cluster",
    type: "circle",
    source: "trains",
    filter: ER_KLYNGE,
    paint: {
      "circle-color": klyngeFarge(),
      // Vokser med antallet, men langsomt: kvadratrot ville vært mer korrekt
      // arealmessig, og `step` er mer forutsigbart å lese av. Tre trinn er
      // nok til å se forskjell på fem og femti uten at Oslo dekker Østlandet.
      "circle-radius": [
        "step", ["get", "point_count"],
        13, 10, 17, 25, 22,
      ],
      "circle-opacity": 0.9,
      "circle-stroke-width": 1.5,
      "circle-stroke-color": "rgba(0,0,0,0.55)",
    },
  });

  map.addLayer({
    id: "trains-cluster-count",
    type: "symbol",
    source: "trains",
    filter: ER_KLYNGE,
    layout: {
      "text-field": ["get", "point_count_abbreviated"],
      "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
      "text-size": ["step", ["get", "point_count"], 12, 10, 13, 25, 15],
      "text-allow-overlap": true,
    },
    paint: {
      // Mørk tekst på den lyse prikken. Hvit forsvant i det gule båndet.
      "text-color": "rgba(8, 11, 16, 0.92)",
    },
  });

  /* Spøkelsene i klyngen, som et grått tall på skulderen av den.
   *
   * Tallet inne i klyngen er og blir `point_count`: det er så mange prikker
   * klyngen står i stedet for, og zoomer du inn skal du finne like mange. Men
   * noen av dem sluttet å sende for en halvtime siden, og det sto ingen steder
   * - `spokelser` ble regnet ut og aldri vist. En klynge på 20 så ut som 20
   * tog som rapporterer nå.
   *
   * Hvorfor et eget tall og ikke demping av hele klyngen: en enslig prikk som
   * er spøkelse tegnes med opacity 0,3, og det er den etablerte måten å si det
   * på. Men 3 av 20 er en dempning på under ti prosent - usynlig, og nettopp
   * det tilfellet saken handlet om. Et tall er lesbart uansett hvor liten
   * andelen er.
   *
   * Grå fordi grå betyr «vi vet ikke» overalt ellers på dette kartet: det er
   * `--c-unknown`, samme farge som en prikk uten forsinkelsesdata og samme
   * farge klyngen selv får når den ikke har et eneste målt tog igjen.
   *
   * allow-overlap og ignore-placement: tallet hører til klyngen sin, og et
   * merke som forsvinner fordi det er trangt, ville vært verre enn ingenting -
   * da leser man 20 som 20 igjen, bare av og til. */
  map.addLayer({
    id: "trains-cluster-spokelser",
    type: "symbol",
    source: "trains",
    filter: ["all", ER_KLYNGE, [">", ["get", "spokelser"], 0]],
    layout: {
      "text-field": ["to-string", ["get", "spokelser"]],
      "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
      "text-size": ["step", ["get", "point_count"], 10, 10, 11, 25, 12],
      /* Ut av prikken, oppe til høyre. Forskyvningen er i em og ikke piksler,
       * så den må følge både radius- og tekststørrelsestrinnene for å havne
       * like langt utenfor kanten på alle tre. Radiene er 13/17/22 px.
       *
       * `["literal", [x, y]]` og ikke bare `[x, y]`: et par tall inne i et
       * uttrykk leses som et uttrykk, og `1.4` er ikke navnet på noen
       * operator. MapLibre KASTER IKKE på det - addLayer sender en
       * error-hendelse og lar laget ligge. Uten literal ble hele merkelappen
       * borte uten at noe feilet, akkurat som den lange formen av
       * `clusterProperties` rett over. */
      "text-offset": [
        "step", ["get", "point_count"],
        ["literal", [1.4, -1.4]], 10,
        ["literal", [1.5, -1.5]], 25,
        ["literal", [1.7, -1.7]],
      ],
      "text-allow-overlap": true,
      "text-ignore-placement": true,
    },
    paint: {
      "text-color": token("--c-unknown"),
      // Glorie, ikke fordi det er pent: det grå tallet ligger utenfor prikken
      // og lander like gjerne oppå en cyan sporlinje som på tom sjø.
      "text-halo-color": "rgba(8, 11, 16, 0.85)",
      "text-halo-width": 1.4,
    },
  });

  // Klikk på en klynge zoomer inn til den løses opp. Det er den forventede
  // oppførselen, og den sparer oss for en popup som uansett ikke kunne sagt
  // noe mer presist enn tallet som allerede står der.
  map.on("click", "trains-cluster", (event) => {
    const klynge = event.features[0];
    map.getSource("trains").getClusterExpansionZoom(
      klynge.properties.cluster_id,
      (feil, zoom) => {
        if (feil) return;
        map.easeTo({
          center: klynge.geometry.coordinates,
          zoom: Math.min(zoom + 0.4, KLYNGE_MAKS_ZOOM + 1.5),
        });
      }
    );
  });
  map.on("mouseenter", "trains-cluster", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "trains-cluster", () => (map.getCanvas().style.cursor = ""));

  // Glød under prikken, så enkelttog er synlige når man er zoomet ut.
  map.addLayer({
    id: "trains-glow",
    type: "circle",
    source: "trains",
    filter: ALENE,
    paint: {
      "circle-color": colorByDelay(),
      // Spøkelsestog får nesten ingen glød - de skal gli inn i bakgrunnen.
      "circle-opacity": ["case", ["get", "stale"], 0.04, 0.15],
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 9, 10, 22],
    },
  });

  map.addLayer({
    id: "trains-dot",
    type: "circle",
    source: "trains",
    filter: ALENE,
    layout: {
      /* Tegnerekkefølgen. MapLibre tegner lav nøkkel først, så høy nøkkel
       * havner ØVERST - og det er de forsinkede togene som skal ligge der.
       *
       * Rundt Oslo S ligger det til enhver tid ti-tjue prikker delvis oppå
       * hverandre. Uten en nøkkel avgjør rekkefølgen i GeoJSON-arrayet hvem
       * som vinner, og den er tilfeldig: den følger hvilken rekkefølge Entur
       * nevnte kjøretøyene i. Et tog som er tjue minutter forsinket kunne
       * altså bli borte under et som var i rute, og avviket - det ene man
       * faktisk skal se - forsvant.
       *
       * Båndene og ikke `delay` direkte: `delay` kan være null, og null i en
       * sorteringsnøkkel gir udefinert rekkefølge. Spøkelsestog havner
       * nederst; de er informasjon om datakvalitet, ikke om punktlighet. */
      "circle-sort-key": [
        "case",
        ["get", "stale"], -1,
        ["match", ["get", "band"],
          "ukjent", 0,
          "i_rute", 1,
          "grense", 2,
          "forsinket", 3,
          "mye", 4,
          0],
      ],
    },
    paint: {
      "circle-color": colorByDelay(),
      /* Større enn før (3,2 → 4,4 på zoom 4). På oversiktskartet var en
       * prikk på drøye tre piksler for liten til at fargen kunne leses -
       * og fargen ER hele budskapet. Tre piksler holder til å se AT det er
       * et tog, ikke til å se om det er grønt eller gult. */
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 4.4, 10, 9],
      // Beregnede SJ-tog tegnes som vanlige prikker, likt med de målte.
      // Skillet lever nå bare i popupen og i panelteksten - grå farge
      // markerer fortsatt tog uten sanntidsdata.
      "circle-opacity": ["case", ["get", "stale"], 0.3, 1],
      "circle-stroke-width": 1,
      "circle-stroke-color": "rgba(0,0,0,0.6)",
    },
  });

  /* Kjøreretningen. Over prikken, under etiketten.
   *
   * `filter: ["has", "heading"]` er det som gjør pila ærlig: bare tog vi
   * faktisk kjenner retningen på får en. Vy og Go-Ahead publiserer ingen
   * `bearing`, så deres piler kommer av bevegelse mellom to hentinger, og de
   * dukker derfor opp ti sekunder etter at siden er lastet — ikke med en
   * gang. Et tog som nettopp har kommet inn i feeden står uten pil til det
   * har flyttet seg 25 meter. Det er meningen: en pil som gjetter er verre
   * enn ingen pil.
   *
   * minzoom 6: på oversiktskartet er prikken tre piksler bred, og en trekant
   * på fire piksler ved siden av den er støy og ikke informasjon. */
  map.addImage(PIL.navn, lagPilbilde(), { pixelRatio: PIL.ratio, sdf: true });

  map.addLayer({
    id: "trains-arrow",
    type: "symbol",
    source: "trains",
    minzoom: 6,
    filter: ["all", ALENE, ["has", "heading"]],
    layout: {
      "icon-image": PIL.navn,
      "icon-rotate": ["get", "heading"],
      // Grader fra kartets nord, ikke fra skjermens topp. Roterer man kartet,
      // følger pila sporet i stedet for å bli stående og peke mot skjermkanten.
      "icon-rotation-alignment": "map",
      "icon-size": ["interpolate", ["linear"], ["zoom"], 6, 0.4, 10, 1],
      // Pila skal alltid tegnes, og aldri dytte vekk en linjeetikett. Uten
      // begge to forsvinner den så snart det er trangt.
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
    },
    paint: {
      // Nøyaktig samme uttrykk som prikken. Se punkt 1 i kommentaren over PIL.
      "icon-color": colorByDelay(),
      "icon-opacity": ["case", ["get", "stale"], 0.3, 1],
    },
  });

  /* Søketreffene. Egen kilde uten klyngedannelse, slik at et treff er synlig
   * på alle zoomnivå - se applyTrainFilter. Tom det aller meste av tida.
   *
   * Samme farger og former som de vanlige togprikkene, men uten dempingen av
   * spøkelsestog: har du søkt fram et bestemt tog, skal du se det selv om det
   * har mistet posisjonen sin. */
  map.addSource("sokte-tog", { type: "geojson", data: EMPTY });

  map.addLayer({
    id: "sokte-tog-glod",
    type: "circle",
    source: "sokte-tog",
    paint: {
      "circle-color": colorByDelay(),
      "circle-opacity": 0.18,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 11, 10, 24],
    },
  });

  map.addLayer({
    id: "sokte-tog-dot",
    type: "circle",
    source: "sokte-tog",
    paint: {
      "circle-color": colorByDelay(),
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 5, 10, 9.5],
      "circle-stroke-width": 1.5,
      "circle-stroke-color": "rgba(255,255,255,0.75)",
    },
  });

  map.on("click", "sokte-tog-dot", showPopup);
  map.on("mouseenter", "sokte-tog-dot", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "sokte-tog-dot", () => (map.getCanvas().style.cursor = ""));

  // Linjekoden (L1, R11, F4) er språket passasjerene bruker.
  map.addLayer({
    id: "trains-label",
    type: "symbol",
    source: "trains",
    minzoom: 7.5,
    filter: ["all", ALENE, LABEL_BASE],
    layout: {
      "text-field": ["get", "line"],
      "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
      "text-size": 11,
      "text-offset": [0, 1.2],
      "text-anchor": "top",
    },
    paint: {
      "text-color": token("--text"),
      "text-halo-color": "rgba(0,0,0,0.85)",
      "text-halo-width": 1.4,
    },
  });

  map.on("click", "trains-dot", showPopup);
  map.on("mouseenter", "trains-dot", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "trains-dot", () => (map.getCanvas().style.cursor = ""));

  // Klikk på tom kartflate fjerner ruta. Denne generelle handleren fyrer på
  // HVERT klikk, også på prikkene - så vi sjekker først om klikket traff et
  // tog. Gjorde det det, har showPopup allerede tatt seg av ruta; ellers er det
  // et klikk på «ingenting», og vi tømmer.
  map.on("click", (event) => {
    // Alle lagene som representerer tog, ikke bare det ene. Klikker man på en
    // klynge eller et søketreff, er det ikke et klikk på «ingenting», og ruta
    // skal ikke forsvinne under hendene på en.
    const lag = ["trains-dot", "sokte-tog-dot", "trains-cluster"].filter(
      (id) => map.getLayer(id)
    );
    if (map.queryRenderedFeatures(event.point, { layers: lag }).length === 0) {
      tommRute();
      // Samme klikk rydder avviksmarkeringen. Den er også noe man har valgt,
      // og et klikk på ingenting betyr «vis meg kartet, ikke det jeg pekte på».
      tommAvvik();
    }
  });

  buildBreakdown();
  start();
});

/* --- Tid ------------------------------------------------------------------
 * Entur og backend sender UTC ("...10:13:30.552Z"). Det er riktig: lagre og
 * send alltid UTC, konverter først når det vises. Konverteringen skjer her.
 *
 * new Date(...) tolker tidssonen i teksten, og getHours() gir timen i
 * nettleserens egen sone. Å klippe tegn ut av ISO-strengen ser ut som
 * formatering, men hopper over tidssonen — det var feilen som ga to timers
 * avvik. */

function clock(iso) {
  const date = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function ago(iso) {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000));
  if (seconds < 60) return `${seconds} s siden`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min siden`;
  return `${Math.round(minutes / 60)} t siden`;
}

/* 299 -> "4:59". Avvik skrives alltid som minutter og sekunder; «5 min»
 * ville skjult forskjellen på 4:01 og 5:59, som er nettopp den forskjellen
 * tersklene handler om. Brukes både av tooltipen på ringdiagrammet og av
 * historikkpanelet. */
function mmss(sekunder) {
  const total = Math.round(Math.abs(sekunder));
  const tekst = `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  return sekunder < 0 ? `−${tekst}` : tekst;
}

/* Fargen et antall sekunder hører til, etter grensene i BANDS. Slår opp i
 * samme tabell som tooltipen og tegnforklaringen leser, så 4:59 ser like gult
 * ut i historikkpanelet som på kartet. */
function bandVar(sekunder) {
  if (sekunder === null || sekunder === undefined) return "--c-unknown";
  const treff = BANDS.find(
    (b) => b.id !== "ukjent" && (b.til === null || sekunder < b.til)
  );
  return (treff || BANDS[BANDS.length - 1]).varName;
}

/* --- Popup ---------------------------------------------------------------- */

function formatDelay(seconds) {
  if (seconds === null) return "Forsinkelse ukjent";
  // Entur leverer delay som flyttall (152.0). Rund av før vi regner, ellers
  // ender vi med "2 min 32.5 s".
  const total = Math.round(seconds);
  if (total <= 0) {
    const early = Math.abs(total);
    return early < 60 ? "I rute" : `${Math.round(early / 60)} min før rute`;
  }
  if (total < 60) return "I rute";
  const minutes = Math.floor(total / 60);
  return `${minutes} min ${String(total % 60).padStart(2, "0")} s forsinket`;
}

/* Kompassgrader som ord. Åtte sektorer à 45 grader, der «nord» dekker fra 337,5
 * til 22,5 — derfor halvsteget før divisjonen. Ordet og ikke tallet: «kjører mot
 * nordøst» er noe man kan sammenligne med kartet, «kjører mot 47°» er det ikke. */
const KOMPASS = ["nord", "nordøst", "øst", "sørøst", "sør", "sørvest", "vest", "nordvest"];

function retningsnavn(grader) {
  return KOMPASS[Math.round((((grader % 360) + 360) % 360) / 45) % 8];
}

function showPopup(event) {
  const feature = event.features[0];
  const p = feature.properties;
  const delay = p.delay === "" || p.delay === null || p.delay === undefined
    ? null
    : Number(p.delay);

  const rows = [];

  // properties er strenger etter GeoJSON-serialisering, så objektet må
  // tolkes tilbake.
  let stock = null;
  try {
    stock = typeof p.stock === "string" ? JSON.parse(p.stock) : p.stock;
  } catch (error) {
    stock = null;
  }
  if (stock && stock.type) {
    // "BM71 sett 15" for målt materiell, "Vanligvis BM76" for SJ, der vi
    // bare vet hva som pleier å gå på linja.
    const head = stock.typical
      ? `Vanligvis ${esc(stock.type)}`
      : `${esc(stock.type)} ${esc(stock.kind || "sett")} ${esc(stock.unit)}`;

    const facts = [`${esc(stock.topSpeed)} km/t`, esc(stock.drive)];
    if (stock.power) facts.push(`${esc(stock.power.toLocaleString("nb-NO"))} kW`);

    rows.push(head + " · " + facts.join(" · "));
    if (!stock.typical && stock.maker)
      rows.push(`${esc(stock.maker)} · ${esc(stock.built)}`);
  } else if (p.serviceType) {
    // Ingen materielldata. Vy legger tognummeret i vehicleId og publiserer
    // ikke settnummer noe sted, så for de fleste togene på kartet finnes det
    // ingenting å slå opp - verken i Vehicle Positions eller i Journey
    // Planner. Trafikktypen er det Entur faktisk har, og den er målt: den
    // står på hver eneste tur. Den sier hva slags trafikk toget kjører, ikke
    // hvilket sett det er, og linja er formulert deretter.
    rows.push(esc(p.serviceType));
  }

  if (p.speed !== "" && p.speed !== null && p.speed !== undefined)
    rows.push(`Fart ${Math.round(Number(p.speed) * 3.6)} km/t`);
  if (p.heading !== "" && p.heading !== null && p.heading !== undefined)
    rows.push(`Kjører mot ${esc(retningsnavn(Number(p.heading)))}`);
  if (p.lastUpdated)
    rows.push(`Sist sett ${clock(p.lastUpdated)} · ${ago(p.lastUpdated)}`);
  if (p.stale === true || p.stale === "true")
    rows.push("Uten fersk posisjon — forsinkelsen er ikke til å stole på");
  if (p.computed === true || p.computed === "computed" || p.computed === "true") {
    if (p.previousStop && p.nextStop)
      rows.push(`${esc(p.previousStop)} → ${esc(p.nextStop)}`);
    const live = p.realtime === true || p.realtime === "true";
    rows.push(
      live
        ? "Beregnet posisjon — SJ sender ikke GPS"
        : "Ingen sanntidsdata — plassert etter rutetabell"
    );
  }

  const band = BANDS.find((b) => b.id === p.band) || BANDS[4];

  new maplibregl.Popup({ offset: 12 })
    .setLngLat(feature.geometry.coordinates)
    .setHTML(
      `<div class="pop-line ${fargeklasse(band.varName)}">${
        esc(p.line) || "Ukjent linje"
      }${p.trainNumber ? `<span class="pop-train">Tog ${esc(p.trainNumber)}</span>` : ""}</div>
       ${p.lineName && p.lineName !== p.line
         ? `<div class="pop-route">${esc(p.lineName)}</div>`
         : ""}
       <div class="pop-delay">${formatDelay(delay)}</div>
       <div class="pop-meta">${rows.join("<br>")}</div>`
    )
    .addTo(map);

  // Ruteopptegning. Bare SJ-tog har trasé i backend: positions() merker dem
  // computed:true og legger journey-ID-en på properties.id, som er nettopp det
  // /api/route trenger. GPS-tog har ingen trasé - da tømmer vi ruta, slik at
  // streken alltid hører til toget popupen viser, ikke et tog man klikket før.
  // (properties er strenger etter GeoJSON-serialisering, derfor begge former.)
  const erSJ =
    p.computed === true || p.computed === "true" || p.computed === "computed";
  if (erSJ && p.id) {
    tegnRute(p.id);
  } else {
    tommRute();
  }
}

/* --- Valgt rute -----------------------------------------------------------
 * Én kilde ("valgt-rute") med én LineString, hentet fra /api/route/{id}.
 * tegnRute bytter innholdet, tommRute nullstiller. Begge er trygge å kalle når
 * som helst - de sjekker at kilden finnes før de rører den. */

async function tegnRute(turId) {
  try {
    const res = await fetch(`/api/route/${encodeURIComponent(turId)}`);
    if (!res.ok) {
      // 404: SJ-tur uten sporgeometri (straight), eller ukjent ID. Ingen rute
      // å vise - fjern en eventuell gammel så vi ikke blir stående med feil.
      tommRute();
      return;
    }
    const feature = await res.json();
    const kilde = map.getSource("valgt-rute");
    if (kilde) {
      kilde.setData(feature);
      valgtTurId = turId;
    }
  } catch (error) {
    console.warn("Kunne ikke hente rute:", error);
    tommRute();
  }
}

function tommRute() {
  valgtTurId = null;
  const kilde = map.getSource("valgt-rute");
  if (kilde) kilde.setData(EMPTY);
}

/* --- Ringdiagram ----------------------------------------------------------
 * Bygget av vanlige SVG-sirkler med stroke-dasharray. Hvert bånd får en
 * buelengde proporsjonal med andelen sin, og forskyves forbi de foregående.
 *
 * Grunnen til at det er en ring og ikke et kakediagram: midten er tom, og der
 * ligger tallet folk faktisk er ute etter. Et kakediagram tvinger deg til å
 * sammenligne vinkler; en ring med tall i midten gir svaret direkte. */

const DONUT = { size: 120, radius: 48, width: 15 };

function renderDonut(counts, active) {
  const svg = document.getElementById("donut");
  const { size, radius, width } = DONUT;
  const center = size / 2;
  const circumference = 2 * Math.PI * radius;

  if (!active) {
    svg.innerHTML = "";
    return null;
  }

  // Andelen returneres fordi sammendraget i gripefeltet skal si det samme som
  // ringen. To steder som viser samme tall skal ikke regne det hver for seg.
  const andel = counts.i_rute / active;
  const share = Math.round(andel * 100);
  let offset = 0;
  const arcs = [];

  for (const band of BANDS) {
    const value = counts[band.id] || 0;
    if (!value) continue;

    let length = (value / active) * circumference;
    // Liten luke mellom segmentene, men aldri så stor at et lite bånd blir
    // borte helt.
    const gap = length > 4 ? 1.5 : 0;

    // data-band og data-antall er det tooltipen leser. De ligger på selve
    // sirkelen fordi det er den som treffes av markøren — sektoren er strøket,
    // og med fill="none" er det bare strøket som svarer på hover.
    arcs.push(
      `<circle class="donut-seg" data-band="${band.id}" data-antall="${value}"
        cx="${center}" cy="${center}" r="${radius}"
        fill="none" stroke="${token(band.varName)}" stroke-width="${width}"
        stroke-dasharray="${length - gap} ${circumference - length + gap}"
        stroke-dashoffset="${-offset}"
        transform="rotate(-90 ${center} ${center})" />`
    );
    offset += length;
  }

  svg.innerHTML = `
    <circle cx="${center}" cy="${center}" r="${radius}" fill="none"
      stroke="${token("--border")}" stroke-width="${width}" opacity="0.35" />
    ${arcs.join("")}
    <text x="${center}" y="${center - 1}" class="donut-value"
      text-anchor="middle" dominant-baseline="middle">${share}%</text>
    <text x="${center}" y="${center + 19}" class="donut-label"
      text-anchor="middle">i rute</text>`;

  return andel;
}

/* --- Panel ---------------------------------------------------------------- */

/* Fordelingslista under ringen. Radene får samme data-band som sektorene, så
 * de svarer på hover med nøyaktig samme forklaring — det er de samme båndene,
 * og to steder som viser det samme skal ikke forklare det ulikt.
 *
 * tabindex="0" fordi tooltipen ellers bare finnes for den som kan bruke mus.
 * Sektorene i SVG-en kan ikke få fokus på samme måte, så disse fem radene er
 * veien inn i forklaringene med tastatur. */
function buildBreakdown() {
  document.getElementById("breakdown").innerHTML = BANDS.map(
    (b) => `
      <div class="row" data-band="${b.id}" tabindex="0"
           aria-label="${b.label}: ${bandIntervall(b)}">
        <span class="swatch ${fargeklasse(b.varName)}"></span>
        <span>${b.label}</span>
        <span class="n" id="n-${b.id}">–</span>
      </div>`
  ).join("");
}

function renderMeta(meta) {
  document.getElementById("count").textContent = meta.count;
  document.getElementById("updated").textContent =
    `oppdatert ${clock(meta.fetchedAt)}`;

  settSammendrag(meta.count, renderDonut(meta.counts, meta.active));

  BANDS.forEach((b) => {
    document.getElementById(`n-${b.id}`).textContent = meta.counts[b.id] ?? 0;
  });

  const linjer = [];

  // Spøkelsestog TEGNES, men teller ikke: avviket deres vokser mot en rutetid
  // toget aldri innfrir, og det er et tall vi ikke kan forsvare.
  if (meta.stale) {
    linjer.push(
      `${meta.stale} tog uten fersk posisjon, tegnet nedtonet og holdt utenfor ` +
      `tallene over. Er kartet zoomet ut, står de som et grått tall ved siden ` +
      `av klyngen de ligger i.`
    );
  }

  // Beregnede tog TELLER, og gjorde det ikke før 22. august. Begrunnelsen for
  // å holde dem utenfor gjaldt POSISJONEN; ringen handler om FORSINKELSEN, og
  // den kommer fra Journey Planner for både målte og beregnede tog. Uten dem
  // ble tallet systematisk for pent - fjerntogene, som oftest er forsinket, er
  // nettopp de uten GPS.
  if (meta.computed) {
    // De to grunnene betyr forskjellige ting og skal ikke summeres bort.
    // "ingen-gps" er SJ, som aldri publiserer posisjon. "mangler-posisjon" er
    // tog fra operatører som ellers gjør det, men ikke for denne turen - det
    // er hullet Bane NOR ser og vi ikke gjorde før 21. august.
    const grunner = meta.computedReasons || {};
    const utenGps = grunner["ingen-gps"] || 0;
    const mangler = grunner["mangler-posisjon"] || 0;
    let tekst = `${meta.computed} tog med beregnet posisjon, tegnet som ring`;
    if (utenGps && mangler) {
      tekst += ` (${utenGps} uten GPS, ${mangler} uten posisjon akkurat nå)`;
    }
    const talt = meta.countsComputed ?? 0;
    tekst += talt
      ? `. ${talt} av dem teller i tallene over — avviket er målt, det er` +
        ` posisjonen som er regnet ut.`
      : ".";
    linjer.push(tekst);
  }
  if (meta.ferdige) {
    // Ikke et spøkelse og ikke et hull: et ekte kjøretøy som har kjørt ferdig
    // og står hensatt. Det sender fortsatt GPS, men det er ikke et tog i
    // trafikk, og Bane NOR tegner det ikke heller. Tallet står her fordi et
    // tog som forsvinner uten forklaring ser ut som en feil.
    linjer.push(
      `${meta.ferdige} tog har fullført turen sin og er tatt ut av kartet.`
    );
  }

  const ghosts = document.getElementById("ghosts");
  if (linjer.length) {
    ghosts.textContent = linjer.join(" ");
    ghosts.hidden = false;
  } else {
    ghosts.hidden = true;
  }
}

function setError(message) {
  const box = document.getElementById("error");
  box.textContent = message || "";
  box.hidden = !message;
}

/* --- Bunnsheet (mobil) -----------------------------------------------------
 * På skrivebord ligger de tre panelene fritt i hvert sitt hjørne. På telefon
 * er det ikke plass til dem og et kart samtidig, og løsningen fram til 23.
 * august var en fast stripe nederst: tallene alltid framme, kartet alltid
 * beskåret. Her er stromkart-mekanikken i stedet.
 *
 * Sheetet har tre stopp. NEDE er bare gripefeltet, med de to tallene som er
 * verdt et blikk — kartet får resten. HALVVEIS er telleren og ringen over et
 * halvt kart, og det er tilstanden man møter appen i. OPPE er lesestilling.
 *
 * Tre ting er verdt å kjenne før du endrer noe her:
 *
 *   GEOMETRIEN MÅLES, ANTAS IKKE. Høyden er 88dvh i CSS, og gripefeltet vokser
 *   med hjemindikatoren på iPhone. Begge leses av DOM-en ved oppstart og på
 *   nytt ved rotasjon. Skriver man tallene inn her i stedet, er de feil på den
 *   første telefonen som ikke ligner utviklerens.
 *
 *   POINTER OG IKKE TOUCH. Det er vindusbredden som avgjør om sheetet finnes,
 *   ikke hva slags peker du har. Et smalt vindu på skrivebord får samme sheet,
 *   og da må musa kunne dra i det.
 *
 *   INNHOLDET ER inert NÅR SHEETET LIGGER NEDE. Det står fortsatt i DOM-en,
 *   utenfor skjermkanten, og uten dette kunne man tabbe seg inn i et søkefelt
 *   ingen ser. */

const MOBIL = window.matchMedia("(max-width: 768px)");

const sheet = document.getElementById("sheet");
const sheetGripe = document.getElementById("sheet-gripe");
const sheetInnhold = document.getElementById("sheet-innhold");
const sheetSammendrag = document.getElementById("sheet-sammendrag");

// Nedenfra og opp, og rekkefølgen brukes: et tastetrykk eller et kast flytter
// sheetet ett hakk i denne lista.
const SHEET_STOPP = ["nede", "halvveis", "oppe"];

// Hvor langt sheetet er skjøvet ned i hver tilstand, i piksler. 0 er helt oppe.
let sheetY = { nede: 0, halvveis: 0, oppe: 0 };
let sheetTilstand = "halvveis";

// Hvor langt fingeren må ha flyttet seg for at det er en dragning og ikke et
// trykk, og hvor langt den må dra for at et kast skal telle som «neste stopp»
// selv om det nærmeste stoppet er det man kom fra.
const SHEET_TRYKK = 6;
const SHEET_KAST = 50;

function malSheet() {
  if (!MOBIL.matches) return;
  const hoyde = sheet.offsetHeight;
  sheetY = {
    oppe: 0,
    halvveis: Math.round(hoyde * 0.5),
    nede: Math.max(0, hoyde - sheetGripe.offsetHeight),
  };
  settSheetTilstand(sheetTilstand, false);
}

/* Hvor mye av sheetet som står opp av skjermkanten. Attribusjonen legger seg
 * over denne kanten, og taket ved halvveis-stoppet er der fordi kontrollen
 * ellers ville blitt skjøvet ut av bildet i det øyeblikket sheetet dekker
 * kartet likevel. */
function sheetSynlig() {
  const hoyde = sheet.offsetHeight;
  return Math.min(hoyde - sheetY[sheetTilstand], hoyde - sheetY.halvveis);
}

function settSheetTilstand(tilstand, animer = true) {
  if (!MOBIL.matches) return;
  sheetTilstand = tilstand;

  // Første plassering skal ikke gli på plass — den skal bare stå der.
  if (!animer) sheet.classList.add("drar");
  sheet.style.transform = `translateY(${sheetY[tilstand]}px)`;
  if (!animer) requestAnimationFrame(() => sheet.classList.remove("drar"));

  const apent = tilstand !== "nede";
  sheetGripe.setAttribute("aria-expanded", String(apent));
  sheetGripe.setAttribute(
    "aria-label",
    apent ? "Legg bort tallene og vis kartet" : "Vis tall og driftsmeldinger"
  );
  sheetInnhold.toggleAttribute("inert", !apent);

  document.documentElement.style.setProperty(
    "--sheet-synlig",
    `${Math.round(sheetSynlig())}px`
  );
}

function sheetSteg(fra, retning) {
  const i = SHEET_STOPP.indexOf(fra) + retning;
  return SHEET_STOPP[Math.min(SHEET_STOPP.length - 1, Math.max(0, i))];
}

/* Nærmeste stopp til der fingeren slapp, med ett unntak: har du dratt et godt
 * stykke i én retning uten å komme halvveis til neste stopp, er det likevel
 * det du ba om. Uten unntaket blir en rask, kort bevegelse ignorert. */
function sheetLandingsplass(y, fra, dy) {
  let naermest = SHEET_STOPP[0];
  for (const stopp of SHEET_STOPP) {
    if (Math.abs(sheetY[stopp] - y) < Math.abs(sheetY[naermest] - y)) {
      naermest = stopp;
    }
  }
  if (naermest === fra && Math.abs(dy) > SHEET_KAST) {
    // Opp på skjermen er mindre y, og mindre y er neste stopp i lista.
    return sheetSteg(fra, dy < 0 ? 1 : -1);
  }
  return naermest;
}

let sheetDrag = null;

sheetGripe.addEventListener("pointerdown", (event) => {
  if (!MOBIL.matches) return;
  sheetDrag = {
    peker: event.pointerId,
    fraY: event.clientY,
    fraTilstand: sheetTilstand,
    start: sheetY[sheetTilstand],
    flyttet: 0,
  };
  // Fangst, ellers mister vi bevegelsen i det fingeren glir utenfor knappen —
  // og den gjør den med en gang, siden gripefeltet er femti piksler høyt.
  sheetGripe.setPointerCapture(event.pointerId);
  sheet.classList.add("drar");
});

sheetGripe.addEventListener("pointermove", (event) => {
  if (!sheetDrag || event.pointerId !== sheetDrag.peker) return;
  const dy = event.clientY - sheetDrag.fraY;
  sheetDrag.flyttet = Math.max(sheetDrag.flyttet, Math.abs(dy));
  const y = Math.min(sheetY.nede, Math.max(0, sheetDrag.start + dy));
  sheet.style.transform = `translateY(${y}px)`;
});

function slippSheet(event) {
  if (!sheetDrag || event.pointerId !== sheetDrag.peker) return;
  const { fraTilstand, start, fraY, flyttet } = sheetDrag;
  sheetDrag = null;
  sheet.classList.remove("drar");

  // Kort nok bevegelse er et trykk, ikke en dragning: da veksler sheetet
  // mellom nede og halvveis, samme to tilstander som aria-expanded beskriver.
  if (flyttet < SHEET_TRYKK) {
    settSheetTilstand(fraTilstand === "nede" ? "halvveis" : "nede");
    return;
  }

  const dy = event.clientY - fraY;
  const y = Math.min(sheetY.nede, Math.max(0, start + dy));
  settSheetTilstand(sheetLandingsplass(y, fraTilstand, dy));
}

sheetGripe.addEventListener("pointerup", slippSheet);
sheetGripe.addEventListener("pointercancel", slippSheet);

/* Tastatur. Pointerhendelsene over tar seg av trykket med finger og mus, så
 * denne skal bare svare på Enter og mellomrom — og det er nettopp de klikkene
 * som kommer uten pekerdetaljer (detail === 0). Uten sjekken ville hvert
 * fingertrykk vekslet to ganger. */
sheetGripe.addEventListener("click", (event) => {
  if (event.detail !== 0) return;
  settSheetTilstand(sheetTilstand === "nede" ? "halvveis" : "nede");
});

// Piltastene går ett stopp av gangen, og er den eneste veien til «oppe» for
// den som ikke bruker peker.
sheetGripe.addEventListener("keydown", (event) => {
  const retning = event.key === "ArrowUp" ? 1 : event.key === "ArrowDown" ? -1 : 0;
  if (!retning || !MOBIL.matches) return;
  event.preventDefault();
  settSheetTilstand(sheetSteg(sheetTilstand, retning));
});

/* Fokus som havner i innholdet mens sheetet står halvveis, er fokus på noe som
 * godt kan ligge nedenfor skjermkanten — typisk søkefeltet man nettopp trykket
 * på. Da skal sheetet opp; en fokusring på noe man ikke ser er en blindvei.
 * Ligger sheetet nede, er innholdet inert, og da fyrer dette aldri. */
sheetInnhold.addEventListener("focusin", () => {
  if (MOBIL.matches && sheetTilstand !== "oppe") settSheetTilstand("oppe");
});

/* Sammendraget i gripefeltet: det eneste som er lesbart når sheetet ligger
 * nede. Andelen kommer fra renderDonut og ikke fra en egen utregning, så
 * stripa og ringen aldri kan si to forskjellige ting. */
function settSammendrag(antall, andel) {
  sheetSammendrag.textContent =
    andel === null
      ? `${antall} tog i trafikk`
      : `${antall} tog · ${prosent(andel)} i rute`;
}

// Rotasjon, delt skjerm og en adresselinje som gjemmer seg endrer høyden
// stoppene er målt mot. Det gjør innholdet også: sheetet er like høyt som det
// som står i det, så en utslått historikk eller et varmekart som slås på
// flytter alle tre stoppene.
window.addEventListener("resize", malSheet);
new ResizeObserver(malSheet).observe(sheet);

/* Går vinduet over grensa, skal ingenting av mobiltilstanden bli hengende
 * igjen. transform gjør riktignok ingenting på en display:contents-boks, men
 * inert ville gjort hele panelrekka ubrukelig på skrivebord. */
MOBIL.addEventListener("change", (event) => {
  if (event.matches) {
    malSheet();
    return;
  }
  sheet.style.transform = "";
  sheetInnhold.removeAttribute("inert");
});

// Måles og plasseres med en gang, ikke etter at kartet har lastet: sheetet skal
// stå riktig i første maling, ellers ser man det skli på plass.
malSheet();

/* --- Tooltip på punktlighetsbåndene ---------------------------------------
 * Ringdiagrammet viser fire farger og en prosent. Fargene betyr ingenting før
 * man vet hvilke minutter de dekker, og den kunnskapen sto tidligere bare i
 * tegnforklaringen nederst til venstre — et annet sted på skjermen enn det man
 * ser på. Nå står definisjonen der markøren er.
 *
 * Én handler for både sektorene og fordelingsradene, via delegering på et
 * felles opphav. Ringen tegnes på nytt hvert 15. sekund, så en handler festet
 * direkte på sektorene ville forsvunnet ved første oppdatering.
 *
 * Teksten kommer fra BANDS. Panelet, tegnforklaringen og entur.py deler
 * dermed ett sett grenser, og en endring i tersklene kan ikke etterlate en
 * tooltip som sier noe annet enn kartet gjør. */

const tip = document.getElementById("tip");

// Hvor langt fra markøren tooltipen står, og hvor nær kanten den får komme
// før den snur. 14 piksler er nok til at pekeren ikke dekker første bokstav.
const TIP_AVSTAND = 14;
const TIP_MARG = 8;

function tipInnhold(band, antall) {
  const tall = antall === null
    ? ""
    : `<span class="tip-omrade">${antall} tog</span>`;

  // «Ukjent» har ingen grenser å skrive ut - båndet er definert ved at
  // tallet mangler, ikke ved hvor stort det er. Da står bare forklaringen.
  const intervall = band.fra === null && band.til === null
    ? ""
    : `<div><strong>${bandIntervall(band)}</strong> forsinket</div>`;

  return `
    <div class="tip-head">
      <span class="swatch ${fargeklasse(band.varName)}"></span>
      <span class="tip-navn">${band.label}</span>
      ${tall}
    </div>
    ${intervall}
    <div>${band.hjelp}</div>`;
}

/* Plasser tooltipen ved (x, y), men innenfor vinduet. Måling skjer etter at
 * innholdet er satt og elementet er synlig — en skjult boks har ingen
 * størrelse, og da ville speilingen ved høyre kant regnet med bredde 0. */
function plasserTip(x, y) {
  const boks = tip.getBoundingClientRect();

  let venstre = x + TIP_AVSTAND;
  if (venstre + boks.width > window.innerWidth - TIP_MARG) {
    venstre = x - TIP_AVSTAND - boks.width;
  }
  // Kom den utenfor på venstre side også, er vinduet smalere enn tooltipen.
  // Da klemmes den inn mot kanten i stedet for å havne utenfor skjermen.
  venstre = Math.max(TIP_MARG, venstre);

  let topp = y + TIP_AVSTAND;
  if (topp + boks.height > window.innerHeight - TIP_MARG) {
    topp = y - TIP_AVSTAND - boks.height;
  }
  topp = Math.max(TIP_MARG, topp);

  tip.style.left = `${Math.round(venstre)}px`;
  tip.style.top = `${Math.round(topp)}px`;
}

function visTip(element, x, y) {
  const band = BANDS.find((b) => b.id === element.dataset.band);
  if (!band) return;

  // Sektorene kjenner antallet sitt; radene i fordelingen har det i sin egen
  // celle. Finner vi det ingen av stedene, står tooltipen uten tall - den
  // handler om definisjonen, ikke om tellingen.
  const fraSektor = element.dataset.antall;
  const fraRad = document.getElementById(`n-${band.id}`)?.textContent;
  const antall = Number(fraSektor ?? fraRad);

  tip.innerHTML = tipInnhold(band, Number.isFinite(antall) ? antall : null);
  tip.hidden = false;
  plasserTip(x, y);
}

function skjulTip() {
  tip.hidden = true;
}

/* Sheetet er felles opphav for både #donut og #breakdown, på mobil som på
 * skrivebord. Én lytter dekker begge, og overlever at ringen tegnes om. */
const tipRot = sheet;

tipRot.addEventListener("mouseover", (event) => {
  const mal = event.target.closest("[data-band]");
  if (mal) visTip(mal, event.clientX, event.clientY);
});

tipRot.addEventListener("mousemove", (event) => {
  if (!tip.hidden && event.target.closest("[data-band]")) {
    plasserTip(event.clientX, event.clientY);
  }
});

tipRot.addEventListener("mouseout", (event) => {
  // Bare når markøren forlater båndet helt. Beveger den seg mellom to
  // elementer inne i samme rad, skal tooltipen bli stående.
  const mal = event.target.closest("[data-band]");
  if (mal && !mal.contains(event.relatedTarget)) skjulTip();
});

/* Tastatur: fordelingsradene har tabindex, så de kan fokuseres. Tooltipen
 * plasseres da ved raden i stedet for ved markøren, som ikke har flyttet seg. */
tipRot.addEventListener("focusin", (event) => {
  const mal = event.target.closest("[data-band]");
  if (!mal) return;
  const boks = mal.getBoundingClientRect();
  visTip(mal, boks.right, boks.bottom);
});

tipRot.addEventListener("focusout", skjulTip);

// En tooltip som henger igjen over et kart man har begynt å dra i, ser ut som
// en feil. Rulling og kartbevegelse rydder den bort.
window.addEventListener("scroll", skjulTip, true);
map.on("movestart", skjulTip);

/* --- Nyhetsstripa ---------------------------------------------------------
 * Driftsmeldingene fra SIRI-SX, én av gangen, nederst til venstre. Overtok
 * plassen etter tegnforklaringen «Forsinkelse» 20. august.
 *
 * Tre valg som er verdt å kjenne før du endrer noe her:
 *
 *   RULLER OPPOVER, IKKE SIDELENGS. En vannrett marquee er den klassiske
 *   nyhetsstripa, men den forutsetter en flate på tvers av hele skjermen.
 *   I en 340 px boks rekker man verken å lese ferdig en setning eller å
 *   treffe den med musa mens den beveger seg. Her står hver melding stille i
 *   seks sekunder og bytter med en kort glidning oppover.
 *
 *   NEDTELLINGEN TELLES I requestAnimationFrame, ikke i setInterval. Da
 *   stopper den av seg selv når fanen er skjult - nettleseren slutter å kalle
 *   rAF - og pausen på hover blir eksakt: vi legger bare ikke til tid.
 *
 *   INGEN AUTOROTASJON VED prefers-reduced-motion. Innholdet som bytter seg
 *   selv ER bevegelsen her, ikke bare glidningen. Pilene gir alt innholdet
 *   uansett. */

const NYHET_MS = 6000;
const NYHET_REFRESH_MS = 60000;

// Må matche NIVAER i avvik.py. Rekkefølgen betyr ingenting her - backend har
// allerede sortert - men navnene må stemme, ellers får meldingen ingen farge.
const NYHET_NIVA = ["stort", "middels", "rettet", "info"];

const nyhetVindu = document.getElementById("nyhet-vindu");
const nyhetTeller = document.getElementById("nyhet-teller");
const nyhetFot = document.querySelector(".nyhet-fot");
const nyhetStrek = document.querySelector("#nyhet-fremdrift span");

let nyheter = [];
let nyhetIndex = 0;
let nyhetGaatt = 0;      // ms vist av gjeldende melding
let nyhetSisteRamme = 0;
let nyhetPause = false;
// Serveren serverer forrige sett med `foreldet: true` når hentingen feiler.
// Uten dette flagget rullet et gammelt sett videre som om det var ferskt.
let nyheterForeldet = false;
let nyhetRaf = null;

const roligBevegelse = window.matchMedia("(prefers-reduced-motion: reduce)");

/* Det den valgte meldingen gjelder, tegnet i kartet. To former, og hvilken
 * som brukes bestemmer backend - se «Hva meldingen peker på i kartet» i
 * avvik.py:
 *
 *   STREKNING. «Buss for tog mellom Kristiansand og Gjerstad.» Da lyser hele
 *   banestykket, rutet på ekte spor. Ringer rundt de to endestasjonene ville
 *   sagt at det er noe galt to STEDER; det er banen mellom dem som er stengt.
 *
 *   STEDER. «Heisen på Sandvika er ute av drift.» Da er stedet hele
 *   opplysningen, og en ring rundt det sier akkurat det.
 *
 * INGENTING TEGNES AV SEG SELV. Stripa bytter melding hvert sjette sekund, og
 * fram til nå fulgte ringene med på rotasjonen: kartet blinket i ringer på
 * steder ingen hadde spurt om. Nå er markeringen svar på en handling - hover
 * viser, klikk låser og flytter kameraet.
 *
 * Egne kilder av samme grunn som «valgt-rute»: refresh() rører bare «trains»,
 * så markeringen blir stående til man velger noe annet. */
function leggTilAvvikslag() {
  map.addSource("avvik-steder", { type: "geojson", data: EMPTY });
  map.addSource("avvik-strekning", { type: "geojson", data: EMPTY });

  // Strekningen først, så ringene havner over den i lagstabelen. En melding er
  // aldri begge deler, men rekkefølgen skal ikke avhenge av det.
  map.addLayer({
    id: "avvik-strek-glod",
    type: "line",
    source: "avvik-strekning",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": token("--c-vhigh"),
      "line-width": ["interpolate", ["linear"], ["zoom"], 4, 6, 10, 14, 14, 22],
      "line-opacity": 0.2,
      "line-blur": 5,
    },
  });

  map.addLayer({
    id: "avvik-strek",
    type: "line",
    source: "avvik-strekning",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": token("--c-vhigh"),
      "line-width": ["interpolate", ["linear"], ["zoom"], 4, 2, 10, 4, 14, 6],
      "line-opacity": 0.95,
    },
  });

  map.addLayer({
    id: "avvik-glod",
    type: "circle",
    source: "avvik-steder",
    paint: {
      "circle-color": token("--c-vhigh"),
      "circle-opacity": 0.18,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 10, 12, 26],
      "circle-blur": 0.6,
    },
  });

  map.addLayer({
    id: "avvik-ring",
    type: "circle",
    source: "avvik-steder",
    paint: {
      "circle-color": "transparent",
      "circle-stroke-color": token("--c-vhigh"),
      "circle-stroke-width": 1.6,
      "circle-stroke-opacity": 0.9,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 5, 12, 13],
    },
  });
}

// Fargen på markeringen er den samme som prikken foran meldingen i stripa.
// Nøklene må matche NYHET_NIVA og reglene i app.css: ser man rødt i boksen,
// skal det være rødt i kartet, ellers er det ikke opplagt at de hører sammen.
const AVVIK_FARGE = {
  stort: "--c-vhigh",
  middels: "--c-mid",
  rettet: "--c-low",
  info: "--text-dim",
};

function merkAvvikIKart(melding) {
  const steder = map.getSource("avvik-steder");
  const strekning = map.getSource("avvik-strekning");
  if (!steder || !strekning) return;

  const punkter = (melding && melding.punkter) || [];
  const linjer = (melding && melding.strekninger) || [];

  steder.setData({
    type: "FeatureCollection",
    features: punkter.map((koordinat) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: koordinat },
      properties: {},
    })),
  });

  strekning.setData(
    linjer.length
      ? {
          type: "Feature",
          geometry: { type: "MultiLineString", coordinates: linjer },
          properties: {},
        }
      : EMPTY
  );

  // Settes på lagene og ikke per objekt: alt som tegnes tilhører samme
  // melding, så det er én verdi og ikke en egenskap i dataene.
  const farge = token(AVVIK_FARGE[(melding && melding.niva) || "info"] || "--text-dim");
  map.setPaintProperty("avvik-glod", "circle-color", farge);
  map.setPaintProperty("avvik-ring", "circle-stroke-color", farge);
  map.setPaintProperty("avvik-strek-glod", "line-color", farge);
  map.setPaintProperty("avvik-strek", "line-color", farge);
}

/* Den låste meldingen - den man har KLIKKET. Hover tegner oppå den et
 * øyeblikk og legger den tilbake når musa går ut igjen. Uten de to
 * tilstandene ville et streif med musa slettet det man nettopp valgte. */
let avvikValgt = null;

// Musa gikk ut av meldingen: legg tilbake det som var låst, eller ingenting.
function slippForhandsvisning() {
  merkAvvikIKart(avvikValgt);
}

function tommAvvik() {
  avvikValgt = null;
  merkAvvikIKart(null);
}

/* Kartkoblingen, og den hører til KLIKKET alene. Hover skal peke, ikke flytte:
 * et kamera som rykker av seg selv mens man drar musa forbi en liste er den
 * sikreste måten å miste det man holdt på å se på.
 *
 * Ett sted kan man fly til; en strekning må rammes inn. maxZoom hindrer at en
 * melding om én enkelt stasjon kaster kartet helt ned på gatenivå, der man
 * mister all sammenheng. */
function velgAvvik(melding) {
  /* Klikk på den samme igjen låser opp igjen. Ellers er det ingen vei ut av
   * markeringen uten å klikke i tom kartflate, og det er ikke noe man gjetter.
   * Sammenligningen går på id og ikke på objektet: hentingen hvert minutt
   * bytter ut meldingsobjektene, og knappen som ble bygget forrige gang lukker
   * fortsatt om det gamle. Mangler `situationNumber`, faller vi tilbake på
   * objektet - da ville alle de tomme id-ene sett like ut for hverandre.
   *
   * Markeringen blir stående så lenge musa er over meldingen - det er
   * forhåndsvisningen, ikke låsen, og den forsvinner når man flytter musa. */
  const samme = melding.id
    ? avvikValgt && avvikValgt.id === melding.id
    : avvikValgt === melding;
  if (samme) {
    avvikValgt = null;
    merkAvvikIKart(melding);
    return;
  }

  avvikValgt = melding;
  merkAvvikIKart(melding);
  if (!melding.boks) return;

  const [vest, sor, ost, nord] = melding.boks;
  if (vest === ost && sor === nord) {
    map.flyTo({ center: [vest, sor], zoom: 11, essential: true });
  } else {
    map.fitBounds(
      [[vest, sor], [ost, nord]],
      { padding: 90, maxZoom: 11, essential: true }
    );
  }
}

/* Undertittelen: linjene, hvor mange stasjoner, og når meldingen kom.
 * Alle tre kan mangle, og da skal ikke skilletegnene bli stående igjen -
 * derfor filter(Boolean) og ikke en fast mal. */
function nyhetMeta(melding) {
  const deler = [];
  if (melding.linjer && melding.linjer.length) {
    deler.push(melding.linjer.join(", "));
  }
  if (melding.antallSteder === 1 && melding.steder.length) {
    deler.push(melding.steder[0]);
  } else if (melding.antallSteder > 1) {
    // Navnene når de får plass, ellers antallet. «Skøyen, Lysaker +3» sier
    // hvor man havner ved klikk; «5 stasjoner» sier bare hvor mange.
    const vist = melding.steder.slice(0, 2).join(", ");
    const rest = melding.antallSteder - Math.min(2, melding.steder.length);
    deler.push(rest > 0 ? `${vist} +${rest}` : vist);
  }
  /* Antall avganger, når meldingen står for flere enn én. For grupperte
   * meldinger ER dette hele poenget: teksten er redusert til merkelappen
   * («Færre vogner»), og tallet er det som sier hvor stort det er. */
  if (melding.avganger > 1) deler.push(`${melding.avganger} avganger`);

  /* Alder, ikke klokkeslett. Sto det `04:23:00` her, måtte leseren regne
   * selv - og en melding fra i natt så like fersk ut som en fra ett minutt
   * siden. `6 t siden` sier det med én gang. Serveren luker allerede bort alt
   * eldre enn avvik.MAKS_ALDER_TIMER, så dette er andre lag med samme sikring:
   * blir stripa gammel likevel, står det der i stedet for å være skjult. */
  if (melding.fra) deler.push(ago(melding.fra));
  return deler.join(" · ");
}

function byggNyhet(melding) {
  const knapp = document.createElement("button");
  knapp.type = "button";
  knapp.className = "nyhet";
  knapp.dataset.niva = NYHET_NIVA.includes(melding.niva) ? melding.niva : "info";
  // Teksten klippes til to linjer i CSS; title gir resten uten et klikk.
  knapp.title = melding.stikkord
    ? `${melding.stikkord}: ${melding.tekst}`
    : melding.tekst;

  const stikkord = melding.stikkord
    ? `<span class="nyhet-stikkord">${esc(melding.stikkord)}:</span> `
    : "";
  const meta = nyhetMeta(melding);

  knapp.innerHTML =
    `<span class="nyhet-tekst">${stikkord}${esc(melding.tekst)}</span>` +
    (meta ? `<span class="nyhet-meta">${esc(meta)}</span>` : "");

  /* Hover peker, klikk låser. Både mus og tastatur: den som tabber seg
   * gjennom stripa skal se det samme som den som drar musa over den. */
  knapp.addEventListener("mouseenter", () => merkAvvikIKart(melding));
  knapp.addEventListener("mouseleave", () => slippForhandsvisning());
  knapp.addEventListener("focus", () => merkAvvikIKart(melding));
  knapp.addEventListener("blur", () => slippForhandsvisning());
  knapp.addEventListener("click", () => velgAvvik(melding));
  return knapp;
}

/* Én melding erstatter en annen: den gamle glir opp og ut, den nye kommer inn
 * nedenfra. Begge ligger i vinduet samtidig i 320 ms, derfor position:absolute
 * på den utgående - ellers ville den dyttet den nye nedover mens den forsvant. */
function visNyhet(index, animer = true) {
  if (!nyheter.length) return;

  nyhetIndex = ((index % nyheter.length) + nyheter.length) % nyheter.length;
  nyhetGaatt = 0;

  /* Alle utgående, ikke bare den ene. Klikker man raskt på pilene, rekker
   * ikke den forrige å bli fjernet før den neste kommer - og da ville en
   * querySelector(".nyhet") plukket den utdaterte som «gammel» og latt den
   * synlige bli stående for godt. Vi rydder alle unntatt den siste. */
  const staaende = [...nyhetVindu.querySelectorAll(".nyhet")];
  staaende.slice(0, -1).forEach((el) => el.remove());
  const gammel = staaende[staaende.length - 1];
  const ny = byggNyhet(nyheter[nyhetIndex]);

  if (gammel && animer) {
    gammel.style.position = "absolute";
    gammel.style.top = "0";
    gammel.style.left = "0";
    gammel.style.right = "0";
    gammel.classList.add("gaar-ut");
    setTimeout(() => gammel.remove(), 340);
    ny.classList.add("kommer-inn");
    nyhetVindu.appendChild(ny);
    // To rammer, ikke én: den første maler elementet i startposisjon, den
    // andre starter overgangen. Uten den dobbelte ventingen slår nettleseren
    // sammen begge tilstandene og det blir ingen glidning.
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        ny.classList.remove("kommer-inn");
        ny.classList.add("pa-plass");
      })
    );
  } else {
    nyhetVindu.replaceChildren(ny);
  }

  nyhetTeller.textContent =
    `${nyhetIndex + 1} / ${nyheter.length}` +
    (nyheterForeldet ? " · ikke oppdatert" : "");

  /* Merk: ingen merkAvvikIKart her. Rotasjonen skal ikke tegne i kartet - se
   * kommentaren over leggTilAvvikslag. Det som er valgt blir stående, også
   * når stripa har rullet videre til noe annet. */
}

function nyhetLoop(na) {
  nyhetRaf = requestAnimationFrame(nyhetLoop);

  /* Klemt til én ramme ved 30 Hz. Nettleseren slutter å kalle rAF når fanen
   * er skjult, så den første rammen etter at man kommer tilbake har et delta
   * på alt fra sekunder til timer. Uten taket ville stripa hoppet et ukjent
   * antall meldinger fram i det øyeblikket man ser på den igjen. */
  const delta = nyhetSisteRamme ? Math.min(na - nyhetSisteRamme, 33) : 0;
  nyhetSisteRamme = na;

  const staar = nyhetPause || nyheter.length < 2 || roligBevegelse.matches;
  if (staar) return;

  nyhetGaatt += delta;
  if (nyhetGaatt >= NYHET_MS) {
    visNyhet(nyhetIndex + 1);
  } else if (nyhetStrek) {
    nyhetStrek.style.width = `${(nyhetGaatt / NYHET_MS) * 100}%`;
  }
}

function startNyhetLoop() {
  if (nyhetRaf !== null) return;
  nyhetSisteRamme = 0;
  nyhetRaf = requestAnimationFrame(nyhetLoop);
}

/* En melding uten data. Tom stripe og feilet henting er ikke det samme:
 * «ingen avvik» er en opplysning, «fikk ikke tak i dem» er fravær av en. */
function nyhetBeskjed(tekst, klasse) {
  nyheter = [];
  nyhetTeller.textContent = "";
  nyhetFot.hidden = true;
  tommAvvik();
  nyhetVindu.replaceChildren();
  const rad = document.createElement("div");
  rad.className = `nyhet ${klasse}`;
  rad.dataset.niva = klasse === "nyhet-tom" ? "rettet" : "info";
  rad.innerHTML = `<span class="nyhet-tekst">${esc(tekst)}</span>`;
  nyhetVindu.appendChild(rad);
}

async function hentNyheter() {
  try {
    const svar = await fetch("/api/avvik", { cache: "no-store" });
    const data = await svar.json();
    if (!svar.ok) throw new Error(data.error || "avvik utilgjengelig");

    nyheterForeldet = Boolean(data.foreldet);

    const nye = data.meldinger || [];
    if (!nye.length) {
      nyhetBeskjed("Ingen driftsmeldinger nå.", "nyhet-tom");
      return;
    }

    // Behold plassen i løypa når innholdet er det samme. Uten dette ville
    // hentingen hvert minutt kastet leseren tilbake til melding 1 midt i
    // setningen på melding 9.
    const samme =
      nye.length === nyheter.length &&
      nye.every((m, i) => m.id === nyheter[i].id && m.tekst === nyheter[i].tekst);

    /* Meldingsobjektene byttes ut ved hver henting, så et valg som peker på
     * det gamle objektet ville blitt stående og tegne noe som ikke lenger er
     * i stripa. Vi flytter valget over på den nye utgaven av samme melding,
     * eller slipper det hvis den er borte. */
    const valgtId = avvikValgt && avvikValgt.id;
    nyheter = nye;
    if (valgtId) {
      const fortsatt = nyheter.find((m) => m.id === valgtId);
      avvikValgt = fortsatt || null;
      merkAvvikIKart(avvikValgt);
    }
    nyhetFot.hidden = nyheter.length < 2;
    if (!samme) visNyhet(0, false);
    startNyhetLoop();
  } catch (error) {
    // Bare hvis vi ikke allerede har meldinger å vise. En stripe som står med
    // det den hadde er bedre enn en som tømmer seg fordi nettet blunket.
    if (!nyheter.length) {
      nyhetBeskjed("Fikk ikke hentet driftsmeldinger.", "nyhet-feil");
    } else {
      // Vi beholder det vi har - men da skal det stå at det står stille.
      nyheterForeldet = true;
      visNyhet(nyhetIndex, false);
    }
  }
}

// Pause mens man leser. Både mus og tastatur: den som tabber seg inn i stripa
// skal ikke få meldingen byttet under fingrene.
nyhetVindu.addEventListener("mouseenter", () => (nyhetPause = true));
nyhetVindu.addEventListener("mouseleave", () => (nyhetPause = false));
nyhetVindu.addEventListener("focusin", () => (nyhetPause = true));
nyhetVindu.addEventListener("focusout", () => (nyhetPause = false));

document.getElementById("nyhet-forrige").addEventListener("click", () => {
  if (nyheter.length) visNyhet(nyhetIndex - 1);
});
document.getElementById("nyhet-neste").addEventListener("click", () => {
  if (nyheter.length) visNyhet(nyhetIndex + 1);
});

/* Bryteren for varmekartet. Valget huskes i localStorage: den som har slått
 * laget på, har sagt hva slags kart de vil se, og skal slippe å si det igjen
 * ved hver lasting. Feiler lagringen — privat modus, full disk — skal ikke
 * kartet stoppe av det, derfor try. */
const fhBryter = document.getElementById("flaskehals-paa");

fhBryter.addEventListener("change", () => {
  settFlaskehalser(fhBryter.checked);
  try {
    localStorage.setItem("togkart-flaskehalser", fhBryter.checked ? "1" : "0");
  } catch (error) {
    /* uten lagring virker alt annet like bra */
  }
});

try {
  if (localStorage.getItem("togkart-flaskehalser") === "1") {
    fhBryter.checked = true;
  }
} catch (error) {
  /* samme her */
}

const fhHelp = document.getElementById("flaskehals-help");
fhHelp.addEventListener("click", () => {
  const note = document.getElementById("flaskehals-note");
  const apen = note.hasAttribute("hidden");
  note.toggleAttribute("hidden", !apen);
  fhHelp.setAttribute("aria-expanded", String(apen));
});

// Den lille i-en slår forklaringen av og på.
const help = document.getElementById("nyhet-help");
help.addEventListener("click", () => {
  const note = document.getElementById("nyhet-note");
  const open = note.hasAttribute("hidden");
  note.toggleAttribute("hidden", !open);
  help.setAttribute("aria-expanded", String(open));
});

/* --- Søk ------------------------------------------------------------------
 * To kilder, to hastigheter:
 *   Tog     - finnes i `latest`, søkes lokalt, svarer momentant
 *   Stasjon - må hentes fra Entur Geocoder, derfor 250 ms forsinkelse så vi
 *             ikke fyrer av et kall per tastetrykk */

const scope = document.getElementById("scope");
const input = document.getElementById("q");
const results = document.getElementById("results");

// trains-label har allerede et filter. Det må bevares når vi legger på søket.
let searchTimer = null;

/* Vis bare søketreffene i kartet, eller alt igjen.
 *
 * DETTE VAR ET FILTER TIL 21. AUGUST, og et filter kan ikke lenger gjøre
 * jobben. Klyngedannelsen skjer på KILDENIVÅ, før noe lag ser dataene: under
 * zoom 7 er et tog som ligger i en klynge ikke lenger et eget punkt i det
 * hele tatt. Et lagfilter på `id` hadde derfor ikke noe å treffe, og søk på
 * oversiktskartet ville vist en tom skjerm.
 *
 * Løsningen er en egen, uklynget kilde som treffene tegnes fra. Da virker
 * søket likt på alle zoomnivå, og vi slipper å skru klyngedannelsen av og på
 * - noe MapLibre uansett ikke tillater uten å bygge kilden på nytt.
 *
 * Mens et søk er aktivt skjules ALLE de vanlige toglagene, klyngene inkludert.
 * Klyngene måtte uansett bort: de teller alle togene på stedet, også de som
 * ikke er treff, så en klynge med «12» ved siden av ett søketreff ville vært
 * en direkte usannhet. */
function applyTrainFilter(treff) {
  // Søket kan i prinsippet fyre før kartet er ferdig lastet. Da finnes ikke
  // lagene ennå, og neste refresh() kaller oss uansett på nytt.
  if (!map.getSource("sokte-tog")) return;

  const soker = Boolean(treff);
  const synlig = soker ? "none" : "visible";

  ["trains-glow", "trains-dot", "trains-label", "trains-arrow",
   "trains-cluster", "trains-cluster-count",
   "trains-cluster-spokelser"].forEach((lag) => {
    if (map.getLayer(lag)) map.setLayoutProperty(lag, "visibility", synlig);
  });

  map.getSource("sokte-tog").setData(
    soker ? { type: "FeatureCollection", features: treff } : EMPTY
  );
}

function matchTrains(query) {
  const needle = query.toLowerCase();
  return latest.features.filter((feature) => {
    const p = feature.properties;
    return (
      (p.trainNumber && String(p.trainNumber).startsWith(query)) ||
      (p.line && p.line.toLowerCase().startsWith(needle)) ||
      (p.lineName && p.lineName.toLowerCase().includes(needle))
    );
  });
}

function showResults(rows) {
  if (!rows.length) {
    results.innerHTML = '<li class="empty">Ingen treff</li>';
    results.hidden = false;
    return;
  }
  results.innerHTML = rows
    .map(
      (row, index) => `
        <li tabindex="0" data-index="${index}">
          <span class="kind">${esc(row.kind)}</span>
          <span class="hit">${esc(row.title)}</span>
          <span class="sub">${esc(row.sub)}</span>
        </li>`
    )
    .join("");
  results.hidden = false;

  [...results.children].forEach((item) => {
    const row = rows[Number(item.dataset.index)];
    if (!row) return;
    const go = () => {
      map.flyTo({ center: row.center, zoom: row.zoom, essential: true });
      if (row.feature) {
        showPopup({ features: [row.feature] });
      }
      results.hidden = true;

      // På telefon står sheetet oppe mens man søker, og dekker kartet. Har man
      // først valgt et treff, er det kartet man vil se — ikke lista man kom
      // fra. blur() først, så tastaturet følger med ned.
      input.blur();
      settSheetTilstand("nede");
    };
    item.addEventListener("click", go);
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter") go();
    });
  });
}

async function runSearch() {
  const query = input.value.trim();
  const mode = scope.value;

  if (!query) {
    results.hidden = true;
    applyTrainFilter(null);
    return;
  }

  const rows = [];

  if (mode === "all" || mode === "trains") {
    const hits = matchTrains(query);
    // Kartet viser bare treffene mens du skriver.
    applyTrainFilter(hits);
    hits.slice(0, 8).forEach((feature) => {
      const p = feature.properties;
      rows.push({
        kind: "Tog",
        title: `${p.line || "?"}${p.trainNumber ? ` · ${p.trainNumber}` : ""}`,
        sub: p.lineName || "",
        center: feature.geometry.coordinates,
        zoom: 10,
        feature,
      });
    });
  } else {
    applyTrainFilter(null);
  }

  showResults(rows);

  if (mode === "all" || mode === "stations") {
    try {
      const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
      const body = await response.json();
      (body.results || []).forEach((place) => {
        rows.push({
          kind: "Stasjon",
          title: place.name,
          sub: place.label.includes(",") ? place.label.split(",").pop().trim() : "",
          center: [place.lon, place.lat],
          zoom: 12,
        });
      });
      showResults(rows);
    } catch (error) {
      showResults(rows);
    }
  }
}

input.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, 250);
});

scope.addEventListener("change", runSearch);

input.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    input.value = "";
    runSearch();
    input.blur();
  }
});

document.addEventListener("click", (event) => {
  if (!results.contains(event.target) && event.target !== input) {
    results.hidden = true;
  }
});

/* --- Historikk ------------------------------------------------------------
 * To visninger på samme flate, fra samme database:
 *
 *   Operatører - hvem kjører flest turer i rute
 *   Rushtid    - hvilke strekninger som taper mest tid morgen og ettermiddag
 *
 * Begge kommer ferdig regnet fra backend (analyse.py). Denne koden tegner,
 * den regner ikke - all definisjon av «i rute», «togtur» og «rush» bor ett
 * sted, og det stedet er Python. Da kan ikke panelet og CLI-en svare
 * forskjellig på samme spørsmål. */

const HIST_REFRESH_MS = 5 * 60 * 1000;

const prosent = (andel) => `${Math.round(andel * 100)} %`;

/* Én rad: plassering, navn, hovedtall, en detaljlinje og en strek.
 *
 * Streken har to dimensjoner, og de måler hver sin ting:
 *   lengden = hvor OFTE toget er i rute (andelen)
 *   fargen  = hvor MYE det bommer når det bommer (median avvik, samme
 *             terskler som prikkene på kartet)
 *
 * En kort grønn strek er et selskap som er sent støtt og stadig, men bare
 * litt. En lang rød er et som stort sett går presist og så plutselig ikke.
 * Det er to forskjellige måter å være upålitelig på, og et enkelt tall
 * skjuler forskjellen.
 *
 * `tallFarge` er med fordi fargen alltid skal høre til tallet den står på:
 * i rushfanen er hovedtallet selve medianen og får båndfargen, i
 * operatørfanen er det en andel, og en andel har ingen båndfarge. Da står
 * den nøytralt, og båndfargen flyttes ned på medianen i detaljlinja. */
function histRad({ plass, navn, merke, tall, tallFarge, detalj, andel, sekunder }) {
  const klasse = fargeklasse(bandVar(sekunder));
  // Bredden er den ene verdien som ikke kan bli en klasse - den er
  // kontinuerlig, én per rad. Den settes gjennom CSSOM av settBarBredder()
  // etter at markupen står; se kommentaren over fargeklasse().
  return `
    <div class="hist-rad">
      <span class="hist-plass">${esc(plass)}</span>
      <span class="hist-navn">${esc(navn)}${merke || ""}</span>
      <span class="hist-tall${tallFarge ? " " + klasse : ""}">${esc(tall)}</span>
      <span class="hist-detalj">${detalj}</span>
      <span class="hist-bar">
        <span class="${klasse}" data-andel="${Math.round(andel * 100)}"></span>
      </span>
    </div>`;
}

/* Strekbreddene, satt etter at radene står i DOM-en.
 *
 * `element.style.width` er CSSOM og ikke et style-attributt i markup, og
 * style-src gjelder bare det siste. Dette er altså ikke en omgåelse av
 * policyen - det er den veien policyen peker på. */
function settBarBredder(boks) {
  boks.querySelectorAll(".hist-bar > span").forEach((strek) => {
    strek.style.width = `${strek.dataset.andel}%`;
  });
}

const MERKE_BEREGNET =
  '<span class="hist-merke" title="En andel av turene har posisjon beregnet ' +
  'fra rutetider i stedet for målt med GPS. SJ har aldri GPS; de andre ' +
  'mangler posisjon for noen av turene sine. Avviket kommer fra Journey ' +
  'Planner uansett.">' +
  "&#8202;*</span>";

function renderOperatorer(data) {
  const boks = document.getElementById("rangering");
  const liste = data.operatorer || [];

  if (!liste.length) {
    boks.innerHTML = `
      <p class="hist-tom">
        Ingen togturer å rangere ennå. Databasen fyller seg mens serveren går —
        kom tilbake om et døgn.
      </p>`;
    return;
  }

  boks.innerHTML = liste
    .map((o) =>
      histRad({
        plass: `${o.plass}.`,
        navn: o.navn,
        merke: o.andelBeregnet > 0 ? MERKE_BEREGNET : "",
        tall: prosent(o.andelIRute),
        detalj:
          `median <span class="hist-median ` +
          `${fargeklasse(bandVar(o.medianAvvikSekunder))}">` +
          `${mmss(o.medianAvvikSekunder)}</span> · ` +
          `${esc(o.turer)} ${o.turer === 1 ? "tur" : "turer"} · ${esc(o.linjer)} linjer`,
        andel: o.andelIRute,
        sekunder: o.medianAvvikSekunder,
      })
    )
    .join("");

  settBarBredder(boks);
}

function renderRush(data) {
  const boks = document.getElementById("rushprofil");
  const titler = { morgen: "Morgenrush", ettermiddag: "Ettermiddagsrush" };
  const biter = [];

  for (const [navn, periode] of Object.entries(data.perioder || {})) {
    const timer =
      `${String(periode.fraTime).padStart(2, "0")}–` +
      `${String(periode.tilTime).padStart(2, "0")}`;
    biter.push(`<p class="hist-gruppe">${esc(titler[navn] || navn)} ${timer}</p>`);

    if (!periode.strekninger.length) {
      // Tomt vindu og for tynt vindu er to forskjellige ting, og forskjellen
      // avgjør om man skal vente eller lete etter en feil.
      const grunn = periode.utelattForFåTurer
        ? `${esc(periode.utelattForFåTurer)} linjer har færre enn ` +
          `${esc(data.minTurer)} turer i vinduet`
        : "ingen togturer logget i dette vinduet ennå";
      biter.push(`<p class="hist-tom">Ingen strekninger å vise — ${grunn}.</p>`);
      continue;
    }

    biter.push(
      periode.strekninger
        .map((s, i) =>
          histRad({
            plass: `${i + 1}.`,
            navn: s.linje,
            tall: mmss(s.medianAvvikSekunder),
            tallFarge: true,
            detalj:
              `${esc(s.strekning)} · ${esc(s.turer)} turer · ` +
              `${prosent(s.andelIRute)} i rute`,
            andel: s.andelIRute,
            sekunder: s.medianAvvikSekunder,
          })
        )
        .join("")
    );
  }

  boks.innerHTML = biter.join("");
  settBarBredder(boks);
}

/* Foten sier hvor tynt grunnlaget er. Et panel som viser en rangering uten å
 * si at den bygger på ett døgn, later som om den vet mer enn den gjør. */
function renderHistFot(operatorer) {
  const deler = [];
  const dogn = operatorer?.vindu?.dognMedData;

  if (dogn) {
    deler.push(`${dogn} døgn med data`);
    if (dogn < 3) deler.push("for tynt til å konkludere");
  }
  if (operatorer?.utenOperator) {
    deler.push(
      `${operatorer.utenOperator} turer uten operatørmerking (logget før ` +
      `kolonnen fantes)`
    );
  }
  if (operatorer?.operatorer?.some((o) => o.andelBeregnet > 0)) {
    deler.push("* posisjon beregnet, ikke målt");
  }

  document.getElementById("hist-fot").textContent = deler.join(" · ");
}

async function hentHistorikk() {
  const bokser = [
    document.getElementById("rangering"),
    document.getElementById("rushprofil"),
  ];

  try {
    const [a, b] = await Promise.all([
      fetch("/api/statistikk/operatorer", { cache: "no-store" }),
      fetch("/api/statistikk/rush", { cache: "no-store" }),
    ]);
    if (!a.ok || !b.ok) throw new Error("statistikk utilgjengelig");

    const operatorer = await a.json();
    const rush = await b.json();

    renderOperatorer(operatorer);
    renderRush(rush);
    renderHistFot(operatorer);
  } catch (error) {
    // Historikken er et tillegg. Feiler den, skal kartet stå som før - derfor
    // ingen setError() her, bare en linje i panelet den gjelder.
    bokser.forEach((boks) => {
      boks.innerHTML = '<p class="hist-tom">Fikk ikke hentet historikken.</p>';
    });
    document.getElementById("hist-fot").textContent = "";
  }
}

/* Faner. Én knapp av gangen er valgt, og panelene byttes med hidden i stedet
 * for display i CSS - da følger skjermlesere med på skiftet. */
const FANER = [
  { knapp: "tab-operatorer", panel: "fane-operatorer" },
  { knapp: "tab-rush", panel: "fane-rush" },
];

FANER.forEach(({ knapp }) => {
  document.getElementById(knapp).addEventListener("click", () => {
    FANER.forEach((fane) => {
      const valgt = fane.knapp === knapp;
      document.getElementById(fane.knapp).setAttribute("aria-selected", String(valgt));
      document.getElementById(fane.panel).toggleAttribute("hidden", !valgt);
    });
  });
});

// Sammenslåing. Panelet dekker en fjerdedel av kartet i høyden; den som vil
// se Nordland i stedet skal slippe å lete etter en måte å legge det bort.
const histToggle = document.getElementById("hist-toggle");
histToggle.addEventListener("click", () => {
  const kropp = document.getElementById("hist-body");
  const apen = kropp.hasAttribute("hidden");
  kropp.toggleAttribute("hidden", !apen);
  histToggle.setAttribute("aria-expanded", String(apen));
  histToggle.textContent = apen ? "–" : "+";
  histToggle.setAttribute(
    "aria-label",
    apen ? "Skjul historikkpanelet" : "Vis historikkpanelet"
  );
});

// På telefon deler historikken bunnsheetet med telleren og tegnforklaringen,
// og en utslått rushliste ville dyttet kartet ut av bildet. Der starter den
// sammenslått. Vi kaller knappen i stedet for å sette hidden selv, så det
// finnes bare én kodesti som kjenner tilstanden.
if (window.matchMedia("(max-width: 768px)").matches) {
  histToggle.click();
}

const histHelp = document.getElementById("hist-help");
histHelp.addEventListener("click", () => {
  const note = document.getElementById("hist-note");
  const apen = note.hasAttribute("hidden");
  note.toggleAttribute("hidden", !apen);
  histHelp.setAttribute("aria-expanded", String(apen));
});

/* --- Oppdateringsløkke ---------------------------------------------------- */

async function refresh() {
  try {
    const response = await fetch("/api/trains", { cache: "no-store" });
    const body = await response.json();

    if (!response.ok) {
      setError(body.error || `Serverfeil (${response.status})`);
      return;
    }

    latest = body.geojson;
    map.getSource("trains").setData(latest);

    // Valgt tog kan ha kommet fram og forsvunnet fra feeden. Da skal ikke ruta
    // bli stående alene - en strek til et tog som ikke er der lenger er nettopp
    // den «ser ut som data, men er det ikke» vi ellers jobber for å unngå.
    if (valgtTurId && !latest.features.some((f) => f.properties.id === valgtTurId)) {
      tommRute();
    }

    renderMeta(body.meta);
    if (input.value.trim()) runSearch();  // hold filteret i takt med nye data
    setError(body.stale ? `${body.error} — viser siste kjente posisjoner` : null);
  } catch (error) {
    setError("Mistet kontakt med serveren");
  }
}

let timer = null;
let histTimer = null;
let nyhetTimer = null;
let fhTimer = null;

function start() {
  refresh();
  timer = setInterval(refresh, CONFIG.refreshMs);

  // Historikken endrer seg i timesskala, ikke sekundskala. Backend cacher den
  // uansett i fem minutter, så tettere polling ville bare gitt samme svar.
  hentHistorikk();
  histTimer = setInterval(hentHistorikk, HIST_REFRESH_MS);

  // Driftsmeldingene ligger et sted imellom: de kommer og går i minuttskala,
  // og backend cacher dem i 60 sekunder. Tettere polling gir samme svar.
  hentNyheter();
  nyhetTimer = setInterval(hentNyheter, NYHET_REFRESH_MS);

  // Varmekartet endrer seg i døgnskala - det er medianer over en uke - så
  // dette er nesten bare her for at en fane som står åpen i et døgn ikke skal
  // vise gårsdagens tall. Hentes bare når laget faktisk er på.
  settFlaskehalser(fhBryter.checked);
  fhTimer = setInterval(() => {
    if (flaskehalserPaa) hentFlaskehalser();
  }, FH_REFRESH_MS);
}

// Ingen grunn til å hente data til en fane ingen ser på.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    clearInterval(timer);
    clearInterval(histTimer);
    clearInterval(nyhetTimer);
    clearInterval(fhTimer);
    timer = null;
    histTimer = null;
    nyhetTimer = null;
    fhTimer = null;
  } else if (!timer) {
    start();
  }
});
