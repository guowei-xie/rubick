"""rubick-skill.md 的版本契约:客户端 Agent 靠 frontmatter 的 version 判断要不要自更新。

改了正文却没升 version,已装的客户端就永远拿不到这次修改 —— 下面的快照专门拦这一种。
"""
import hashlib
import re
from pathlib import Path

SKILL_FILE = Path(__file__).resolve().parents[2] / "frontend" / "public" / "rubick-skill.md"

# 改了 skill 正文:先把 frontmatter 的 version 升一格,再把这里两项一起更新
SNAPSHOT_VERSION = "2026.09.24.1"
SNAPSHOT_BODY_SHA256 = "d0a6104afe2465897a2d8759957bdd68724337526c227d39df23af6f0b180f1a"


def _split():
    text = SKILL_FILE.read_text(encoding="utf-8")
    # 客户端校验「真是 skill」也是看这个开头,别让 BOM 或空行混进来
    assert text.startswith("---\n")
    _, front, body = text.split("---\n", 2)
    meta = dict(
        line.split(":", 1) for line in front.strip().splitlines() if ":" in line
    )
    return {k.strip(): v.strip() for k, v in meta.items()}, body


def test_frontmatter_has_name_and_version():
    meta, _ = _split()
    assert meta["name"] == "rubick-skill"
    # 客户端按 . 切段逐段比数字,格式乱了比较就不可靠
    assert re.fullmatch(r"\d{4}\.\d{2}\.\d{2}\.\d+", meta["version"])


def test_body_change_requires_version_bump():
    meta, body = _split()
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if digest != SNAPSHOT_BODY_SHA256:
        assert meta["version"] != SNAPSHOT_VERSION, (
            "rubick-skill.md 正文变了但 version 没升:已安装的客户端不会拉到这次修改。"
            "请升 frontmatter 的 version,再更新本测试的 SNAPSHOT_VERSION / SNAPSHOT_BODY_SHA256"
        )
        raise AssertionError(
            f"version 已升,请把快照更新为 SNAPSHOT_VERSION={meta['version']!r}、"
            f"SNAPSHOT_BODY_SHA256={digest!r}"
        )
    assert meta["version"] == SNAPSHOT_VERSION
