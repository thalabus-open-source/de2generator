# Plan: Minimaler Client für de²generator

Branch `dev-client-implementation` · Stand 2026-11-24 17:59 (Europe/Berlin)

Arbeitsregeln stehen in `CLAUDE.md`. Dieses Dokument enthält keine Keys, URLs, internen
Hosts oder lokalen Pfade.

## 1. Ziel und Abgrenzung

- Ziel: de²generator läuft lokal, und ein minimaler Client erhält darüber eine Antwort
  eines LLM (Kette Client → de²generator → LLM).
- Nicht enthalten: Änderungen am t2b-Code, Token-Streaming zum Client, Tool-Aufrufe,
  RAG-Logik.
- Die Übergabe nach Bitbucket macht t2b nach der Thesis selbst.

## 2. Zielarchitektur

```
client/minimal_client.py
    │  HTTP, nur localhost; schickt Modell und Bearer-Key mit
    ▼
de²generator (nur localhost)
    │  HTTPS, immer Streaming; reicht Modell und Key unverändert durch
    ▼
LiteLLM-Gateway → Modell
```

- de²generator hat keinen eigenen Key und kein eigenes Modell. Beides kommt vom Client.
- Ein Wechsel des Gateways ändert nur Umgebungsvariablen, keinen Code.

## 3. Entscheidungen

| Nr. | Datum | Entscheidung | Grund | Alternative |
|---|---|---|---|---|
| E1 | 2026-11-24 | Option A: `VLLM_BASE_URL` per Umgebungsvariable, Client schickt Key und Modell | kein Eingriff in t2b-Code, keine Secrets im Repo, Gateway-Wechsel ohne Codeänderung | B: `de2generator.model_endpoint` pro Anfrage; C: eigenes lokales LiteLLM |
| E2 | 2026-11-24 | Nativ mit `.venv` (Python 3.13) | gleiche Python-Version wie im `Dockerfile`, kein Docker-Netzwerk unter Windows nötig | Docker |
| E3 | 2026-11-24 | Alles nur über localhost | der Server hat keine eigene Authentifizierung | Bindung an alle Schnittstellen (Standard in `config.json:60`) |
| E4 | 2026-11-24 | Doku und Code-Kommentare Deutsch, Commit-Betreff englisch im Imperativ | wie im PoC | durchgehend Englisch wie im t2b-Code |
| E5 | 2026-11-24 | `llm_request.py` entfällt in diesem Repo; `llm_config.json` bleibt lokal und unversioniert | das Original liegt im PoC, der Client ersetzt das Skript | beide Dateien versioniert lassen |

## 4. Fakten und Annahmen

| Art | Aussage | Beleg oder Prüfweg |
|---|---|---|
| [Fakt] | Der `Authorization`-Header des Clients wird an das LLM durchgereicht | `src/orchestration/supervisor.py:1068`, `src/streaming/vllm_client.py:145`; im Lauf bestätigt (17:47) |
| [Fakt] | Das Modell kommt nur aus dem Request | `src/orchestration/request_context.py:173` |
| [Fakt] | Zum LLM wird immer gestreamt, der Client erhält immer fertiges JSON | `src/streaming/vllm_client.py:103`, `src/api/chat.py:104` |
| [Fakt] | Der Block `de2generator` erreicht das LLM nicht | `src/orchestration/request_context.py:140`; im Mock bestätigt |
| [Fakt] | `VLLM_BASE_URL` wird nicht normalisiert (kein `/v1`, kein Slash am Ende) | `src/core/endpoints.py:17` |
| [Fakt] | Streaming und `stream_options` funktionieren mit dem Gateway | Lauf um 17:47 |
| [Fakt] | Der PoC-Endpunkt ist bereits LiteLLM | Fehlertext des Gateways (17:30) |
| [Fakt] | Eine leere Antwort löst 12 LLM-Aufrufe aus und kommt mit HTTP 200 an | `src/orchestration/supervisor.py:218`, `config.json:40`; im Mock gemessen |
| [Fakt] | `max_tokens` ist keine harte Grenze: Beim Retry nach erschöpfter Denkphase steigt es um bis zu 4000 | `src/orchestration/supervisor.py:877` |
| [Fakt] | Installiert am 2026-11-24: Python 3.13.0, fastapi 0.165.0, starlette 1.10.0, uvicorn 0.54.0, httpx 0.28.1 | `requirements.txt` nennt nur Mindestversionen |
| [Annahme] | Ohne `PYTHONUTF8` scheitert der Server bei umgeleiteter Ausgabe an Emojis | `src/utils/terminal.py:24`; Test gegen den Mock mit Sonderzeichen |
| [Annahme] | Der Zugang über Tailscale führt zum selben LiteLLM | bei t2b nachfragen |

## 5. Schrittplan

| Nr. | Schritt | Abnahme | Status |
|---|---|---|---|
| 0 | `llm_config.json` klären, `config/` aufräumen | nicht mehr getrackt; kein Key-Feld enthalten | erledigt |
| 1 | Arbeitsdoku anlegen | `CLAUDE.md` und `doc/plan.md` committet | erledigt |
| 2 | `.venv` und Serverstart ohne LLM | `/health` 200, Arbeitsbaum sauber | erledigt |
| 3 | Client gegen Mock-LLM | Exit 0, 2, 3 und 4 wie erwartet | erledigt |
| 4 | Server auf echten Endpunkt | `/health/upstream` meldet `reachable` | erledigt |
| 5 | Eine echte Anfrage | Exit 0, Text nicht leer, `recovery_attempts` 0 | erledigt |
| 6 | Push nach Freigabe | Branch auf origin aktuell | offen |

### Mock-Tests vom 2026-11-24

Zwei Prozesse (Mock und Server), Client mit Platzhalter-Key.

| Fall | Exit erwartet | Exit | Upstream-Aufrufe | Dauer |
|---|---|---|---|---|
| `mock/healthy` | 0 | 0 | 1 | 0,3 s |
| `mock/error500` | 3 | 3 | 1 | 0,2 s |
| `mock/empty` | 4 | 4 | 12 | 2,7 s |
| Server aus | 2 | 2 | 0 | – |

## 6. Konfiguration

Nur Variablennamen. Werte stehen in der Shell oder in lokalen, nicht versionierten Dateien.

| Variable | Gelesen von | Zweck |
|---|---|---|
| `VLLM_BASE_URL` | Server | Basis-URL des Gateways |
| `DE2_HOST`, `DE2_PORT` | Server | Bind-Adresse und Port |
| `PYTHONUTF8` | Python | UTF-8-Ausgabe |
| `SSL_CERT_FILE` | httpx im Server | CA-Bundle, nur für den AVG-Workaround |
| `DE2_BASE_URL` | Client | Adresse von de²generator |
| `LLM_MODEL` | Client | Modellname, überschreibbar mit `--model` |
| `LLM_API_KEY` | Client | Bearer-Key, Umgebung oder `.env` |

## 7. Lokaler Betrieb

Alle Befehle im Repo-Root. Platzhalter in spitzen Klammern nur in der Shell ersetzen,
nie hier eintragen.

Einmalig:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Server (Terminal 1, beenden mit Strg+C):

```powershell
$env:DE2_HOST = "<Loopback-Adresse>"
$env:PYTHONUTF8 = "<UTF-8 an>"
$env:SSL_CERT_FILE = "<CA-Bundle>"      # nur bei aktivem AVG-HTTPS-Scan
$env:VLLM_BASE_URL = "<Basis-URL>"      # ohne /v1, ohne Slash am Ende
.venv\Scripts\python.exe src\de2generator.py
```

`VLLM_BASE_URL` lässt sich aus der lokalen Config ableiten, ohne den Wert anzuzeigen:

```powershell
$c = Get-Content -Raw config\llm_config.json | ConvertFrom-Json
$env:VLLM_BASE_URL = ($c.base_url.TrimEnd('/') + $c.endpoint) -replace '/v1/chat/completions$', ''
```

Client (Terminal 2):

```powershell
.venv\Scripts\python.exe client\minimal_client.py --model "<Modellname>" "<Frage>"
```

Mit Mock statt Gateway (kein echter LLM-Aufruf): zuerst den Mock in einem eigenen Terminal
starten, dann `VLLM_BASE_URL` auf dessen Adresse setzen und `LLM_API_KEY` mit einem
Platzhalter belegen. Adresse und Szenarien stehen in `src/test/mock_llm_backend.py`.

```powershell
.venv\Scripts\python.exe src\test\mock_llm_backend.py
```

### TLS und AVG

- Ursache: AVG Web/Mail Shield ersetzt auf dem Entwicklungsrechner das TLS-Zertifikat des
  Gateways. Windows vertraut dem AVG-Root, httpx (certifi) nicht. `/health/upstream` meldet
  dann `CERTIFICATE_VERIFY_FAILED`.
- **Empfehlung: AVG-Ausnahme für den LLM-Host.** Dann gilt das echte Zertifikat, und AVG
  liest Key und Prompts nicht mit.
- Workaround: `SSL_CERT_FILE` zeigt nur für den Serverprozess auf ein lokales Bundle aus
  certifi und AVG-Root (in `.venv`, nicht versioniert). Die TLS-Prüfung bleibt aktiv.
- Keine Lösung: die TLS-Prüfung abschalten.

## 8. Beobachtungen zum t2b-Code

Nur gemeldet, nichts geändert.

| Thema | Beobachtung | Beleg |
|---|---|---|
| Fehlertext des Gateways | Wird verworfen. HTTP-Fehler des Gateways kommen beim Client als allgemeiner 500 an (für 500 beobachtet, für 401 aus dem Code abgeleitet); die Ursache war nur per Direkttest sichtbar | `src/streaming/vllm_client.py:196`, `src/api/chat.py:121`; 17:28–17:30 |
| Cooldown des Verifiers | Beginnt nach dem ersten Fehler, die Doku nennt drei | `src/detection/semantic.py:101`, `doc/CONFIGURATION.md:89`; 17:47 |
| `SEMANTIC_LLM_ENABLED` | Steuert nur die UI-Anzeige, der Verifier läuft immer | `src/api/webui.py:82`, `src/orchestration/supervisor.py:191` |
| Doku-Drift | Antwortfelder in der API-Doku weichen ab; `config.example.json` fehlt; dokumentierte INFO-Logs erscheinen nicht | `doc/API.md:73` und `src/response/builder.py:147`; `doc/DEPLOYMENT.md:67`; `doc/CONFIGURATION.md:489` und `src/core/config.py:35` |
| `recovery_attempts` | Zählt nur den letzten Neustart: bei 12 Aufrufen steht dort 4 | `src/orchestration/supervisor.py:284`; Mock-Test |
| Tool-Aufrufe | `tool_calls` werden nicht gelesen, `finish_reason` ist fest `"stop"` | `src/streaming/vllm_client.py:266`, `src/response/builder.py:138` |
| `debug_stack.py` | Nutzt `os.killpg`, das es unter Windows nicht gibt | `src/test/debug_stack.py:99` |

## 9. Offene Fragen

An Roland:
- AVG-Ausnahme statt Workaround einrichten?

An t2b (Luca, Aurelien):
- Ist das Backend von `qwen-3.8-180b-next` wieder erreichbar (Ausfall 2026-11-24)?
- Eigener LiteLLM-Zugang und Weg über Tailscale?
- Sind Streaming zum Client und `tool_calls` geplant?
- Ist das wirkungslose `SEMANTIC_LLM_ENABLED` bekannt?
- Vorgaben für `client/` (Framework, Tests, Lizenz)? `LICENSE` ist AGPL-3.0,
  `README.md:347` enthält einen Platzhalter.
- Wird `dev` auf GitHub weiter gepflegt, sollen Änderungen hierher gemergt werden?

## 10. Statuslog

| Zeitpunkt (Europe/Berlin) | Schritt | Ergebnis | Commit |
|---|---|---|---|
| 2026-11-24 17:25 | 2 | `.venv` und Abhängigkeiten installiert | – |
| 2026-11-24 17:25 | 3 | Client geschrieben; Fehlerpfade ohne LLM geprüft (Exit 1 und 2) | – |
| 2026-11-24 17:25 | 2 | Server nur auf localhost gestartet, `/health` 200; Upstream scheitert an TLS (AVG) | – |
| 2026-11-24 17:28 | 4 | Neustart mit `SSL_CERT_FILE` (freigegeben); Upstream erreichbar, 401 ohne Key | – |
| 2026-11-24 17:28 | 5 | Anfrage 1, `qwen-3.8-180b-next`: Exit 3, HTTP 500 nach 0,8 s | – |
| 2026-11-24 17:29 | 5 | Anfrage 2, gleiches Modell: Exit 3, HTTP 500 nach 0,6 s | – |
| 2026-11-24 17:30 | 5 | Zwei Direkttests ohne de²generator (freigegeben), mit und ohne Streaming: HTTP 500, LiteLLM meldet „Connection error“ zum Modell | – |
| 2026-11-24 17:47 | 5 | Modellliste (11 Einträge); Anfrage 3, `qwen-3.5-35b`: Exit 0, „Hallo! 👋“, 5,6 s, `recovery_attempts` 0, erstes Token (Denkphase) nach 0,4 s, 271 Tokens aus | – |
| 2026-11-24 17:54 | 3 | Mock-Tests bis 17:55: Exit 0, 3, 4 und 2 wie erwartet; Anzeige von `ttft` im Client korrigiert | – |
| 2026-11-24 17:55 | 0 | `llm_request.py` entfernt, `llm_config.json` nicht mehr versioniert | 4f11ddf |
| 2026-11-24 17:56 | 3 | Client committet | 889240a |
| 2026-11-24 17:59 | 1 | `CLAUDE.md` und `doc/plan.md` angelegt | dieser Commit |
