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
# runuser ligger i util-linux og er alltid der. Og koden må hentes som
# `togkart`, ikke root - ellers eier root plutselig filer i et tre som
# systemd-tjenesten kjører under en annen bruker.
foer=$(git rev-parse HEAD)
runuser -u "$BRUKER" -- git pull --ff-only
etter=$(git rev-parse HEAD)

if [ "$foer" = "$etter" ]; then
    echo "Ingen nye innsjekkinger. Starter om likevel."
else
    echo "Oppdatert: $(git log --oneline "$foer..$etter" | wc -l) innsjekking(er)"
    git log --oneline "$foer..$etter"
fi

# Bare når fila faktisk er endret. Et `pip install` på hver utrulling er
# et nettverkskall og en risiko uten gevinst.
if ! git diff --quiet "$foer" "$etter" -- requirements.txt; then
    echo "requirements.txt er endret - installerer på nytt"
    runuser -u "$BRUKER" -- "$ROT/.venv/bin/pip" install -q -r requirements.txt
fi

systemctl restart togkart

echo -n "Venter på at appen svarer"
for _ in $(seq 1 "$VENT"); do
    if svar=$(curl -fsS -m 5 "$HELSE" 2>/dev/null); then
        echo
        echo "$svar"
        echo
        echo "OK - $(git rev-parse --short HEAD) er ute."
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
