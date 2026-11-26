# Minimaler Client

Kleiner Kommandozeilen-Client für de²generator. Er schickt eine Frage über
`POST /v1/chat/completions` an de²generator; der reicht sie an ein OpenAI-kompatibles
LLM-Gateway weiter. Der Client gibt die Antwort aus.

- Modell und Bearer-Key kommen vom Client; de²generator reicht beides unverändert durch.
- de²generator antwortet mit fertigem JSON, nicht als Token-Stream.
- Einzige Abhängigkeit ist `httpx` aus `requirements.txt`.

## Einrichtung

Im Repo-Root, mit Python 3.13:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Konfiguration

Nur über Umgebungsvariablen. Werte gehören in die Shell oder in eine lokale `.env` im
Repo-Root (von Git ignoriert), nie in versionierte Dateien.

| Variable | Gelesen von | Zweck |
|---|---|---|
| `VLLM_BASE_URL` | Server | Basis-URL des Gateways, ohne `/v1` und ohne Slash am Ende |
| `DE2_HOST`, `DE2_PORT` | Server | Bind-Adresse und Port; nur an localhost binden, der Server hat keine eigene Authentifizierung |
| `PYTHONUTF8` | Python | UTF-8-Ausgabe |
| `SSL_CERT_FILE` | Server | eigenes CA-Bundle, nur falls nötig |
| `DE2_BASE_URL` | Client | Adresse von de²generator |
| `LLM_MODEL` | Client | Modellname, überschreibbar mit `--model` |
| `LLM_API_KEY` | Client | Bearer-Key; Fallback: `.env` im Repo-Root |

## Start

Server (erstes Terminal, Repo-Root, beenden mit Strg+C):

```powershell
$env:DE2_HOST = "<nur localhost>"
$env:PYTHONUTF8 = "<an>"
$env:VLLM_BASE_URL = "<Basis-URL des Gateways>"
.venv\Scripts\python.exe src\de2generator.py
```

Client (zweites Terminal):

```powershell
.venv\Scripts\python.exe client\minimal_client.py --model "<Modellname>" "<Frage>"
```

Weitere Optionen: `--base-url`, `--max-tokens` (Standard 2048) und `--timeout` in Sekunden
(Standard 120). `--help` zeigt alle.

Ohne echtes Modell: zuerst `src/test/mock_llm_backend.py` starten, `VLLM_BASE_URL` auf
dessen Adresse setzen und als Modell ein Mock-Szenario angeben. Adresse und Szenarien stehen
im Docstring des Mocks. `LLM_API_KEY` dabei mit einem Platzhalter belegen.

## Ausgabe und Exit-Codes

Die Antwort geht auf stdout. Eine Statistikzeile (Dauer, `recovery_attempts`, Tokens) und
Fehler gehen auf stderr.

| Exit | Bedeutung |
|---|---|
| 0 | Antwort erhalten |
| 1 | Konfiguration fehlt oder ist ungültig |
| 2 | de²generator nicht erreichbar oder Zeitüberschreitung |
| 3 | HTTP-Fehler oder unerwartete Antwort |
| 4 | leere Antwort trotz HTTP 200 |

HTTP-Fehler des Gateways gibt de²generator als HTTP 500 weiter, ohne den ursprünglichen
Fehlertext.

## Sicherheit

- Der Key wird nie ausgegeben, auch nicht in Fehlermeldungen. Key und URLs werden aus
  Fehlertexten entfernt.
- Über http geht der Key nur an localhost.
- Anfragen mit Key laufen nie über einen System-Proxy.
