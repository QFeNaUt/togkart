---
title: "Tegnforklaringen «Forsinkelse» tilbake, under ringdiagrammet"
labels: enhancement, frontend
---
Gradientstripa nederst til venstre ble tatt ut 20. august for å gi plass til
nyhetsstripa. Den er ikke slettet fordi den var feil — den forklarte fargene
på kartprikkene, og det er fortsatt en reell oppgave. Den tapte en
plasskonkurranse, og hjørnet var det eneste ledige.

Argumentet for å ta den ut: en gradient fra grønt til rødt med «0 / 4 min /
6 min / 15 min+» under er nesten selvforklarende for den som allerede ser
prikkene, og ringdiagrammet i `#info` sier det samme med tall og har sin egen
tooltip per bånd. Argumentet mot: fargene på kartet er nå den eneste
forklaringen på seg selv, og skalaen er ikke lineær.

Skal den fram igjen, er markupen denne — den satt i `#sheet` i `index.html`,
etter historikkpanelet:

```html
<div id="legend" class="panel">
  <div class="legend-head">
    <div class="subtitle">Forsinkelse</div>
    <button class="info-badge" type="button" id="legend-help"
            aria-expanded="false" aria-controls="legend-note"
            aria-label="Slik leser du kartet">i</button>
  </div>
  <div class="legend-bar"></div>
  <div class="legend-labels">
    <span>0</span><span>4 min</span><span>6 min</span><span>15 min+</span>
  </div>
  <div class="legend-note" id="legend-note" hidden>
    Hvert punkt er ett tog som rapporterer posisjon nå. Fargen viser
    avvik fra rutetabell. Grensen ved 4 minutter følger den norske
    punktlighetsdefinisjonen for lokaltog. Grå punkter mangler
    forsinkelsesdata. Skalaen er ikke lineær.
  </div>
</div>
```

Tre ting som må følge med:

- **CSS-reglene** `#legend`, `.legend-head`, `.legend-bar` og `.legend-labels`
  ble fjernet fra `app.css`. `.legend-note` og `.info-badge` står igjen —
  historikkpanelet og nyhetsstripa bruker dem begge.
- **JS-handleren** for `legend-help` er borte. Den var fire linjer, og den nye
  `nyhet-help` rett under er den samme koden med et annet id.
- **Mobilregelen trenger ingenting.** Den navnga paneler med id fram til 23.
  august — og der sto `#nyheter` skrevet inn på plassen `#legend` hadde. Nå
  treffer den `#sheet .panel`, så et panel som legges inn i sheetet blir flatet
  ut uten at noen må huske å skrive det opp.

Den enkleste veien tilbake er ikke det gamle hjørnet, som nå er opptatt:
tallene «0 / 4 / 6 / 15 min+» kunne stått som en tynn stripe **under
ringdiagrammet** i `#info`, der båndene allerede er navngitt. Da forklarer
fargene seg der de telles, og hjørnet forblir nyhetsstripas.

