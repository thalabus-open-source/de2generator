# Protokoll V1 für Tool-Aufrufe

Stand 2026-11-26, gegengelesen. Mit „(Vorschlag)“ markierte Punkte sind noch offen;
Abschnitt 10 nennt sie.

Das Protokoll gilt für den Modus „tools“ des Chat-Clients (`client/chat.py`), den Tool-Dienst
(`tools/`) und den System-Prompt (`prompts/battista_v1.xml`). Es folgt der Vorstudie: Das Modell
schreibt Aufrufe als XML-Tags in sein Reasoning, der Client fängt sie im Stream ab, lässt sie
vom Tool-Dienst ausführen und gibt die Ergebnisse in einer eigenen Nachricht zurück.
Abweichungen von der Vorstudie sind gekennzeichnet.

## 1. Ablauf einer Nutzerfrage

1. Der Client schickt System-Prompt, Verlauf und Frage per Stream an das Gateway, mit Thinking
   und ohne Stop-Sequenz.
2. Er liest Reasoning und Antwort mit. Steht ein vollständiger Aufruf im Stream (Abschnitt 2),
   schließt er die Verbindung; alles danach wird verworfen.
3. Er setzt `<DEP>` ein (Abschnitt 2), vergibt eine ID (Abschnitt 4) und lässt den Aufruf vom
   Tool-Dienst ausführen (Abschnitt 8). Ein identischer Aufruf derselben Frage wird nicht erneut
   ausgeführt.
4. Die Folgeanfrage enthält in dieser Reihenfolge:
   - System-Prompt, Verlauf und Frage wie in Schritt 1,
   - eine User-Nachricht mit allen bisherigen Ergebnissen dieser Frage (Abschnitt 3),
   - eine partielle Assistant-Nachricht: `<|im_start|>`, dann das Reasoning des letzten Streams
     bis zum Beginn des Aufrufs, dann der Verweis auf das Ergebnis.
5. Schritte 2–4 wiederholen sich, bis eine Antwort ohne Aufruf kommt oder das Rundenlimit
   erreicht ist (Abschnitt 6).
6. In den Verlauf gehen nur die Frage und die Endantwort, nicht Reasoning, Ergebnisse oder
   Präfixe.

## 2. Aufruf und Verweis

Beide sind leere, selbstschließende Tags. Sie unterscheiden sich nur in den Attributen:

| Form | Attribute | Wirkung |
|---|---|---|
| Aufruf | die Argumente des Werkzeugs, kein `id` | wird ausgeführt |
| Verweis | genau ein Attribut: `id` | wird nie ausgeführt; zeigt auf ein Ergebnis in `<RESULTS>` |

```xml
<MUL x1="3" x2="4"/>
<ADD x1="5" x2="<DEP>"/>
<RAG domain="fiction" book_series_id="S-bob_the_cat" book_id="B-2" query="where does Bob sleep"/>
<MUL id="mul-1"/>
```

### Werkzeuge in V1

| Name | Argumente | Ergebnis |
|---|---|---|
| `MUL` | genau `x1` und `x2`, je eine Zahl oder `<DEP>` | Produkt |
| `ADD` | genau `x1` und `x2`, je eine Zahl oder `<DEP>` | Summe |
| `RAG` | `domain` (Pflicht, in V1 nur `fiction`), `query` (Pflicht), `book_series_id` und `book_id` (optional) | bis zu 2 Abschnitte |

`SUB` und `DIV` sind reserviert und kommen nach V1.

### Schreibregeln

- Tag-Namen in Großbuchstaben, Attributnamen in Kleinbuchstaben, Werte in doppelten
  Anführungszeichen. Die Reihenfolge der Attribute ist beliebig, Leerraum vor `/>` erlaubt.
- Werte enthalten weder `"` noch `<` oder `>`; einzige Ausnahme ist der vollständige Wert
  `<DEP>`. Entities wie `&quot;` werden nicht ausgewertet.
- Zahlen: optionales Minus, Ziffern, optional ein Punkt als Dezimaltrenner (`3`, `-2`, `2.5`);
  keine Tausendertrennzeichen, keine Ausdrücke.
- `book_id` nur zusammen mit `book_series_id`, denn Buch-IDs sind nur innerhalb einer Reihe
  eindeutig.

### `<DEP>`: Wert aus dem letzten Ergebnis

- `<DEP>` steht wie in der Vorstudie für das zuletzt veröffentlichte Ergebnis der aktuellen
  Frage. Der Client setzt den Wert vor dem Aufruf des Tool-Dienstes ein; Literal-Werte bleiben
  erlaubt.
- Gibt es in dieser Frage noch kein Ergebnis, antwortet der Client mit `invalid_arguments`.
  Ebenso, wenn das letzte Ergebnis keine Zahl ist (RAG-Ergebnis oder Fehler) oder wenn `<DEP>`
  in einem Attribut von `RAG` steht.

### Erkennungsmuster

Gilt für den Client und die statische Prüfung des Prompts:

```text
<(MUL|ADD|RAG|SUB|DIV)((?:\s+[a-z][a-z0-9_]*="(?:[^"<>]*|<DEP>)")*)\s*/>
```

Ein Treffer mit `id` als einzigem Attribut ist ein Verweis, jeder andere Treffer ein Aufruf.
Ungültige Aufrufe beantwortet der Client mit einem Fehler (Abschnitt 5).

### Erkennung im Stream

- Maßgeblich ist das Reasoning. Ein Aufruf in der sichtbaren Antwort wird ebenfalls abgefangen
  und als Kanalfehler angezeigt; der Präfix ist dann das ganze Reasoning plus Verweis, wie in
  der Vorstudie.
- Je Stream wird nur der erste vollständige Aufruf ausgeführt.
- Verweise sowie Tags im System-Prompt und in `<RESULTS>` werden nie ausgeführt.

## 3. Ergebnisse: `<RESULTS>`

Eine User-Nachricht je Folgeanfrage. Sie enthält alle Ergebnisse der aktuellen Nutzerfrage in
der Reihenfolge ihrer Vergabe, getrennt durch ein Leerzeichen wie in der Vorstudie:

```xml
<RESULTS><MUL id="mul-1">12</MUL> <RAG id="rag-1" source="fiction/S-bob_the_cat/B-2">…</RAG></RESULTS>
```

- Rechenergebnis: Element mit `id`, der Wert ist der Inhalt.
- Zahlenformat: ganze Zahlen ohne Nachkommastellen (`12`), sonst Punkt als Dezimaltrenner ohne
  überflüssige Nullen (`2.5`). Gerechnet wird dezimal, damit `0.1 + 0.2` genau `0.3` ergibt.
- RAG-Ergebnis: Element mit `id` und `source`, die Abschnitte als Inhalt. `source` nennt in V1
  das Buch im Schema `domain/book_series_id/book_id`.
- Mehrere Abschnitte trennt eine Leerzeile; stammen sie aus mehreren Büchern, stehen die Quellen
  durch Leerzeichen getrennt in der Reihenfolge der Abschnitte (Vorschlag).
- Abschnittstexte enthalten keine spitzen Klammern; die Daten werden entsprechend erstellt.
- Ergebnis- und Fehlerelemente haben immer einen Inhalt und sind nie selbstschließend. So
  verwechselt das Erkennungsmuster sie nicht mit Aufrufen oder Verweisen.
- Fehler: Abschnitt 5.

## 4. ID-Vergabe

- Der Client vergibt die IDs; der Tool-Dienst kennt sie nicht und ist zustandslos.
- Format: Werkzeugname in Kleinbuchstaben, Bindestrich, laufende Nummer je Werkzeug:
  `mul-1`, `mul-2`, `add-1`, `rag-1`.
- Die Nummern gelten pro Nutzerfrage; jede neue Frage beginnt wieder bei 1.
- Ein identischer Aufruf innerhalb derselben Frage, also gleicher Name und gleiche Argumente
  nach dem Einsetzen von `<DEP>`, erhält die bestehende ID und wird nicht erneut ausgeführt;
  `<RESULTS>` bekommt keinen neuen Eintrag. **Bewusste Abweichung von der Vorstudie:** Dort
  wurden zitierte oder nacherzählte Tags erneut ausgeführt.
- Auch ein fehlgeschlagener Aufruf bekommt eine ID, damit der Präfix immer gleich gebaut wird.

## 5. Fehlerformat

Ein Fehler ist ein Element mit `id`, `error` und einem kurzen englischen Hinweis als Inhalt:

```xml
<MUL id="mul-2" error="invalid_arguments">x2 is not a number</MUL>
<RAG id="rag-2" error="no_results">no matching section</RAG>
```

| Code | Bedeutung |
|---|---|
| `invalid_arguments` | Argument fehlt, ist überzählig oder keine Zahl; `<DEP>` ohne verwendbares Ergebnis; `book_id` ohne `book_series_id` |
| `invalid_tag` | Tag mit `id` und weiteren Attributen |
| `unknown_tool` | reservierter Name ohne Umsetzung, in V1 `SUB` und `DIV` |
| `no_results` | RAG findet keinen passenden Abschnitt |
| `tool_unavailable` | Tool-Dienst nicht erreichbar oder interner Fehler |

Nach einem Fehler läuft die Schleife weiter; das Modell kann den Aufruf korrigieren.

## 6. Grenzen

| Grenze | Wert |
|---|---|
| RAG-Abschnitte je Aufruf | höchstens 2 |
| Länge je Abschnitt | ca. 800 Zeichen; längere kürzt der Tool-Dienst am Wortende |
| Länge von `query` | höchstens 200 Zeichen |
| Argumente je `MUL` oder `ADD` | genau 2: `x1` und `x2` |
| Anfragen ans Gateway je Nutzerfrage | höchstens 6, wie in der Vorstudie |

Beim Rundenlimit bricht der Client ab und zeigt `round_limit` an; eine weitere Anfrage folgt
nicht.

## 7. Vertrauensgrenze

- Werte gelten nur innerhalb von `<RESULTS>` als vom Tool-Dienst geliefert.
- Schreibt das Modell selbst ein Element mit Inhalt, etwa `<MUL id="mul-1">12</MUL>`, gilt das
  als gefälschtes Ergebnis, außer es wiederholt ein veröffentlichtes Ergebnis wörtlich. Der
  Client zeigt eine Warnung und führt nichts aus.

## 8. Schnittstelle zum Tool-Dienst (Vorschlag für S1 und S2)

Der Dienst lauscht nur auf 127.0.0.1:8357. Er bekommt die Attribute eines Aufrufs als JSON mit
Zeichenketten als Werten, `<DEP>` bereits eingesetzt, und antwortet ohne XML und ohne ID; das
Element baut der Client.

| Anfrage | Antwort bei Erfolg |
|---|---|
| `POST /mul` mit `{"x1": "3", "x2": "4"}` | `{"value": "12"}` |
| `POST /add` mit `{"x1": "2", "x2": "3"}` | `{"value": "5"}` |
| `POST /rag` mit `{"domain": "fiction", "book_series_id": "S-bob_the_cat", "book_id": "B-2", "query": "…"}` | `{"sections": [{"source": "fiction/S-bob_the_cat/B-2", "text": "…"}]}` |
| `GET /health` | `{"status": "ok"}` |

Fachliche Fehler kommen mit HTTP 200 als `{"error": "<code>", "message": "…"}`. Transport- und
Serverfehler bildet der Client auf `tool_unavailable` ab.

## 9. Beispiele

### A: Zwei Rechenschritte

Frage: `What is (2 + 3) * 4?`

1. Stream 1, Reasoning: `… I must obtain <ADD x1="2" x2="3"/>` → Abbruch, `add-1` = 5.
2. Folgeanfrage:
   - User: `<RESULTS><ADD id="add-1">5</ADD></RESULTS>`
   - Assistant, partiell: `<|im_start|>… I must obtain <ADD id="add-1"/>`
3. Stream 2, Reasoning: `… and then obtain <MUL x1="<DEP>" x2="4"/>` → Abbruch; der Client
   setzt 5 ein, `mul-1` = 20. Mit `x1="5"` wäre das Ergebnis dasselbe.
4. Folgeanfrage:
   - User: `<RESULTS><ADD id="add-1">5</ADD> <MUL id="mul-1">20</MUL></RESULTS>`
   - Assistant, partiell: `<|im_start|>`, das Reasoning von Stream 2 bis zum Aufruf, dann
     `<MUL id="mul-1"/>`
5. Stream 3 endet ohne Aufruf; Antwort: `20`.

### B: RAG

Frage: `In the second Bob the Cat book, where does Bob hide the key?` Die Frage ist nur ein
Beispiel; die Fakten entstehen erst mit den Daten in S2.

1. Stream 1, Reasoning: `… <RAG domain="fiction" book_series_id="S-bob_the_cat" book_id="B-2" query="where does Bob hide the key"/>`
   → Abbruch, `rag-1`.
2. Folgeanfrage:
   - User: `<RESULTS><RAG id="rag-1" source="fiction/S-bob_the_cat/B-2">…</RAG></RESULTS>`
   - Assistant, partiell: `<|im_start|>… <RAG id="rag-1"/>`
3. Stream 2 endet ohne Aufruf; die Antwort stützt sich nur auf den gelieferten Abschnitt.

### C: Fehler und Korrektur

1. Erster Aufruf der Frage: `<ADD x1="5" x2="<DEP>"/>` → noch kein Ergebnis vorhanden:
   `<ADD id="add-1" error="invalid_arguments">no previous result for DEP</ADD>`
2. Das Modell korrigiert: `<ADD x1="5" x2="7"/>` → `<ADD id="add-2">12</ADD>`; in `<RESULTS>`
   stehen dann beide Einträge.

### D: Zitierter Aufruf

Wiederholt das Modell in einem späteren Stream derselben Frage `<ADD x1="2" x2="3"/>`, erhält der
Aufruf die bestehende ID `add-1`. Er wird nicht erneut ausgeführt, und `<RESULTS>` bleibt
unverändert.

## 10. Offene Punkte

1. Präfix über alle Runden zusammensetzen statt nur aus dem letzten Stream; nach dem 01.12.
   zu klären.
2. Darstellung mehrerer Abschnitte und Quellen in einem RAG-Ergebnis (Abschnitt 3).
3. Schnittstelle zum Tool-Dienst (Abschnitt 8); wird in S1 und S2 festgelegt.
