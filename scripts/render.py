"""Network-free, escaped, share-safe bill output. All totals come from fairsplit."""
from html import escape
from fairsplit import money

LABELS = {"synthetic_demo": "合成演示数据", "user_confirmed": "用户确认账单",
          "official_total_agreed_weights": "官方总额 · 按约定拆分"}
SOURCE = {"synthetic_demo": "演示", "user_confirmed": "用户确认", "official_order": "官方订单", "agreed_weight": "约定拆分"}
POLICIES = {"equal": "适用人均分", "proportional": "适用金额比例", "specified": "明确指定"}


def md_text(value):
    return escape(str(value)).replace("|", "\\|").replace("`", "\\`").replace("[", "\\[")


def markdown(result, comparison=None):
    names = {p["id"]: p["name"] for p in result["people"]}
    rows = ["# 麦麦公平账 · McFairSplit", "", LABELS[result["data_mode"]],
            f"版本 {result['version']} · " + ("组织者已确认" if result["status"] == "organizer_confirmed" else "待组织者确认"),
            "", f"**订单实付 ¥{money(result['totals']['paid_minor'])} · 金额核对通过**", "",
            "| 人员 | 餐品 | 优惠 | 费用 | 应承担 |", "|---|---:|---:|---:|---:|"]
    for p in result["people"]:
        rows.append(f"| {md_text(p['name'])} | {money(p['basis_minor'])} | −{money(p['discount_minor'])} | {money(p['fee_minor'])} | **{money(p['total_minor'])}** |")
    t = result["totals"]
    rows += [f"| 合计 | {money(t['basis_minor'])} | −{money(t['discount_minor'])} | {money(t['fee_minor'])} | **{money(t['paid_minor'])}** |", "", "## 餐品归属", ""]
    for item in result["items"]:
        shares = "、".join(f"{md_text(names[p])} ¥{money(v)}" for p, v in item["shares"].items())
        rows.append(f"- {md_text(item['name'])}：{shares}（{SOURCE[item['source']]}）")
    rows += ["", "## 优惠与费用规则", ""]
    for pool in result["pools"]:
        shares = "、".join(f"{md_text(names[p])} ¥{money(v)}" for p, v in pool["shares"].items())
        rows.append(f"- {md_text(pool['name'])}：{POLICIES[pool['policy']]}；{shares}（{SOURCE[pool['source']]}）")
    rows += ["", "## 结算建议", ""]
    for tr in result["transfers"]:
        rows.append(f"- {md_text(names[tr['from']])} → {md_text(names[tr['to']])}：¥{money(tr['amount_minor'])}")
    if not result["transfers"]:
        rows.append("无需组内转账。")
    if comparison:
        rows += ["", f"## 另一种分法：{POLICIES[comparison['alternate_policy']]}", "", "| 人员 | 当前 | 另一种分法 | 变化 |", "|---|---:|---:|---:|"]
        alternate = {p["id"]: p for p in comparison["alternate"]["people"]}
        for p in result["people"]:
            d = comparison["delta_minor"][p["id"]]
            rows.append(f"| {md_text(p['name'])} | {money(p['total_minor'])} | {money(alternate[p['id']]['total_minor'])} | {'+' if d>0 else ''}{money(d)} |")
    rows += ["", "按约定规则计算；取整精确到分。结算建议不代表已付款或已到账。", ""]
    return "\n".join(rows)


def html(result, comparison=None):
    names = {p["id"]: p["name"] for p in result["people"]}
    e = lambda value: escape(str(value), quote=True)
    t = result["totals"]
    person_cards = []
    for i, p in enumerate(result["people"]):
        details = []
        for item in result["items"]:
            if p["id"] in item["shares"]:
                details.append(f'<li><span>{e(item["name"])}</span><b>¥{money(item["shares"][p["id"]])}</b></li>')
        for pool in result["pools"]:
            if p["id"] in pool["shares"]:
                sign = "−" if pool["kind"] == "discount" else "+"
                details.append(f'<li><span>{e(pool["name"])}</span><b>{sign}¥{money(pool["shares"][p["id"]])}</b></li>')
        payer = '<span class="tag">本单垫付者</span>' if p["id"] == result["payer_id"] else ""
        person_cards.append(f'''<article class="person"><div class="person-head"><div class="avatar a{i%4}">{e(p['name'][:1])}</div><div><h3>{e(p['name'])}</h3>{payer}</div><strong class="person-total">¥{money(p['total_minor'])}</strong></div><details><summary>查看金额来源</summary><ul>{''.join(details)}</ul></details></article>''')
    table = "".join(f'<tr><td>{e(p["name"])}</td><td>{money(p["basis_minor"])}</td><td class="green">−{money(p["discount_minor"])}</td><td>{money(p["fee_minor"])}</td><td class="bold">{money(p["total_minor"])}</td></tr>' for p in result["people"])
    rules = "".join(f'<li><span class="dot"></span><div><b>{e(pool["name"])}</b><p>{POLICIES[pool["policy"]]} · {e("、".join(names[p] for p in pool["eligible"]))} · {SOURCE[pool["source"]]}</p></div><strong>{"−" if pool["kind"]=="discount" else "+"}¥{money(pool["amount_minor"])}</strong></li>' for pool in result["pools"])
    transfers = "".join(f'<div class="transfer"><span>{e(names[tr["from"]])} <span class="arrow">→</span> {e(names[tr["to"]])}</span><b>¥{money(tr["amount_minor"])}</b></div>' for tr in result["transfers"]) or '<p>无需组内转账。</p>'
    comparison_html = ""
    if comparison:
        alt = {p["id"]: p for p in comparison["alternate"]["people"]}
        cr = ""
        for p in result["people"]:
            d = comparison["delta_minor"][p["id"]]
            cr += f'<tr><td>{e(p["name"])}</td><td>{money(p["total_minor"])}</td><td>{money(alt[p["id"]]["total_minor"])}</td><td class="{"red" if d>0 else "green"}">{"+" if d>0 else ""}{money(d)}</td></tr>'
        comparison_html = f'<section class="panel"><div class="section-heading"><div><span class="eyebrow">WHAT IF</span><h2>换一种分法，会怎样？</h2></div><span class="pill">{POLICIES[comparison["alternate_policy"]]}</span></div><p class="muted">同一笔实付，只改变已选优惠规则。以下为比较草稿，尚未替换当前账单。</p><div class="table-scroll"><table><thead><tr><th>人员</th><th>当前 ¥</th><th>另一种分法 ¥</th><th>变化 ¥</th></tr></thead><tbody>{cr}</tbody></table></div></section>'
    confirmed = "组织者已确认" if result["status"] == "organizer_confirmed" else "待组织者确认"
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"><title>麦麦公平账 · 分账明细</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f6f5f0;color:#23251f;font:15px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif}}main{{max-width:1120px;margin:auto;padding:36px 28px 60px}}.topbar{{display:flex;align-items:center;justify-content:space-between;margin-bottom:38px}}.brand{{font-weight:850;font-size:19px;letter-spacing:-.4px}}.brand small{{font-size:11px;color:#7a7e70;margin-left:10px;letter-spacing:1.5px}}.logo{{display:inline-grid;place-items:center;background:#f6c945;border-radius:13px;width:38px;height:38px;margin-right:10px;font-size:23px;vertical-align:middle}}.pill,.tag{{display:inline-block;border:1px solid #dfe1d6;border-radius:30px;padding:5px 12px;font-size:12px;white-space:nowrap}}.hero{{display:grid;grid-template-columns:1.3fr 1fr;gap:30px;align-items:end;margin-bottom:30px}}.eyebrow{{font-size:11px;font-weight:750;color:#777d6b;letter-spacing:2px}}h1{{font-size:43px;line-height:1.25;letter-spacing:-2px;margin:13px 0 14px}}h1 em{{font-style:normal;box-shadow:inset 0 -12px #f6d86b}}p{{margin:6px 0}}.muted{{color:#777d70;font-size:13px}}.receipt{{background:#fff;border:1px solid #e3e4d9;border-radius:20px;padding:24px 28px}}.receipt-line{{display:flex;justify-content:space-between;font-size:13px;color:#777d70}}.grand{{font-size:47px;font-weight:850;font-variant-numeric:tabular-nums;letter-spacing:-1.5px;margin:5px 0}}.verified{{border-top:1px dashed #d8dccd;padding-top:13px;font-size:12px;color:#3a7655}}.persons{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:28px 0}}.person{{background:white;border:1px solid #e3e4d9;border-radius:17px;padding:17px}}.person-head{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}}.avatar{{width:36px;height:36px;display:grid;place-items:center;border-radius:12px;font-weight:800}}.a0{{background:#fff0b9}}.a1{{background:#e3efd9}}.a2{{background:#e4e8f4}}.a3{{background:#f8e1d7}}h3{{font-size:15px;margin:0}}.tag{{font-size:10px;padding:1px 6px;border:0;color:#7d723e;background:#fcf5dd}}.person-total{{font-size:25px;letter-spacing:-.7px;display:block;width:100%;margin-top:8px;font-variant-numeric:tabular-nums}}details{{border-top:1px solid #eeefe8;margin-top:12px;padding-top:10px;font-size:12px}}summary{{cursor:pointer;color:#777d70}}ul{{list-style:none;margin:0;padding:0}}details li{{display:flex;justify-content:space-between;gap:10px;margin-top:8px}}.layout{{display:grid;grid-template-columns:1.7fr 1fr;gap:18px;align-items:start}}.panel{{background:#fff;border:1px solid #e3e4d9;border-radius:20px;padding:24px;margin-bottom:18px}}h2{{font-size:19px;letter-spacing:-.5px;margin:5px 0 15px}}.section-heading{{display:flex;justify-content:space-between;gap:12px;align-items:center}}table{{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}}th{{font-size:11px;color:#7a7f70;font-weight:500;background:#f7f8f2}}th,td{{padding:13px 9px;text-align:right;border-bottom:1px solid #edf0e5}}th:first-child,td:first-child{{text-align:left}}tfoot td{{font-weight:800;background:#fcfaf1}}.green{{color:#357653}}.red{{color:#b65b46}}.bold{{font-weight:800}}.rules li{{display:flex;align-items:start;gap:9px;padding:13px 0;border-bottom:1px solid #eef0e7;font-size:12px}}.rules li:last-child{{border:0}}.rules p{{font-size:11px;color:#7a7f70}}.rules strong{{margin-left:auto;white-space:nowrap}}.dot{{width:6px;height:6px;border-radius:50%;background:#d8b341;margin-top:8px;flex-shrink:0}}.transfer{{display:flex;justify-content:space-between;padding:12px 0;border-bottom:1px solid #e4e6dc;font-size:13px}}.arrow{{color:#a4a996;margin:0 6px}}.settlement{{background:#edf2e5;border-color:#dee6d2}}.note{{font-size:11px;color:#838975;margin-top:15px}}footer{{display:flex;justify-content:space-between;gap:20px;color:#8c9183;font-size:11px;margin-top:32px;border-top:1px solid #e1e4d7;padding-top:18px}}.table-scroll{{overflow-x:auto}}@media(max-width:760px){{main{{padding:22px 16px}}.hero,.layout{{grid-template-columns:1fr}}h1{{font-size:34px}}.persons{{grid-template-columns:repeat(2,1fr)}}.brand small{{display:none}}.panel{{padding:18px}}footer{{display:block}}.grand{{font-size:43px}}}}@media print{{body{{background:white}}main{{padding:0}}.person details{{display:block}}details ul{{display:block}}.panel,.person,.receipt{{break-inside:avoid}}}}
</style></head><body><main><header class="topbar"><div class="brand"><span class="logo">≋</span>麦麦公平账 <small>McFairSplit</small></div><span class="pill">{e(LABELS[result['data_mode']])}</span></header><div class="hero"><div><div class="eyebrow">A LITTLE MEAL. A CLEAR BILL.</div><h1>一起吃，<br>每一分都<em>算明白。</em></h1><p class="muted">餐品归谁，优惠怎么分，金额都有据可查。</p></div><div class="receipt"><div class="receipt-line"><span>本单实付</span><span>{len(result['people'])} 人 · v{result['version']}</span></div><div class="grand">¥{money(t['paid_minor'])}</div><div class="verified">✓ 商品 {money(t['basis_minor'])} − 优惠 {money(t['discount_minor'])} + 费用 {money(t['fee_minor'])} = 实付</div></div></div><div class="persons">{''.join(person_cards)}</div><div class="layout"><div><section class="panel"><div class="section-heading"><div><span class="eyebrow">THE BREAKDOWN</span><h2>这笔钱，怎么分？</h2></div><span class="pill">{confirmed}</span></div><div class="table-scroll"><table><thead><tr><th>人员</th><th>餐品 ¥</th><th>优惠 ¥</th><th>费用 ¥</th><th>应承担 ¥</th></tr></thead><tbody>{table}</tbody><tfoot><tr><td>合计</td><td>{money(t['basis_minor'])}</td><td class="green">−{money(t['discount_minor'])}</td><td>{money(t['fee_minor'])}</td><td>{money(t['paid_minor'])}</td></tr></tfoot></table></div><p class="note">金额按约定规则分摊，尾分采用稳定的最大余数法。套餐拆分金额不等于官方逐件成交价。</p></section>{comparison_html}</div><aside><section class="panel settlement"><span class="eyebrow">SETTLE UP</span><h2>结算建议</h2>{transfers}<p class="note">{e(names[result['payer_id']])} 已垫付整单 ¥{money(t['paid_minor'])}。此处为转账建议，不代表已转账或到账。</p></section><section class="panel"><span class="eyebrow">OUR AGREEMENT</span><h2>这次采用的规则</h2><ul class="rules">{rules}</ul></section></aside></div><footer><span>McFairSplit · 麦麦公平账 · 独立开发，非麦当劳官方产品</span><span>v{result['version']} · {e(LABELS[result['data_mode']])} · {confirmed}</span></footer></main></body></html>'''
