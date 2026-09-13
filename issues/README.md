# Issue-kropper til import

Midlertidig mappe. Hver `NN-*.md` er én oppgave hentet ut av den gamle
`STATUS.md`, klar til å bli et GitHub-issue.

Løste saker er fjernet herfra etter hvert:

- **01** «Tog som mangler i kartet» — 21. august
- **02** «SJ-dekning: 12 av Bane NORs 16 SJN-tog» — 22. august
- **03** «11 av 73 tog lå over 10 km fra Bane NORs posisjon» — 22. august.
  Erstattet av en smalere sak om selve ommålingen; hypotese 1 er avlivet og
  målestokken etablert, men kjernespørsmålet står.
- **05** «Mobilvisningen er en fast stripe, ikke et bunnsheet du kan dra i» —
  23. august.
- **06** «Klyngene teller spøkelsestog uten å vise det» — 23. august.
- **07** «SRI-hash på MapLibre, eller last ned filene til static/» —
  23. august. Filene ble lastet ned; SRI ble ikke nødvendig.
- **09** «Seks inline-stiler hindrer style-src 'self'» — 23. august. Det var
  sju.
- **11** «To HTTP-biblioteker: samle på httpx» — 23. august. `smoketest.py`
  var ikke arkivert, den er den første kommandoen i README.
- **12** «ask() finnes i fem utgaver — samle i prober/felles.py» — 24. august.
  Én av de fem sjekket ikke `errors`, og løy derfor om skjemaet.

Alle er dokumentert i [CHANGELOG.md](../CHANGELOG.md) og
[docs/undersokelser.md](../docs/undersokelser.md).

## Slik importeres de

```powershell
gh auth login
.\issues\opprett-issues.ps1 -TorrKjoring   # se hva som ville skjedd
.\issues\opprett-issues.ps1                # opprett på ekte
```

Skriptet oppretter etikettene først (idempotent, `--force`), og deretter ett
issue per fil i filnavnrekkefølge.

Kroppene viser til hverandre med **tittel**, ikke med `#`-nummer, så
rekkefølgen betyr ingenting og skriptet kan kjøres mot et repo som allerede
har issues. Det sto `#`-numre her til å begynne med; de brøt i det øyeblikket
den første saken ble lukket og filen fjernet.

## Etikettene

| Etikett | Betyr |
|---|---|
| `bug` | Noe er galt |
| `enhancement` | Ny funksjonalitet |
| `data` | Datakvalitet og kilder |
| `frontend` | Kart, lag og grensesnitt |
| `sikkerhet` | Sikkerhet |
| `drift` | Drift, utrulling og overvåking |
| `opprydding` | Teknisk gjeld |
| `undersokelse` | Måling mangler, ikke kode |
| `verifisering` | Skal kjøres og bekreftes |
| `prioritet-hoy` | Tas først |

## Etterpå

**Slett denne mappa.** To steder å vedlikeholde er ett for mye — det var
nettopp den feilen `STATUS.md` gjorde.
