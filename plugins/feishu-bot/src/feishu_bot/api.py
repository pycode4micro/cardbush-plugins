"""Small typed operations; no business-platform, database or workflow imports."""

from __future__ import annotations

import re
import uuid as uuid_module
from typing import Any
from urllib.parse import quote

from .client import FeishuClient, FeishuError
from .validation import a1_range, cell_rows, choice, identifier, integer, json_size, write_range


def sheet_path(token: str, version: int = 2) -> str:
    return f"/open-apis/sheets/v{version}/spreadsheets/{identifier(token, 'spreadsheet_token')}"


def table_path(token: str, table_id: str | None = None) -> str:
    path = f"/open-apis/bitable/v1/apps/{identifier(token, 'app_token')}/tables"
    return path if table_id is None else f"{path}/{identifier(table_id, 'table_id')}"


def pagination(page_size: int, page_token: str, maximum: int = 100) -> dict:
    return {
        "page_size": integer(page_size, 1, maximum, "page_size"),
        **({"page_token": page_token} if page_token else {}),
    }


def title_check(title: str) -> str:
    if not title.strip() or len(title) > 100 or re.search(r"[\[\]:*?/\\\x00-\x1f]", title):
        raise ValueError("页签名称必须为 1–100 个字符，且不含 []:*?/\\ 或控制字符。")
    return title


def message_body(msg_type: str, content: dict, request_uuid: str = "") -> dict:
    choice(
        msg_type,
        ("text", "post", "interactive", "image", "file", "audio", "media", "sticker", "share_chat", "share_user"),
        "msg_type",
    )
    if msg_type == "text" and (not isinstance(content.get("text"), str) or not content["text"]):
        raise ValueError("text 消息的 content 必须包含非空 text 字符串。")
    result = {
        "msg_type": msg_type,
        "content": json_size(content, 30_000 if msg_type in ("post", "interactive") else 150_000),
    }
    if request_uuid:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", request_uuid):
            raise ValueError("request_uuid 必须为 1–50 个字母、数字、连字符或下划线。")
        result["uuid"] = request_uuid
    return result


class FeishuAPI:
    def __init__(self, client: FeishuClient):
        self.client = client

    async def bot_info(self) -> dict:
        """读取当前应用机器人的信息，验证应用身份；不发送消息。"""
        return await self.client.request("GET", "/open-apis/bot/v3/info")

    async def chats_list(self, page_size: int = 20, page_token: str = "") -> dict:
        """分页列出机器人可访问的群；保留 has_more/page_token，不能将一页误当全部。"""
        return await self.client.request("GET", "/open-apis/im/v1/chats", params=pagination(page_size, page_token))

    async def chat_get(self, chat_id: str) -> dict:
        """读取指定群的信息。"""
        return await self.client.request("GET", f"/open-apis/im/v1/chats/{identifier(chat_id, 'chat_id')}")

    async def messages_list(
        self,
        chat_id: str,
        page_size: int = 20,
        page_token: str = "",
        start_time: str = "",
        end_time: str = "",
        sort_type: str = "ByCreateTimeAsc",
    ) -> dict:
        """分页读取群历史消息。时间为 Unix 秒数字字符串；需要飞书消息读取权限。"""
        params = {
            "container_id_type": "chat",
            "container_id": identifier(chat_id, "chat_id"),
            "sort_type": choice(sort_type, ("ByCreateTimeAsc", "ByCreateTimeDesc"), "sort_type"),
            **pagination(page_size, page_token, 50),
        }
        for key, val in (("start_time", start_time), ("end_time", end_time)):
            if val:
                if not val.isdigit():
                    raise ValueError(f"{key} 必须是 Unix 秒数字字符串。")
                params[key] = val
        if start_time and end_time and int(start_time) >= int(end_time):
            raise ValueError("start_time 必须早于 end_time。")
        return await self.client.request("GET", "/open-apis/im/v1/messages", params=params)

    async def message_get(self, message_id: str) -> dict:
        """读取一条消息的内容及元数据。"""
        return await self.client.request("GET", f"/open-apis/im/v1/messages/{identifier(message_id, 'message_id')}")

    async def message_send(
        self,
        receive_id: str,
        content: dict[str, Any],
        receive_id_type: str = "chat_id",
        msg_type: str = "text",
        request_uuid: str = "",
    ) -> dict:
        """向明确指定的用户或群发送消息。content 是对象（文本用 {text: ...}）。同 UUID 一小时内去重。"""
        if not receive_id.strip():
            raise ValueError("receive_id 不能为空。")
        choice(receive_id_type, ("open_id", "union_id", "user_id", "email", "chat_id"), "receive_id_type")
        request_uuid = request_uuid or str(uuid_module.uuid4())
        body = {"receive_id": receive_id, **message_body(msg_type, content, request_uuid)}
        try:
            data = await self.client.request(
                "POST", "/open-apis/im/v1/messages", params={"receive_id_type": receive_id_type}, body=body
            )
        except FeishuError as exc:
            raise FeishuError(
                f"{exc} request_uuid={request_uuid}",
                code=exc.code,
                http_status=exc.http_status,
                request_id=exc.request_id,
                outcome=exc.outcome,
            ) from None
        return {"request_uuid": request_uuid, "data": data}

    async def message_reply(
        self, message_id: str, content: dict[str, Any], msg_type: str = "text", request_uuid: str = ""
    ) -> dict:
        """回复指定消息；需要修改能力和用户对回复操作的授权。"""
        path = f"/open-apis/im/v1/messages/{identifier(message_id, 'message_id')}/reply"
        request_uuid = request_uuid or str(uuid_module.uuid4())
        body = message_body(msg_type, content, request_uuid)
        try:
            data = await self.client.request("POST", path, body=body)
        except FeishuError as exc:
            raise FeishuError(
                f"{exc} request_uuid={request_uuid}",
                code=exc.code,
                http_status=exc.http_status,
                request_id=exc.request_id,
                outcome=exc.outcome,
            ) from None
        return {"request_uuid": request_uuid, "data": data}

    async def message_recall(self, message_id: str) -> dict:
        """撤回机器人消息。受飞书发送身份、时间及权限规则限制；属于删除操作。"""
        return await self.client.request("DELETE", f"/open-apis/im/v1/messages/{identifier(message_id, 'message_id')}")

    async def webhook_send(self, content: dict[str, Any], msg_type: str = "text") -> dict:
        """向环境变量中配置的自定义群机器人发送消息；只支持发送，不具有表格读取能力。"""
        choice(msg_type, ("text", "post", "interactive", "image", "share_chat"), "msg_type")
        json_size(content, 19_000)
        if msg_type == "text" and not isinstance(content.get("text"), str):
            raise ValueError("text 消息需要 content.text 字符串。")
        payload = {"msg_type": msg_type, "card" if msg_type == "interactive" else "content": content}
        return await self.client.webhook(payload)

    async def spreadsheet_get(self, spreadsheet_token: str) -> dict:
        """读取电子表格元数据；token 取自 /sheets/ 后的 URL 段。"""
        return await self.client.request("GET", sheet_path(spreadsheet_token, 3))

    async def sheets_list(self, spreadsheet_token: str) -> dict:
        """列出电子表格所有页签，包含 sheet_id、标题和行列容量。"""
        return await self.client.request("GET", sheet_path(spreadsheet_token, 3) + "/sheets/query")

    async def sheet_read(self, spreadsheet_token: str, range: str, value_render_option: str = "ToString") -> dict:
        """读取 sheet_id!A1:B20 范围。ToString 保留编码文本；返回值遵循飞书空白行省略规则。"""
        choice(
            value_render_option, ("ToString", "FormattedValue", "Formula", "UnformattedValue"), "value_render_option"
        )
        return await self.client.request(
            "GET",
            sheet_path(spreadsheet_token) + "/values/" + quote(a1_range(range), safe=""),
            params={"valueRenderOption": value_render_option},
        )

    async def sheet_create(self, spreadsheet_token: str, title: str) -> dict:
        """在已有电子表格内新建一个空白页签。"""
        return await self.client.request(
            "POST",
            sheet_path(spreadsheet_token) + "/sheets_batch_update",
            body={"requests": [{"addSheet": {"properties": {"title": title_check(title)}}}]},
        )

    async def sheet_rename(self, spreadsheet_token: str, sheet_id: str, title: str) -> dict:
        """重命名已有页签。"""
        return await self.client.request(
            "POST",
            sheet_path(spreadsheet_token) + "/sheets_batch_update",
            body={
                "requests": [
                    {
                        "updateSheet": {
                            "properties": {"sheetId": identifier(sheet_id, "sheet_id"), "title": title_check(title)}
                        }
                    }
                ]
            },
        )

    async def sheet_delete(self, spreadsheet_token: str, sheet_id: str) -> dict:
        """删除整个页签及数据；只在用户明确要求删除该页签时使用。"""
        return await self.client.request(
            "POST",
            sheet_path(spreadsheet_token) + "/sheets_batch_update",
            body={"requests": [{"deleteSheet": {"sheetId": identifier(sheet_id, "sheet_id")}}]},
        )

    async def sheet_write(self, spreadsheet_token: str, range: str, values: list[list[Any]]) -> dict:
        """覆盖指定范围。values 为矩形数组，单次最多 5000 行、100 列；不会自动清空范围外数据。"""
        cell_rows(values)
        write_range(range, values)
        return await self.client.request(
            "PUT",
            sheet_path(spreadsheet_token) + "/values",
            body={"valueRange": {"range": a1_range(range), "values": cell_rows(values)}},
        )

    async def sheet_append(self, spreadsheet_token: str, range: str, values: list[list[Any]]) -> dict:
        """从范围首列第一个空白位置追加，固定 INSERT_ROWS 避免覆盖。不是幂等操作，勿盲目重试。"""
        cell_rows(values)
        write_range(range, values)
        return await self.client.request(
            "POST",
            sheet_path(spreadsheet_token) + "/values_append",
            params={"insertDataOption": "INSERT_ROWS"},
            body={"valueRange": {"range": a1_range(range), "values": cell_rows(values)}},
        )

    async def sheet_add_dimension(self, spreadsheet_token: str, sheet_id: str, dimension: str, length: int) -> dict:
        """在页签尾部增加空白行或列。dimension=ROWS/COLUMNS，单次 1–5000。"""
        return await self.client.request(
            "POST",
            sheet_path(spreadsheet_token) + "/dimension_range",
            body={
                "dimension": {
                    "sheetId": identifier(sheet_id, "sheet_id"),
                    "majorDimension": choice(dimension, ("ROWS", "COLUMNS"), "dimension"),
                    "length": integer(length, 1, 5000, "length"),
                }
            },
        )

    async def bitable_tables_list(self, app_token: str, page_size: int = 100, page_token: str = "") -> dict:
        """分页列出多维表格的数据表；app_token 取自 /base/ 后的 URL 段。"""
        return await self.client.request("GET", table_path(app_token), params=pagination(page_size, page_token))

    async def bitable_fields_list(
        self, app_token: str, table_id: str, page_size: int = 100, page_token: str = ""
    ) -> dict:
        """分页读取数据表字段名和字段类型；写入前据此构造 fields。"""
        return await self.client.request(
            "GET", table_path(app_token, table_id) + "/fields", params=pagination(page_size, page_token)
        )

    async def bitable_records_search(
        self,
        app_token: str,
        table_id: str,
        page_size: int = 100,
        page_token: str = "",
        view_id: str = "",
        field_names: list[str] | None = None,
        filter: dict[str, Any] | None = None,
        sort: list[dict[str, Any]] | None = None,
    ) -> dict:
        """只读查询多维表格记录（HTTP POST）。分页最多 500；filter/sort 为飞书官方结构。"""
        body: dict[str, Any] = {}
        if view_id:
            body["view_id"] = identifier(view_id, "view_id")
        if field_names is not None:
            if len(field_names) > 200:
                raise ValueError("field_names 最多 200 个字段。")
            body["field_names"] = field_names
        if filter is not None:
            body["filter"] = filter
        if sort is not None:
            if len(sort) > 100:
                raise ValueError("sort 最多 100 项。")
            body["sort"] = sort
        json_size(body, 200_000)
        return await self.client.request(
            "POST",
            table_path(app_token, table_id) + "/records/search",
            params=pagination(page_size, page_token, 500),
            body=body,
        )

    async def bitable_record_get(self, app_token: str, table_id: str, record_id: str) -> dict:
        """按 record_id 读取一条多维表格记录。"""
        return await self.client.request(
            "GET", table_path(app_token, table_id) + f"/records/{identifier(record_id, 'record_id')}"
        )

    async def bitable_records_create(self, app_token: str, table_id: str, records: list[dict[str, Any]]) -> dict:
        """批量新建 1–500 条记录，每项形如 {fields: {字段名: 值}}。不会自动分批或重试。"""
        self._records_check(records)
        return await self.client.request(
            "POST", table_path(app_token, table_id) + "/records/batch_create", body={"records": records}
        )

    async def bitable_records_update(self, app_token: str, table_id: str, records: list[dict[str, Any]]) -> dict:
        """批量修改 1–500 条记录，每项包含 record_id 和 fields；未提供的字段保持原值。"""
        self._records_check(records, updating=True)
        return await self.client.request(
            "POST", table_path(app_token, table_id) + "/records/batch_update", body={"records": records}
        )

    async def bitable_records_delete(self, app_token: str, table_id: str, record_ids: list[str]) -> dict:
        """批量删除明确指定的 1–500 条记录；这是删除操作。"""
        integer(len(record_ids), 1, 500, "record_ids 数量")
        if len(set(record_ids)) != len(record_ids):
            raise ValueError("record_ids 不能重复。")
        records = [identifier(x, "record_id") for x in record_ids]
        return await self.client.request(
            "POST", table_path(app_token, table_id) + "/records/batch_delete", body={"records": records}
        )

    @staticmethod
    def _records_check(records: list[dict], updating: bool = False) -> None:
        integer(len(records), 1, 500, "records 数量")
        for record in records:
            allowed = {"fields", "record_id"} if updating else {"fields"}
            if not isinstance(record, dict) or not isinstance(record.get("fields"), dict) or set(record) - allowed:
                raise ValueError("每条记录必须包含 fields 对象；更新时还需要 record_id。")
            if updating:
                identifier(record.get("record_id"), "record_id")
        if updating and len({r["record_id"] for r in records}) != len(records):
            raise ValueError("同一更新批次不能重复 record_id。")
        json_size(records, 1_800_000)
