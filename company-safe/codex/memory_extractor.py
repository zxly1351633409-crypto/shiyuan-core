from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass


MAX_CANDIDATE_LENGTH = 300
MAX_CANDIDATES_PER_EVENT = 3

_SENTENCE_SPLIT = re.compile(r"[\r\n。！？!?；;]+")
_LEADING_DECORATION = re.compile(r"^[\s>*#\-•·\d.、（）()]+")
_REMEMBER_PREFIX = re.compile(r"^(?:请)?记住(?:一下)?[：:,，\s]*")
_QUESTION_CUE = re.compile(r"(?:吗|么|呢|什么|是否|能否|可否|怎么|如何|为什么|哪(?:个|些|里)?|几(?:个|次|点)?)\s*$")
_SECRET_OR_RAW_DATA = re.compile(
    r"(?ix)(?:"
    r"(?:password|passwd|secret|token|api[_\- ]?key|access[_\- ]?key)\s*[:=]\s*\S+"
    r"|sk-[a-z0-9_\-]{8,}"
    r"|-----BEGIN\s+(?:RSA|OPENSSH|EC|DSA)?\s*PRIVATE\s+KEY-----"
    r"|(?:[a-z]:\\|\\\\|/home/|/users/)\S+"
    r"|https?://\S+"
    r"|[\w.+-]+@[\w.-]+\.[a-z]{2,}"
    r"|\b\d{12,}\b"
    r")"
)
_CONFIDENTIAL_CUE = re.compile(r"(?:保密|机密|未公开|项目代号|客户姓名|供应商名称|内部路径|人员评价)")

_TRIGGERS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    (
        "preference",
        re.compile(r"(?:^|[，,\s])我(?:更)?(?:喜欢|偏好|倾向|不喜欢|讨厌|习惯|常用|希望|不希望)"),
        0.84,
    ),
    (
        "workflow_preference",
        re.compile(r"(?:^|[，,\s])(?:以后|后续)(?:请|需要|希望|默认|都要|不要|不再)"),
        0.86,
    ),
    (
        "workflow_preference",
        re.compile(r"(?:^|[，,\s])我的(?:原则|工作方式|开发流程|沟通方式|习惯|偏好)(?:是|为|：|:)"),
        0.88,
    ),
    (
        "fact",
        re.compile(r"(?:^|[，,\s])我(?:目前是|现在是|是|主要使用|通常使用|常用的是)"),
        0.80,
    ),
)


@dataclass(frozen=True)
class ExtractedMemory:
    kind: str
    content: str
    confidence: float
    scope: str = "personal"
    sensitivity: str = "private"

    def model_dump(self) -> dict[str, object]:
        return asdict(self)


def normalize_memory_content(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content).casefold()
    return re.sub(r"[^\w\u3400-\u9fff]+", "", normalized)


def memory_fingerprint(content: str) -> str:
    return hashlib.sha256(normalize_memory_content(content).encode("utf-8")).hexdigest()


def _candidate_from_sentence(sentence: str) -> ExtractedMemory | None:
    sentence = _LEADING_DECORATION.sub("", sentence).strip(" \t:：,，。")
    if not sentence or len(sentence) < 6 or len(sentence) > MAX_CANDIDATE_LENGTH:
        return None
    if _QUESTION_CUE.search(sentence):
        return None
    if _SECRET_OR_RAW_DATA.search(sentence) or _CONFIDENTIAL_CUE.search(sentence):
        return None

    explicit_remember = bool(_REMEMBER_PREFIX.match(sentence))
    if explicit_remember:
        sentence = _REMEMBER_PREFIX.sub("", sentence).strip(" \t:：,，。")
        if len(sentence) < 4 or len(sentence) > MAX_CANDIDATE_LENGTH:
            return None
        if _SECRET_OR_RAW_DATA.search(sentence) or _CONFIDENTIAL_CUE.search(sentence):
            return None
        kind = "workflow_preference" if re.search(r"(?:以后|后续|流程|默认|每次|始终)", sentence) else "fact"
        return ExtractedMemory(kind=kind, content=sentence, confidence=0.96)

    for kind, pattern, confidence in _TRIGGERS:
        if pattern.search(sentence):
            return ExtractedMemory(kind=kind, content=sentence, confidence=confidence)
    return None


def extract_memory_candidates(text: str) -> list[ExtractedMemory]:
    if not isinstance(text, str) or not text.strip():
        return []
    candidates: list[ExtractedMemory] = []
    seen: set[str] = set()
    for sentence in _SENTENCE_SPLIT.split(text[:12000]):
        candidate = _candidate_from_sentence(sentence)
        if candidate is None:
            continue
        fingerprint = memory_fingerprint(candidate.content)
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        candidates.append(candidate)
        if len(candidates) >= MAX_CANDIDATES_PER_EVENT:
            break
    return candidates
