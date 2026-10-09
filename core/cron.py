"""极简 5 字段 cron 解析器。

语法: 分 时 日 月 周 (与 crontab 一致)
支持: * , - / 数字, 星期英文缩写(mon-sun), @daily 等别名
语义: "日"与"周"同时受限时按标准 cron 取并集, 否则取交集
"""
import datetime

ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}

_DOW_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def _token_to_int(tok, names):
    tok = tok.strip().lower()
    if names and tok in names:
        return names[tok]
    return int(tok)


def _parse_field(field, low, high, names=None):
    values = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise ValueError("字段 '%s' 含空片段" % field)
        body, step = part, 1
        if "/" in part:
            body, _, step_s = part.partition("/")
            try:
                step = int(step_s)
            except ValueError:
                raise ValueError("步长无效: '%s'" % part)
            if step <= 0:
                raise ValueError("步长必须为正数: '%s'" % part)
        if body == "*":
            start, end = low, high
        elif "-" in body:
            a, _, b = body.partition("-")
            start = _token_to_int(a, names)
            end = _token_to_int(b, names)
        else:
            start = _token_to_int(body, names)
            end = high if "/" in part else start
        if not (low <= start <= high and low <= end <= high):
            raise ValueError("字段 '%s' 取值超出范围 [%d,%d]" % (field, low, high))
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError("字段 '%s' 未匹配到任何值" % field)
    return values


def _parse(expr):
    expr = (expr or "").strip()
    if not expr:
        raise ValueError("cron 表达式不能为空")
    expr = ALIASES.get(expr.lower(), expr)
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron 表达式需要 5 个字段: 分 时 日 月 周")
    return {
        "minute": _parse_field(parts[0], 0, 59),
        "hour": _parse_field(parts[1], 0, 23),
        "dom": _parse_field(parts[2], 1, 31),
        "month": _parse_field(parts[3], 1, 12),
        "dow": {0 if v == 7 else v for v in _parse_field(parts[4], 0, 7, _DOW_NAMES)},
        "dom_star": parts[2] == "*",
        "dow_star": parts[4] == "*",
    }


def _day_match(t, p):
    dom_ok = t.day in p["dom"]
    # Python weekday(): 周一=0; cron: 周日=0
    dow_ok = ((t.weekday() + 1) % 7) in p["dow"]
    if p["dom_star"] and p["dow_star"]:
        return True
    if p["dom_star"]:
        return dow_ok
    if p["dow_star"]:
        return dom_ok
    return dom_ok or dow_ok


def next_after(expr, after=None):
    """返回 expr 在 after 之后的下一次触发时间(本地时间)。

    表达式非法时抛 ValueError; 一年内无匹配时返回 None。
    """
    p = _parse(expr)
    t = (after or datetime.datetime.now()).replace(second=0, microsecond=0)
    t += datetime.timedelta(minutes=1)
    limit = t + datetime.timedelta(days=366)
    while t < limit:
        if t.month not in p["month"]:
            if t.month == 12:
                t = t.replace(year=t.year + 1, month=1, day=1, hour=0, minute=0)
            else:
                t = t.replace(month=t.month + 1, day=1, hour=0, minute=0)
            continue
        if not _day_match(t, p):
            t = (t + datetime.timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if t.hour not in p["hour"]:
            t = (t + datetime.timedelta(hours=1)).replace(minute=0)
            continue
        if t.minute not in p["minute"]:
            t += datetime.timedelta(minutes=1)
            continue
        return t
    return None
