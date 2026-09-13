"""Ser etter bokser som finnes på skrivebord, men ikke på telefon.

    pip install playwright && playwright install chromium
    python prober/sjekk_mobil.py                      # mot togkartet.no
    python prober/sjekk_mobil.py --url http://127.0.0.1:8000

Playwright står med vilje ikke i `requirements.txt`. Den er et verktøy for den
som ser på siden, ikke noe appen trenger for å kjøre — og den drar med seg
115 MB Chromium.

Hvorfor dette ikke er øyemål: på mobil ligger alle panelene i ett bunnsheet
med tre stopp, og appen møter deg i det midterste. Historikk og nyhetsstripe
ligger da under skjermkanten uten at noe er galt — de skal dras fram. Proben
måler derfor hver skjerm i opptil tre tilstander, og skiller tre utfall:

    MANGLER   ikke i DOM-en i det hele tatt
    SKJULT    i DOM-en, men uten layout (display:none eller [hidden])
    SYNLIG    har størrelse

Dommen er streng på det ene som betyr noe: en boks som er SYNLIG på
skrivebord skal være SYNLIG i minst én mobiltilstand.

`wait_until` er `domcontentloaded` og ikke `networkidle`. MapLibre henter
fliser så lenge kartet lever, så siden blir aldri stille — `networkidle`
henger til tidsavbruddet slår inn.
"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

# Boksene siden er bygget av. Rekkefølgen følger index.html.
BOKSER = [
    ("#info", "Telleren"),
    ("#donut", "Ringdiagrammet"),
    ("#breakdown", "Fordelingen per bånd"),
    ("#flaskehals-panel", "Flaskehalser"),
    ("#historikk", "Historikk (panelet)"),
    ("#hist-body", "Historikk (innholdet)"),
    ("#tab-operatorer", "Fanen Operatører"),
    ("#tab-rush", "Fanen Rushtid"),
    ("#rangering", "Operatørrangeringen"),
    ("#rushprofil", "Rushtidsprofilen"),
    ("#nyheter", "Nyhetsstripa"),
    ("#nyhet-vindu", "Meldingsvinduet"),
    ("#q", "Søkefeltet"),
    (".sheet-gripe", "Dragehåndtaket"),
]

SKJERMER = [
    ("skrivebord", 1440, 900, False),
    ("pixel-portrett", 412, 915, True),
    ("pixel-landskap", 915, 412, True),
    ("iphone-se", 375, 667, True),
]

MAAL = """(sel) => {
  const el = document.querySelector(sel);
  if (!el) return { tilstand: "MANGLER" };
  const r = el.getBoundingClientRect();
  const st = getComputedStyle(el);
  if (r.width === 0 || r.height === 0 || st.display === "none" ||
      st.visibility === "hidden") {
    return { tilstand: "SKJULT" };
  }
  return { tilstand: "SYNLIG", bredde: Math.round(r.width),
           hoyde: Math.round(r.height) };
}"""

SETT_SHEET = """(t) => {
  // Bredden avgjør om sheetet finnes i det hele tatt. Funksjonen er global
  // uansett, og returnerer stille på skrivebord - uten denne sjekken får
  // skrivebordet en «+oppe»-kolonne som bare er en kopi av seg selv.
  if (!window.matchMedia("(max-width: 768px)").matches) return false;
  if (typeof settSheetTilstand !== "function") return false;
  settSheetTilstand(t, false);
  return true;
}"""

RUSHFANEN = """() => {
  const t = document.getElementById("tab-rush");
  if (!t || t.getAttribute("aria-selected") === "true") return false;
  t.click();
  return true;
}"""

SLAA_UT = """() => {
  const b = document.getElementById("hist-toggle");
  if (!b || b.getAttribute("aria-expanded") === "true") return false;
  b.click();
  return true;
}"""


def maal_side(side, navn, utmappe):
    funn = {sel: side.evaluate(MAAL, sel) for sel, _ in BOKSER}
    side.screenshot(path=str(utmappe / f"{navn}.png"))
    return funn


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="https://togkartet.no")
    p.add_argument("--ut", default="prober/skjermbilder")
    args = p.parse_args()

    utmappe = Path(args.ut)
    utmappe.mkdir(parents=True, exist_ok=True)

    resultat = {}
    with sync_playwright() as pw:
        nettleser = pw.chromium.launch()
        for navn, b, h, touch in SKJERMER:
            ktx = nettleser.new_context(
                viewport={"width": b, "height": h},
                device_scale_factor=2 if touch else 1,
                is_mobile=touch,
                has_touch=touch,
            )
            side = ktx.new_page()
            print(f"  {navn}: laster", flush=True)
            side.goto(args.url, wait_until="domcontentloaded", timeout=30000)

            # Tallene kommer fra /api/trains. Vent til telleren har et tall,
            # ellers måler vi et skjelett.
            try:
                side.wait_for_function(
                    "() => { const e = document.querySelector('.count strong');"
                    " return e && e.textContent.trim().length > 0"
                    " && e.textContent.trim() !== '–'; }",
                    timeout=30000,
                )
            except Exception:
                print(f"  {navn}: ADVARSEL - telleren fylte seg ikke ut", flush=True)
            side.wait_for_timeout(2000)

            # 1. Slik siden møter deg.
            resultat[navn] = maal_side(side, navn, utmappe)
            print(f"  {navn}: målt", flush=True)


            # 2. Sheetet dratt helt opp. Returnerer False på skrivebord, der
            #    funksjonen ikke finnes fordi sheetet ikke gjør det.
            if side.evaluate(SETT_SHEET, "oppe"):
                side.wait_for_timeout(700)
                resultat[navn + "+oppe"] = maal_side(side, navn + "-oppe", utmappe)
                print(f"  {navn}: sheet oppe", flush=True)

                # 3. Og historikken slått ut. Vi kaller .click() i DOM-en og
                #    ikke Playwrights click(): den nekter når elementet ligger
                #    under skjermkanten, og vi måler layout, ikke om noe er
                #    treffbart med tommelen.
                if side.evaluate(SLAA_UT):
                    side.wait_for_timeout(700)
                    resultat[navn + "+hist"] = maal_side(
                        side, navn + "-historikk", utmappe
                    )
                    print(f"  {navn}: historikk slått ut", flush=True)

            # Rushtidsfanen ligger bak et klikk i begge oppsett, og fanene
            # ligger inne i #hist-body. Derfor til slutt: nå er panelet
            # utslått i den tilstanden skjermen kan ha det.
            if side.evaluate(RUSHFANEN):
                side.wait_for_timeout(600)
                resultat[navn + "+rush"] = maal_side(side, navn + "-rush", utmappe)
                print(f"  {navn}: rushfanen", flush=True)
            ktx.close()
        nettleser.close()

    bredde = max(len(n) for n in resultat)
    print()
    print(f"{'boks':<26}" + "  ".join(f"{n:<{bredde}}" for n in resultat))
    print("-" * (26 + (bredde + 2) * len(resultat)))
    for sel, navn in BOKSER:
        rad = "  ".join(f"{resultat[s][sel]['tilstand']:<{bredde}}" for s in resultat)
        print(f"{navn:<26}" + rad)

    # Dommen: synlig på skrivebord, men ikke i NOEN mobiltilstand.
    fasit = resultat["skrivebord"]
    mobile = [n for n in resultat if n != "skrivebord"]
    avvik = []
    for sel, navn in BOKSER:
        if sel == ".sheet-gripe":
            continue  # finnes bare på mobil, med vilje
        if fasit[sel]["tilstand"] != "SYNLIG":
            continue
        if not any(resultat[m][sel]["tilstand"] == "SYNLIG" for m in mobile):
            sett = sorted({resultat[m][sel]["tilstand"] for m in mobile})
            avvik.append(f"{navn}: SYNLIG på skrivebord, {'/'.join(sett)} på telefon")

    print()
    print(f"Skjermbilder: {utmappe.resolve()}")
    if avvik:
        print()
        print("AVVIK:")
        for a in avvik:
            print(f"  - {a}")
        return 1
    print()
    print("Hver boks som er synlig på skrivebord er synlig på telefon også,")
    print("i minst én av bunnsheetets tilstander.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
