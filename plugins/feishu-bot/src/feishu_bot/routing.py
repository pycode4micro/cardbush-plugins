"""Reusable account-to-sheet routing, independent of any source business system."""

from __future__ import annotations

import re
from typing import Any

from .api import FeishuAPI, title_check
from .client import FeishuError
from .validation import cell_rows, column_name, column_number, identifier, integer, json_size


def build_plan(
    sheets: list[dict],
    records: list[dict],
    column_order: list[str],
    account_id_field: str,
    account_name_field: str,
    start_column: str,
    start_row: int,
) -> list[dict]:
    if not records or len(records) > 5000:
        raise ValueError("records 必须包含 1–5000 条记录，请按独立批次执行。")
    if not column_order or len(column_order) > 100 or len(set(column_order)) != len(column_order):
        raise ValueError("column_order 必须明确指定 1–100 个互不重复的字段。")
    integer(start_row, 1, 2_000_000, "start_row")
    column_name(column_number(start_column) + len(column_order) - 1)
    groups: dict[str, dict] = {}
    for record in records:
        account_id = record.get(account_id_field)
        if not isinstance(account_id, str):
            raise TypeError("账号 ID 必须使用字符串，避免长数字精度丢失或前导零消失。")
        identifier(account_id, account_id_field)
        if any(key not in record for key in column_order):
            raise ValueError("记录缺少 column_order 指定字段；空值请明确填写 null。")
        if record[column_order[0]] in (None, ""):
            raise ValueError("追加范围的首列不能留空，否则飞书会把该行当作下一次追加起点。")
        if account_id not in groups:
            name = record.get(account_name_field)
            if not isinstance(name, str) or not name.strip():
                raise ValueError("记录缺少非空账号名称。")
            groups[account_id] = {"account_id": account_id, "account_name": name, "values": []}
        groups[account_id]["values"].append([record[key] for key in column_order])
    plan = []
    assigned_sheet_ids: set[str] = set()
    for account_id, group in groups.items():
        # Match a complete ID token. 123 must not match 1234, including alphanumeric IDs.
        pattern = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(account_id)}(?![A-Za-z0-9_-])")
        matches = [sheet for sheet in sheets if pattern.search(str(sheet.get("title", "")))]
        if len(matches) > 1:
            raise ValueError(f"账号 {account_id} 命中多个页签；请先消除歧义，尚未执行写入。")
        if matches and matches[0].get("resource_type", "sheet") not in ("sheet", None, ""):
            raise ValueError(f"账号 {account_id} 匹配的页签不是可写的普通表格。")
        sheet = matches[0] if matches else None
        if sheet:
            if sheet["sheet_id"] in assigned_sheet_ids:
                raise ValueError("不同账号匹配到同一页签；请先消除标题歧义，尚未执行写入。")
            assigned_sheet_ids.add(sheet["sheet_id"])
        title = str(sheet["title"]) if sheet else title_check(f"{group['account_name']}({account_id})")
        plan.append(
            {
                **group,
                "title": title,
                "sheet_id": sheet["sheet_id"] if sheet else None,
                "action": "append" if sheet else "create_and_append",
                "grid_properties": dict(sheet.get("grid_properties", {})) if sheet else {},
                "start_column": start_column,
                "start_row": start_row,
            }
        )
        cell_rows(group["values"])
    json_size(records, 1_800_000)
    return plan


class AccountRouter:
    def __init__(self, api: FeishuAPI):
        self.api = api

    async def _plan(
        self,
        spreadsheet_token: str,
        records: list[dict],
        column_order: list[str],
        account_id_field: str,
        account_name_field: str,
        start_column: str,
        start_row: int,
    ) -> list[dict]:
        identifier(spreadsheet_token, "spreadsheet_token")
        # Validate input before contacting Feishu, then validate all remote matches before any write.
        build_plan([], records, column_order, account_id_field, account_name_field, start_column, start_row)
        data = await self.api.sheets_list(spreadsheet_token)
        return build_plan(
            data.get("sheets", []), records, column_order, account_id_field, account_name_field, start_column, start_row
        )

    async def sheet_route_plan(
        self,
        spreadsheet_token: str,
        records: list[dict[str, Any]],
        column_order: list[str],
        account_id_field: str = "__account_id",
        account_name_field: str = "__account_name",
        start_column: str = "A",
        start_row: int = 1,
    ) -> dict:
        """只读预览按账号 ID 分组后的目标页签及行数；已有同 ID 页签复用，缺失时计划新建。"""
        plan = await self._plan(
            spreadsheet_token, records, column_order, account_id_field, account_name_field, start_column, start_row
        )
        return {
            "groups": [
                {k: v for k, v in group.items() if k not in ("values", "grid_properties")}
                | {"row_count": len(group["values"])}
                for group in plan
            ],
            "row_count": len(records),
            "executed": False,
        }

    async def sheet_route_append(
        self,
        spreadsheet_token: str,
        records: list[dict[str, Any]],
        column_order: list[str],
        account_id_field: str = "__account_id",
        account_name_field: str = "__account_name",
        start_column: str = "A",
        start_row: int = 1,
    ) -> dict:
        """按账号匹配页签并追加；需要同时开启读和写。自动建缺失页签、扩容，不写表头。

        多页签操作不具备事务性。返回已完成、失败和待处理项；部分失败后禁止整批重放。
        """
        self.api.client.settings.require("write")
        self.api.client.settings.require("read")
        async with self.api.client.sheet_write_lock:
            plan = await self._plan(
                spreadsheet_token, records, column_order, account_id_field, account_name_field, start_column, start_row
            )
            completed = []
            for index, group in enumerate(plan):
                sheet_id = group["sheet_id"]
                side_effects: list[dict] = []
                stage = "prepare"
                try:
                    if not sheet_id:
                        stage = "create_sheet"
                        created = await self.api.sheet_create(spreadsheet_token, group["title"])
                        replies = created.get("replies", [])
                        properties = replies[0].get("addSheet", {}).get("properties", {}) if replies else {}
                        sheet_id = properties.get("sheetId")
                        if not sheet_id:
                            raise FeishuError(
                                "新建页签已返回成功但缺少 sheetId，请查询页签列表核实。", outcome="unknown"
                            )
                        identifier(sheet_id, "sheet_id")
                        side_effects.append({"action": "created_sheet", "sheet_id": sheet_id})
                        # Fresh capacity avoids assumptions about Feishu's default row/column count.
                        stage = "read_capacity"
                        current = await self.api.sheets_list(spreadsheet_token)
                        matches = [s for s in current.get("sheets", []) if s.get("sheet_id") == sheet_id]
                        if not matches:
                            raise FeishuError("新建页签暂未出现在列表；停止执行，请重新读取后处理。")
                        grid = matches[0].get("grid_properties", {})
                    else:
                        grid = group["grid_properties"]
                    end_column = column_number(start_column) + len(column_order) - 1
                    end_row = start_row + len(group["values"]) - 1
                    for dimension, key, needed in (
                        ("COLUMNS", "column_count", end_column),
                        ("ROWS", "row_count", end_row),
                    ):
                        stage = f"extend_{dimension.lower()}"
                        capacity = grid.get(key)
                        if not isinstance(capacity, int) or capacity < 1:
                            raise FeishuError("无法确定页签容量，停止追加以避免错误定位。")
                        while capacity < needed:
                            amount = min(5000, needed - capacity)
                            await self.api.sheet_add_dimension(spreadsheet_token, sheet_id, dimension, amount)
                            side_effects.append({"action": "extended", "dimension": dimension, "length": amount})
                            capacity += amount
                    stage = "append"
                    target_range = f"{sheet_id}!{start_column}{start_row}:{column_name(end_column)}{end_row}"
                    result = await self.api.sheet_append(spreadsheet_token, target_range, group["values"])
                    completed.append(
                        {
                            "account_id": group["account_id"],
                            "sheet_id": sheet_id,
                            "row_count": len(group["values"]),
                            "result": result,
                            "side_effects": side_effects,
                        }
                    )
                except (FeishuError, ValueError, TypeError) as exc:
                    error = (
                        exc.as_dict() if isinstance(exc, FeishuError) else {"message": str(exc), "outcome": "rejected"}
                    )
                    return {
                        "ok": False,
                        "completed": completed,
                        "failed": {
                            "account_id": group["account_id"],
                            "sheet_id": sheet_id,
                            "stage": stage,
                            "side_effects": side_effects,
                            "error": error,
                        },
                        "pending_account_ids": [g["account_id"] for g in plan[index + 1 :]],
                        "next_action": "核实失败账号的页签及实际数据，只处理未完成部分；不要整批重放。",
                    }
            return {"ok": True, "completed": completed, "row_count": len(records)}
