"""将分账结果导出成Markdown和单文件HTML。"""
from html import escape
from pathlib import Path
import re
from string import Template

from fairsplit import money

ASSETS = Path(__file__).resolve().parent.parent / "assets"
LABELS = {"synthetic_demo": "演示账单", "user_confirmed": "用户确认账单",
          "official_total_agreed_weights": "官方总额 · 约定拆分"}
SOURCE = {"synthetic_demo": "演示", "user_confirmed": "用户确认", "official_order": "官方订单", "agreed_weight": "约定拆分"}
POLICIES = {"equal": "适用人均分", "proportional": "按适用金额比例", "specified": "指定金额"}


def md_text(value):
    text = escape(str(value)).replace("\\", "\\\\")
    return re.sub(r"([`*_{}\[\]()#+.!|~\-])", r"\\\1", text)


def discount(value):
    return ("−" if value else "") + money(value)


def pool_scope(pool, result):
    names = {p["id"]: p["name"] for p in result["people"]}
    scope = "适用人员：" + "、".join(names[p] for p in pool["eligible"])
    if pool["kind"] == "discount":
        items = {item["id"]: item["name"] for item in result["items"]}
        scope += "；适用商品：" + "、".join(f"{items[i]} [{i}]" for i in pool["eligible_items"])
    return scope


def markdown(result, comparison=None):
    names = {p["id"]: p["name"] for p in result["people"]}
    status = "组织者已确认" if result["status"] == "organizer_confirmed" else "待组织者确认"
    totals = result["totals"]
    rows = ["# 麦麦公平账 · McFairSplit", "", LABELS[result["data_mode"]],
            f"版本 {result['version']} · {status}", "",
            f"**本单实付 ¥{money(totals['paid_minor'])} · 合计一致**", "",
            "| 人员 | 餐品 | 优惠 | 费用 | 应承担 |", "|---|---:|---:|---:|---:|"]
    for p in result["people"]:
        rows.append(f"| {md_text(p['name'])} | {money(p['basis_minor'])} | {discount(p['discount_minor'])} | {money(p['fee_minor'])} | **{money(p['total_minor'])}** |")
    rows += [f"| 合计 | {money(totals['basis_minor'])} | {discount(totals['discount_minor'])} | {money(totals['fee_minor'])} | **{money(totals['paid_minor'])}** |", "", "## 餐品归属", ""]
    for item in result["items"]:
        shares = "、".join(f"{md_text(names[p])} ¥{money(v)}" for p, v in item["shares"].items())
        rows.append(f"- {md_text(item['name'])}：{shares}（{SOURCE[item['source']]}）")
    rows += ["", "## 优惠和费用", ""]
    for pool in result["pools"]:
        shares = "、".join(f"{md_text(names[p])} ¥{money(v)}" for p, v in pool["shares"].items())
        rows.append(f"- {md_text(pool['name'])}：{POLICIES[pool['policy']]}；{shares}（{SOURCE[pool['source']]}）；{md_text(pool_scope(pool, result))}")
    rows += ["", "## 转账建议", ""]
    for t in result["transfers"]:
        rows.append(f"- {md_text(names[t['from']])} → {md_text(names[t['to']])}：¥{money(t['amount_minor'])}")
    if not result["transfers"]:
        rows.append("无需组内转账。")
    if comparison:
        rows += ["", f"## 优惠改为{POLICIES[comparison['alternate_policy']]}", "", "| 人员 | 当前 | 另一种分法 | 变化 |", "|---|---:|---:|---:|"]
        alternate = {p["id"]: p for p in comparison["alternate"]["people"]}
        for p in result["people"]:
            change = comparison["delta_minor"][p["id"]]
            rows.append(f"| {md_text(p['name'])} | {money(p['total_minor'])} | {money(alternate[p['id']]['total_minor'])} | {'+' if change > 0 else ''}{money(change)} |")
    rows += ["", "按约定分摊，折扣不超过适用餐费。以上是转账建议，未核验到账。", ""]
    return "\n".join(rows)


def person_card(person, result, index):
    pid = person["id"]
    details = []
    for item in result["items"]:
        if pid in item["shares"]:
            details.append(f'<li><span>{escape(item["name"])}</span><b>¥{money(item["shares"][pid])}</b></li>')
    for pool in result["pools"]:
        if pid in pool["shares"]:
            value = pool["shares"][pid]
            amount = discount(value) if pool["kind"] == "discount" else "+" + money(value)
            details.append(f'<li><span>{escape(pool["name"])}</span><b>{amount} 元</b></li>')
    payer = '<span class="tag">垫付者</span>' if pid == result["payer_id"] else ""
    return f'''<article class="person">
      <div class="person-head">
        <div class="avatar a{index % 4}">{escape(person['name'][:1])}</div>
        <div><h3>{escape(person['name'])}</h3>{payer}</div>
        <strong class="person-total">¥{money(person['total_minor'])}</strong>
      </div>
      <details><summary>查看明细</summary><ul>{''.join(details)}</ul></details>
    </article>'''


def comparison_panel(comparison):
    if comparison is None:
        return ""
    alternate = {p["id"]: p for p in comparison["alternate"]["people"]}
    rows = []
    for person in comparison["base"]["people"]:
        change = comparison["delta_minor"][person["id"]]
        color = "red" if change > 0 else "green"
        rows.append(f'<tr><td>{escape(person["name"])}</td><td>{money(person["total_minor"])}</td>'
                    f'<td>{money(alternate[person["id"]]["total_minor"])}</td>'
                    f'<td class="{color}">{"+" if change > 0 else ""}{money(change)}</td></tr>')
    return f'''<section class="panel">
      <div class="section-heading"><div><span class="eyebrow">分法对比</span><h2>优惠换个分法</h2></div>
        <span class="pill">{POLICIES[comparison['alternate_policy']]}</span></div>
      <p class="muted">只调整选中的优惠分法，总实付不变。这份对比尚未替换当前账单。</p>
      <div class="table-scroll"><table>
        <thead><tr><th>人员</th><th>当前 ¥</th><th>另一种分法 ¥</th><th>变化 ¥</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table></div>
    </section>'''


def html(result, comparison=None):
    names = {p["id"]: p["name"] for p in result["people"]}
    totals = result["totals"]
    table_rows = []
    for p in result["people"]:
        table_rows.append(f'<tr><td>{escape(p["name"])}</td><td>{money(p["basis_minor"])}</td>'
                          f'<td class="green">{discount(p["discount_minor"])}</td>'
                          f'<td>{money(p["fee_minor"])}</td><td class="bold">{money(p["total_minor"])}</td></tr>')
    rules = []
    for pool in result["pools"]:
        amount = discount(pool["amount_minor"]) if pool["kind"] == "discount" else "+" + money(pool["amount_minor"])
        scope = escape(pool_scope(pool, result))
        rules.append(f'''<li><span class="dot"></span>
          <div><b>{escape(pool['name'])}</b><p>{POLICIES[pool['policy']]} · {SOURCE[pool['source']]}</p><p>{scope}</p></div>
          <strong>{amount} 元</strong></li>''')
    transfers = "".join(f'<div class="transfer"><span>{escape(names[t["from"]])} '
                        f'<span class="arrow">→</span> {escape(names[t["to"]])}</span>'
                        f'<b>¥{money(t["amount_minor"])}</b></div>' for t in result["transfers"])
    notice = '<p class="demo-note">这是一份演示账单，金额、优惠和支付信息均为示例。</p>' if result["data_mode"] == "synthetic_demo" else ""
    return Template((ASSETS / "bill.html").read_text(encoding="utf-8")).substitute(
        style=(ASSETS / "bill.css").read_text(encoding="utf-8"),
        data_label=escape(LABELS[result["data_mode"]]), demo_notice=notice,
        version=result["version"], status="组织者已确认" if result["status"] == "organizer_confirmed" else "待组织者确认",
        people_count=len(result["people"]), total=money(totals["paid_minor"]), basis=money(totals["basis_minor"]),
        discount=discount(totals["discount_minor"]), discount_total=money(totals["discount_minor"]), fee=money(totals["fee_minor"]),
        cards="".join(person_card(p, result, i) for i, p in enumerate(result["people"])),
        table_rows="".join(table_rows), comparison=comparison_panel(comparison),
        transfers=transfers or '<p>无需组内转账。</p>', payer=escape(names[result["payer_id"]]), rules="".join(rules))
