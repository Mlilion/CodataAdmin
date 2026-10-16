"""SQL 补丁的可执行性守卫。

## 为什么需要这个文件

本仓的补丁执行器**按分号切语句**逐条执行，并且**吞掉单条失败继续往下走**。
于是一个注释里的分号会造成这样的连锁：

1. 分号把上一条语句切断，分号后的注释正文**丢掉了 `--` 前缀**（它已不在行首）；
2. 那段正文成为下一个片段的开头，粘在紧随其后的真语句前面；
3. 库端报 1064，执行器记一行日志继续；
4. **"补丁跑完了"与"什么也没建出来"看起来完全一样。**

而新库是靠 `create_all` 建表的，所以**全套测试照样全绿** —— 缺陷只在已有库执行补丁
后才显形，通常要等到线上某次写入才炸。

这个 bug 已经复发**三次**（`ae8dd599`、`1301bbb6`、`5eaf63fb`），三次都是同一个形状，
其中两次那句"注释里不要写分号"的提醒**自己就写了那个字符**。三次都没有任何自动
检查能拦住它 —— `tests/` 下此前没有一处读 `sql/patches/`。

实测危害（2026-08-05 在 dev 上做的 A/B）：修复前 `2026-08-02-semantic-mcp-token.sql`
在目标表不存在的库上有 1 条语句报错、`sem_mcp_token` **建不出来**（那张表是 MCP 令牌
管理面的全部依赖）；`2026-07-21-semantic-model-tree.sql` 的 `sem_model` 同样建不出来。
修复后两者都正常建出。

## 判据

按同样的方式切分并剥离注释后，每个非空片段都必须以一个 SQL 关键字开头。
片段以别的东西开头 == 有注释正文或字符串内容漏进了语句位置。

★ 判据刻意只有这一条，不做 SQL 语法校验：语法要靠数据库判，而这里要拦的是
**分号切分把文件切错**这一件事，它是可以纯文本判定的。
"""

from pathlib import Path

import pytest

PATCH_DIR = Path(__file__).resolve().parents[1] / "sql" / "patches"

# 允许作为语句开头的关键字。缺哪个就补哪个 —— 但**不要**把判据改成"跳过检查"。
_SQL_KEYWORDS = frozenset({
    "ALTER", "ANALYZE", "BEGIN", "CALL", "COMMIT", "CREATE", "DEALLOCATE",
    "DELETE", "DESCRIBE", "DROP", "EXECUTE", "EXPLAIN", "FLUSH", "GRANT",
    "INSERT", "LOCK", "OPTIMIZE", "PREPARE", "RENAME", "REPLACE", "REVOKE",
    "ROLLBACK", "SELECT", "SET", "SHOW", "START", "TRUNCATE", "UNLOCK",
    "UPDATE", "USE", "WITH",
})


def _executable_fragments(sql_text: str) -> list[str]:
    """按分号切分 + 剥掉整行注释，返回会被真正送去执行的那些片段。

    这段逻辑必须与仓里跑补丁的方式保持一致：先按 `;` 切，再丢掉以 `--` 开头的**整行**。
    注意剥离是按行做的 —— 所以行**中间**的分号才有害（它后面的正文不再以 `--` 开头），
    而注释行**末尾**的分号（回滚示例那种）无害。
    """
    fragments = []
    for chunk in sql_text.split(";"):
        body = "\n".join(
            line for line in chunk.split("\n") if not line.strip().startswith("--")
        ).strip()
        if body:
            fragments.append(body)
    return fragments


def _first_token(fragment: str) -> str:
    return fragment.lstrip("( \n\t").split()[0].upper().strip("`")


def _patch_files() -> list[Path]:
    return sorted(PATCH_DIR.glob("*.sql"))


def test_patch_directory_is_found():
    """★ 先确认真扫到了文件。

    没有这条，`glob` 因为路径写错而返回空列表时，下面的参数化测试会**零用例通过** ——
    一个什么都没检查的绿灯，比没有守卫更糟（它让人以为已经有守卫了）。
    """
    files = _patch_files()

    assert files, f"没有扫到任何补丁文件，PATCH_DIR 可能不对: {PATCH_DIR}"
    assert len(files) >= 20, f"补丁文件只扫到 {len(files)} 个，路径可能不完整"


@pytest.mark.parametrize("patch", _patch_files(), ids=lambda p: p.name)
def test_every_fragment_starts_with_a_sql_keyword(patch: Path):
    """★★ 每个可执行片段都必须以 SQL 关键字开头。

    失败说明该文件里有一个分号把语句切错了 —— 常见来源两种：
    ① 注释行**中间**的分号（后面还有正文）；
    ② 写在字符串字面量里的分号（如 `COMMENT '不可逆; 明文只给一次'`）。

    改法：注释里把"分号"写成中文词而不是那个字符；字符串里的分号换成逗号。
    """
    fragments = _executable_fragments(patch.read_text(encoding="utf-8"))
    broken = [
        (i, frag[:100].replace("\n", " ⏎ "))
        for i, frag in enumerate(fragments)
        if _first_token(frag) not in _SQL_KEYWORDS
    ]

    assert not broken, (
        f"{patch.name} 有 {len(broken)} 个片段不以 SQL 关键字开头 —— "
        f"分号把语句切错了，该补丁在已有库上会静默少执行语句:\n"
        + "\n".join(f"  片段 {i}: {text}" for i, text in broken)
    )


@pytest.mark.parametrize("patch", _patch_files(), ids=lambda p: p.name)
def test_no_semicolon_in_the_middle_of_a_comment_line(patch: Path):
    """★ 直接盯住成因，而不只是盯住后果。

    上一条测的是"切坏了没有"，这条测的是"有没有那个会把它切坏的写法"。两条都要：
    前者可能被巧合掩盖（分号刚好落在两条语句之间时不产生坏片段），而这个写法**迟早**
    会在有人插入新语句时变成缺陷。

    只查注释行**中间**的分号：行尾分号（`-- DROP TABLE t;` 这种回滚示例）不会造成
    坏片段，且这类示例在全仓大量存在、是有用的文档。
    """
    offenders = []
    for lineno, line in enumerate(patch.read_text(encoding="utf-8").split("\n"), 1):
        stripped = line.strip()
        if not stripped.startswith("--") or ";" not in stripped:
            continue
        if stripped.split(";", 1)[1].strip():  # 分号后还有正文
            offenders.append((lineno, stripped[:100]))

    assert not offenders, (
        f"{patch.name} 的注释行里有分号且后面还有正文 —— 切分后那段正文会丢掉 `--` "
        f"前缀、粘到下一条语句头上。请把'分号'写成中文词，或把正文挪到下一行:\n"
        + "\n".join(f"  L{no}: {text}" for no, text in offenders)
    )


def test_common_file_permissions_are_seeded_by_patch():
    """通用文件接口有后端权限守卫，权限点必须能在菜单/角色体系里授权。"""
    patch = PATCH_DIR / "2026-08-26-common-file-permissions.sql"

    sql = patch.read_text(encoding="utf-8")

    assert "module_common:file:upload" in sql
    assert "module_common:file:download" in sql
