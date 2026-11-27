# ruff: noqa: E501
"""Query expansion module for industrial multi-query retrieval."""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from qorvexa.knowledge.rag import normalize_arabic

# Max character limit per generated variant
MAX_VARIANT_CHARS: int = 256

# Industrial bilingual synonyms & technical domain phrase mappings.
# Maps normalized phrases to their alternative domain expressions (Arabic <-> English).
_INDUSTRIAL_SYNONYMS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    # Air leakage / failure modes
    (
        re.compile(r"\b(?:air leakage|air leak|pneumatic leak)\b", re.IGNORECASE),
        ("تسريب هواء", "pneumatic pressure drop", "air compressor leakage"),
    ),
    (
        re.compile(r"(?<![\u0621-\u064A])(?:تسريب هواء|تسريب هوائي|تهريب هواء|تسريب الضاغط)(?![\u0621-\u064A])"),
        ("air leakage", "انخفاض ضغط الهواء", "pneumatic line leak"),
    ),
    # Pressure sensors & TP3
    (
        re.compile(r"\b(?:pressure sensor tp3)\b", re.IGNORECASE),
        ("مستشعر ضغط TP3", "pneumatic pressure transmitter TP3", "compressor discharge pressure TP3"),
    ),
    (
        re.compile(r"(?<![\u0621-\u064A])(?:مستشعر الضغط|مستشعر ضغط)(?![\u0621-\u064A])"),
        ("pressure sensor TP3", "pressure transmitter TP3", "قياس الضغط الهوائي"),
    ),
    # Oil leak
    (
        re.compile(r"\b(?:oil leak|oil leakage)\b", re.IGNORECASE),
        ("تسريب زيت", "lubricant leak", "oil level drop"),
    ),
    (
        re.compile(r"(?<![\u0621-\u064A])(?:تسريب زيت|تهريب زيت)(?![\u0621-\u064A])"),
        ("oil leak", "مستوي زيت التزييت", "lubrication system inspection"),
    ),
    # Temperature / Thermal overheating
    (
        re.compile(r"\b(?:overheating|thermal fault|high temperature)\b", re.IGNORECASE),
        ("ارتفاع درجة الحرارة", "thermal inspection", "motor temperature limit"),
    ),
    (
        re.compile(r"(?<![\u0621-\u064A])(?:ارتفاع درجة الحراره|ارتفاع الحراره|سخونه)(?![\u0621-\u064A])"),
        ("overheating", "thermal warning", "درجة حرارة المحرك"),
    ),
    # Valves / Solenoids
    (
        re.compile(r"\b(?:drain valve|exhaust valve|solenoid valve)\b", re.IGNORECASE),
        ("صمام تفريغ", "صمام تصريف", "pneumatic drain valve"),
    ),
    (
        re.compile(r"(?<![\u0621-\u064A])(?:صمام تفريغ|صمام تصريف|صمام التنفيس)(?![\u0621-\u064A])"),
        ("drain valve", "discharge valve", "solenoid valve status"),
    ),
    # Operating Regimes
    (
        re.compile(r"\b(?:unloaded state|unloaded regime|idle mode)\b", re.IGNORECASE),
        ("وضع تفريغ", "تشغيل بدون حمل", "unloaded compressor state"),
    ),
    (
        re.compile(r"(?<![\u0621-\u064A])(?:وضع تفريغ|بدون حمل|تشغيل بدون حمل)(?![\u0621-\u064A])"),
        ("unloaded regime", "unloaded state", "compressor idle mode"),
    ),
    (
        re.compile(r"\b(?:loaded state|loaded regime|pumping mode)\b", re.IGNORECASE),
        ("وضع تحميل", "تشغيل بالحمل", "air production cycle"),
    ),
    (
        re.compile(r"(?<![\u0621-\u064A])(?:وضع تحميل|بالحمل|تشغيل بالحمل)(?![\u0621-\u064A])"),
        ("loaded regime", "loaded state", "pressure build up"),
    ),
)


@runtime_checkable
class QueryExpander(Protocol):
    """Protocol for query expansion components in multi-query retrieval."""

    def expand_query(self, query: str, *, max_variants: int = 3) -> list[str]:
        """Expand original query into a list of queries including original + variants."""
        ...


class PassThroughQueryExpander:
    """Null expander returning only the sanitized original query."""

    def expand_query(self, query: str, *, max_variants: int = 3) -> list[str]:
        cleaned = query.strip()
        return [cleaned] if cleaned else []


class RuleBasedQueryExpander:
    """Deterministic bilingual (Arabic-English) query expander using industrial domain terms."""

    def __init__(
        self,
        rules: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] | None = None,
    ) -> None:
        self._rules = rules or _INDUSTRIAL_SYNONYMS

    def expand_query(self, query: str, *, max_variants: int = 3) -> list[str]:
        cleaned = query.strip()[:MAX_VARIANT_CHARS]
        if not cleaned:
            return []

        variants: list[str] = [cleaned]
        seen_normalized: set[str] = {normalize_arabic(cleaned).lower()}
        limit = max(0, min(max_variants, 5))

        normalized_q = normalize_arabic(cleaned)

        try:
            for pattern, alternatives in self._rules:
                if len(variants) >= 1 + limit:
                    break

                match = pattern.search(cleaned) or pattern.search(normalized_q)
                if not match:
                    continue

                for alt in alternatives:
                    if len(variants) >= 1 + limit:
                        break

                    # Construct alternative: substitute matching phrase or append domain synonym
                    candidate = pattern.sub(alt, cleaned)
                    if candidate == cleaned:
                        candidate = f"{cleaned} {alt}"

                    candidate = " ".join(candidate.split())[:MAX_VARIANT_CHARS].strip()
                    norm_candidate = normalize_arabic(candidate).lower()

                    if candidate and norm_candidate not in seen_normalized:
                        seen_normalized.add(norm_candidate)
                        variants.append(candidate)

        except Exception:
            # Safe fallback: guarantee original query is returned
            return [cleaned]

        return variants[: 1 + limit]
