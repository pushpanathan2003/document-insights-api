import re
from collections import Counter

_STOP_WORDS = frozenset(
    "the and for that with this from have are was were will would there their about which into than then them "
    "they you your our not but can has had its been also more most such these those over under".split()
)


def generate_summary(title: str, content: str) -> str:
    words = re.findall(r"[A-Za-z0-9']+", content)
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", content.strip()) if part.strip()]
    excerpt = " ".join(sentences[:2])
    if len(excerpt) > 240:
        excerpt = excerpt[:237].rstrip() + "..."
    keyword_counts = Counter(
        word.lower() for word in words if len(word) > 3 and word.lower() not in _STOP_WORDS
    )
    keywords = ", ".join(word for word, _ in keyword_counts.most_common(3))
    summary = f"{title}: {excerpt} ({len(words)} words"
    if keywords:
        summary += f"; key terms: {keywords}"
    return summary + ")"
