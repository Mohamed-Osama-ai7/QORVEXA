"""The multi-stage understanding pipeline and its multi-turn session.

INPUT -> SOURCE_PROCESSING -> LANGUAGE_UNDERSTANDING -> FACTORY_TERMINOLOGY_RESOLUTION ->
ENTITY_RECOGNITION -> CLAIM_EXTRACTION -> EVIDENCE_VERIFICATION -> SEMANTIC_ANALYSIS ->
CONTRADICTION_ANALYSIS -> DOMAIN_MAPPING -> GAP_ANALYSIS -> CLARIFICATION_PLAN ->
STRUCTURED_UNDERSTANDING. Every stage reports an explicit result; failures are explicit and fail
closed (never an optimistic partial result). The report is deterministic, hashed and idempotent for
identical inputs, terminology snapshot, pipeline version and provider identity.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from qorvexa.industrial.understanding.adapters import (
    LLMProvider,
    OntologyAdapter,
    ProviderUnavailable,
    call_provider,
    drafts_to_claims,
)
from qorvexa.industrial.understanding.claims import extract_segment_claims
from qorvexa.industrial.understanding.clarification import (
    ClarificationState,
    analyze_gaps,
    choose_language,
    plan_questions,
)
from qorvexa.industrial.understanding.contradictions import find_contradictions
from qorvexa.industrial.understanding.entities import TermUse, find_entities
from qorvexa.industrial.understanding.grounding import verify_claim
from qorvexa.industrial.understanding.mapping import map_to_candidates
from qorvexa.industrial.understanding.models import (
    MAX_CLAIMS,
    PIPELINE_VERSION,
    Claim,
    EntityMention,
    Grounding,
    GroundingResult,
    LanguageInfo,
    Readiness,
    Segment,
    SourceDocument,
    Stage,
    StageResult,
    StageStatus,
    UnderstandingReport,
    UnsupportedFeature,
)
from qorvexa.industrial.understanding.source import (
    UnderstandingInputError,
    detect_language,
    segment,
    source_sha256,
    validate_document,
)
from qorvexa.industrial.understanding.terminology import TerminologyBook, TermResolution


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class UnderstandingSession:
    """One factory conversation. Terminology comes only from the factory's own book."""

    def __init__(
        self,
        book: TerminologyBook,
        *,
        provider: LLMProvider | None = None,
        ontology: OntologyAdapter | None = None,
    ) -> None:
        self._book = book
        self._provider = provider
        self._ontology = ontology
        self._docs: dict[str, SourceDocument] = {}
        self._claims: list[Claim] = []
        self._entities: list[EntityMention] = []
        self._langs: list[LanguageInfo] = []
        self._turn_langs: list[LanguageInfo] = []
        self._uses: list[TermResolution] = []
        self._unsupported: list[UnsupportedFeature] = []
        self._rejected: set[str] = set()
        self._clar = ClarificationState()
        self._by_source: dict[tuple[str, str, str], UnderstandingReport] = {}
        self._last_subject: dict[str, EntityMention | None] = {}
        self._llm_status = "NOT_USED" if provider is None else "AVAILABLE"
        self._currency: str | None = None

    @property
    def clarification_state(self) -> ClarificationState:
        return self._clar

    # ------------------------------------------------------------------ public
    def submit(self, doc: SourceDocument) -> UnderstandingReport:
        stages: list[StageResult] = [
            StageResult(stage=Stage.INPUT, status=StageStatus.OK, summary=doc.source_id)
        ]
        if doc.factory_id != self._book.factory_id:
            raise UnderstandingInputError(
                "FACTORY_MISMATCH", "document belongs to a different factory"
            )
        validate_document(doc)
        sk = (doc.source_id, source_sha256(doc), self._book.snapshot_sha256())
        if sk in self._by_source:
            return self._by_source[sk]  # idempotent resubmission
        key = self._idem_key(doc)
        if doc.source_id in self._docs:
            raise UnderstandingInputError(
                "DUPLICATE_SOURCE", f"{doc.source_id} was already submitted"
            )
        self._currency = doc.default_currency or self._currency
        self._docs[doc.source_id] = doc
        entries = self._book.entries()

        norm, segs = segment(doc)
        stages.append(
            StageResult(
                stage=Stage.SOURCE_PROCESSING,
                status=StageStatus.OK,
                summary=f"{len(segs)} segments",
            )
        )
        langs = [detect_language(s) for s in segs]
        self._langs.extend(langs)
        self._turn_langs = langs
        stages.append(
            StageResult(
                stage=Stage.LANGUAGE_UNDERSTANDING,
                status=StageStatus.OK,
                summary=",".join(sorted({x.primary for x in langs})),
            )
        )

        claims: list[Claim] = []
        unsupported: list[UnsupportedFeature] = []
        prev = self._last_subject.get(doc.source_id[:0])  # subject context is per conversation
        n_terms = 0
        for seg in segs:
            ents, uses = find_entities(doc, seg, norm, entries)
            self._uses.extend(u.resolution for u in uses)
            n_terms += len(uses)
            self._entities.extend(ents)
            seg_claims, seg_unsup = extract_segment_claims(doc, seg, norm, ents, uses, prev)
            claims.extend(seg_claims)
            unsupported.extend(seg_unsup)
            subj = next((e for e in ents if e.entity_type not in {"SHIFT", "OPERATION"}), None)
            prev = subj or prev
        self._last_subject[""] = prev
        stages.append(
            StageResult(
                stage=Stage.FACTORY_TERMINOLOGY_RESOLUTION,
                status=StageStatus.OK,
                summary=f"{n_terms} term matches",
            )
        )
        stages.append(
            StageResult(
                stage=Stage.ENTITY_RECOGNITION,
                status=StageStatus.OK,
                summary=f"{len(self._entities)} mentions",
            )
        )

        llm_note = "rules only"
        ext_status = StageStatus.OK
        if self._provider is not None:
            try:
                resp = call_provider(self._provider, "extract_claims", doc.text)
                drafts, dropped = drafts_to_claims(doc, resp.items, resp.provider_id)
                claims.extend(drafts)
                self._llm_status = f"OK:{resp.provider_id}"
                llm_note = f"rules + {len(drafts)} LLM drafts ({dropped} malformed dropped)"
            except ProviderUnavailable as exc:
                self._llm_status, ext_status = f"UNAVAILABLE:{exc}", StageStatus.UNAVAILABLE
                llm_note = "rules only; LLM provider unavailable"
        else:
            self._llm_status = "NOT_USED"
        if len(self._claims) + len(claims) > MAX_CLAIMS:
            raise UnderstandingInputError("TOO_MANY_CLAIMS", f"more than {MAX_CLAIMS} claims")
        stages.append(
            StageResult(stage=Stage.CLAIM_EXTRACTION, status=ext_status, summary=llm_note)
        )

        seen = {c.claim_id for c in self._claims}
        new_claims = [c for c in claims if c.claim_id not in seen]
        results = {c.claim_id: verify_claim(doc, c) for c in new_claims}
        for c in new_claims:
            if results[c.claim_id].outcome is Grounding.UNSUPPORTED:
                self._rejected.add(c.claim_id)
        self._claims.extend(new_claims)
        self._unsupported.extend(unsupported)
        stages.append(
            StageResult(
                stage=Stage.EVIDENCE_VERIFICATION,
                status=StageStatus.OK,
                summary=f"{len(self._rejected)} unsupported rejected",
            )
        )
        stages.append(
            StageResult(
                stage=Stage.SEMANTIC_ANALYSIS,
                status=StageStatus.OK,
                summary="semantic types assigned",
            )
        )

        report = self._finish(doc, stages, key)
        self._by_source[sk] = report
        return report

    # ------------------------------------------------------------------ internals
    def _idem_key(self, doc: SourceDocument) -> str:
        pid = getattr(self._provider, "provider_id", "none")
        parts = [
            doc.factory_id, doc.source_id, source_sha256(doc), str(doc.turn), doc.line_id or "",
            doc.department or "", doc.speaker_role or "", doc.default_currency or "",
            self._book.snapshot_sha256(), PIPELINE_VERSION, str(pid),
            ",".join(sorted(self._docs)),
        ]  # fmt: skip
        return _digest("|".join(parts))

    def _grounding(self) -> dict[str, GroundingResult]:
        return {c.claim_id: verify_claim(self._docs[c.source_id], c) for c in self._claims}

    def _finish(
        self, doc: SourceDocument, stages: list[StageResult], key: str
    ) -> UnderstandingReport:
        claims = sorted(self._claims, key=lambda c: (c.turn, c.orig_start, c.claim_id))
        grounding = self._grounding()
        live = [c for c in claims if c.claim_id not in self._rejected]
        contradictions = find_contradictions(live, grounding, self._entities)
        stages.append(
            StageResult(
                stage=Stage.CONTRADICTION_ANALYSIS,
                status=StageStatus.OK,
                summary=f"{len(contradictions)} findings",
            )
        )
        candidates = map_to_candidates(live, grounding, self._entities, self._unsupported)
        stages.append(
            StageResult(
                stage=Stage.DOMAIN_MAPPING,
                status=StageStatus.OK,
                summary=f"{len(candidates)} candidates",
            )
        )
        unknown_concepts: tuple[str, ...] = ()
        if self._ontology is not None:
            unknown_concepts = tuple(
                sorted(
                    {
                        e.concept
                        for e in self._book.entries()
                        if e.status.value == "CONFIRMED" and not self._ontology.knows(e.concept)
                    }
                )
            )
        res = list({(r.surface, r.status.value, r.term_id or ""): r for r in self._uses}.values())
        gaps = analyze_gaps(
            live,
            grounding,
            self._entities,
            res,
            contradictions,
            self._unsupported,
            candidates,
            self._currency,
            unknown_concepts,
        )
        stages.append(
            StageResult(
                stage=Stage.GAP_ANALYSIS, status=StageStatus.OK, summary=f"{len(gaps)} gaps"
            )
        )
        lang = choose_language(self._turn_langs)  # reply in the latest speaker's language
        questions, self._clar = plan_questions(gaps, self._clar, lang)
        stages.append(
            StageResult(
                stage=Stage.CLARIFICATION_PLAN,
                status=StageStatus.OK,
                summary=f"{len(questions)} questions ({lang})",
            )
        )
        blocking = any(g.blocking for g in gaps) or bool(self._unsupported)
        failed = any(s.status is StageStatus.FAILED for s in stages)
        if failed or any(g.code in {"CONTRADICTION", "UNSUPPORTED_FEATURE"} for g in gaps):
            ready = Readiness.BLOCKED
        elif blocking:
            ready = Readiness.NEEDS_CLARIFICATION
        else:
            ready = Readiness.CANDIDATES_FOR_HUMAN_REVIEW
        stages.append(
            StageResult(
                stage=Stage.STRUCTURED_UNDERSTANDING, status=StageStatus.OK, summary=ready.value
            )
        )
        # unique deterministic entity list
        ents = sorted(
            {e.mention_id: e for e in self._entities}.values(),
            key=lambda e: (e.orig_start, e.mention_id),
        )
        report = UnderstandingReport(
            idempotency_key=key,
            factory_id=self._book.factory_id,
            source_ids=tuple(sorted(self._docs)),
            source_sha256=tuple(source_sha256(self._docs[s]) for s in sorted(self._docs)),
            stages=tuple(stages),
            languages=tuple(self._langs),
            entities=tuple(ents),
            claims=tuple(claims),
            grounding=tuple(grounding[c.claim_id] for c in claims),
            rejected_claim_ids=tuple(sorted(self._rejected)),
            term_resolutions=tuple(
                sorted((r.surface, r.status.value, r.term_id or "") for r in self._uses)
            ),
            contradictions=tuple(contradictions),
            gaps=tuple(gaps),
            unsupported_features=tuple(self._unsupported),
            candidates=tuple(candidates),
            questions=tuple(questions),
            readiness=ready,
            llm_status=self._llm_status,
        )
        sealed = report.model_copy(
            update={"report_sha256": _digest(report.model_dump_json(exclude={"report_sha256"}))}
        )
        return sealed


__all__ = ["Callable", "Segment", "TermUse", "UnderstandingSession"]
