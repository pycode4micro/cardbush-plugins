"""Local validation performed before remote side effects."""

import json
import math
import re
from typing import Any


def identifier(value: str, name: str = "id") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
        raise ValueError(f"{name} 必须为非空 ID/token，不能填写完整 URL。")
    return value


def a1_range(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]+![A-Z]{1,3}(?:[1-9][0-9]*)?(?::[A-Z]{1,3}(?:[1-9][0-9]*)?)?", value
    ):
        raise ValueError("range 必须使用 sheet_id!A1:B20 或 sheet_id!A:Z 格式，使用页签 ID 而非名称。")
    return value


def write_range(value: str, values: list[list[Any]]) -> str:
    """Validate that the explicit target can contain the entire matrix."""
    a1_range(value)
    match = re.fullmatch(r"[^!]+!([A-Z]+)([0-9]*)(?::([A-Z]+)([0-9]*))?", value)
    start_col, start_row, end_col, end_row = match.groups()
    end_col = end_col or start_col
    width = column_number(end_col) - column_number(start_col) + 1
    if width < len(values[0]):
        raise ValueError("range 的列范围小于 values 宽度或顺序颠倒。")
    if start_row and end_row and int(end_row) - int(start_row) + 1 < len(values):
        raise ValueError("range 的行范围小于 values 行数或顺序颠倒。")
    if start_row and match.group(3) is None and len(values) > 1:
        raise ValueError("单个单元格范围不能写入多行数据。")
    return value


def integer(value: int, minimum: int, maximum: int, name: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须为 {minimum} 到 {maximum} 之间的整数。")
    return value


def choice(value: str, allowed: tuple[str, ...], name: str) -> str:
    if value not in allowed:
        raise ValueError(f"{name} 必须为以下值之一：{', '.join(allowed)}。")
    return value


def json_size(value: Any, maximum: int) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError):
        raise ValueError("数据必须是有效 JSON，不能包含 NaN/Infinity。") from None
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError(f"数据超过本工具 {maximum} 字节限制，请拆分批次。")
    return encoded


def cell_rows(values: list[list[Any]]) -> list[list[Any]]:
    if not isinstance(values, list) or not 1 <= len(values) <= 5000:
        raise ValueError("values 必须包含 1 到 5000 行。")
    if any(not isinstance(row, list) or not row for row in values):
        raise ValueError("每一行必须是非空数组。")
    width = len(values[0])
    if width > 100 or any(len(row) != width for row in values):
        raise ValueError("values 必须是矩形数组，每行最多 100 列。")
    for row in values:
        for value in row:
            if value is not None and not isinstance(value, (str, bool, int, float, dict, list)):
                raise ValueError("单元格必须是 JSON 值。")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("单元格不能包含 NaN/Infinity。")
            if isinstance(value, str) and len(value) > 50000:
                raise ValueError("单元格文本不能超过 50000 字符。")
    json_size(values, 1_800_000)
    return values


def column_number(label: str) -> int:
    if not re.fullmatch(r"[A-Z]{1,3}", label):
        raise ValueError("列名必须是 A 到 ZZZ 之间的大写字母。")
    result = 0
    for char in label:
        result = result * 26 + ord(char) - 64
    return result


def column_name(number: int) -> str:
    integer(number, 1, 18278, "column")
    result = ""
    while number:
        number, rem = divmod(number - 1, 26)
        result = chr(65 + rem) + result
    return result
