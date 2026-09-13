# TogKart i container.
#
#     docker compose build
#     docker compose up -d
#
# Fire valg som er verdt å kjenne før du endrer noe her:
#
# 1. Python 3.13, ikke 3.14 som i .venv lokalt. Hele kjøreveien kompilerer
#    under 3.11 (målt: `py -3.11 -m compileall` på alle fjorten modulene gikk
#    gjennom), så det er ingenting i koden som krever 3.14. 3.13 er derimot
#    den nyeste versjonen der uvloop og httptools - som følger med
#    `uvicorn[standard]` - har ferdige hjul for linux/amd64. Uten hjul bygges
#    de fra kilde, og det finnes ingen kompilator i `-slim`.
#
# 2. Bindingen er 0.0.0.0, ikke 127.0.0.1 som drift/README.md punkt 5 sier.
#    Den anvisningen er skrevet for uvicorn under systemd, rett på verten, og
#    der er den riktig. Inne i en container er containergrensen isolasjonen:
#    binder du til loopback der, er det ingenting - heller ikke cloudflared -
#    som når appen. `compose.yaml` publiserer ingen port, så resultatet er
#    like utilgjengelig utenfra som en loopback-binding på verten.
#
# 3. TZ=Europe/Oslo styrer BARE tidsstempler i loggen. All logikk som lagrer
#    eller regner er eksplisitt tidssonebevisst - `ZoneInfo("Europe/Oslo")` i
#    analyse.py og vedlikehold.py, UTC i historikk.py - så døgnene i arkivet
#    blir de samme uansett hva TZ står til. Men en logglinje i UTC ved siden
#    av en vedlikeholdsjobb som kjører klokka fire norsk tid er en unødig
#    hoderegning klokka tre om natta.
#
# 4. Én arbeider. `--workers 2` ville gitt to prosesser som hver har sin egen
#    cache, sin egen strupe-teller og sin egen bakgrunnsjobb: dobbelt så mange
#    kall mot Entur, halv effekt av ratebegrensningen, og to skrivere til
#    historikk.db. Skal appen skaleres, skal cachene ut av prosessen først.

FROM python:3.13-slim

# Tidssonetabellene til libc. Python-pakken `tzdata` i requirements.txt dekker
# `zoneinfo`, men ikke `time.localtime()` - og det er den logging bruker. Uten
# denne står loggen i UTC uansett hva TZ settes til.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV TZ=Europe/Oslo \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Alt kjøres fra prosjektroten. Står containeren i feil mappe, feiler uvicorn
# med `Directory 'static' does not exist`.
WORKDIR /app

# Avhengighetene i sitt eget lag, før koden: de endrer seg sjelden, koden
# ofte. Snur du rekkefølgen, installeres alt på nytt hver gang app.py røres.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Egen bruker uten hjemmeområde og uten skall. Appen skriver til nøyaktig ett
# sted - historikk.db på bind mountet - og uid-en må eie den katalogen på
# verten:  sudo chown -R 10001:10001 /var/lib/togkart
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin togkart

# `.dockerignore` holder .venv, databasen, .env, dokumentasjonen og
# byggeskriptene ute. `prober/` og `sjekk.py` blir med med vilje: runbooken i
# docs/feilsoking.md er skrevet rundt dem, og de er det du vil ha tilgjengelig
# inne i containeren når noe ser rart ut.
COPY --chown=togkart:togkart . .

USER togkart

EXPOSE 8000

# /api/health svarer 503 når `ok` er usann - det var nettopp poenget med
# endringen 23. august - så statuskoden alene er kriterium nok.
#
# `start-period` er 90 s fordi kartet er tomt til første Entur-henting går
# gjennom, og en kald helsesjekk er målt til 2,3 sekunder. `interval` på 60 s
# er trygt: sjekken henter bare fra Entur når tallene er eldre enn
# TOGKART_HELSE_TTL (fem minutter), resten er varm cache på 1,5 ms.
#
# Merk at Docker ikke starter en container på nytt fordi den er `unhealthy`.
# Dette er et signal til `docker ps` og til deg, ikke en selvhelbredelse.
HEALTHCHECK --interval=60s --timeout=15s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8000/api/health', timeout=10.0).status_code == 200 else 1)"]

CMD ["uvicorn", "app:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--no-server-header"]
