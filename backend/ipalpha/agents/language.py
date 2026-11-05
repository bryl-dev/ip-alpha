import re
from functools import lru_cache

from lingua import Language, LanguageDetector, LanguageDetectorBuilder

_KANA = re.compile(r"[\u3040-\u30ff]")
_LANGUAGES = (
    Language.ENGLISH,
    Language.JAPANESE,
    Language.CHINESE,
    Language.KOREAN,
    Language.SPANISH,
    Language.PORTUGUESE,
    Language.FRENCH,
    Language.GERMAN,
)
# lingua guesses wildly on short ASCII strings ("AC6" comes back as Spanish).
_MIN_ASCII_CHARS = 30
_MIN_CONFIDENCE = 0.6


@lru_cache
def _detector() -> LanguageDetector:
    return LanguageDetectorBuilder.from_languages(*_LANGUAGES).build()


def detect_language(text: str) -> str:
    """ISO 639-1 code. Kana is decisive for Japanese; kanji-only text is left to lingua."""
    text = text.strip()
    if not text:
        return "en"
    if _KANA.search(text):
        return "ja"
    if text.isascii() and len(text) < _MIN_ASCII_CHARS:
        return "en"
    best = _detector().compute_language_confidence_values(text)[0]
    if text.isascii() and best.value < _MIN_CONFIDENCE:
        return "en"
    return best.language.iso_code_639_1.name.lower()
