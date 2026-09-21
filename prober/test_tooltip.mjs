/* Regresjonstest for tooltipen i panelene. Krever ikke nett, ikke database,
 * og ingen nettleser.
 *
 * Kjøres fra prosjektroten:
 *     node prober/test_tooltip.mjs
 *
 * Bakgrunnen
 * ----------
 * Forklaringen på medianen ble lagt inn 21. september 2026 og virket på PC.
 * På en Galaxy S24 Ultra skjedde ingenting når man trykket på tallet. Årsaken
 * var at hele tooltipen hang på hover, og en telefon har ingen. Det gjaldt
 * ringdiagrammet og fordelingsradene like mye — de hadde hatt samme
 * begrensning siden de ble laget, uten at noen sa fra.
 *
 * Grunnen til at ingen sa fra er verdt å merke seg, for den gjelder fortsatt:
 * **trykkveien er usynlig fra en PC.** Den kan brytes av en hvilken som helst
 * endring i lytterne uten at noe ser galt ut for den som gjør endringen.
 * Derfor står den her og ikke bare i et minne.
 *
 * Hva som testes
 * --------------
 * Seksjonen i app.js kjøres mot en DOM som er stor nok til å boble hendelser,
 * og de tre inngangene måles hver for seg:
 *
 *     berøring   trykk åpner, nytt trykk lukker, trykk utenfor rydder
 *     mus        hover åpner og følger markøren, klikk er en nullitet
 *     tastatur   fokus åpner i begge verdener
 *
 * Den viktigste enkeltpåstanden er at de SYNTETISKE museventene en telefon
 * sender etter et trykk ikke får røre noe. Gjør de det, lukker tooltipen seg
 * i samme bevegelse som åpnet den, eller legger seg under fingeren.
 */

import fs from "node:fs";

const src = fs.readFileSync(new URL("../static/app.js", import.meta.url), "utf8");
const START = 'const tip = document.getElementById("tip");';
const SLUTT = 'map.on("movestart", skjulTip);';

const a = src.indexOf(START);
const b = src.indexOf(SLUTT, a);
if (a < 0 || b < 0) {
  console.error("Fant ikke tooltip-seksjonen i app.js. Er ankrene endret?");
  process.exit(1);
}
const seksjon = src.slice(a, b + SLUTT.length);

/* En DOM som kan akkurat nok: slektskap, closest(), contains() og bobling.
 * Ikke jsdom — et helt DOM-bibliotek for fire hendelsestyper er en
 * avhengighet CI må installere for å teste tretti linjer. */
let harHover = false;

class El {
  constructor(dataset = {}, forelder = null) {
    this.dataset = dataset;
    this.forelder = forelder;
    this.lyttere = {};
    this.hidden = true;
    this.style = {};
    this.innerHTML = "";
  }
  addEventListener(type, fn) {
    (this.lyttere[type] = this.lyttere[type] || []).push(fn);
  }
  getBoundingClientRect() {
    return { left: 10, top: 20, right: 60, bottom: 40, width: 50, height: 20 };
  }
  closest(velger) {
    const nokler = velger
      .split(",")
      .map((s) => s.trim().replace(/^\[data-(.+)\]$/, "$1"));
    let n = this;
    while (n) {
      if (n.dataset && nokler.some((k) => n.dataset[k] !== undefined)) return n;
      n = n.forelder;
    }
    return null;
  }
  contains(annen) {
    let n = annen;
    while (n) {
      if (n === this) return true;
      n = n.forelder;
    }
    return false;
  }
}

const doc = new El();
const sheet = new El({}, doc);
const rad = new El({}, sheet);
const median = new El({ tip: "median" }, rad);
const utenfor = new El({}, sheet);
const tipEl = new El({}, doc);

doc.getElementById = (id) => (id === "tip" ? tipEl : null);

/* `matches` må være LEVENDE. En ekte MediaQueryList oppdaterer seg selv, og
 * koden leser HAR_HOVER.matches ved hver hendelse nettopp derfor. Returnerte
 * stubben en fryst verdi, ville testen målt seg selv i stedet for koden -
 * og det gjorde den i første utkast. */
const win = {
  matchMedia: (q) => ({
    get matches() {
      return q === "(hover: hover)" ? harHover : false;
    },
  }),
  addEventListener() {},
  innerWidth: 412,
  innerHeight: 915,
};

function fyr(mal, type, ekstra = {}) {
  const ev = { target: mal, clientX: 5, clientY: 5, relatedTarget: null, ...ekstra };
  let n = mal;
  while (n) {
    (n.lyttere[type] || []).forEach((fn) => fn(ev));
    n = n.forelder;
  }
}

new Function(
  "document", "window", "sheet", "map", "BANDS", "fargeklasse", "bandIntervall",
  seksjon
)(doc, win, sheet, { on() {} }, [], () => "", () => "");

let feil = 0;
const vist = () => !tipEl.hidden;

function sjekk(navn, faktisk, ventet) {
  if (faktisk === ventet) {
    console.log(`  OK   ${navn}`);
  } else {
    console.log(`  FEIL ${navn} — fikk ${JSON.stringify(faktisk)}, ventet ${JSON.stringify(ventet)}`);
    feil++;
  }
}

function testBeroring() {
  console.log("berøring — en telefon har ingen hover");
  harHover = false;

  fyr(median, "click");
  sjekk("trykk åpner forklaringen", vist(), true);
  // Ved elementet (left 10 + TIP_AVSTAND 14), ikke ved fingeren på clientX 5.
  sjekk("plassert ved tallet, ikke under fingeren", tipEl.style.left, "24px");

  fyr(median, "click");
  sjekk("nytt trykk på samme tall lukker", vist(), false);
  fyr(median, "click");
  sjekk("og et tredje åpner igjen", vist(), true);
  fyr(utenfor, "click");
  sjekk("trykk utenfor rydder bort", vist(), false);

  // Det som var selve feilen: syntetiske musehendelser skal ikke røre noe.
  fyr(median, "click");
  fyr(median, "mouseout", { relatedTarget: utenfor });
  sjekk("syntetisk mouseout lukker den ikke", vist(), true);
  fyr(median, "mousemove", { clientX: 300, clientY: 600 });
  sjekk("syntetisk mousemove flytter den ikke", tipEl.style.left, "24px");
  fyr(utenfor, "click");
}

function testMus() {
  console.log("\nmus — hover eier tooltipen, trykk rører den ikke");
  harHover = true;

  fyr(median, "mouseover", { clientX: 100, clientY: 200 });
  sjekk("hover åpner", vist(), true);
  sjekk("plassert ved markøren", tipEl.style.left, "114px");
  fyr(median, "mousemove", { clientX: 120, clientY: 200 });
  sjekk("følger markøren", tipEl.style.left, "134px");
  fyr(median, "mouseout", { relatedTarget: utenfor });
  sjekk("mouseout skjuler", vist(), false);
  fyr(median, "click");
  sjekk("klikk er en nullitet når hover finnes", vist(), false);
}

function testTastatur() {
  console.log("\ntastatur — skal virke i begge verdener");
  for (const h of [true, false]) {
    harHover = h;
    fyr(median, "focusin");
    sjekk(`fokus åpner (hover: ${h})`, vist(), true);
    fyr(median, "focusout");
    sjekk(`fokus ut skjuler (hover: ${h})`, vist(), false);
  }
}

console.log("tooltipen på berøring, mus og tastatur — regresjonstest\n");
testBeroring();
testMus();
testTastatur();

if (feil) {
  console.log(`\n${feil} feil.`);
  process.exit(1);
}
console.log("\nAlt grønt.");
