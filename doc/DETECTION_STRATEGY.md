# Detection Strategy & Three-Strike Logic

## Overview

de²generator uses multiple detectors running in parallel to identify generation degeneration. To prevent false positives, **quality detectors** employ a **three-strike confirmation system** before triggering recovery.

**Exception:** Structure validators (JSON/XML/HTML syntax) use **instant rejection** on first error (syntax is binary).

## Three-Strike Rule

### Checkpoint States

| State | Description | Recovery Triggered? |
|-------|-------------|-------------------|
| **OK** | All detectors report healthy | ❌ No |
| **CANDIDATE_REJECT** | One detector anomaly (strike 1) | ❌ No |
| **REJECTED** | Either 3 strikes from same detector OR structure syntax error | ✅ **Yes** |

### Flow Example

```
Token Stream
    ↓
Checkpoint 1 → Entropy: 6.5 OK
               RepetitionDetector: OK
               → State: OK
    ↓
Checkpoint 2 → Entropy: 3.2 (below 3.5)
               RepetitionDetector: OK
               → State: CANDIDATE_REJECT (strike 1)
    ↓
Checkpoint 3 → Entropy: 3.1 (below 3.5)
               RepetitionDetector: OK
               → State: CANDIDATE_REJECT (strike 2)
    ↓
Checkpoint 4 → Entropy: 2.9 (below 3.5)
               RepetitionDetector: OK
               → State: CANDIDATE_REJECT (strike 3)
                         ↓ ESCALATE
                    → State: REJECTED ✅
                              ↓
                    Rollback to Checkpoint 1
                    (before first strike)
                              ↓
                    Inject continuation prompt
                              ↓
                    Retry generation
```

## Detector Types

### 1. Structure Detectors (Instant Rejection)

Validates syntax for JSON, XML, HTML.

**Behavior:** ❌ **Instant rejection on first syntax error** (no three-strike rule)

**Rationale:** Syntax is binary (valid or invalid). No ambiguity → no need for confirmation.

**Example:**
```json
// Valid checkpoint: {"users": [{"id": 1, "name": "Alice"}]}
// Invalid checkpoint: {"users": [{"id": 1, "name": "Alice"},, }
                                                           ^^ syntax error
```

Detection result: `REJECTED` (instant) → rollback to last valid

### 2. Quality Detectors (Three-Strike Rule)

Analyze text quality (entropy, repetition, semantic meaning).

**Behavior:** Requires 3 consecutive anomalies to confirm degradation

**Rationale:** Quality is subjective. Single anomaly could be legitimate variation → requires confirmation.

## Detector Implementations

### Entropy Detector

**Purpose:** Detect character-level degeneration (repetition, gibberish)

**Calculation:**
```
Shannon Entropy H = -Σ P(c) × log₂(P(c))

Where:
  P(c) = probability of character c in the text block
  H = bits per character

Scaled to bits per byte: H_scaled = H (for ASCII)
```

**Healthy Range:** 3.5-7.0 bits/byte
- Below 3.5: Repetitive or degenerate (e.g., "aaaaaa", "    ", repeated words)
- 3.5-7.0: Normal natural language
- Above 7.0: High entropy (gibberish, binary data)

**Detection Rules:**
1. Calculate entropy of checkpoint text
2. If entropy < 3.5 OR entropy > 7.0:
   - Return `CANDIDATE_REJECT` (strike)
3. Else:
   - Return `OK` (no strike)

**Three-Strike Escalation:**
- Strike 1: Low/high entropy checkpoint → `CANDIDATE_REJECT`
- Strike 2: Another anomaly → `CANDIDATE_REJECT`
- Strike 3: Third consecutive anomaly → Escalate to `REJECTED`

**Example Flow (Healthy Recovery):**
```
Checkpoint 1: Entropy 6.5 → OK
Checkpoint 2: Entropy 2.1 → CANDIDATE_REJECT (strike 1)
Checkpoint 3: Entropy 6.8 → OK (strike reset)
Checkpoint 4: Entropy 2.3 → CANDIDATE_REJECT (strike 1 again)
Checkpoint 5: Entropy 7.5 → CANDIDATE_REJECT (strike 2)
Checkpoint 6: Entropy 8.2 → CANDIDATE_REJECT (strike 3) → ESCALATE TO REJECTED
                  ↓
              Rollback to Checkpoint 4 (before strikes)
```

### Repetition Detector

**Purpose:** Detect token-level repetition patterns

**Metrics (100-token rolling window):**
- `distinct_ratio` = unique_tokens / total_tokens
- `bigram_ratio` = repeated_bigrams / total_bigrams
- `trigram_ratio` = repeated_trigrams / total_trigrams

**Health Score Calculation:**
```
health_score = (distinct_ratio × 0.40 +
               (1 - bigram_ratio) × 0.35 +
               (1 - trigram_ratio) × 0.25) × 100
```

**Thresholds (from config.json):**
- Below `critical_score` (40): Immediate warning
- Between `critical_score` and `warning_score` (40-60): Warning state
- Above `warning_score` (60): OK

**Three-Strike Logic:**
- Health score < critical_score → `CANDIDATE_REJECT`
- Health score ≥ warning_score → `OK`

### Semantic Progress Detector (Async)

**Purpose:** Detect semantic-level degeneration (stagnation, poor structure, incoherence)

**Architecture:** Asynchronous background thread pool

**Validation Categories:**
1. Character repetition (e.g., "aaaa", "!!!!")
2. Word repetition (same word 3+ times)
3. Phrase repetition (verbatim from context or block)
4. Semantic stagnation (no new information)
5. Poor structure (malformed sentences, bad punctuation)
6. Incoherence (gibberish, corrupted sequences)

**Result Handling:** Out-of-order results (async from thread pool)

```
Checkpoint Processing (Main Thread):
  T0: Emit cp[0] to verifier thread (async)
  T1: Emit cp[1] to verifier thread (async)
  T2: Continue streaming
  T3: cp[0] result arrives → Strike count += 1
  T4: cp[2] result arrives (out of order)
  T5: cp[1] result arrives → Strike count evaluated
```

**Three-Strike Rule with Out-of-Order Results:**

The detector tracks strikes by **checkpoint position order**, not arrival order:

```python
strike_count[cp_position] += 1

# When evaluating strikecount for new result:
# Look at latest 3 consecutive positions
# If all 3 have strikes: REJECTED
```

**Example with Out-of-Order Arrivals:**
```
cp[0]: emit → result arrives at T5 → CANDIDATE_REJECT (strike 1)
cp[1]: emit → result arrives at T3 → CANDIDATE_REJECT (strike 2, out of order)
cp[2]: emit → result arrives at T4 → CANDIDATE_REJECT (strike 3, also out of order)

At T5: Three consecutive checkpoints (0,1,2) all have strikes
       → Escalate cp[2] to REJECTED
       → Rollback to cp[0]
```

## Detector Scope by Content Type

| Content Type | Active Detectors |
|--------------|-----------------|
| **JSON** | Structure (instant) + Entropy (3-strike) + Semantic (3-strike, async) |
| **XML** | Structure (instant) + Entropy (3-strike) + Semantic (3-strike, async) |
| **HTML** | Structure (instant) + Entropy (3-strike) + Semantic (3-strike, async) |
| **Plain Text** | Entropy (3-strike) + Repetition (3-strike) + Semantic (3-strike, async) |

## Combining Multiple Detectors

When multiple detectors run on same checkpoint, **most severe result wins**:

```python
results = [
    entropy_detector.detect(text),           # Returns "ok" or "candidate_reject"
    repetition_detector.detect(text),        # Returns "ok" or "candidate_reject"
    semantic_detector.get_result(cp_id)      # Returns "ok" or "candidate_reject" (async)
]

checkpoint.severity = max(results, key=severity_priority)
# severity_priority: "rejected" > "candidate_reject" > "ok"
```

**Example:**
```
Entropy: CANDIDATE_REJECT (strike 1)
Repetition: OK
Semantic: OK
→ Final state: CANDIDATE_REJECT (most severe)

Entropy: OK
Repetition: CANDIDATE_REJECT (strike 1)
Semantic: CANDIDATE_REJECT (strike 1)
→ Final state: CANDIDATE_REJECT

Entropy: CANDIDATE_REJECT (strike 2)
Repetition: CANDIDATE_REJECT (strike 3)
Semantic: REJECTED (instant from 3 strikes)
→ Final state: REJECTED (most severe)
```

## Recovery Strategies

When a checkpoint is rejected, de²generator chooses a recovery strategy:

### 1. Continuation (Default)

Roll back to last healthy checkpoint, inject continuation prompt:

```
Context: "The capital of France is"
[ROLLBACK]
New prompt: "...is Paris. The capital of Germany is"
[RETRY GENERATION]
```

**When used:** Most common, straightforward continuation

### 2. Thinking Resume

For extended thinking models, resume with deeper reasoning:

```
Thinking exhausted: threshold reached
New prompt: "Let me reconsider with more careful reasoning..."
[RETRY WITH THINKING BUDGET INCREASE]
```

**When used:** Model's thinking budget was exceeded

### 3. No-Think Retry

For extended thinking models, disable thinking and retry directly:

```
Thinking produced degradation
New prompt: "Provide the answer directly without reasoning:"
[RETRY WITHOUT THINKING]
```

**When used:** Thinking process itself is causing degradation

## Asynchronous Semantic Validation

The semantic detector runs **non-blocking** in background threads:

```
Main Stream:
  Token 1 → Stream immediately
  Token 2 → Stream immediately
  [Checkpoint 1 created]
  Token 3 → Stream immediately
  [Checkpoint 2 created]
  Token 4 → Complete

Semantic Thread:
  [Gets cp[1]] → Query verifier LLM (100-200ms)
  [Gets cp[2]] → Query verifier LLM (100-200ms)
  [Results arrive out of order]
  [Update checkpoint states asynchronously]
```

**Benefits:**
- Main generation stream never blocked
- Verifier latency doesn't affect user experience
- Results update UI in real-time
- Three-strike logic still works despite async arrivals

## Configuration

Adjust detection sensitivity in `config.json`:

```json
{
  "detection": {
    "entropy": {
      "min_healthy": 3.5,          # Lower = more sensitive
      "max_healthy": 7.0,          # Higher = catches gibberish
      "min_block_chars": 100       # Lower = faster but less reliable
    },
    "repetition": {
      "window_tokens": 100,
      "critical_score": 40,        # Lower = more sensitive
      "warning_score": 60          # Lower = more warnings
    }
  },
  "semantic_llm": {
    "enabled": true,
    "min_block_chars": 200,        # Only validate substantial blocks
    "min_failing_categories": 2    # How many categories must fail to reject
  }
}
```

## Tuning Guide

### Reduce False Positives (Too Many Recoveries)

1. **Increase** `entropy.min_healthy` (e.g., 3.5 → 4.0)
2. **Decrease** `entropy.max_healthy` (e.g., 7.0 → 7.5)
3. **Increase** `repetition.critical_score` (e.g., 40 → 60)
4. **Disable** semantic verifier or increase `min_failing_categories`

### Increase Detection (Catch More Degeneration)

1. **Decrease** `entropy.min_healthy` (e.g., 3.5 → 3.0)
2. **Increase** `entropy.max_healthy` (e.g., 7.0 → 6.5)
3. **Decrease** `repetition.critical_score` (e.g., 40 → 25)
4. **Enable** semantic verifier with lower `min_failing_categories`

## Testing Detection

Unit tests in `src/test/de2generator_test.py`:

```python
def test_entropy_healthy():
    result = entropy_detector.detect(normal_english_text)
    assert result.severity == "ok"

def test_entropy_degenerate():
    result = entropy_detector.detect("aaaa bbbb cccc dddd")
    assert result.severity == "candidate_reject"

def test_three_strike_escalation():
    for i in range(3):
        checkpoint = create_candidate_reject_checkpoint()
        strikes = manager.check(checkpoint)
        if i < 2:
            assert checkpoint.severity == "candidate_reject"
        else:
            assert checkpoint.severity == "rejected"
```

Run tests:
```bash
python -m pytest src/test/ -v -k detection
```

## Best Practices

1. **Start permissive** (high thresholds) → **gradually tighten** after observing real data
2. **Monitor logs** for false positives/negatives
3. **Use semantic verifier** for critical applications
4. **Adjust based on your model**: LLaMA vs Qwen vs GPT have different entropy distributions
5. **Test with real prompts** before deploying to production
