from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class OperationalCorrection:
    category: str
    content: str
    priority: int
    explicit: bool
    scope: str = "global"
    origin: str = "bounded"
    content_fingerprint: str = ""
    conflict_key: str = ""
    polarity: str = "directive"
    rationale: str = ""
    success_signal: str = ""
    anti_pattern: str = ""


_EXPLICIT = re.compile(
    r"(?:记住|以后|后续|每次|始终|一直|必须|严格|需要|不要再|别再|避免|默认|"
    r"我希望|我要求|警告|我感觉|总感觉|我的反馈)"
)
_INTERNAL_EVALUATION = re.compile(r"十元内部(?:测试|验收|[^。！!？?\n]{0,40}(?:测试|验收))")
_OPEN_PERSISTENT = re.compile(
    r"(?:以后|后续|每次|始终|一直|默认|长期|记住|不要再|别再|不许再|"
    r"我说过|已经说过|这个我说过|别把|不要混淆|不能混淆)"
)
_OPEN_DIRECTIVE = re.compile(
    r"(?:不要|别|不许|禁止|必须|应该|需要|只(?:能|可|要)|不是|而是|称呼|自称|身份)"
)
_OPEN_OPERATIONAL = re.compile(
    r"(?:你|十元|助手|agent|AI|回复|回答|对话|说话|称呼|自称|身份|语气|文件|"
    r"开发|任务|会话|记忆|规则|Hana|Hanako|Codex|身体|Core|系统)",
    re.IGNORECASE,
)
_ONE_OFF = re.compile(
    r"(?:这次|本次|这一轮|今天|现在|当前|临时|一次性|这个文件|这张图|这一张|刚才这一步)"
)
_NEGATIVE = re.compile(r"(?:不要|别|不许|禁止|不能|不可|不是|避免|停止|取消)")
_SECRETISH = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{12,}|bearer\s+[a-z0-9._-]{12,}|"
    r"(?:password|passwd|token|api[_-]?key|secret)\s*[:=]\s*\S+)"
)
_PATHS_AND_IDENTIFIERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)https?://\S+"), "[链接已省略]"),
    (re.compile(r"(?i)(?<![\w.])[\w.+-]+@[\w.-]+\.[a-z]{2,}"), "[邮箱已省略]"),
    (re.compile(r"(?i)(?:[a-z]:\\|\\\\)[^\s，。；;]+"), "[本地路径已省略]"),
    (re.compile(r"(?<!\w)/(?:[^\s/]+/){1,}[^\s，。；;]*"), "[本地路径已省略]"),
    (re.compile(r"(?i)\b[0-9a-f]{24,}\b"), "[长标识已省略]"),
)
_SPLIT_SENTENCES = re.compile(r"[\n\r。！？!?；;]+")

_RULES: tuple[tuple[str, int, str, str, str, str, re.Pattern[str]], ...] = (
    (
        "felt_understanding",
        99,
        "把已经记住的内容转化为当下可感知的理解：结合历史反馈、当前感受和真实任务状态作出有判断的回应；不能只复述画像、规则或声称十元有记忆。",
        "用户在意的不是档案数量，而是换到任何已接入会话后，都能少解释一次，并感到十元认出了这次话背后的真正诉求。",
        "回应能主动连上相关旧反馈，点明这次最影响体验的落差，并直接接走一个具体负担；让用户少解释一次，无需再教一遍同样的相处方式。",
        "罗列‘我记得你喜欢什么’、倾倒整份画像、空泛说‘我懂你’，或只增加 emoji，却没有让当前判断和行动发生变化。",
        re.compile(
            r"(?is)(?=.*(?:懂我|理解我|懂我的感受|理解我的感受|接住我|被理解))"
            r"(?=.*(?:没有|并没有|不够|不像|总感觉|仍旧|还是|别的会话|不同会话|反馈|重复))"
        ),
    ),
    (
        "memory_continuity",
        100,
        "跨会话先恢复用户过去的明确建议与纠正；不要只说历史里有，也不要让用户重复说明同一条规则。",
        "用户真正厌倦的是每开一个会话就像重新认识一次；历史存在但没有改变新会话行为，等同于没有被记住。",
        "新会话能自行带出当前问题相关的旧纠正并自然执行，只有真实歧义才询问，且不会要求用户复制旧对话。",
        "只汇报历史条数、说‘已经存档’，或把整段旧聊天贴回来，却仍让用户重新解释前因后果。",
        re.compile(
            r"(?is)(?=.*(?:记忆|记住|想得起来|知道|前因后果|不同会话|跨会话|新会话))"
            r"(?=.*(?:重复|又说|再说|没记住|不知道|想不起来|同样|建议|纠正|以前))"
        ),
    ),
    (
        "response_human_warm",
        92,
        "回复要自然、温和并贴合用户当下的语气；优先使用清晰短句和适量分段，避免连续长难句、报告腔和机械装饰。",
        "用户需要的是能理解上下文并清楚回应的对话者，而不是套用固定语气模板的项目报告。",
        "先回应真正的问题或感受，再用易读的短段落说清事情；温度来自具体理解、自然节奏和主动减轻负担。",
        "照抄第三方角色设定、机械堆叠符号，或主体仍是长难句和验收报告，只在结尾追加情绪词。",
        re.compile(
            r"(?is)(?=.*(?:回复|对话|说话|语气|表达))"
            r"(?=.*(?:人性|人味|可爱|元气|调皮|温度|冷冰冰|长难句|短句|分段|hana|hanako|报告腔))"
        ),
    ),
    (
        "development_alignment_progress",
        90,
        "开发前先恢复历史、现有产物和用户纠正并对齐范围；开发中持续写进度，不能做半截便停下或用口头承诺代替记录。",
        "用户需要随时离开电脑也能放心回来接续；突然停下和缺少记录会把项目重新变成只能靠人脑维护的负担。",
        "开工前有可核对的目标与边界，过程中有持续检查点，结束时真实说明完成、缺口和下一步，换身体后能继续。",
        "只在聊天里说‘我会继续’，改了几行就沉默，或为了显得完成而省略失败、回滚和未验收项。",
        re.compile(
            r"(?is)(?=.*(?:开发|推进|进度|做一半|半截|停下|断了|续上))"
            r"(?=.*(?:对齐|了解清楚|记录|总账|恢复历史|继续|别停|不要停|为什么停))"
        ),
    ),
    (
        "storage_hygiene",
        88,
        "生成的文件必须进入对应项目的统一专用目录，先检查现有结构；不得向磁盘根目录、共享盘根目录或工作区根部随意散落产物。",
        "散落文件会把工具带来的效率重新变成整理成本，也破坏用户对自动化可以放心运行的信任。",
        "动手前先识别归属目录；产物、临时件、备份与报告各归其位，交付时能一句话说明放在哪里。",
        "为了方便脚本直接写盘符根目录、在工作区根部堆临时文件，或事后才让用户自己判断哪些能删。",
        re.compile(
            r"(?is)(?=.*(?:路径|根目录|盘符|磁盘|共享盘|文件|产物|脚本))"
            r"(?=.*(?:散落|散乱|乱放|专用文件夹|统一文件夹|整齐|归纳|归类))"
        ),
    ),
    (
        "full_history_not_receipt",
        86,
        "900 字回执和任务卡只用于快速接班，不能冒充完整记忆；需要保留并检索获准的完整可见历史、原始证据和后续增量。",
        "用户要的是连续的人，而不是每次只拿到一张失真的摘要；关键分歧、理由和感受不能被压缩掉。",
        "快速接班先用摘要，追问细节时能回到原始可见历史与证据，并区分原话、后续结论和当前状态。",
        "把 900 字摘要称为完整记忆，或只保存结论不保存为何改变，导致以后又走回已经否决的路线。",
        re.compile(
            r"(?is)(?=.*(?:900\s*字|回执|任务卡|摘要))"
            r"(?=.*(?:完整|旧历史|历史|记忆|不够|缺口|原始证据))"
        ),
    ),
    (
        "cross_body_continuity",
        85,
        "Codex 与 Hana 换身体时，应自动恢复最近目标、进展、结果、证据和下一步；不要要求用户复制转述后才能承接。",
        "对用户而言 Codex 和 Hana 是十元的不同身体；换入口不该等于换了一个不认识当前工作的助手。",
        "另一身体开口前知道最近公开进展、已做决定、有效证据和下一步，并能说明是否存在并发冲突。",
        "只知道任务标题却不知道做到哪里，或让用户先总结 Codex/Hana 刚才干了什么才能继续。",
        re.compile(
            r"(?is)(?=.*(?:codex|hana|hanako|身体|agent))"
            r"(?=.*(?:无缝|承接|切换|正在|干了什么|知道|记忆|任务卡|同步|转述))"
        ),
    ),
    (
        "company_data_boundary",
        100,
        "工作设备可以拥有同等的本地记忆与工作连续性能力，但受限资料不得自动上传私人存储；能力一致不等于数据互通。",
        "连续体验不能绕过组织的数据与合规边界。",
        "受限设备在本地提供记忆与接续能力，只导出经过授权和审阅的抽象交接，原文默认留在原设备。",
        "自动同步受限聊天、项目文件或人员资料，或反过来因不能外传就彻底关闭本地记忆。",
        re.compile(
            r"(?is)(?=.*(?:公司|企业微信|内网))"
            r"(?=.*(?:不让上传|不能上传|不许上传|不上传|nas|数据互通|能力一致|同步))"
        ),
    ),
    (
        "evidence_before_completion",
        89,
        "不能把代码修改、机械测试或 Agent 自述当作真实完成；必须按任务风险检查实际界面、文件、运行结果与用户验收证据。",
        "用户评价的是最终使用体验，不是代码看起来是否合理；过早宣布完成会迫使用户反复替系统做验收。",
        "按风险看到真实界面或运行结果，保留可追溯证据，明确机械通过与用户体验验收之间的差别。",
        "只跑语法检查或单元测试就说‘好了’，把 UI 改名当成功能改变，或用 Agent 自述代替实际结果。",
        re.compile(
            r"(?is)(?=.*(?:完成|做好|验收|验证|测试|证据|实际|真实))"
            r"(?=.*(?:宣称|不能|不要|根本没有|没跑|冒充|界面|用户验收|自行测试))"
        ),
    ),
)


def operational_correction_definitions() -> dict[str, OperationalCorrection]:
    """Return bounded canonical rules without creating user evidence."""
    definitions: dict[str, OperationalCorrection] = {}
    for category, priority, content, rationale, success_signal, anti_pattern, _pattern in _RULES:
        fingerprint = hashlib.sha256(_normalized_rule_text(content).encode("utf-8")).hexdigest()
        definitions[category] = OperationalCorrection(
            category=category,
            content=content,
            priority=priority,
            explicit=False,
            content_fingerprint=fingerprint,
            conflict_key=category,
            rationale=rationale,
            success_signal=success_signal,
            anti_pattern=anti_pattern,
        )
    return definitions


def correction_source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def correction_scope_identifier(kind: str, value: str) -> str:
    normalized = value.strip().casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{kind}:{digest}"


def _normalized_rule_text(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text.lower())


def correction_similarity(left: str, right: str) -> float:
    """Return a conservative Chinese-friendly similarity for canonical rules."""
    left_key = _normalized_rule_text(left)
    right_key = _normalized_rule_text(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    sequence = SequenceMatcher(None, left_key, right_key).ratio()
    left_pairs = {left_key[index : index + 2] for index in range(max(1, len(left_key) - 1))}
    right_pairs = {right_key[index : index + 2] for index in range(max(1, len(right_key) - 1))}
    overlap = 2 * len(left_pairs & right_pairs) / max(1, len(left_pairs) + len(right_pairs))
    return max(sequence, overlap)


def _redact_open_content(text: str) -> str:
    sanitized = _SECRETISH.sub("[凭据已省略]", text)
    for pattern, replacement in _PATHS_AND_IDENTIFIERS:
        sanitized = pattern.sub(replacement, sanitized)
    return re.sub(r"\s+", " ", sanitized).strip()


def _infer_scope(text: str, body: str | None, device: str | None, project: str | None) -> str:
    lowered = text.lower()
    has_hana = "hana" in lowered or "hanako" in lowered
    has_codex = "codex" in lowered
    if has_hana != has_codex:
        return "body:hana" if has_hana else "body:codex"
    if project and re.search(r"(?:本项目|这个项目|该项目|此项目)", text):
        return correction_scope_identifier("project", project)
    if device and re.search(r"(?:本机|这台电脑|当前电脑|此电脑)", text):
        return correction_scope_identifier("device", device)
    return "global"


def _open_conflict_key(content: str) -> str:
    key = content
    key = re.sub(r"^(?:跨会话操作要求：|用户要求：)", "", key)
    key = re.sub(
        r"(?:以后|后续|每次|始终|一直|默认|长期|记住|不要再|别再|不许再|"
        r"不要|不许|禁止|必须|应该|需要|不能|不可|不是|避免|停止|取消|而是|请|应当)",
        "",
        key,
    )
    normalized = _normalized_rule_text(key)
    return normalized[:180] or _normalized_rule_text(content)[:180]


def _canonicalize_open_sentence(sentence: str) -> str:
    canonical = _redact_open_content(sentence.strip(" -—_\t，,。.!！?？"))
    canonical = re.sub(r"^(?:我希望|我要求|我的要求是|请你|请)", "", canonical).strip()
    canonical = canonical.replace("你的", "十元的").replace("你", "十元")
    canonical = re.sub(r"^十元是十元[，,]\s*", "十元的身份是十元，", canonical)
    canonical = re.sub(r"(?:了)+$", "", canonical).strip("，,。 ")
    if len(canonical) > 260:
        canonical = canonical[:257].rstrip() + "…"
    return f"跨会话操作要求：{canonical}。"


def _extract_open_corrections(
    text: str,
    *,
    body: str | None,
    device: str | None,
    project: str | None,
) -> list[OperationalCorrection]:
    results: list[OperationalCorrection] = []
    seen: set[str] = set()
    for raw_sentence in _SPLIT_SENTENCES.split(text):
        sentence = raw_sentence.strip()
        if len(sentence) < 4 or not _OPEN_OPERATIONAL.search(sentence):
            continue
        identity_boundary = bool(
            re.search(
                r"(?:你|十元).{0,24}(?:身份|称呼|自称|是).{0,24}(?:不是|不要|只能|必须|而是)",
                sentence,
            )
        )
        if not _OPEN_DIRECTIVE.search(sentence) or not (
            _OPEN_PERSISTENT.search(sentence) or identity_boundary
        ):
            continue
        if _ONE_OFF.search(sentence) and not re.search(
            r"(?:以后|后续|每次|始终|一直|默认|长期|我说过|已经说过|不要混淆|身份)",
            sentence,
        ):
            continue
        if any(pattern.search(sentence) for *_, pattern in _RULES):
            continue
        content = _canonicalize_open_sentence(sentence)
        normalized = _normalized_rule_text(content)
        if len(normalized) < 6 or normalized in seen:
            continue
        seen.add(normalized)
        scope = _infer_scope(sentence, body, device, project)
        conflict_key = _open_conflict_key(content)
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        category_seed = f"{scope}|{fingerprint}"
        category = "open_" + hashlib.sha256(category_seed.encode("utf-8")).hexdigest()[:20]
        identity_boundary = bool(
            identity_boundary
            or re.search(r"(?:身份|称呼|自称|不是.*(?:鲸|Hana|Hanako|Codex))", sentence, re.IGNORECASE)
        )
        results.append(
            OperationalCorrection(
                category=category,
                content=content,
                priority=98 if identity_boundary else 94,
                explicit=bool(_EXPLICIT.search(sentence) or re.search(r"(?:不要混淆|我说过|已经说过|身份)", sentence)),
                scope=scope,
                origin="open-v2",
                content_fingerprint=fingerprint,
                conflict_key=conflict_key,
                polarity="negative" if _NEGATIVE.search(sentence) else "positive",
            )
        )
    return results


def extract_operational_corrections(
    text: str,
    *,
    body: str | None = None,
    device: str | None = None,
    project: str | None = None,
) -> list[OperationalCorrection]:
    """Extract only bounded, cross-task operating corrections.

    These are instructions for how a body should work, not claims about the
    user's identity. The canonical text deliberately avoids copying raw prompts.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    bounded = text[:12000]
    if _INTERNAL_EVALUATION.search(bounded[:1500]):
        return []
    explicit = bool(_EXPLICIT.search(bounded))
    matches: list[OperationalCorrection] = []
    for category, priority, content, rationale, success_signal, anti_pattern, pattern in _RULES:
        if pattern.search(bounded):
            fingerprint = hashlib.sha256(_normalized_rule_text(content).encode("utf-8")).hexdigest()
            matches.append(
                OperationalCorrection(
                    category=category,
                    content=content,
                    priority=priority,
                    explicit=explicit,
                    content_fingerprint=fingerprint,
                    conflict_key=category,
                    rationale=rationale,
                    success_signal=success_signal,
                    anti_pattern=anti_pattern,
                )
            )
    matches.extend(
        _extract_open_corrections(
            bounded,
            body=body,
            device=device,
            project=project,
        )
    )
    return matches
