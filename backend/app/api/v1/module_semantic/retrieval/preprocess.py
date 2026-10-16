"""检索预处理（设计 §6.2；参考方案 §3①）。

归一化 → **剥离时间词** → 切 token。

为什么必须剥时间词（坑清单第 2 条）：
"2026年6月的订阅收入" 里的 "2026年6月" 会稀释与 sub_revenue 的相似度；
且时间窗属于结构化意图（交给 P4 的意图组装 + 填参），不该参与语义匹配。

时间解析用规则实现（不引 jionlp/duckling）：覆盖中文常见问法即够，
识别不到就 time_range=None —— 缺时间窗由校验器拦（参考方案规则 4），不在这里瞎补。
"""

import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

from .tokenizer import tokenize

# 疑问词、虚词、量词：它们进 token 会拖累多 token AND 匹配。
# 「order_class 是什么」若带上「是」「什么」，AND 匹配必然零召回 ——
# 而 order_class 本可精确命中。这是 dev 端到端暴露的真因（不是"需要向量路"）。
#
# ★ 只收真正无检索价值的词。业务词绝不入表（"表"在"该用哪张表"里是实词，
# 问的就是选表；"数"在"付费用户数"里也是实词）。
_STOPWORDS: frozenset[str] = frozenset({
    # 疑问
    "什么", "哪些", "哪个", "哪张", "哪", "怎么", "如何", "为什么", "多少", "几",
    "是否", "能否", "可否",
    # 语气/助词
    "吗", "呢", "吧", "啊", "呀", "了", "的", "地", "得", "着", "过",
    "是", "有", "在", "和", "与", "或", "跟", "同", "及", "以及",
    "请", "帮", "我", "你", "他", "它", "咱", "咱们", "我们", "你们",
    # 泛动词/泛指（无区分度）
    "能", "可以", "需要", "想", "要", "看", "看看", "查", "查查", "查询",
    "给", "给我", "告诉", "说", "说明", "知道", "了解",
    "直接", "一下", "一个", "这个", "那个", "这些", "那些", "该", "该用",
    # 泛概念（与任何指标都沾边，等于没过滤）
    "区别", "差别", "不同", "情况", "数据", "指标", "结果",
    # 低区分度：「人」几乎所有用户类指标都沾。保留它会让 arppu(人均收入) 侥幸压过
    # paying_users(付费用户数) —— dev 评测实测到的排序错乱。
    "人", "个", "些", "点", "次",
})

# 量词前缀：jieba 会把「哪张表」切成 哪 + 张表 —— 量词粘在实词上成了不存在的词，
# 库里搜不到。剥掉前缀还原实词（dev 评测实测：「该用哪张表」剩「张表」故零命中）。
_MEASURE_PREFIX = re.compile(r"^(?:张|个|条|笔|家|位|只|台|份|项|种|类|次|批)(?=.)")

# 通用单字：几乎所有语义资产都含它们，单独作为检索词毫无区分度。
# 剥离后只剩这类字时应保留上下文（dev 实测「该用哪张表」剥成「表」会被所有维表挤占）。
# 「赚」「退」这类单字不在此列 —— 它们只沾收入/退款类指标，有区分度。
_GENERIC_CHARS: frozenset[str] = frozenset({
    "表", "库", "列", "字", "值", "量", "数", "率", "额", "id", "码", "名", "类", "型",
})


@dataclass
class Preprocessed:
    """预处理结果。"""

    keyword: str
    """归一化并剥掉时间词后的检索词"""
    time_range: tuple[str, str] | None = None
    """识别出的时间窗 (start, end)，ISO 日期；未识别为 None"""
    tokens: list[str] = field(default_factory=list)
    """分词结果，供多词 AND 模糊匹配"""
    raw: str = ""
    """原始输入（保留供日志与澄清提示）"""


def _month_end(year: int, month: int) -> int:
    """月末日按实际月份算（闰年 2 月是 29）。"""
    return calendar.monthrange(year, month)[1]


def _normalize(text: str) -> str:
    """全角转半角 + 小写 + 去首尾空格。

    NFKC 把 ＳＵＢ 这类全角字母折成半角，同时不破坏中文与下划线。
    """
    return unicodedata.normalize("NFKC", text).strip().lower()


# ---- 时间词规则：从具体到宽泛，先匹配到的先用 ----

_RE_YMD = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
_RE_ISO_RANGE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*(?:到|至|~|-{1,2})\s*(\d{4}-\d{2}-\d{2})")
_RE_ISO_ONE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_RE_YM = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")
_RE_YEAR_ONLY = re.compile(r"(\d{4})\s*年(?!\s*\d)")
_RE_BARE_MONTH = re.compile(r"(?<!\d)(\d{1,2})\s*月(?!\s*\d)")
_RE_LAST_N_DAYS = re.compile(r"(?:近|最近|过去)\s*(\d{1,3})\s*天")


def _extract_time(text: str, today: date) -> tuple[str, str] | None:
    """抽时间窗，返回 (start, end)；识别不到返回 None。不修改入参文本。"""
    if m := _RE_ISO_RANGE.search(text):
        return m.group(1), m.group(2)

    if m := _RE_YMD.search(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        iso = f"{y:04d}-{mo:02d}-{d:02d}"
        return iso, iso

    if m := _RE_ISO_ONE.search(text):
        return m.group(1), m.group(1)

    if m := _RE_YM.search(text):
        y, mo = int(m.group(1)), int(m.group(2))
        return f"{y:04d}-{mo:02d}-01", f"{y:04d}-{mo:02d}-{_month_end(y, mo):02d}"

    if m := _RE_LAST_N_DAYS.search(text):
        n = max(1, int(m.group(1)))
        start = today - timedelta(days=n - 1)
        return start.isoformat(), today.isoformat()

    if "上月" in text or "上个月" in text:
        y, mo = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        return f"{y:04d}-{mo:02d}-01", f"{y:04d}-{mo:02d}-{_month_end(y, mo):02d}"

    if "本月" in text or "这个月" in text:
        y, mo = today.year, today.month
        return f"{y:04d}-{mo:02d}-01", f"{y:04d}-{mo:02d}-{_month_end(y, mo):02d}"

    if "昨天" in text or "昨日" in text:
        d = today - timedelta(days=1)
        return d.isoformat(), d.isoformat()

    if "今天" in text or "今日" in text:
        return today.isoformat(), today.isoformat()

    # 省略年份的月份（"6月充值收入"），按参考日期补年
    if m := _RE_BARE_MONTH.search(text):
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            y = today.year
            return f"{y:04d}-{mo:02d}-01", f"{y:04d}-{mo:02d}-{_month_end(y, mo):02d}"

    if m := _RE_YEAR_ONLY.search(text):
        y = int(m.group(1))
        return f"{y:04d}-01-01", f"{y:04d}-12-31"

    return None


_TIME_WORDS = re.compile(
    r"\d{4}-\d{2}-\d{2}|\d{4}\s*年|\d{1,2}\s*月|\d{1,2}\s*日|"
    r"(?:近|最近|过去)\s*\d{1,3}\s*天|上个?月|这个?月|本月|昨天|昨日|今天|今日|到|至|"
    # 范围量词：「2025年全年」的"全年"若不剥，会污染检索词导致零命中
    r"全年|整年|全月|整月|当月|当年|全季|整季"
)
# 剥完时间词残留的连接词/助词，去掉以免污染匹配
_FILLER = re.compile(r"^[的\s~\-—到至]+|[的\s~\-—到至]+$")


def _strip_time_words(text: str) -> str:
    stripped = _TIME_WORDS.sub(" ", text)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return _FILLER.sub("", stripped).strip()


def _strip_measure_prefix(token: str) -> str:
    """剥掉量词前缀（"张表"→"表"）。剥完为空则保留原样。"""
    stripped = _MEASURE_PREFIX.sub("", token)
    return stripped or token


def _drop_stopwords(tokens: list[str]) -> list[str]:
    """剔除疑问词与虚词，并还原被量词粘连的实词。

    ★ 剥不空：全是虚词时保留原 token —— 空 tokens 会让检索退化成全表召回。

    ★ 剥到单个通用字（如「表」）**不**退回全 token：实测退回含虚词的全 token 会让
    多 token AND 全灭（R@K 从 100% 掉到 97.5%），比剥到单字更差。单字导致的是
    排序问题（能召回但被通用资产挤占），交给 search 的通用词降权处理。
    """
    cleaned = [_strip_measure_prefix(t) for t in tokens]
    kept = [t for t in cleaned if t.lower() not in _STOPWORDS]
    return kept or cleaned or tokens


def preprocess(query: str, *, today: str | None = None) -> Preprocessed:
    """归一化 + 剥时间词 + 切 token。

    参数:
    - query: 用户原始问法
    - today: 相对时间的参考日期（ISO）。默认取系统当天；测试传固定值以免结果漂移
    """
    raw = query or ""
    ref = date.fromisoformat(today) if today else date.today()

    normalized = _normalize(raw)
    time_range = _extract_time(normalized, ref)

    keyword = _strip_time_words(normalized) if time_range else normalized
    # 纯时间词查询剥完会空 —— 空 keyword 会让检索退化成全表召回，故回退原文
    if not keyword:
        keyword = normalized

    all_tokens = [t for t in tokenize(keyword).split() if t]
    tokens = _drop_stopwords(all_tokens)

    # keyword 也用净化后的实词重组：精确路的等值/ILIKE 与 FTS 的 plainto_tsquery
    # 都基于 keyword，只净化 tokens 不够 —— 留着「是什么」FTS 同样匹配不上。
    if tokens != all_tokens:
        keyword = " ".join(tokens)

    return Preprocessed(
        keyword=keyword or normalized,
        time_range=time_range,
        tokens=tokens,
        raw=raw,
    )
