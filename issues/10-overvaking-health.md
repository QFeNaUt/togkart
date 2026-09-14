---
title: "Overvåking: ingen ser på /api/health"
labels: drift
---

Fortsatt åpen. Endepunktet er klart; det er overvåkeren som mangler.

**Hva endepunktet gir** (siden 23. august, og med bakgrunnsjobben siden 14.
september):

- **503 når `ok` er usann.** Statuskoden alene er nok som kriterium.
- **`alderSekunder`** skiller «nede nå» fra «har stått en stund».
- **`poller.sisteOkSekunderSiden`** vesentlig over `poller.intervallSekunder`
  betyr at bakgrunnsjobben står, selv om appen svarer.
- **Ett minutt er en trygg frekvens.** Bakgrunnsjobben holder cachen varm, så
  en sjekk koster nesten aldri en Entur-henting.

**Rettet 14. september:** denne saken sa at Uptime Kuma i LXC 102 «sjekker
innenfra, men ser ikke om tunnelen er nede». Begge delene var feil:

1. **Det finnes ingen «innenfra».** Uvicorn binder `127.0.0.1` inne i LXC 106,
   så appen kan ikke nås fra en annen container. Uptime Kuma må bruke
   `https://togkartet.no/api/health`.
2. **Den adressen går gjennom hele kjeden**: ut på internett, gjennom
   Cloudflare og tilbake gjennom tunnelen. Uptime Kuma ser altså at tunnelen er
   nede.

Den virkelige begrensningen er en annen: **en overvåker i samme hus kan ikke
melde at huset er borte.** Ved strømbrudd, linjebrudd eller en vert som ikke
kommer opp igjen, forsvinner Uptime Kuma sammen med alt den skulle passe på.

**Det som gjenstår:**

1. **Uptime Kuma:** HTTP(s) mot `https://togkartet.no/api/health`, intervall
   60 s, godtatt status `200-299`, og **en varsling** — ellers er det bare et
   dashbord ingen ser på.
2. **Én sjekk utenfor huset**, for tilfellet over. Cloudflare Health Checks
   (Traffic → Health Checks) eller en gratis ekstern tjeneste. Om Cloudflare
   sin er tilgjengelig på Free-planen, er ikke verifisert.
3. **Se begge bli røde.** `systemctl stop togkart` i LXC 106, vent på varsel,
   start igjen. En helsesjekk som aldri har vært rød, er en helsesjekk du ikke
   vet noe om.

Oppskriften står i `drift/tunnel-og-tjeneste.md` punkt 6.

Relatert, og fanget samme dag: LXC 105 og 106 sto uten `onboot`, så en omstart
av verten ville latt begge nettstedene ligge av. Rettet — se punkt 0 samme sted.
