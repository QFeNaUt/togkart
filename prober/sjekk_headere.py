"""Verifiser at sikkerhetsheaderne faktisk sendes - på hvert eneste svar.

Kjør:  python prober/sjekk_headere.py

Verken nett eller database. Appen bygges i minnet med Starlettes testklient,
og lifespan startes aldri - ingen vedlikeholdsjobb, ingen historikk.db, ingen
Entur. Derfor kan den stå i CI.

Hvorfor en probe og ikke bare en kodelesing: en policy man ikke kan teste, er
en policy man tror man har. De to måtene dette kan være stille feil på er

  1. **Headeren mangler et sted.** Middleware registrert i feil rekkefølge, en
     rute som svarer før den, et unntak noen la inn for å få noe til å virke.
     Sjekk 1 og 2 går derfor gjennom en HTML-side, et API-svar og en 404.

  2. **Policyen sier noe annet enn den skal.** En CSP med en skrivefeil i et
     direktivnavn blir stilltiende ignorert av nettleseren - `frame-ancestor`
     i stedet for `frame-ancestors` gir ingen advarsel noe sted. Sjekk 3
     leser hvert direktiv og krever at det står på lista over kjente navn.

Sjekk 5 er den som fanger drift over tid: CSP-en i `app.py` og CSP-en i
`drift/cloudflare.md` skal være ordrett den samme. To steder som beskriver
samme policy er én for mye, og den dagen de spriker er det dokumentasjonen
som lyver.

**Kjør den to ganger.** Sjekk 4 har to grener, og bare den ene kjøres av
gangen:

    python prober/sjekk_headere.py
    TOGKART_MILJO=prod python prober/sjekk_headere.py

Den andre er den som betyr noe. Unntaket for Swagger-sidene finnes bare i
utvikling, og påstanden «ute sendes CSP-en på hvert eneste svar, uten unntak»
er ikke verifisert før den har kjørt med prod-miljøet satt. CI kjører begge.
"""

import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app import CSP, SIKKERHETSHEADERE, app

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FEIL = 0

# Alle direktivnavn CSP nivå 3 kjenner. Lista finnes fordi en skrivefeil i et
# direktivnavn ikke er en feil for nettleseren - den hopper bare over det.
KJENTE_DIREKTIV = {
    "default-src", "script-src", "script-src-elem", "script-src-attr",
    "style-src", "style-src-elem", "style-src-attr", "img-src", "font-src",
    "connect-src", "media-src", "object-src", "child-src", "frame-src",
    "worker-src", "manifest-src", "prefetch-src", "frame-ancestors",
    "form-action", "base-uri", "sandbox", "report-uri", "report-to",
    "upgrade-insecure-requests", "block-all-mixed-content",
    "require-trusted-types-for", "trusted-types",
}


def si(ok, tittel, detalj=""):
    global FEIL
    if not ok:
        FEIL += 1
    print(f"{tittel:<34} {'OK  ' if ok else 'FEIL'} {detalj}")


klient = TestClient(app)


print("\n1. Headerne står på hvert svar\n")

# Forsiden, et API-svar som ikke rører nett, og en sti som ikke finnes.
# /api/health kaller get_snapshot() og kan gå mot Entur - den er utelatt med
# vilje, så proben holder løftet om å være uten nett.
for sti, forventet in (("/", 200), ("/openapi.json", None), ("/finnes-ikke", 404)):
    svar = klient.get(sti)
    if forventet is not None and svar.status_code != forventet:
        si(False, f"{sti}", f"ga {svar.status_code}, ventet {forventet}")
        continue
    mangler = [n for n in SIKKERHETSHEADERE if n not in svar.headers]
    # CSP-en er med vilje utelatt på dokumentasjonssidene.
    if sti == "/openapi.json":
        mangler = [n for n in mangler if n != "Content-Security-Policy"]
    si(not mangler, f"{sti} ({svar.status_code})",
       f"mangler {', '.join(mangler)}" if mangler else "alle fem")


print("\n2. Verdiene er de vi mener\n")

svar = klient.get("/")
for navn, ventet in SIKKERHETSHEADERE.items():
    fikk = svar.headers.get(navn, "")
    si(fikk == ventet, navn, "" if fikk == ventet else f"fikk «{fikk[:60]}»")


print("\n3. CSP-en er velformet\n")

direktiv = {}
for bit in [d.strip() for d in CSP.split(";") if d.strip()]:
    navn, _, resten = bit.partition(" ")
    direktiv[navn] = resten.strip()

ukjente = sorted(n for n in direktiv if n not in KJENTE_DIREKTIV)
si(not ukjente, "alle direktivnavn er kjente",
   f"ukjent: {', '.join(ukjente)}" if ukjente else f"{len(direktiv)} direktiv")

si("default-src" in direktiv, "default-src finnes",
   "uten den gjelder ingenting for det du glemte")

# Den viktigste enkeltlinja, og fra 23. august gjelder den style-src også:
# datafargene i app.js er klasser nå, ikke style-attributter.
for felt in ("script-src", "style-src"):
    verdi = direktiv.get(felt, "")
    si("'unsafe-inline'" not in verdi and "'unsafe-eval'" not in verdi,
       f"{felt} uten unsafe-*", f"«{verdi}»")

si(direktiv.get("frame-ancestors") == "'none'", "frame-ancestors 'none'",
   direktiv.get("frame-ancestors", "mangler"))
si(direktiv.get("object-src") is None or direktiv.get("object-src") == "'none'",
   "object-src er none eller arvet", direktiv.get("object-src", "arver default-src"))

# MapLibre starter web workers fra en blob-URL. Uten dette tegner kartet ikke
# en eneste flis, og feilen er stille i alt annet enn konsollen.
si(direktiv.get("worker-src") == "blob:", "worker-src blob:",
   direktiv.get("worker-src", "mangler - kartet blir svart"))


print("\n4. Ingen unntak lekker ut i produksjon\n")

# Unntaket for Swagger gjelder bare stier som ikke finnes når appen står ute.
# Er docs slått av, skal ingen sti i det hele tatt slippe unna CSP-en.
from app import ER_PROD, _UTEN_CSP  # noqa: E402

if ER_PROD:
    si(not _UTEN_CSP, "ingen unntak i det hele tatt", f"{len(_UTEN_CSP)} stier")
    si("Content-Security-Policy" in klient.get("/openapi.json").headers,
       "selv 404 får CSP", "ute gjelder policyen hvert svar")
else:
    si(bool(_UTEN_CSP), "docs-stiene finnes (utvikling)",
       f"unntatt CSP: {len(_UTEN_CSP)} stier")
    si(all(s.startswith("/api/") or s == "/openapi.json" for s in _UTEN_CSP),
       "unntaket er avgrenset", "bare dokumentasjonsstier")
    print("\n   Kjør på nytt med TOGKART_MILJO=prod for å se prod-grenen.")


print("\n5. app.py og drift/cloudflare.md sier det samme\n")

rot = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
doktekst = io.open(os.path.join(rot, "drift", "cloudflare.md"), encoding="utf-8").read()

# Policyen står i doket som én linje i en kodeblokk, kjent på default-src.
linjer = [l.strip() for l in doktekst.splitlines() if l.strip().startswith("default-src")]
si(len(linjer) == 1, "policyen står ett sted i doket", f"fant {len(linjer)}")

if len(linjer) == 1:
    normaliser = lambda t: re.sub(r"\s+", " ", t).strip().rstrip(";")
    si(normaliser(linjer[0]) == normaliser(CSP), "ordrett lik CSP-en i app.py",
       "" if normaliser(linjer[0]) == normaliser(CSP) else "\n  dok: " + linjer[0] + "\n  app: " + CSP)


print()
if FEIL:
    print(f"{FEIL} feil.")
    sys.exit(1)
print("Alt grønt.")
