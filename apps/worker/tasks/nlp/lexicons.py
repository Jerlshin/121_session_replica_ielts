"""Small, curated, code-level word lists backing the rule-based fluency
and lexical metrics (Spec 03 §4.1, §4.2). Deliberately kept as short
Python constants rather than versioned content assets like
`packages/prompt-templates` — these are code-adjacent lookup tables, not
licensed prose content requiring their own review/changelog discipline.
"""

# Filled-pause tokens (Spec 03 §4.1), single-word only — transcripts store
# one word per row, so multi-word filler phrases ("you know") are matched
# as bigrams in fluency.py instead, not as dictionary entries here.
FILLED_PAUSES = frozenset(
    {
        "um",
        "umm",
        "uh",
        "uhh",
        "erm",
        "er",
        "hmm",
        "like",
    }
)

# Multi-word filled-pause phrases, matched as consecutive-word bigrams.
FILLED_PAUSE_BIGRAMS = frozenset(
    {
        ("you", "know"),
    }
)

# Discourse connectives (Spec 03 §4.1) — organizing extended discourse, a
# coherence signal distinct from individual-sentence fluency.
DISCOURSE_MARKERS = frozenset(
    {
        "firstly",
        "secondly",
        "thirdly",
        "finally",
        "however",
        "moreover",
        "furthermore",
        "additionally",
        "therefore",
        "consequently",
        "meanwhile",
        "nevertheless",
        "nonetheless",
        "besides",
        "so",
        "because",
        "although",
        "though",
        "otherwise",
        "instead",
        "similarly",
        "likewise",
        "overall",
        "anyway",
    }
)

# Self-repair markers (Spec 03 §4.1), single-word only — explicit
# correction cues that distinguish disfluency-from-repair from
# disfluency-from-difficulty.
SELF_REPAIR_MARKERS = frozenset(
    {
        "sorry",
        "actually",
        "rather",
    }
)

# Multi-word self-repair phrases, matched as consecutive-word bigrams.
SELF_REPAIR_BIGRAMS = frozenset(
    {
        ("i", "mean"),
    }
)

# A modest starter collocation/idiom bank (Spec 03 §4.2) — statistical
# matching only; the spec's LLM-assisted supplementary flagging pass is a
# documented extension point, not implemented (see lexical.py's docstring).
COLLOCATIONS_AND_IDIOMS = frozenset(
    {
        "make a decision",
        "take a break",
        "pay attention",
        "have a look",
        "give it a try",
        "on the other hand",
        "at the end of the day",
        "in my opinion",
        "as far as i know",
        "to be honest",
        "keep in touch",
        "make sense",
        "take part in",
        "get used to",
        "look forward to",
        "run out of",
        "come up with",
        "figure out",
        "in the long run",
        "for the most part",
        "under the weather",
        "hit the books",
        "piece of cake",
        "break the ice",
        "once in a while",
        "on a regular basis",
        "in terms of",
        "as a matter of fact",
        "take into account",
        "make up my mind",
    }
)
