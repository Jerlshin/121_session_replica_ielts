"""compute_lexical_metrics (Spec 03 §2.2, §4.2): MTLD/MATTR lexical
diversity, a CEFR-band proxy distribution, off-top-5000 rarity ratio,
collocation/idiom matching, and LanguageTool word-choice/collocation
flags. Computed per phase (part1/part2/part3) and as a session aggregate,
written to feature_vectors.

The spec's "LLM-assisted idiomatic-phrasing flagging pass" (supplementing
the statistical collocation matcher) is a documented extension point, not
built this phase — no concrete implementation to build against without
inventing one; same reasoning as the neural grammar-error second opinion
in providers/grammar_check.py.
"""
import re
import uuid

from celery_app import app
from feature_vectors import write_feature_vector
from nlp_common import PHASE_BUCKET_ORDER, TranscriptWord, load_words_by_phase, parse_words, zipf
from providers.grammar_check import GrammarCheckProvider, LanguageToolProvider
from tasks.nlp.lexicons import COLLOCATIONS_AND_IDIOMS

TASK_NAME = "compute_lexical_metrics"

# Documented proxy for a licensed graded lexicon (Oxford 5000 / EVP) — no
# such asset is available; wordfreq's real Zipf frequency drives these
# thresholds instead. Same posture as Spec 01 §7's rubric-assets pattern
# (real licensed content is a later ops/legal decision, not fabricated
# here).
CEFR_PROXY_BANDS = (
    ("A1_A2", 5.5),
    ("B1", 4.5),
    ("B2", 3.5),
    ("C1_C2", float("-inf")),
)
# Approx Zipf floor for "top ~5000 English words" (COCA-style rarity
# proxy) — same wordfreq data backs this, no separate frequency asset.
TOP_5000_ZIPF_FLOOR = 4.0

LEXICAL_ERROR_CATEGORIES = {"COLLOCATIONS", "STYLE", "REDUNDANCY", "WORD_CHOICE"}

MATTR_WINDOW = 25
MTLD_TTR_THRESHOLD = 0.72

_NON_ALPHA = re.compile(r"[^a-zA-Z']")


def _normalize(word: str) -> str:
    return _NON_ALPHA.sub("", word).lower()


def _cefr_band(word: str) -> str:
    z = zipf(word)
    for band, floor in CEFR_PROXY_BANDS:
        if z >= floor:
            return band
    return "C1_C2"


def _mtld_factors(tokens: list[str]) -> float:
    """One direction of the MTLD algorithm (Zheng & Yu): walk tokens,
    resetting a running type-token-ratio segment each time it drops to the
    threshold, counting whole + one fractional factor for the remainder."""
    if not tokens:
        return 0.0
    factor_count = 0.0
    types: set[str] = set()
    token_count = 0
    for token in tokens:
        types.add(token)
        token_count += 1
        if len(types) / token_count <= MTLD_TTR_THRESHOLD:
            factor_count += 1
            types = set()
            token_count = 0
    if token_count > 0:
        ttr = len(types) / token_count
        factor_count += (1 - ttr) / (1 - MTLD_TTR_THRESHOLD)
    return factor_count


def mtld(tokens: list[str]) -> float:
    """MTLD, averaged forward and backward — length-independent, unlike
    raw TTR, which is invalid here because turn lengths vary by design
    across phases/candidates (Spec 03 §4.2)."""
    if len(tokens) < 10:
        return float(len(tokens))  # too little data for a meaningful factor count
    forward_factors = _mtld_factors(tokens)
    backward_factors = _mtld_factors(list(reversed(tokens)))
    forward_mtld = len(tokens) / forward_factors if forward_factors > 0 else float(len(tokens))
    backward_mtld = len(tokens) / backward_factors if backward_factors > 0 else float(len(tokens))
    return round((forward_mtld + backward_mtld) / 2, 2)


def mattr(tokens: list[str], window_size: int = MATTR_WINDOW) -> float:
    """Moving-average type-token ratio — the other length-independent
    diversity measure Spec 03 §4.2 calls for alongside MTLD."""
    n = len(tokens)
    if n == 0:
        return 0.0
    if n <= window_size:
        return round(len(set(tokens)) / n, 3)
    ttrs = [
        len(set(tokens[i : i + window_size])) / window_size for i in range(n - window_size + 1)
    ]
    return round(sum(ttrs) / len(ttrs), 3)


def _collocation_match_count(normalized_words: list[str]) -> int:
    if not normalized_words:
        return 0
    max_len = max(len(phrase.split()) for phrase in COLLOCATIONS_AND_IDIOMS)
    n = len(normalized_words)
    count = 0
    for start in range(n):
        for length in range(2, max_len + 1):
            if start + length > n:
                break
            phrase = " ".join(normalized_words[start : start + length])
            if phrase in COLLOCATIONS_AND_IDIOMS:
                count += 1
    return count


def _lemmatize(words: list[TranscriptWord]) -> list[str]:
    doc = parse_words([w.word for w in words])
    return [token.lemma_.lower() for token in doc if re.search(r"[a-zA-Z]", token.text)]


def _empty_metrics() -> dict:
    return {
        "mtld": 0.0,
        "mattr": 0.0,
        "cefr_distribution": {"A1_A2": 0.0, "B1": 0.0, "B2": 0.0, "C1_C2": 0.0},
        "beyond_b2_ratio": 0.0,
        "off_top_5000_rarity_ratio": 0.0,
        "collocation_match_count": 0,
        "lexical_appropriacy_error_count": 0,
        "lexical_appropriacy_error_rate_per_100_words": 0.0,
        "total_words": 0,
    }


def _metrics_for_words(words: list[TranscriptWord], provider: GrammarCheckProvider) -> dict:
    if not words:
        return _empty_metrics()

    lemmas = _lemmatize(words)
    normalized = [w for w in (_normalize(word.word) for word in words) if w]
    total = len(normalized) or 1

    cefr_counts = {"A1_A2": 0, "B1": 0, "B2": 0, "C1_C2": 0}
    rare_count = 0
    for w in normalized:
        cefr_counts[_cefr_band(w)] += 1
        if zipf(w) < TOP_5000_ZIPF_FLOOR:
            rare_count += 1
    cefr_distribution = {band: round(count / total, 3) for band, count in cefr_counts.items()}

    text = " ".join(word.word for word in words)
    errors = provider.check(text)
    lexical_errors = [e for e in errors if e.category in LEXICAL_ERROR_CATEGORIES]

    return {
        "mtld": mtld(lemmas),
        "mattr": mattr(lemmas),
        "cefr_distribution": cefr_distribution,
        "beyond_b2_ratio": cefr_distribution["C1_C2"],
        "off_top_5000_rarity_ratio": round(rare_count / total, 3),
        "collocation_match_count": _collocation_match_count(normalized),
        "lexical_appropriacy_error_count": len(lexical_errors),
        "lexical_appropriacy_error_rate_per_100_words": round(
            len(lexical_errors) / total * 100, 2
        ),
        "total_words": len(words),
    }


@app.task(name="tasks.nlp.compute_lexical_metrics", bind=True, max_retries=3, time_limit=120)
def compute_lexical_metrics(
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
            write_feature_vector(session_uuid, "lexical", phase, metrics)
            results[phase] = metrics

        session_metrics = _metrics_for_words(session_words, active_provider)
        session_metrics["provenance"] = {
            "source": getattr(active_provider, "source_name", "unknown"),
            "word_count": len(session_words),
        }
        write_feature_vector(session_uuid, "lexical", "session", session_metrics)
        results["session"] = session_metrics

        return results
    except Exception as exc:
        raise self.retry(exc=exc) from exc
