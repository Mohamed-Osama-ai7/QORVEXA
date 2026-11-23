# Industrial Understanding & Factory Language Intelligence

Package `backend/src/qorvexa/industrial/understanding/` · tests `backend/tests/test_industrial_understanding.py` (43).
Output is UNTRUSTED understanding: never mathematical validation, solver readiness or human approval.

## Pipeline (explicit, versioned: `qorvexa-understanding/1`, `understanding-pipeline/1.0`)
`INPUT → SOURCE_PROCESSING → LANGUAGE_UNDERSTANDING → FACTORY_TERMINOLOGY_RESOLUTION → ENTITY_RECOGNITION → CLAIM_EXTRACTION →
EVIDENCE_VERIFICATION → SEMANTIC_ANALYSIS → CONTRADICTION_ANALYSIS → DOMAIN_MAPPING → GAP_ANALYSIS → CLARIFICATION_PLAN →
STRUCTURED_UNDERSTANDING`. Each stage returns a `StageResult` (OK / FAILED / UNAVAILABLE / SKIPPED). `UnderstandingSession.submit(doc)`
is multi-turn (claims, entities, contradictions, gaps and clarification state accumulate), idempotent (same source + terminology
snapshot returns the same report object), bounded (20 000 chars, 400 segments, 500 claims) and deterministic: the report hash does
not depend on wall-clock time or `PYTHONHASHSEED` (tested across processes).

| Module | Role |
|---|---|
| `source.py` | verbatim text, search-only normalization with exact normalized→original offset maps, clause segmentation (terminators stay in the clause), script / code-switching / Arabizi / register *hints* |
| `numbers.py` | exact digits (Arabic-Indic too), spoken Arabic/English numbers, ranges, percent, rates, units via the Industrial Units Engine, currency only with factory context, ambiguity flags, explicit zero vs absence |
| `lexicon.py` | curated exact-phrase generic hints (GENERIC_UNCONFIRMED) and semantic cue lists; no fuzzy matching |
| `terminology.py` | factory-scoped, versioned, auditable dictionary; `TerminologyStore` protocol with in-memory and SQLite implementations; context-aware resolution |
| `entities.py` | confirmed-term entities, explicit designators ("Machine A", "line 2", shifts), generic nouns left unresolved |
| `claims.py` | compositional claim extraction: type, negation, condition, scope (shift/line/time), exact quote and offsets |
| `grounding.py` | independent verification against the ORIGINAL source: GROUNDED / PARTIALLY_GROUNDED / UNSUPPORTED / AMBIGUOUS / CONFLICTED |
| `contradictions.py` | same-scope conflicts only (exact unit conversion); distinct machines/shifts/targets/history never conflict; evidence preserved |
| `mapping.py` | grounded claims → Production Planning candidates; `emit_proposals` → untrusted ledger proposals |
| `clarification.py` | gaps (dependency tiers, no business weights) and multi-turn questions in en / ar / ar-EG |
| `adapters.py` | provider-neutral contracts: `LLMProvider`, `UnavailableProvider`, `OntologyAdapter`, strict draft parsing, bounded retries |
| `benchmark.py` | synthetic benchmark + scripted adversarial provider double |

## Behaviour that is implemented
- **Semantic types are never equivalent:** "Machine A produces 100 units per shift" (FACT) ≠ "The target is 100" (TARGET) ≠ "used to produce 100"
  (HISTORICAL) ≠ "can produce 100 if Machine B is available" (CONDITIONAL, with the condition text). Questions, hypotheses and
  recommendations are not facts. Downtime ≠ slow operation; scrap/waste ≠ defects; material shortage ≠ excess consumption; capacity ≠
  throughput; price ≠ margin (price stays NEEDS_CLARIFICATION).
- **Grounding** re-parses the quote itself: quote at offsets, number, unit, subject, negation, condition and target/history cues. A model
  cannot certify its own output; a draft with a fabricated quote/number/subject is UNSUPPORTED and dropped from mapping; a flipped negation,
  target-as-actual or dropped condition is CONFLICTED.
- **Terminology:** LLM/system actors can only propose. Only a human `TERMINOLOGY_REVIEWER` whose `Principal.factories` contains the factory can
  confirm / reject / deprecate / correct / roll back. Every change appends a version and a history event (actor, time, note); nothing is
  overwritten; rollback is a new version. Same-scope conflicts raise `TERM_CONFLICT`; a narrower scope (line / department) is a legal override.
  Resolution uses factory, line, department, machine ids, speaker role, surrounding text and declared alternatives with context cues; a
  confirmed or ambiguous factory term overrides the generic hint on its span (an ambiguous term never falls back to a guess). A
  `TerminologyBook` is bound to one factory; there is no cross-factory read path; a session rejects documents of another factory; the SQLite store
  detects rows that do not belong to their key.
- **No authoritative entity from an uncertain expression:** only CONFIRMED terms with an entity type give a `canonical_id`.
- **Unsupported features** (setup/changeover, sequencing, batch, integer, schedule, planned maintenance) stay visible, create blocking
  `feature:` candidates and a BLOCKED readiness; they are never dropped.
- **Clarification:** language follows the latest speaker (Egyptian colloquial/Arabizi → natural ar-EG; formal markers → ar; else en); each
  question carries why, affected fields and a blocking flag; resolved gaps disappear, open ones are re-asked with a counter, never duplicated.
- **Integration:** candidates become UNTRUSTED PROPOSALS in the claim ledger via `emit_proposals` (requires a non-human `EXTRACTOR` principal).
  Humans confirm in the ledger; the formulation, units gate, admission gate and decision engine then apply unchanged.

## Benchmark (synthetic, same-author, regenerable: `understanding.benchmark.run_benchmark()`)
56 cases: 40 generic-language, 16 factory-specific; 10 scripted adversarial provider drafts (9 must be rejected/flagged, 1 valid accepted).
Measured on the committed code:

| Metric | Result |
|---|---|
| claim recall / grounded-claim precision | 35/35 · 35/35 |
| numeric & unit grounding | 26/26 |
| forbidden claim types avoided (target↔actual, history, etc.) | 15/15 |
| contradiction cases (incl. no-false-positive scopes, exact conversion) | 7/7 |
| required gaps found | 8/8 · unsupported features 3/3 |
| terminology resolution / ambiguity | 7/7 · candidate mapping 10/10 |
| fabricated/inconsistent LLM drafts rejected or flagged | 9/9 (valid draft accepted 1/1) |
| determinism (same process; plus cross-process hash-seed test) | 56/56 |

**These numbers are NOT accuracy claims.** The cases were written by the engine's author; perfect scores show the implemented rules do
what they were designed for, not that real factory language is understood. Real coverage needs real, consented, de-identified transcripts and
independent annotation (next dependency).

## Provider-dependent vs implemented
Implemented and tested: everything above using deterministic rules. Provider-dependent (Antigravity): a concrete `LLMProvider` for claim and
terminology proposals (`call_provider`, `drafts_to_claims`, `propose_terms_with_llm` already enforce budgets, strict parsing, grounding and
PROPOSED-only storage). With no provider the pipeline reports `llm_status=NOT_USED`/`UNAVAILABLE` and still runs the rule extractor.

## Contracts for Antigravity
- Build `Principal(..., factories=frozenset({factory_id}))` ONLY from the authenticated token (reviewer: `Role.TERMINOLOGY_REVIEWER`, human; service
  extractor: `Role.EXTRACTOR`, non-human). `Principal.factories=None` is not accepted for terminology writes (`FACTORY_FORBIDDEN`).
- One `TerminologyBook(store, factory_id)` and one `UnderstandingSession(book, provider=, ontology=)` per factory conversation; persist the
  terminology store (`SqliteTerminologyStore(path)` or a `TerminologyStore` implementation) and keep session state server-side.
- Show per claim: quote with highlighted offsets, grounding outcome and reasons, semantic type, negation, condition; show contradictions with
  both claims; show gaps/questions with `why`, `affected`, `blocking`; show candidates with status SUPPORTED / NEEDS_CLARIFICATION /
  BLOCKING_FEATURE. Never label anything "validated"; display `report.disclaimer`.
- Terminology review UI: PROPOSED entries with provenance; confirm/reject/deprecate/correct/rollback with a required note; show
  `TERM_CONFLICT` and history.
- Provider adapter: implement `LLMProvider.complete_json(ProviderRequest) -> ProviderResponse`; treat document text as data; honour
  `timeout_seconds` / `max_output_tokens`; raise `ProviderUnavailable` when unavailable.
- Route `report.candidates` to the ledger only through `emit_proposals`; humans confirm via the existing governed-session endpoints.

## Limitations (genuine)
- Rule coverage is a seed vocabulary of exact phrases; unseen dialect, slang, spellings, morphology and long-range context are not understood
  without a provider (and a provider's output is only as good as its grounding allows).
- One negation/condition/type per clause; compound sentences are split only at terminators and a few contrast words. Units must be adjacent to the
  number (or follow "of <resource>" before a "per" cue).
- Register detection (colloquial vs formal) is a marker heuristic, not dialect identification. Spoken-number grammar is partial.
- Entity linking needs confirmed terminology; subject carry-over across clauses is limited to the previous mention and is only PARTIALLY_GROUNDED.
- Demand/order semantics (commitment vs cap) and price vs margin stay human decisions by design.
