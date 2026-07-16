# Design Document — AI Memory and Mode Improvements

## Overview

This feature delivers three focused improvements to the Python/FastAPI + LangGraph AI backend:

1. **Session-Based Conversation Memory**: Replace the per-request UUID `thread_id` with a stable, session-scoped `thread_id` so that the LangGraph checkpointer can persist and reload conversation history across messages in the same chat session.
2. **University Research (`uni_info`) — Extended Specificity Detection**: Expand the `is_specific` flag to also fire when a query contains a recognisable department/program name (extracted by `_extract_program_from_text` or from `ner_context.program`), and make web queries department-aware.
3. **Career Research (`career_info`) — General/Specific Mode Separation**: Mirror the `is_specific` pattern from `uni_info` in `career_info`, routing the node through either a focused or a full-template code path based on query content.

All three changes are self-contained within the `ai/` Python package. No database schema changes or new external services are required.

---

## Architecture

The system follows an event-driven pipeline architecture built on LangGraph. The entry point is the FastAPI endpoint in `app/gate.py`, which resolves a `thread_id`, constructs a LangGraph `config`, and invokes `app_graph` (a compiled `StateGraph`).

```mermaid
flowchart TD
    Client -->|POST /b2b/ask_intelligent| GateEndpoint[gate.py\nask_intelligent_system]
    GateEndpoint -->|resolve thread_id| SessionManager[Session Manager\nresolve_thread_id()]
    SessionManager --> LangGraphConfig[LangGraph config\nthread_id + callbacks]
    LangGraphConfig --> AppGraph[app_graph.ainvoke]
    AppGraph --> NER[ner_node]
    NER -->|mode=research| UniInfo[uni_info_node]
    NER -->|mode=career| CareerInfo[career_info_node]
    UniInfo --> SpecificityDetector1[Specificity Detector\nis_specific]
    SpecificityDetector1 -->|True| QueryBuilderSpecific[Query Builder\ndept-specific queries]
    SpecificityDetector1 -->|False| QueryBuilderGeneral[Query Builder\ngeneral queries]
    CareerInfo --> SpecificityDetector2[Specificity Detector\nis_career_specific]
    SpecificityDetector2 -->|True, dept known| CareerSpecific[Specific Mode\nfocused queries + prompt]
    SpecificityDetector2 -->|True, dept missing| Clarification[Clarification Message\nno LLM/web call]
    SpecificityDetector2 -->|False| CareerGeneral[General Mode\n3 fixed queries + full template]
```

### Thread ID Resolution Flow

```mermaid
flowchart TD
    A[Incoming session_id] --> B{Is session_id valid?\nnon-empty, not None,\nnot 'default_session'}
    B -->|Yes| C[thread_id = session_id]
    B -->|No| D[thread_id = 'anon:{uuid4().hex}'\nno history reuse]
    C --> E[LangGraph reads existing\ncheckpointer state for thread_id]
    D --> F[LangGraph starts fresh state]
```

---

## Components and Interfaces

### 1. Session Manager (`app/gate.py`)

**Current behaviour**: `request_thread_id = f"{req.session_id}:{uuid4().hex}"` — a new thread every request.

**New behaviour**: A `resolve_thread_id(session_id: str | None) -> str` function with the following contract:

| Input `session_id` | Returned `thread_id` | History reused? |
|---|---|---|
| Non-empty, not `"default_session"` | `session_id` as-is | ✅ Yes |
| `None` | `f"anon:{uuid4().hex}"` | ❌ No |
| `""` (empty string) | `f"anon:{uuid4().hex}"` | ❌ No |
| `"default_session"` | `f"anon:{uuid4().hex}"` | ❌ No |
| Field absent from JSON | `f"anon:{uuid4().hex}"` | ❌ No (default `None`) |

The function is extracted from the inline logic in `ask_intelligent_system` to make it independently testable.

**Rate limiting and token tracking remain session_id-keyed** — no change to `B2BTokenTracker` or `check_rate_limit`.

The response body already returns whatever was provided; for anonymous sessions the newly generated `thread_id` should be added to the response so the frontend can reuse it:

```python
return {
    "answer": final_answer,
    "session_id": effective_session_id,  # echoes back what was used / generated
    "school": school_name,
    ...
}
```

### 2. Specificity Detector — `uni_info` (`nodes/uni_info.py`)

**Current**: `is_specific` is `True` only when `ner_web_query` is non-empty AND a topic keyword appears in the user text.

**New**: `is_specific` is `True` when the user text contains a university name AND at least one of:
- A detectable department/program name (`ner_context.program` is non-empty **or** `_extract_program_from_text(user_text)` returns non-None).
- A topic keyword from the existing list: `kulüp, yurt, staj, burs, ücret, kampüs, yemek, ulaşım, spor, müfredat, hoca`.

The check no longer requires `ner_web_query` to be non-empty — that was an accidental gate.

```python
detected_program = ner_ctx.get("program") or _extract_program_from_text(user_text)
is_specific = bool(
    detected_program
    or any(kw in user_text.lower() for kw in _UNI_TOPIC_KEYWORDS)
)
```

### 3. Query Builder — `uni_info` (`nodes/uni_info.py`)

**New** `_build_uni_queries(uni: str, program: str | None, keyword: str | None, user_text: str) -> list[str]`:

| Detected context | Query prefix | Minimum queries |
|---|---|---|
| Program only | `"{uni} {program}"` | 3 (müfredat, taban puan/kontenjan, öğrenci yorumları) |
| Keyword only | `"{uni} {keyword}"` | 3 (existing behaviour) |
| Both | `"{uni} {program} {keyword}"` | 3 |
| Neither (general) | `"{uni}"` | 4 (existing general queries) |

### 4. Specificity Detector — `career_info` (`nodes/career_info.py`)

**New** `_detect_career_specificity(user_text: str) -> bool`:

Returns `True` when the text contains at least one signal from these categories:
- Company/employer name (proper noun + "şirket", "a.ş.", "ltd", well-known company names such as "google", "amazon", "microsoft", "tüpraş", etc., detected via a regex/keyword list).
- Position or title keyword (e.g., "yazılım mühendisi", "data scientist", "proje yöneticisi", "stajyer").
- Geographic work condition (country or city + "çalış", "iş", "iş fırsatı").
- Specific course or technology (names from `_extract_program_from_text` or known technology names: "python", "java", "react", "sql", etc.).
- Evaluation adjective ("zor", "kolay", "tavsiye", "değer mi", "mantıklı mı").

Returns `False` when the text is only `<dept_name> + (nasıl | nedir | anlat | hakkında)` — the general-question pattern.

### 5. Career Info Node — General / Specific Path (`nodes/career_info.py`)

**General path (unchanged)**:
- 3 fixed queries: müfredat, kariyer/maaş, istihdam.
- System prompt: existing `_CAREER_INFO_SYSTEM_PROMPT` (full template).
- `human_content` signal: `"Soru tipi: GENEL — tam kariyer analizi yap"`.

**Specific path (new)**:
- `is_career_specific=True` and `dept` is detected.
- Queries: combine raw `user_text` with `dept` (e.g., `f"{dept} {user_text[:80]}"` as first query, plus 2 supporting queries).
- `human_content` signal: `"Soru tipi: SPESİFİK — sadece sorulan konuyu cevapla, tam şablon doldurma"`.
- System prompt: updated `_CAREER_INFO_SYSTEM_PROMPT` with a conditional section for specific questions (see Prompts section below).

**No-dept path (new)**:
- `is_career_specific=True` but `dept` is `""` after all extraction attempts.
- Return a clarification `AIMessage` immediately — no web search, no LLM call.

### 6. Prompts (`prompts/career_info.py`)

The prompt template gains a conditional section instructing the LLM to select the format based on the signal in `human_content`:

```
## FORMAT SEÇİMİ:
Eğer `human_content`'te "Soru tipi: SPESİFİK" yazıyorsa:
  - Sadece sorulan konuyu yanıtla.
  - Müfredat, kariyer yolları, istihdam şablonunu DOLDURMA.
  - Kısa ve odaklı yaz (maks. 300 kelime).

Eğer "Soru tipi: GENEL" yazıyorsa:
  - Mevcut tam şablonu kullan (Müfredat, Kariyer Yolları, İstihdam, vb.).
```

---

## Data Models

No new Pydantic models or database schemas are needed. The existing `AgentState` TypedDict and `AskRequest` Pydantic model are sufficient with minor additions.

### `AskRequest` — no schema change needed

`session_id` already defaults to `"default_session"`. The field validator can be updated to treat `"default_session"` as `None` internally, but the wire format is unchanged.

### `AgentState` — optional additions

Two optional boolean flags can be added to `AgentState` for traceability (not strictly required but aid debugging):

```python
# In core/state.py AgentState TypedDict
is_specific: Optional[bool]          # Set by uni_info_node
is_career_specific: Optional[bool]   # Set by career_info_node
```

### `resolve_thread_id` return type

```python
@dataclass
class ThreadResolution:
    thread_id: str
    is_anonymous: bool
    effective_session_id: str   # what to echo back to frontend
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Valid session_id is used as thread_id directly

*For any* non-empty string `s` that is not `"default_session"`, calling `resolve_thread_id(s)` SHALL return a `ThreadResolution` where `thread_id == s`.

**Validates: Requirements 1.1, 1.4**

---

### Property 2: Invalid session_id always produces a unique anonymous thread_id

*For any* value of `session_id` in `{None, "", "default_session"}`, calling `resolve_thread_id(session_id)` SHALL return a `thread_id` matching the pattern `r'^anon:[0-9a-f]{32}$'`. Furthermore, *for any* two independent calls with invalid session_ids, the returned `thread_id` values SHALL be distinct (no reuse).

**Validates: Requirements 1.3, 1.5, 1.6**

---

### Property 3: Uni specificity detector fires on program or keyword, never both required

*For any* user query string that contains a university name AND either (a) a detectable department/program name OR (b) a topic keyword from `_UNI_TOPIC_KEYWORDS`, the specificity detector SHALL return `is_specific=True`. Conversely, *for any* query containing only a university name with neither a program nor a keyword, the detector SHALL return `is_specific=False`.

**Validates: Requirements 2.1**

---

### Property 4: Uni query builder produces at least 3 queries with correct prefix

*For any* combination of `(uni, program, keyword)` where at least one of `program` or `keyword` is non-None, `_build_uni_queries` SHALL return a list of at least 3 query strings, each starting with a prefix that includes `uni` and whichever of `program`/`keyword` are present.

**Validates: Requirements 2.2**

---

### Property 5: Specific uni signal always appears in human_content when is_specific=True

*For any* invocation of `uni_info_node` where the specificity detector returns `True`, the assembled `human_content` string SHALL contain the substring `"SPESİFİK"` and SHALL NOT contain the section headers `"📌 Genel Bakış"`, `"🎓 Akademik Yapı"`, or `"⚖️ Artılar & Eksiler"`.

**Validates: Requirements 2.3**

---

### Property 6: Career specificity detector correctly classifies queries by signal category

*For any* user query string containing at least one signal from the career specificity categories (company name, job title, geographic condition, technology name, or evaluation adjective), `_detect_career_specificity` SHALL return `True`. *For any* query string consisting only of a department name followed by a general question verb (`nasıl`, `nedir`, `anlat`, `hakkında`), it SHALL return `False`.

**Validates: Requirements 3.1, 3.2**

---

### Property 7: Specific career signal appears in human_content when is_career_specific=True and dept is known

*For any* invocation of `career_info_node` where `is_career_specific=True` and `dept` is non-empty, the assembled `human_content` string SHALL contain `"SPESİFİK"`, and the generated query list SHALL include at least one query string that contains both the `dept` name and a substring from the user's original question.

**Validates: Requirements 3.3**

---

### Property 8: Missing dept in specific career mode always returns clarification, never searches

*For any* invocation of `career_info_node` where `is_career_specific=True` but no department can be extracted (dept is empty after all extraction attempts), the node SHALL return exactly one `AIMessage` whose content is a clarification request, and SHALL NOT invoke `_ddg_quick` or `llm_responder`.

**Validates: Requirements 3.6**

---

## Error Handling

### Session Manager

| Scenario | Behaviour |
|---|---|
| `session_id` is a valid string but exceeds 256 characters | Treat as anonymous (fall through to `anon:` path) |
| Redis checkpointer unavailable at `thread_id` read time | LangGraph falls back to `MemorySaver`; request succeeds with empty history |
| LangGraph `ainvoke` raises an exception | Existing `try/except` in `ask_intelligent_system` catches it and returns the error response unchanged |

### Specificity Detectors

| Scenario | Behaviour |
|---|---|
| `_extract_program_from_text` returns `None` but `ner_context.program` is non-empty | Use `ner_context.program` — no error |
| Program extraction raises an exception | Catch silently, treat as `None` (fall back to keyword-only detection) |
| All web queries timeout in specific mode | Return the existing timeout fallback message from `uni_info_node` / `career_info_node` |

### Career Clarification Path

| Scenario | Behaviour |
|---|---|
| `is_career_specific=True`, dept missing | Return `AIMessage` with clarification text exactly once; the clarification is not retried |
| Clarification is sent but user follows up without a dept | Next request re-enters `career_info_node`, detector fires again, clarification sent again (maximum once per request) |

---

## Testing Strategy

### Unit Tests

All new pure-logic functions are testable without I/O:

- `resolve_thread_id(session_id)` — covers all five input cases.
- `_build_uni_queries(uni, program, keyword, user_text)` — covers all four prefix combinations.
- `_detect_career_specificity(user_text)` — covers each signal category present/absent.
- `_UNI_TOPIC_KEYWORDS` membership — confirm the keyword list is complete.

**Framework**: `pytest` (already in use in the `ai/tests/` directory).

### Property-Based Tests

**Framework**: `hypothesis` (already installed — `.hypothesis/` directory present).

Each property test runs a minimum of **100 iterations**.

Tag format per test: `# Feature: ai-memory-and-mode-improvements, Property N: <property_text>`

| Property | Test function | Hypothesis strategy |
|---|---|---|
| P1 — Valid session_id maps to itself | `test_valid_session_id_maps_to_itself` | `st.text(min_size=1).filter(lambda s: s != "default_session")` |
| P2 — Invalid session_id → unique anon thread | `test_invalid_session_id_produces_unique_anon_thread` | `st.sampled_from([None, "", "default_session"])` + two independent calls |
| P3 — Uni specificity fires on program or keyword | `test_uni_specificity_detector` | `st.builds(query_with_program_or_keyword)` + `st.builds(query_general)` |
| P4 — Uni query builder produces ≥3 queries with prefix | `test_uni_query_builder_prefix_and_count` | `st.from_regex(r'[A-Za-zÇĞİÖŞÜçğışöü ]{3,30}')` for uni/program/keyword |
| P5 — Specific signal in human_content when is_specific | `test_uni_specific_signal_in_human_content` | Mock web/LLM; generate (uni, user_text, True/False) pairs |
| P6 — Career specificity classification | `test_career_specificity_detector` | `st.builds(query_with_signal)` + `st.builds(query_general_career)` |
| P7 — Specific career signal in human_content | `test_career_specific_signal_in_human_content` | Mock _ddg_quick and llm_responder; generate (dept, user_text) |
| P8 — No-dept career path returns clarification only | `test_career_no_dept_returns_clarification` | Queries with signals but no extractable dept; assert no mock calls |

### Integration Tests (1–3 examples each)

- **Memory continuity**: Send two messages in the same session; second response must reference context from the first.
- **Rate counter isolation**: Confirm rate counter key is `session_id`, not `thread_id`.
- **Uni mode end-to-end**: Specific query (BAÜ + EEM) → verify at least one dept-prefixed query in DDG calls.
- **Career mode end-to-end**: Specific query (dept + company) → verify focused prompt; general query (dept only) → verify full template.

### What Is NOT Property-Tested

- Redis checkpointer read/write — infrastructure; 1–2 integration examples.
- DDG query execution (network I/O) — mocked in all unit/property tests.
- LLM output content — non-deterministic; verified only that `llm_responder.ainvoke` is called with the correct `SystemMessage` + `HumanMessage`.
- The `_CAREER_INFO_SYSTEM_PROMPT` constant content — single example assertion.
