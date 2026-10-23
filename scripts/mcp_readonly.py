"""Minimal MCP Streamable HTTP client. Fixed official origin, no redirects, read-only."""
import json
import os
import urllib.error
import urllib.request

ALLOWED = {"query-order", "order-list", "now-time-info", "query-meals", "query-meal-detail", "calculate-price"}


class MCPError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


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
                raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise MCPError("响应过大，请缩小查询范围")
                raw = raw.decode("utf-8")
        except urllib.error.HTTPError as exc:
            messages = {401: "连接凭证无效或过期，请更新连接", 429: "服务限流，请稍后按Retry-After重试"}
            raise MCPError(messages.get(exc.code, f"官方服务HTTP {exc.code}；未自动重试")) from None
        except (OSError, UnicodeError):
            raise MCPError("连接失败或超时；未自动重试，请稍后再次查询") from None
        if notification:
            return None
        try:
            if raw.lstrip().startswith("{"):
                payload = json.loads(raw)
            else:
                payload = None
                for block in raw.replace("\r\n", "\n").split("\n\n"):
                    data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
                    if not data:
                        continue
                    candidate = json.loads(data)
                    if candidate.get("id") == body["id"]:
                        payload = candidate
                        break
            if not isinstance(payload, dict) or payload.get("id") != body["id"] or "error" in payload:
                raise MCPError("MCP请求未成功，未自动重试")
            return payload["result"]
        except (ValueError, KeyError, TypeError):
            raise MCPError("无法解析MCP响应，未自动重试") from None

    def initialize(self):
        result = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "McFairSplit", "version": "0.1.0"}})
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
            for block in result.get("content", []):
                if block.get("type") == "text":
                    try:
                        candidate = json.loads(block["text"])
                        if isinstance(candidate, dict):
                            data = candidate
                            break
                    except (ValueError, KeyError):
                        pass
        if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
            raise MCPError("业务数据格式不匹配，需更新适配器")
        return data["data"]


def safe_order(data):
    """Preserve only accounting candidates. Do not export raw customer/delivery data."""
    fields = ("orderStatus", "createTime", "storeName", "realTotalAmount", "productPrice",
              "totalDiscountAmount", "realDeliveryPrice", "realPackingFeeTotalPrice", "tablewarePrice")
    safe = {k: data[k] for k in fields if k in data}
    safe["items"] = [{k: item[k] for k in ("productName", "quantity", "price") if k in item}
                     for item in data.get("orderProductList", [])]
    for raw, item in zip(data.get("orderProductList", []), safe["items"]):
        item["components"] = [{k: c[k] for k in ("itemName", "itemQuantity") if k in c}
                              for c in raw.get("comboItemList", [])]
    safe["coupon_candidates"] = [{k: c[k] for k in ("couponName", "discountAmount") if k in c}
                                  for c in data.get("couponList", [])]
    return {"source": "official_order_snapshot", "unit_status": "requires_confirmation", "order": safe}


def safe_orders(data):
    return [{k: item[k] for k in ("orderId", "storeName", "createTime", "orderStatus", "realTotalAmount") if k in item}
            for item in data.get("list", [])]
