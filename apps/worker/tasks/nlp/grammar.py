"""compute_grammar_metrics (Spec 03 §2.2, §4.3): T-unit-based syntactic
complexity (Mean Length of T-unit, Clauses per T-unit, Dependent Clause
Ratio, complex nominals, Coordination Index, structural range) plus
LanguageTool-backed accuracy (error-free-clause ratio, errors/100 words,
error-type taxonomy). Computed per phase (part1/part2/part3) and as a
session aggregate, written to feature_vectors.
"""
import uuid

from celery_app import app
from feature_vectors import write_feature_vector
from nlp_common import (
    PHASE_BUCKET_ORDER,
    TranscriptWord,
    load_words_by_phase,
    parse_words,
    segment_into_utterances,
)
from providers.grammar_check import GrammarCheckProvider, LanguageToolProvider

TASK_NAME = "compute_grammar_metrics"

# T-unit = one main (independent) clause + any subordinate clauses
# attached to it — the standard applied-linguistics unit for spoken/
# written syntactic complexity (Spec 03 §4.3).
DEPENDENT_CLAUSE_DEPS = {"advcl", "ccomp", "xcomp", "acl", "relcl", "csubj", "csubjpass"}
SUBJECT_DEPS = {"nsubj", "nsubjpass", "expl"}

# LanguageTool's real rule-ID vocabulary isn't something this environment
# can verify (no JRE here) — matched by substring against common naming
# conventions rather than an exact, unverifiable rule-ID list.
_ERROR_TAXONOMY_KEYWORDS = (
    ("subject_verb_agreement", ("AGREEMENT",)),
    ("article", ("ARTICLE", "A_UNCOUNTABLE")),
    ("preposition", ("PREP",)),
    ("tense", ("TENSE", "VERB_FORM")),
    ("word_order", ("WORD_ORDER",)),
)
# Grammar accuracy only counts GRAMMAR/TYPOS-category errors — word-choice/
# collocation/style errors are lexical.py's domain (Spec 03 §4.2), not
# double-counted here.
GRAMMAR_ERROR_CATEGORIES = {"GRAMMAR", "TYPOS"}


def _categorize_error(rule_id: str) -> str:
    upper = rule_id.upper()
    for bucket, keywords in _ERROR_TAXONOMY_KEYWORDS:
        if any(keyword in upper for keyword in keywords):
            return bucket
    return "other"


def _count_tunits_and_clauses(doc) -> dict:
    t_unit_heads = {t.i for t in doc if t.dep_ == "ROOT"}

    # Coordinate independent clauses ("I went home and I ate dinner") are
    # separate T-units, not one T-unit with a compound predicate — a
    # `conj` of a T-unit head only counts as its own T-unit if it has an
    # explicit subject of its own.
    changed = True
    while changed:
        changed = False
        for token in doc:
            if (
                token.dep_ == "conj"
                and token.head.i in t_unit_heads
                and token.i not in t_unit_heads
                and any(child.dep_ in SUBJECT_DEPS for child in token.children)
            ):
                t_unit_heads.add(token.i)
                changed = True

    dependent_clauses = sum(1 for t in doc if t.dep_ in DEPENDENT_CLAUSE_DEPS)
    coordinated_clauses = sum(1 for t in doc if t.dep_ == "conj" and t.pos_ in ("VERB", "AUX"))
    complex_nominals = sum(
        1
        for t in doc
        if t.pos_ in ("NOUN", "PROPN") and any(c.dep_ in ("relcl", "acl") for c in t.children)
    )

    return {
        "t_units": len(t_unit_heads),
        "dependent_clauses": dependent_clauses,
        "coordinated_clauses": coordinated_clauses,
        "complex_nominals": complex_nominals,
    }


def _empty_metrics() -> dict:
    return {
        "mean_length_of_t_unit": 0.0,
        "clauses_per_t_unit": 0.0,
        "dependent_clause_ratio": 0.0,
        "complex_nominals_per_clause": 0.0,
        "coordination_index": 0.0,
        "structural_range": {
            "distinct_tense_aspect_forms": [],
            "has_passive_voice": False,
            "has_conditional_structure": False,
            "has_relative_clause": False,
            "distinct_modals": [],
            "structural_diversity_count": 0,
        },
        "error_free_clause_ratio": 0.0,
        "grammar_error_count": 0,
        "errors_per_100_words": 0.0,
        "error_type_taxonomy": {},
        "t_unit_count": 0,
        "total_words": 0,
    }


def _metrics_for_words(words: list[TranscriptWord], provider: GrammarCheckProvider) -> dict:
    if not words:
        return _empty_metrics()

    chunks = segment_into_utterances(words, pause_threshold_s=0.6)
    total_t_units = 0
    total_dependent_clauses = 0
    total_coordinated_clauses = 0
    total_complex_nominals = 0
    tenses: set[str] = set()
    has_passive = False
    has_conditional = False
    has_relative_clause = False
    modals: set[str] = set()

    for chunk in chunks:
        doc = parse_words([w.word for w in chunk])
        counts = _count_tunits_and_clauses(doc)
        total_t_units += counts["t_units"]
        total_dependent_clauses += counts["dependent_clauses"]
        total_coordinated_clauses += counts["coordinated_clauses"]
        total_complex_nominals += counts["complex_nominals"]

        for token in doc:
            if token.tag_ == "VBD":
                tenses.add("past_simple")
            elif token.tag_ in ("VBP", "VBZ"):
                tenses.add("present_simple")
            elif token.tag_ == "VBG":
                tenses.add("progressive")
            elif token.tag_ == "VBN":
                tenses.add("perfect_or_passive_participle")
            if token.dep_ in ("nsubjpass", "auxpass"):
                has_passive = True
            if token.lower_ == "if":
                has_conditional = True
            if token.dep_ == "relcl":
                has_relative_clause = True
            if token.tag_ == "MD":
                modals.add(token.lower_)

    total_clauses = total_t_units + total_dependent_clauses
    total_words = len(words)

    text = " ".join(w.word for w in words)
    errors = provider.check(text)
    grammar_errors = [e for e in errors if e.category in GRAMMAR_ERROR_CATEGORIES]
    taxonomy: dict[str, int] = {}
    for error in grammar_errors:
        bucket = _categorize_error(error.rule_id)
        taxonomy[bucket] = taxonomy.get(bucket, 0) + 1

    error_free_clause_ratio = (
        max(0.0, 1 - min(len(grammar_errors), total_clauses) / total_clauses)
        if total_clauses
        else 0.0
    )

    return {
        "mean_length_of_t_unit": round(total_words / total_t_units, 2) if total_t_units else 0.0,
        "clauses_per_t_unit": round(total_clauses / total_t_units, 2) if total_t_units else 0.0,
        "dependent_clause_ratio": round(total_dependent_clauses / total_clauses, 3)
        if total_clauses
        else 0.0,
        "complex_nominals_per_clause": round(total_complex_nominals / total_clauses, 3)
        if total_clauses
        else 0.0,
        "coordination_index": round(total_coordinated_clauses / total_clauses, 3)
        if total_clauses
        else 0.0,
        "structural_range": {
            "distinct_tense_aspect_forms": sorted(tenses),
            "has_passive_voice": has_passive,
            "has_conditional_structure": has_conditional,
            "has_relative_clause": has_relative_clause,
            "distinct_modals": sorted(modals),
            "structural_diversity_count": (
                len(tenses)
                + int(has_passive)
                + int(has_conditional)
                + int(has_relative_clause)
                + int(bool(modals))
            ),
        },
        "error_free_clause_ratio": round(error_free_clause_ratio, 3),
        "grammar_error_count": len(grammar_errors),
        "errors_per_100_words": round(len(grammar_errors) / total_words * 100, 2)
        if total_words
        else 0.0,
        "error_type_taxonomy": taxonomy,
        "t_unit_count": total_t_units,
        "total_words": total_words,
    }


@app.task(name="tasks.nlp.compute_grammar_metrics", bind=True, max_retries=3, time_limit=180)
def compute_grammar_metrics(
    self, session_id: str, *, provider: GrammarCheckProvider | None = None
) -> dict:
    session_uuid = uuid.UUID(session_id)
    active_provider = provider or LanguageToolProvider()

    try:
        words_by_phase = load_words_by_phase(session_uuid)

        results = {}
        session_words: list[TranscriptWord] = []
        for phase in PHASE_BUCKET_ORDER:
            phase_words = words_by_phase.get(phase, [])
            session_words.extend(phase_words)
            metrics = _metrics_for_words(phase_words, active_provider)
            metrics["provenance"] = {
                "source": getattr(active_provider, "source_name", "unknown"),
                "word_count": len(phase_words),
            }
            write_feature_vector(session_uuid, "grammar", phase, metrics)
            results[phase] = metrics

        session_metrics = _metrics_for_words(session_words, active_provider)
        session_metrics["provenance"] = {
            "source": getattr(active_provider, "source_name", "unknown"),
            "word_count": len(session_words),
        }
        write_feature_vector(session_uuid, "grammar", "session", session_metrics)
        results["session"] = session_metrics

        return results
    except Exception as exc:
        raise self.retry(exc=exc) from exc
