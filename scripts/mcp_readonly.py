"""麦当劳MCP只读客户端。凭证从环境变量读取。"""
import json
from http.client import HTTPException
import os
import urllib.error
import urllib.request
from time import monotonic
from json_input import parse_json

ALLOWED = {"query-order", "order-list", "now-time-info", "query-meals", "query-meal-detail", "calculate-price"}


class MCPError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


MAX_RESPONSE_BYTES = 8_000_000


def rpc_result(payload, request_id):
    if (not isinstance(payload, dict) or payload.get("id") != request_id
            or "error" in payload or not isinstance(payload.get("result"), dict)):
        raise MCPError("MCP返回失败或响应格式不正确")
    return payload["result"]


def read_result(response, request_id):
    """SSE收到本次请求的完整事件即返回，不等待服务端关闭长连接。"""
    deadline = monotonic()+25
    first = response.readline(MAX_RESPONSE_BYTES+1)
    used = len(first)
    while first and not first.strip(b" \t\r\n"):
        if used > MAX_RESPONSE_BYTES:
            raise MCPError("响应过大，请缩小查询范围")
        if monotonic() >= deadline:
            raise MCPError("等待MCP事件超时，请稍后重试")
        first = response.readline(MAX_RESPONSE_BYTES-used+1)
        used += len(first)
    if used > MAX_RESPONSE_BYTES:
        raise MCPError("响应过大，请缩小查询范围")
    if first.lstrip().startswith(b"{"):
        rest = response.read(MAX_RESPONSE_BYTES-used+1)
        if used + len(rest) > MAX_RESPONSE_BYTES:
            raise MCPError("响应过大，请缩小查询范围")
        return rpc_result(parse_json(first + rest), request_id)

    parts = []
    line = first
    while True:
        text = line.decode("utf-8").rstrip("\r\n")
        if text.startswith("data:"):
            parts.append(text[5:].lstrip(" "))
        if not text and parts:
            payload = parse_json("\n".join(parts))
            parts.clear()
            if not isinstance(payload, dict):
                raise MCPError("SSE事件格式不正确")
            if payload.get("id") == request_id:
                return rpc_result(payload, request_id)
        if not line:
            raise MCPError("SSE连接结束，但没有收到本次请求的结果")
        if monotonic() >= deadline:
            raise MCPError("等待MCP事件超时，请稍后重试")
        line = response.readline(MAX_RESPONSE_BYTES-used+1)
        used += len(line)
        if used > MAX_RESPONSE_BYTES:
            raise MCPError("响应过大，请缩小查询范围")


class Client:
    def __init__(self):
        token = os.environ.get("MCD_MCP_TOKEN", "")
        if not token or "\n" in token or "\r" in token:
            raise MCPError("请在进程环境中配置MCD_MCP_TOKEN；不要把凭证写入账单或代码")
        self.headers = {"Authorization": "Bearer "+token, "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"}
        self.opener = urllib.request.build_opener(NoRedirect())
        self.serial = 0

    def rpc(self, method, params=None, notification=False):
        self.serial += 1
        body = {"jsonrpc": "2.0", "method": method}
        if not notification:
            body["id"] = self.serial
        if params is not None:
            body["params"] = params
        req = urllib.request.Request("https://mcp.mcd.cn", data=json.dumps(body).encode(), headers=self.headers, method="POST")
        try:
            with self.opener.open(req, timeout=25) as response:
                if response.headers.get("Mcp-Session-Id"):
                    self.headers["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
                if notification:
                    return None
                return read_result(response, body["id"])
        except urllib.error.HTTPError as exc:
            messages = {401: "连接凭证无效或过期，请更新连接", 429: "服务限流，请稍后按Retry-After重试"}
            raise MCPError(messages.get(exc.code, f"官方服务HTTP {exc.code}；未自动重试")) from None
        except (OSError, UnicodeError, HTTPException):
            raise MCPError("连接失败或超时；未自动重试，请稍后再次查询") from None
        except (ValueError, KeyError, TypeError):
            raise MCPError("无法解析MCP响应，未自动重试") from None

    def initialize(self):
        result = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "McFairSplit", "version": "0.1.2"}})
        if result.get("protocolVersion") != "2025-06-18":
            raise MCPError("协议协商结果不受此客户端支持")
        self.headers["MCP-Protocol-Version"] = "2025-06-18"
        self.rpc("notifications/initialized", notification=True)
        return result

    def tools(self):
        rows, cursor = [], None
        for _ in range(10):
            result = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            rows.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return rows
        raise MCPError("工具分页超过上限")

    def call(self, tool, arguments):
        if tool not in ALLOWED:
            raise MCPError("公平账客户端只允许分账相关只读工具")
        result = self.rpc("tools/call", {"name": tool, "arguments": arguments})
        if result.get("isError"):
            raise MCPError("官方业务工具返回失败；未输出原始错误以避免暴露订单信息")
        data = result.get("structuredContent")
        if not isinstance(data, dict):
            content = result.get("content", [])
            if not isinstance(content, list) or not all(isinstance(b, dict) for b in content):
                raise MCPError("业务内容格式不正确")
            for block in content:
                if block.get("type") == "text":
                    try:
                        candidate = parse_json(block["text"])
                        if isinstance(candidate, dict):
                            data = candidate
                            break
                    except (ValueError, KeyError):
                        pass
        if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
            raise MCPError("业务数据格式不匹配，需更新适配器")
        return data["data"]


def scalar_fields(row, fields):
    if not isinstance(row, dict):
        raise MCPError("订单字段格式不正确")
    result = {}
    for field in fields:
        value = row.get(field)
        if value is None:
            continue
        if type(value) not in {str, int}:
            raise MCPError("订单字段类型与预期不符，已停止导出")
        result[field] = value
    return result


def order_rows(data, key, required=False):
    if not isinstance(data, dict) or (required and key not in data):
        raise MCPError("订单响应缺少必要字段")
    rows = data.get(key, [])
    if rows is None and not required:
        return []
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise MCPError("订单列表格式不正确，已停止导出")
    return rows


def safe_order(data):
    """只保留分账字段；嵌套对象不能冒充金额或名称进入导出。"""
    fields = ("orderStatus", "createTime", "storeName", "realTotalAmount", "productPrice",
              "totalDiscountAmount", "realDeliveryPrice", "realPackingFeeTotalPrice", "tablewarePrice")
    safe = scalar_fields(data, fields)
    raw_items = order_rows(data, "orderProductList", required=True)
    safe["items"] = [scalar_fields(item, ("productName", "quantity", "price")) for item in raw_items]
    for raw, item in zip(raw_items, safe["items"]):
        item["components"] = [scalar_fields(c, ("itemName", "itemQuantity")) for c in order_rows(raw, "comboItemList")]
    safe["coupon_candidates"] = [scalar_fields(c, ("couponName", "discountAmount")) for c in order_rows(data, "couponList")]
    return {"source": "official_order_snapshot", "unit_status": "requires_confirmation", "order": safe}


def safe_orders(data):
    return [scalar_fields(item, ("orderId", "storeName", "createTime", "orderStatus", "realTotalAmount"))
            for item in order_rows(data, "list", required=True)]
