"""中文分词（替代 zhparser）。

为什么不用 zhparser：它需要在 PG 容器内编译安装扩展，无官方预装镜像。核实 DataSage
的实现后确认 zhparser 的角色**仅是把中文切成空格分隔的 token 串**，随后用
`to_tsvector('simple', tokens)` 建索引 —— `simple` 配置只按空格切分，不做语言处理。
故分词器完全可替换：Python 侧用 jieba，PG 侧仍 `to_tsvector('simple', tokens)`。

收益：PG 只需 pgvector 一个扩展（官方镜像自带），且业务词典进 git 可版本管理
（参考方案坑清单第 3 条要求"客单价""履约时效"这类词不可切分）。

★ 写入与查询必须走同一个 tokenize()，两边分词不一致会召不回。
"""

import re
from pathlib import Path

from app.core.logger import logger

# 业务词典：一行一词，可选 "词 词频 词性"。这些词不该被切碎。
_USER_DICT = Path(__file__).parent / "userdict.txt"

# 标识符：snake_case（order_class）与限定名（dws.dws_order_revenue_df）。
# jieba 会把它们按 _ 和 . 切碎（order / _ / class），而语义模型的 code、字段名、
# 枚举值大量是这种形态，切碎显著损伤检索精度。故先抠出占位、分词后还原。
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[._][A-Za-z0-9]+)+")
# 占位符用纯字母数字，jieba 不会拆（两侧留空格避免与邻词粘连）
_PLACEHOLDER = "zqidentz{}z"

_jieba = None


def _get_jieba():
    """懒加载 jieba（首次分词才付初始化代价），并挂载业务词典。"""
    global _jieba
    if _jieba is None:
        import jieba

        if _USER_DICT.exists():
            try:
                jieba.load_userdict(str(_USER_DICT))
            except Exception as e:  # noqa: BLE001 - 词典坏了不该让检索整体失效
                logger.warning(f"业务词典加载失败，退化为默认词库: {e}")
        _jieba = jieba
    return _jieba


def tokenize(text: str) -> str:
    """切词并以空格连接，供 `to_tsvector('simple', tokens)` 使用。

    标识符（order_class / dws.dws_order_revenue_df）整体保留，中文照常切词。

    jieba 不可用时退化为原文（PG 侧仍按空格切，中文会退化成整串匹配，
    但不会让写入或检索失败）。
    """
    if not text or not text.strip():
        return ""
    try:
        # 抠出标识符换成占位符，避免被按 _ / . 切碎
        found: list[str] = []

        def _stash(m: re.Match[str]) -> str:
            found.append(m.group(0))
            return f" {_PLACEHOLDER.format(len(found) - 1)} "

        masked = _IDENTIFIER.sub(_stash, text)
        words = [w for w in (w.strip() for w in _get_jieba().cut(masked)) if w]

        # 还原：占位符可能被 jieba 切开，故按整词匹配后逐个替换
        out: list[str] = []
        for w in words:
            for idx, original in enumerate(found):
                if w == _PLACEHOLDER.format(idx):
                    out.append(original)
                    break
            else:
                out.append(w)
        return " ".join(out)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"分词失败，退化为原文: {e}")
        return text.strip()
