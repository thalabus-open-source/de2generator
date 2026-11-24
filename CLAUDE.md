# CLAUDE.md – Branch dev-client-implementation

## Projekt
de²generator (t2b AG) ist ein OpenAI-kompatibler Proxy vor dem LLM.
Dieser Branch ergänzt einen minimalen Client (Bachelorarbeit).
Kette: Client → de²generator → LiteLLM → Modell.
Plan und Status stehen in doc/plan.md. Vor jeder Arbeit lesen.

## Arbeitsweise
- Zu Beginn jeder Session: git fetch; neue Commits auf origin/dev melden, nie ohne
  Freigabe mergen.
- Ein freigegebener Schritt pro Session: erst Plan, dann Freigabe, dann Umsetzung.
- Fakten mit Beleg (Datei:Zeile) als [Fakt], Vermutungen als [Annahme] markieren.
- Nicht ausgeführte Prüfungen ausdrücklich nennen.

## Grenzen
- t2b-Code nur mit Freigabe ändern (alles außer neuen Dateien in client/,
  CLAUDE.md und doc/plan.md). Auffälligkeiten melden, nicht beheben.
- Keine LLM-Requests ohne Freigabe. Das gilt auch für /health/upstream und
  /v1/models gegen einen echten Endpunkt. Standard ist der Mock.

## Secrets
- Nie ausgeben oder committen; bei Funden nur Datei und Zeile nennen.
- Keys nur in Umgebung oder .env. llm_config.json nicht öffnen.
- In versionierten Dateien keine Werte, lokalen Pfade oder internen Hosts.

## Git
- Einziges Remote ist origin (GitHub). Nie Bitbucket anlegen oder nutzen.
- Nur dev-client-implementation pushen, nur nach ausdrücklicher Freigabe.
- Nie dev oder main pushen. Kein Force-Push.
- Vor jedem Commit: git status und git diff --cached --stat prüfen; nie Config-, Key-
  oder .env-Dateien stagen.
- Commit: Betreff im Imperativ (max. ~60 Zeichen), Leerzeile,
  2–5 Stichpunkte, kein Co-Authored-By.

## Dokumentation
- Sprache: Doku und Code-Kommentare Deutsch, Commit-Betreff englisch im Imperativ.
- Statuslog in doc/plan.md: JJJJ-MM-TT HH:MM (Europe/Berlin) | Schritt | Ergebnis | Commit
- Thesis-relevante Beobachtungen als fertige Zeilen vorschlagen.
  Die Thesis-Notizen liegen im PoC-Repo, nicht hier.

## Technik
- Nativ mit .venv (py -3.13), kein Docker. Start aus dem Repo-Root.
- Nur localhost: Server an 127.0.0.1 (DE2_HOST=127.0.0.1).
- PYTHONUTF8=1, VLLM_BASE_URL ohne /v1 und ohne End-Slash.
- Unter Windows Mock und Server in zwei Terminals statt debug_stack.py.
