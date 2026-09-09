"""Arabic / English / French text normalisation for search matching.

Normalisation is applied to *queries and search keys only*. Official provider
metadata is never mutated (spec 0B.1) -- the normalised form lives beside the
canonical record, it does not replace it.
"""

from __future__ import annotations

import re
import unicodedata

from ..core.models import Language

# --------------------------------------------------------------------------
# Arabic
# --------------------------------------------------------------------------

# Alef variants -> bare alef; ya/alef-maqsura and ta-marbuta/ha are routinely
# interchanged by real users, so they are folded for matching.
_AR_CHAR_MAP = str.maketrans({
    "أ": "ا",  # alef with hamza above
    "إ": "ا",  # alef with hamza below
    "آ": "ا",  # alef with madda
    "ٱ": "ا",  # alef wasla
    "ى": "ي",  # alef maqsura -> ya
    "ة": "ه",  # ta marbuta -> ha
    "ؤ": "و",  # waw with hamza
    "ئ": "ي",  # ya with hamza
    "ـ": "",        # tatweel
})

# Harakat / tanwin / shadda / sukun and the Quranic marks above them.
_AR_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")

_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩",
                                     "0123456789")
_EASTERN_ARABIC_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹",
                                       "0123456789")

_AR_RANGE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")

# Definite article and common prefixed particles, stripped for matching only.
_AR_PREFIXES = ("وال", "بال", "كال",
                "فال", "لل", "ال")


def normalize_arabic(text: str, *, strip_article: bool = True) -> str:
    """Fold Arabic orthographic variation so equivalent spellings collide."""
    text = unicodedata.normalize("NFKC", text)
    text = _AR_DIACRITICS.sub("", text)
    text = text.translate(_AR_CHAR_MAP)
    text = text.translate(_ARABIC_INDIC_DIGITS).translate(_EASTERN_ARABIC_DIGITS)
    if strip_article:
        tokens = []
        for token in text.split():
            for prefix in _AR_PREFIXES:
                # Only strip when a usable stem remains.
                if token.startswith(prefix) and len(token) - len(prefix) >= 3:
                    token = token[len(prefix):]
                    break
            tokens.append(token)
        text = " ".join(tokens)
    return _collapse(text)


# --------------------------------------------------------------------------
# French
# --------------------------------------------------------------------------

# Elided articles: l'economie -> economie, d'investissement -> investissement.
_FR_ELISION = re.compile(r"\b(?:[ldnjmtcs]|qu|jusqu|lorsqu|puisqu)['’]", re.IGNORECASE)

_FR_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "du", "de", "d", "au", "aux", "a",
    "en", "et", "ou", "pour", "par", "sur", "dans", "avec", "entre", "chez",
    "l", "the", "of",
}


def strip_accents(text: str) -> str:
    """Remove combining marks; leaves non-Latin scripts untouched."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_french(text: str, *, drop_stopwords: bool = True) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _FR_ELISION.sub(" ", text)
    text = strip_accents(text).lower()
    text = _punct_to_space(text)
    tokens = [t for t in text.split() if t]
    if drop_stopwords:
        kept = [t for t in tokens if t not in _FR_STOPWORDS]
        # Never normalise a phrase down to nothing.
        tokens = kept or tokens
    return " ".join(_depluralise(t) for t in tokens)


def _depluralise(token: str) -> str:
    """Very light French/English plural folding for match keys."""
    if len(token) > 4:
        if token.endswith("aux"):
            return token[:-3] + "al"
        if token.endswith(("s", "x")) and not token.endswith(("ss", "us", "is")):
            return token[:-1]
    return token


# --------------------------------------------------------------------------
# English
# --------------------------------------------------------------------------

_EN_STOPWORDS = {
    "the", "a", "an", "of", "for", "in", "on", "at", "to", "and", "or", "by",
    "with", "from", "is", "are", "be", "data", "series", "please", "show",
    "get", "find", "give", "me", "i", "want", "need",
}


def normalize_english(text: str, *, drop_stopwords: bool = True) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = strip_accents(text).lower()
    text = _punct_to_space(text)
    tokens = [t for t in text.split() if t]
    if drop_stopwords:
        kept = [t for t in tokens if t not in _EN_STOPWORDS]
        tokens = kept or tokens
    return " ".join(_depluralise(t) for t in tokens)


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

_PUNCT = re.compile(r"[^\w\s؀-ۿ%]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def _punct_to_space(text: str) -> str:
    return _collapse(_PUNCT.sub(" ", text))


def _collapse(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def detect_language(text: str) -> Language:
    """Cheap, deterministic script/keyword language detection.

    Arabic script wins outright because a mixed query such as
    ``GDP والتضخم في الجزائر`` is still an Arabic-language request.
    """
    if _AR_RANGE.search(text):
        return Language.AR

    lowered = strip_accents(text).lower()
    tokens = set(re.findall(r"[a-z]+", lowered))

    fr_markers = {
        "taux", "chomage", "croissance", "par", "habitant", "pib", "ide",
        "donnees", "recherche", "trouver", "entre", "depuis", "annuel",
        "mensuel", "trimestriel", "interieur", "brut", "produit", "prix",
        "population", "dette", "publique", "echange", "exterieur", "et",
        "pour", "de", "des", "du", "les", "la", "le", "en", "sur",
    }
    en_markers = {
        "the", "and", "for", "from", "with", "rate", "growth", "per", "capita",
        "unemployment", "inflation", "annual", "monthly", "quarterly", "data",
        "find", "show", "get", "between", "since", "gross", "domestic",
        "product", "price", "debt", "public", "exchange", "trade",
    }

    fr_score = len(tokens & fr_markers)
    en_score = len(tokens & en_markers)
    # Accented characters are a strong French signal on their own.
    if text != strip_accents(text):
        fr_score += 2
    return Language.FR if fr_score > en_score else Language.EN


def normalize(text: str, language: Language | None = None, **kwargs) -> str:
    """Normalise ``text`` using the rules for ``language``.

    When ``language`` is omitted it is detected. Arabic queries additionally
    get their Latin fragments normalised, so ``أريد GDP في الجزائر`` matches an
    English ``GDP`` alias.
    """
    language = language or detect_language(text)
    if language is Language.AR:
        arabic = normalize_arabic(text, **kwargs)
        latin = " ".join(re.findall(r"[A-Za-z][A-Za-z0-9%]*", text)).lower()
        return _collapse(f"{arabic} {latin}") if latin else arabic
    if language is Language.FR:
        return normalize_french(text, **kwargs)
    return normalize_english(text, **kwargs)


def normalize_all(text: str) -> set[str]:
    """Every normalised form of ``text``.

    Alias keys are indexed under all three so a French alias is still found by
    an English-normalised query token.
    """
    forms = {
        normalize_english(text),
        normalize_french(text),
        normalize_english(text, drop_stopwords=False),
        normalize_french(text, drop_stopwords=False),
    }
    if _AR_RANGE.search(text):
        forms.add(normalize_arabic(text))
        forms.add(normalize_arabic(text, strip_article=False))
    return {f for f in forms if f}


def tokenize(text: str, language: Language | None = None) -> list[str]:
    return [t for t in normalize(text, language).split() if t]
