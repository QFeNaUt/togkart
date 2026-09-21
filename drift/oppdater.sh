#!/usr/bin/env bash
#
# Oppdater TogKart i produksjon. Kjøres som root i LXC 106:
#
#     /opt/togkart/drift/oppdater.sh
#
# Tre steg som må skje i riktig rekkefølge, og som er lette å ta feil av for
# hånd: hent koden som riktig bruker, installer avhengigheter BARE hvis
# requirements.txt faktisk er rørt, og verifiser at appen kom opp igjen.
#
# Det siste er hele poenget. En `systemctl restart` returnerer med én gang og
# sier ingenting om hvorvidt appen svarer - og /api/health er 503 til første
# Entur-henting er i havn. Skriptet venter til den er 200, eller feiler høyt.

set -euo pipefail

ROT=/opt/togkart
BRUKER=togkart
HELSE=http://127.0.0.1:8000/api/health
VENT=60

cd "$ROT"

# `runuser` og ikke `sudo`: sudo er ikke installert i et minimalt Debian-image,
# runuser ligger i util-linux og er alltid der.
#
# ALLE git-kall går som `togkart`, ikke bare `pull`. Første utgave av dette
# skriptet kjørte `rev-parse` og `log` som root, og da stoppet git med
# «detected dubious ownership»: repoet eies av togkart, og git nekter å kjøre
# i et tre som eies av noen andre enn den som kaller.
#
# Fristelsen er å legge inn `safe.directory` for root. Ikke gjør det - da kan
# root legge igjen root-eide filer i .git, og neste `git pull` som togkart
# feiler på rettigheter i stedet. Kjør som eieren.
g() { runuser -u "$BRUKER" -- git "$@"; }

foer=$(g rev-parse HEAD)
g pull --ff-only
etter=$(g rev-parse HEAD)

if [ "$foer" = "$etter" ]; then
    echo "Ingen nye innsjekkinger. Starter om likevel."
else
    echo "Oppdatert: $(g log --oneline "$foer..$etter" | wc -l) innsjekking(er)"
    g log --oneline "$foer..$etter"
fi

# Bare når fila faktisk er endret. Et `pip install` på hver utrulling er
# et nettverkskall og en risiko uten gevinst.
if ! g diff --quiet "$foer" "$etter" -- requirements.txt; then
    echo "requirements.txt er endret - installerer på nytt"
    runuser -u "$BRUKER" -- "$ROT/.venv/bin/pip" install -q -r requirements.txt
fi

# Unit-fila følger IKKE med en `git pull`. Den skal ligge i
# /etc/systemd/system/, og en endring i drift/ når den aldri før noen kopierer
# den dit. Fram til 21. september gjorde ikke dette skriptet det, og samme dag
# ble MemoryHigh/MemoryMax rullet ut uten å bli tatt i bruk: utrullingen så
# vellykket ut, appen svarte 200, og vernet fantes ikke.
#
# `cmp -s` og ikke en ubetinget kopi: en daemon-reload på hver utrulling er
# støy, og forskjellen er det eneste som er verdt å si fra om.
UNIT=/etc/systemd/system/togkart.service
if ! cmp -s "$ROT/drift/togkart.service" "$UNIT"; then
    echo "togkart.service er endret - installerer den"

    # Verifiser FØR kopiering. En unit som ikke parser gir en tjeneste som
    # ikke starter, og da er det for sent at helsesjekken nedenfor sier fra.
    # Det var dette som fanget den manglende [Unit]-linja 13. september.
    if ! systemd-analyze verify "$ROT/drift/togkart.service"; then
        echo "FEIL: drift/togkart.service passerer ikke systemd-analyze verify." >&2
        echo "Unit-fila er IKKE installert, og tjenesten er urørt." >&2
        exit 1
    fi

    install -m 0644 "$ROT/drift/togkart.service" "$UNIT"
    systemctl daemon-reload
fi

systemctl restart togkart

echo -n "Venter på at appen svarer"
for _ in $(seq 1 "$VENT"); do
    if svar=$(curl -fsS -m 5 "$HELSE" 2>/dev/null); then
        echo
        echo "$svar"
        echo
        # Kjernens tall, ikke systemds. `systemctl show -p MemoryMax` gjengir
        # det som STÅR i unit-fila, ikke det cgruppen faktisk håndhever - og i
        # en unprivilegert LXC er det ikke gitt at de er like.
        tak=$(cat /sys/fs/cgroup/system.slice/togkart.service/memory.max 2>/dev/null || echo ukjent)
        echo "minnetak håndhevet av kjernen: $tak"

        echo "OK - $(g rev-parse --short HEAD) er ute."
        exit 0
    fi
    echo -n "."
    sleep 1
done

echo
echo "FEIL: $HELSE ble ikke 200 innen $VENT sekunder." >&2
echo "Merk at 503 er RIKTIG svar så lenge første Entur-henting ikke er i havn." >&2
echo "De siste linjene fra loggen:" >&2
journalctl -u togkart -n 30 --no-pager >&2
exit 1
