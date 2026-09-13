---
title: "Spøkelseshypotesen: blåser delay opp for parkerte tog?"
labels: undersokelse, data
---

Rest av problem 1. Kveldskjøringen 19. august kl. 23:43 ga to oppblåste
ferdige tog (RE20 398, R13 1671) — begge små, men det er første observerte
tilfelle, og det motsier ordrett fullskalamålingen tidligere samme dag.

Kjør mellom 22 og 24, i samme vindu, når mange tog er parkert:

```powershell
python prober/sjekk_punktlighet.py
python sjekk.py alle
```

To ting til som mangler tall:

- **Flytoget-observasjonen på et større utvalg.** To tog beviser ingenting. Er
  den ekte, er det ikke `delay` som er upålitelig, men Vys `delay` — og det er
  en annen og mer håndterbar sak.
- **Go-Ahead er fortsatt umålt.** 4 tog i feeden, null i utvalget på 20.

Se `docs/undersokelser.md`, problem 1.
