#!/usr/bin/env python3
"""命令行入口：python3 scripts/cli.py --help。"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

from fairsplit import BillError, compare, history, require, save_version, solve, yuan, minor
from mcp_readonly import Client, MCPError, safe_order, safe_orders
from json_input import JSONInputError, parse_json
import render

ROOT = Path(__file__).resolve().parent.parent


def load(path):
    try:
        with open(path, "rb") as stream:
            raw = stream.read(8_000_001)
        require(len(raw) <= 8_000_000, "输入文件超过8MB，请只保留分账所需数据")
        return parse_json(raw.decode("utf-8"))
    except JSONInputError as exc:
        raise BillError(str(exc)) from None
    except UnicodeError:
        raise BillError("输入不是有效的UTF-8 JSON") from None


def write(path, content):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    require(not target.is_symlink(), "导出路径不能是符号链接")
    descriptor, temporary = tempfile.mkstemp(prefix=".fairsplit-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def separate_paths(outputs, inputs=()):
    paths = [Path(p) for p in outputs if p]
    protected = [Path(p) for p in inputs if p]
    def same(a, b):
        return a.resolve() == b.resolve() or (a.exists() and b.exists() and a.samefile(b))
    require(not any(same(a, b) for i, a in enumerate(paths) for b in paths[i+1:]),
            "各导出文件必须使用不同路径")
    require(not any(same(a, b) for a in paths for b in protected), "输出文件不能覆盖输入文件")


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2)+"\n"


def draft(snapshot, total_unit, bill_id, delivery_unit=None):
    require(total_unit in {"yuan", "fen"} and delivery_unit in {None, "yuan", "fen"}, "金额单位须为yuan或fen")
    require(snapshot.get("source") == "official_order_snapshot", "需要公平账只读客户端导出的订单快照")
    order = snapshot.get("order", {})
    require(isinstance(order.get("realTotalAmount"), str), "快照缺少实付金额，不能生成正式账单")
    def convert(raw, unit):
        if unit == "yuan":
            return yuan(raw)
        require(isinstance(raw, str) and re.fullmatch(r"[0-9]+", raw) is not None, "分金额必须是整数形式字符串")
        # 先限制位数，防止超长数字触发int解析上限的未捕获ValueError。
        digits = raw.lstrip("0") or "0"
        require(len(digits) <= 10, "分金额位数超出支持范围")
        return minor(int(digits))
    total = convert(order["realTotalAmount"], total_unit)
    unresolved = ["确认参与人和垫付者；占位人员不代表真实参与人",
                  "确认商品基准与优惠包含关系；当前快照没有逐商品实付",
                  "核对套餐实际组成、优惠适用范围和费用分摊规则"]
    items = []
    for i, item in enumerate(order.get("items", [])):
        items.append({"id": f"item-{i+1}", "name": item.get("productName", f"待核对商品{i+1}"),
                      "amount_minor": None, "source": "agreed_weight", "claims": [],
                      "original_price_reference": item.get("price"),
                      "quantity_reference": item.get("quantity"), "components_reference": item.get("components", [])})
    if not items:
        unresolved.append("补充订单商品，或明确选择按总额约定权重分配")
    fees = []
    for key, label, unit in (("realDeliveryPrice", "实际配送费", delivery_unit),
                             ("realPackingFeeTotalPrice", "实际打包费", "yuan"),
                             ("tablewarePrice", "餐具费", "yuan")):
        value = convert(order[key], unit) if unit and isinstance(order.get(key), str) else None
        if value is None:
            unresolved.append("确认配送费单位，另传--delivery-unit，或手工核对费用" if key == "realDeliveryPrice" and unit is None
                              else f"确认{label}，缺失值不能默认零")
        fees.append({"id": key, "name": label, "amount_minor": value, "source": "official_order" if value is not None else "user_confirmed",
                     "eligible": ["A", "B"], "policy": "equal"})
    state = str(order.get("orderStatus", ""))
    payment_status = "paid" if state in {"2", "4", "6", "8", "10"} else "unknown"
    if payment_status != "paid":
        unresolved.append("订单不是已知已付状态，需核对支付；取消不代表退款到账")
    return {"schema_version": 1, "bill_id": bill_id, "version": 1, "currency": "CNY",
            "data_mode": "official_total_agreed_weights", "payment_status": payment_status,
            "paid_total_minor": total, "payer_id": "A", "payer_paid_minor": total,
            "participants": [{"id": "A", "name": "待确认人员A"}, {"id": "B", "name": "待确认人员B"}],
            "items": items, "discounts": [], "fees": fees, "rules_confirmed": False,
            "organizer_confirmed": False, "unresolved": unresolved,
            "total_unit_confirmed_by_operator": total_unit,
            "delivery_unit_confirmed_by_operator": delivery_unit,
            "snapshot_digest": hashlib.sha256(json_text(snapshot).encode()).hexdigest()}


def parser():
    p = argparse.ArgumentParser(description="麦麦公平账 McFairSplit 0.1.2")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("solve", help="校验、分账，导出分享账单")
    s.add_argument("input", nargs="?", help="账单JSON；演示时省略")
    s.add_argument("--demo", choices=["four_people", "bundle", "one_cent"])
    s.add_argument("--compare", choices=["equal", "proportional"])
    s.add_argument("--pool", help="只比较这一优惠；不填则比较全部优惠，含指定分配时会报错")
    s.add_argument("--json", dest="json_path")
    s.add_argument("--markdown")
    s.add_argument("--html")
    v = sub.add_parser("validate", help="校验完整账单")
    v.add_argument("input")
    v = sub.add_parser("save", help="保存账单版本，不覆盖已有记录")
    v.add_argument("input")
    v.add_argument("--db", required=True)
    v.add_argument("--expected-version", type=int, required=True)
    h = sub.add_parser("history")
    h.add_argument("bill_id")
    h.add_argument("--db", required=True)
    d = sub.add_parser("draft", help="把脱敏官方快照转换为待确认账单，不能直接结算")
    d.add_argument("snapshot")
    d.add_argument("--total-unit", choices=["yuan", "fen"], required=True,
                   help="已核实的总实付金额单位")
    d.add_argument("--delivery-unit", choices=["yuan", "fen"], help="单独核实的配送费单位；不填则保留待确认")
    d.add_argument("--bill-id", required=True)
    d.add_argument("--out", required=True)
    m = sub.add_parser("mcp", help="真实官方MCP，只读白名单；凭证只从环境读取")
    ms = m.add_subparsers(dest="mcp_command", required=True)
    ms.add_parser("probe", help="握手、列工具，并实际调用now-time-info")
    o = ms.add_parser("orders", help="仅在用户请求查找订单时使用；保存最小候选摘要")
    o.add_argument("--out", required=True)
    o = ms.add_parser("order", help="读取用户明确指定的订单；保存白名单会计字段")
    o.add_argument("--id", required=True)
    o.add_argument("--out", required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in {"solve", "validate", "save"}:
            if args.command == "solve" and args.demo:
                require(not args.input, "演示和真实输入二选一")
                bill = load(ROOT/"assets"/"examples"/(args.demo+".json"))
            else:
                require(bool(args.input), "请提供账单JSON或--demo")
                bill = load(args.input)
            result = solve(bill)
            if args.command == "validate":
                print(json_text({"valid": True, "total_minor": result["totals"]["paid_minor"]}), end="")
            elif args.command == "save":
                separate_paths([args.db], [args.input])
                result = save_version(args.db, bill, args.expected_version)
                os.chmod(args.db, 0o600)
                print(json_text({"saved": True, "bill_id": result["bill_id"], "version": result["version"]}), end="")
            else:
                input_path = ROOT/"assets"/"examples"/(args.demo+".json") if args.demo else args.input
                separate_paths([args.json_path, args.markdown, args.html], [input_path])
                comparison = compare(bill, args.compare, args.pool) if args.compare else None
                if args.json_path:
                    write(args.json_path, json_text(comparison or result))
                if args.markdown:
                    write(args.markdown, render.markdown(result, comparison))
                if args.html:
                    write(args.html, render.html(result, comparison))
                print(render.markdown(result, comparison))
        elif args.command == "history":
            print(json_text(history(args.db, args.bill_id)), end="")
        elif args.command == "draft":
            separate_paths([args.out], [args.snapshot])
            data = draft(load(args.snapshot), args.total_unit, args.bill_id, args.delivery_unit)
            write(args.out, json_text(data))
            print(json_text({"draft_created": True, "unresolved_count": len(data["unresolved"]), "settlement_ready": False}), end="")
        elif args.command == "mcp":
            client = Client()
            client.initialize()
            if args.mcp_command == "probe":
                tools = client.tools()
                current_time = client.call("now-time-info", {})
                print(json_text({"connected": True, "protocol": "2025-06-18", "tool_count": len(tools),
                                 "tool_names": [t["name"] for t in tools],
                                 "server_time": {k: current_time[k] for k in ("date", "timezone", "formatted") if k in current_time},
                                 "business_validation": "time tool only; no account order read"}), end="")
            elif args.mcp_command == "orders":
                data = safe_orders(client.call("order-list", {}))
                write(args.out, json_text(data))
                print(json_text({"saved_private_candidates": len(data)}), end="")
            else:
                data = safe_order(client.call("query-order", {"orderId": args.id}))
                write(args.out, json_text(data))
                print(json_text({"saved_accounting_snapshot": True, "unit_status": "requires_confirmation"}), end="")
        return 0
    except (BillError, MCPError) as exc:
        print(json_text({"ok": False, "error": str(exc)}), file=sys.stderr, end="")
        return 2
    except (OSError, TypeError, KeyError, AttributeError, OverflowError, sqlite3.Error):
        print(json_text({"ok": False, "error": "输入结构或文件操作异常；请核对格式与文件权限。未打印原始数据。"}), file=sys.stderr, end="")
        return 2


if __name__ == "__main__":
    sys.exit(main())
