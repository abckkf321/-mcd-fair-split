#!/usr/bin/env python3
"""按商品归属分账。金额用整数分，比例计算用Fraction。"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
from fractions import Fraction
from pathlib import Path


class BillError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise BillError(message)


def minor(value, label="金额"):
    require(type(value) is int and 0 <= value <= 1_000_000_000,
            f"{label}必须是非负整数分，不能用浮点数、布尔值或未知值")
    return value


def yuan(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", value) is not None,
            "元金额必须是最多两位小数的非负十进制字符串")
    whole, _, frac = value.partition(".")
    # 手工按位换算成整数分，不经过Decimal运算，避免受全局decimal上下文精度影响。
    require(len(whole) <= 11, "元金额位数超出支持范围")
    return minor(int(whole + frac.ljust(2, "0")))


def money(value):
    return ("−" if value < 0 else "") + f"{abs(value)//100}.{abs(value)%100:02d}"


def identifier(value, label="标识"):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is not None,
            f"{label}只能包含1—64位字母、数字、下划线或连字符")
    return value


def name(value):
    require(isinstance(value, str) and 0 < len(value.strip()) <= 120,
            "名称必须是1—120个字符")
    require(not any(ord(c) < 32 for c in value), "名称不能包含控制字符")
    return value


def allocate(amount, weights, seed, caps=None):
    """先处理抵扣上限，再用最大余数法分配剩下的分钱。"""
    minor(amount)
    require(isinstance(weights, dict) and bool(weights), "金额池必须有参与人")
    require(all(type(w) is int and w >= 0 for w in weights.values()), "权重必须是非负整数")
    result = dict.fromkeys(weights, 0)
    limits = {p: amount if caps is None else minor(caps.get(p), "抵扣上限") for p in weights}
    active = {p: w for p, w in weights.items() if w > 0 and limits[p] > 0}
    remaining = amount
    require(sum(limits[p] for p in active) >= amount, "金额池超过参与人的可分配上限或没有正权重")
    while remaining:
        require(bool(active), "没有可分配的参与人")
        total_weight = sum(active.values())
        exact = {p: Fraction(remaining*w, total_weight) for p, w in active.items()}
        saturated = [p for p in active if exact[p] > limits[p]]
        if saturated:
            for p in saturated:
                result[p] = limits[p]
                remaining -= limits[p]
                del active[p]
            continue
        floors = {p: q.numerator // q.denominator for p, q in exact.items()}
        left = remaining - sum(floors.values())
        order = sorted(active, key=lambda p: (
            -(exact[p] - floors[p]),
            hashlib.sha256(f"{seed}\0{p}".encode()).hexdigest()))
        for p in order[:left]:
            floors[p] += 1
        for p, value in floors.items():
            require(value <= limits[p], "尾分超出分配上限")
            result[p] = value
        remaining = 0
    require(sum(result.values()) == amount, "金额池未守恒")
    return result


SOURCES = {"synthetic_demo", "user_confirmed", "official_order", "agreed_weight"}
MODES = {"synthetic_demo", "user_confirmed", "official_total_agreed_weights"}


def solve(bill):
    require(isinstance(bill, dict), "账单必须是JSON对象")
    require(type(bill.get("schema_version")) is int and bill["schema_version"] == 1, "仅支持schema_version=1")
    require(not any(bill.get(k) for k in ("refunds", "adjustments", "payments", "transfers")),
            "此版本不支持退款、调整或多人付款记录；不能忽略这些数据后结算")
    bid = identifier(bill.get("bill_id"), "账单ID")
    require(type(bill.get("version")) is int and bill["version"] >= 1, "版本必须是正整数")
    require(bill.get("currency") == "CNY", "首版只支持人民币CNY")
    require(bill.get("data_mode") in MODES, "必须明确数据来源模式")
    require(bill.get("unresolved") == [], "账单仍有待核对项；请先解决unresolved")
    require(bill.get("payment_status") == "paid", "正式分账只支持已经确认支付的订单")
    require(bill.get("rules_confirmed") is True, "请先与用户确认归属和分配规则")
    require(type(bill.get("organizer_confirmed", False)) is bool, "组织者确认必须是布尔值")
    participants = bill.get("participants")
    require(isinstance(participants, list) and 2 <= len(participants) <= 10, "首版支持2—10人")
    people = {}
    for person in participants:
        require(isinstance(person, dict), "人员必须是对象")
        pid = identifier(person.get("id"), "人员ID")
        require(pid not in people, "人员ID重复")
        people[pid] = name(person.get("name"))
    paid = minor(bill.get("paid_total_minor"), "实付总额")
    payer = bill.get("payer_id")
    require(payer in people, "请选择一名有效垫付者")
    require(bill.get("payer_paid_minor") == paid and type(bill.get("payer_paid_minor")) is int,
            "首版要求一名垫付者已支付整笔订单，金额须与实付相同")
    item_list = bill.get("items")
    require(isinstance(item_list, list) and 1 <= len(item_list) <= 30, "首版支持1—30个收费商品行")
    discounts, fees = bill.get("discounts"), bill.get("fees")
    require(isinstance(discounts, list) and len(discounts) <= 30, "优惠列表无效或超过30项")
    require(isinstance(fees, list) and len(fees) <= 30, "费用列表无效或超过30项")
    basis = dict.fromkeys(people, 0)
    item_records = []
    ids = set()
    for item in item_list:
        require(isinstance(item, dict), "商品必须是对象")
        iid = identifier(item.get("id"), "商品ID")
        require(iid not in ids, "商品ID重复，不能重复认领同一收费实例")
        ids.add(iid)
        amount = minor(item.get("amount_minor"), f"商品{iid}金额")
        require(item.get("source") in SOURCES, f"商品{iid}缺少已确认来源")
        if bill["data_mode"] != "synthetic_demo":
            require(item["source"] != "synthetic_demo", "真实账单不能混入演示商品")
        claims = item.get("claims")
        require(isinstance(claims, list) and bool(claims), f"商品{iid}未认领")
        weights = {}
        for claim in claims:
            require(isinstance(claim, dict), "认领必须是对象")
            pid, weight = claim.get("participant_id"), claim.get("weight")
            require(pid in people and pid not in weights, f"商品{iid}有无效或重复认领人")
            require(type(weight) is int and weight > 0, "认领权重必须是正整数")
            weights[pid] = weight
        if "portions" in item:
            require(type(item["portions"]) is int and item["portions"] > 0 and sum(weights.values()) == item["portions"],
                    "按实际份数共享时，认领份数合计须等于portions")
        shares = allocate(amount, weights, f"{bid}:item:{iid}")
        for pid, value in shares.items():
            basis[pid] += value
        item_records.append({"id": iid, "name": name(item.get("name")), "amount_minor": amount,
                             "source": item["source"], "shares": shares})
    gross = sum(basis.values())
    discount_total = sum(minor(p.get("amount_minor"), "优惠金额") for p in discounts if isinstance(p, dict))
    fee_total = sum(minor(p.get("amount_minor"), "费用金额") for p in fees if isinstance(p, dict))
    require(len([p for p in discounts + fees if isinstance(p, dict)]) == len(discounts + fees), "金额池必须是对象")
    require(gross - discount_total + fee_total == paid,
            f"金额无法对账：商品{money(gross)} − 优惠{money(discount_total)} + 费用{money(fee_total)} ≠ 实付{money(paid)}；请核对重复优惠和净费用")
    remaining = dict(basis)
    item_remaining = {r["id"]: dict(r["shares"]) for r in item_records}
    discount_shares, fee_shares = dict.fromkeys(people, 0), dict.fromkeys(people, 0)
    pools = []
    for kind, rows in (("discount", discounts), ("fee", fees)):
        seen = set()
        for pool in rows:
            poolid = identifier(pool.get("id"), "金额池ID")
            require(poolid not in seen, "同类金额池ID重复")
            seen.add(poolid)
            require(pool.get("source") in SOURCES, f"金额池{poolid}缺少已确认来源")
            if bill["data_mode"] != "synthetic_demo":
                require(pool["source"] != "synthetic_demo", "真实账单不能混入演示金额池")
            eligible = pool.get("eligible")
            require(isinstance(eligible, list) and bool(eligible), f"金额池{poolid}缺少适用人")
            require(all(p in people for p in eligible) and len(set(eligible)) == len(eligible), "适用人无效或重复")
            amount = minor(pool.get("amount_minor"))
            policy = pool.get("policy")
            require(kind == "discount" or "eligible_items" not in pool,
                    "eligible_items仅用于优惠；费用请明确适用人员或指定各人金额")
            eligible_items = pool.get("eligible_items", list(item_remaining))
            require(isinstance(eligible_items, list) and bool(eligible_items)
                    and all(i in item_remaining for i in eligible_items)
                    and len(set(eligible_items)) == len(eligible_items), "适用商品无效或重复")
            discount_basis = {p: sum(item_remaining[i].get(p, 0) for i in eligible_items) for p in eligible}
            caps = discount_basis if kind == "discount" else None
            if policy == "specified":
                shares = pool.get("allocations")
                require(isinstance(shares, dict) and set(shares) == set(eligible), "指定分配必须覆盖全部适用人且不含其他人")
                shares = {p: minor(v, "指定分配") for p, v in shares.items()}
                require(sum(shares.values()) == amount, "指定分配与金额池总额不符")
                require(caps is None or all(shares[p] <= caps[p] for p in shares), "指定优惠超过可抵扣金额")
            else:
                require(policy in {"proportional", "equal"}, "规则必须是proportional、equal或specified")
                weights = {p: 1 if policy == "equal" else (discount_basis[p] if kind == "discount" else basis[p]) for p in eligible}
                shares = allocate(amount, weights, f"{bid}:{kind}:{poolid}", caps)
            for pid, value in shares.items():
                if kind == "discount":
                    discount_shares[pid] += value
                    remaining[pid] -= value
                    available = {i: item_remaining[i].get(pid, 0) for i in eligible_items}
                    consumed = allocate(value, available, f"{bid}:consume:{poolid}:{pid}", available)
                    for iid, used in consumed.items():
                        if used:
                            item_remaining[iid][pid] -= used
                else:
                    fee_shares[pid] += value
            pools.append({"id": poolid, "kind": kind, "name": name(pool.get("name")), "policy": policy,
                          "source": pool["source"], "eligible": list(eligible), "eligible_items": eligible_items,
                          "amount_minor": amount, "shares": shares})
    rows = [{"id": p, "name": people[p], "basis_minor": basis[p], "discount_minor": discount_shares[p],
             "fee_minor": fee_shares[p], "total_minor": remaining[p]+fee_shares[p],
             "prepaid_minor": paid if p == payer else 0} for p in people]
    require(sum(r["total_minor"] for r in rows) == paid, "最终金额未守恒")
    transfers = [{"from": p, "to": payer, "amount_minor": remaining[p]+fee_shares[p]}
                 for p in people if p != payer and remaining[p]+fee_shares[p] > 0]
    return {"schema_version": 1, "bill_id": bid, "version": bill["version"], "currency": "CNY",
            "data_mode": bill["data_mode"], "status": "organizer_confirmed" if bill.get("organizer_confirmed") else "ready",
            "totals": {"basis_minor": gross, "discount_minor": discount_total, "fee_minor": fee_total, "paid_minor": paid},
            "payer_id": payer, "people": rows, "items": item_records, "pools": pools,
            "transfers": transfers, "reconciled": True}


def compare(bill, policy="equal", pool_id=None):
    require(policy in {"equal", "proportional"}, "对比只支持equal或proportional")
    original = solve(bill)
    changed = copy.deepcopy(bill)
    changed["organizer_confirmed"] = False
    found = False
    for pool in changed["discounts"]:
        if pool_id is None or pool["id"] == pool_id:
            require(pool["policy"] != "specified", "指定优惠不应被自动覆盖；请先明确更改这项约定")
            pool["policy"] = policy
            found = True
    require(found, "没有找到可比较的优惠池")
    alternate = solve(changed)
    old = {p["id"]: p["total_minor"] for p in original["people"]}
    delta = {p["id"]: p["total_minor"] - old[p["id"]] for p in alternate["people"]}
    require(sum(delta.values()) == 0, "规则对比差额未守恒")
    return {"base": original, "alternate": alternate, "alternate_policy": policy, "delta_minor": delta}


def storage_bill(bill):
    """只保存计算所需字段，不把附带的原始订单或凭证写入账本。"""
    fields = ("schema_version", "bill_id", "version", "currency", "data_mode", "payment_status",
              "paid_total_minor", "payer_id", "payer_paid_minor", "rules_confirmed",
              "organizer_confirmed", "unresolved")
    pick = lambda value, keys: {k: copy.deepcopy(value[k]) for k in keys if k in value}
    saved = pick(bill, fields)
    digest = bill.get("snapshot_digest")
    if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
        saved["snapshot_digest"] = digest
    for key in ("total_unit_confirmed_by_operator", "delivery_unit_confirmed_by_operator"):
        if isinstance(bill.get(key), str) and bill[key] in {"yuan", "fen"}:
            saved[key] = bill[key]
    saved["participants"] = [pick(p, ("id", "name")) for p in bill["participants"]]
    saved["items"] = []
    for item in bill["items"]:
        row = pick(item, ("id", "name", "amount_minor", "source", "portions"))
        row["claims"] = [pick(c, ("participant_id", "weight")) for c in item["claims"]]
        saved["items"].append(row)
    pool_fields = ("id", "name", "amount_minor", "source", "eligible", "policy", "eligible_items")
    for kind in ("discounts", "fees"):
        saved[kind] = []
        for pool in bill[kind]:
            row = pick(pool, pool_fields)
            if pool["policy"] == "specified":
                row["allocations"] = copy.deepcopy(pool["allocations"])
            saved[kind].append(row)
    return saved


def save_version(db_path, bill, expected_version):
    result = solve(bill)
    require(type(expected_version) is int and expected_version >= 0, "expected_version必须是非负整数")
    require(bill["version"] == expected_version+1, "新版本必须等于expected_version+1")
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(fd)
    os.chmod(path, 0o600)
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE IF NOT EXISTS versions (bill_id TEXT, version INTEGER, bill_json TEXT, result_json TEXT, PRIMARY KEY(bill_id, version))")
        con.execute("BEGIN IMMEDIATE")
        current = con.execute("SELECT COALESCE(MAX(version),0) FROM versions WHERE bill_id=?", (bill["bill_id"],)).fetchone()[0]
        require(current == expected_version, "账单版本冲突，请先读取最新版本再修改")
        con.execute("INSERT INTO versions VALUES (?,?,?,?)", (bill["bill_id"], bill["version"], json.dumps(storage_bill(bill), ensure_ascii=False), json.dumps(result, ensure_ascii=False)))
    return result


def history(db_path, bill_id):
    identifier(bill_id)
    require(Path(db_path).is_file(), "账本不存在")
    with sqlite3.connect(db_path) as con:
        rows = con.execute("SELECT version,result_json FROM versions WHERE bill_id=? ORDER BY version", (bill_id,)).fetchall()
    return [{"version": v, "result": json.loads(s)} for v, s in rows]
