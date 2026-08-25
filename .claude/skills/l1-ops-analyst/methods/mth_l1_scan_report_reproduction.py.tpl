def check_conclusion_number_stripping(text):
    """结论审查·数字剥离测试骨架：删掉数字/百分号后仍须是完整判断句。

    True=通过（剥离后仍成立）；False=疑似罗列或含导语，需重写。
    轻量占位，最终判定由 LLM 按 rubric 复核。
    """
    import re
    stripped = re.sub(r"[-\u2212+]?\d[\d,.]*\s*(pp|%|\u4e2a|\u5143)?", "", text)
    stripped = stripped.strip(" \uff0c\u3002\u3001\uff1a;\uff1b\u2014-").strip()
    if len(stripped) < 6:
        return False
    banned = ("\u5982\u4e0b", "\u5305\u62ec", "\u5c55\u793a", "\u7efc\u4e0a")
    if any(w in text for w in banned):
        return False
    return True
