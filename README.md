# 麦麦公平账 · McFairSplit

一起点餐，按各自吃的分账。

有人只喝了一杯饮料，有人分走套餐里的汉堡，鸡块大家一起吃。一张订单里的优惠和配送费该怎么算？麦麦公平账先整理餐品归属和大家同意的规则，再算出每人应付多少，并列出计算明细。

当前版本 **0.1.1**。Python 3.10+，只用标准库。可独立运行命令行，也可作为 Skill 使用。独立开发，非麦当劳官方产品。

## 先跑一个例子

```bash
git clone https://github.com/abckkf321/-mcd-fair-split.git mcd-fair-split
cd mcd-fair-split
python3 scripts/cli.py solve --demo four_people --compare equal --html exports/demo.html
```

用浏览器打开 `exports/demo.html`，可以展开每个人的金额来源，查看优惠换一种分法后的差额。示例中的人员、价格、优惠和支付均为合成数据。

四人示例合计 88 元，当前规则下的结果为：

| 小王 | 小李 | 阿青 | 小陈 |
|---|---|---|---|
| 31.74 元 | 23.91 元 | 10.00 元 | 22.35 元 |

另外两个例子：`--demo bundle` 演示两人按 2:1 拆套餐，`--demo one_cent` 演示一分钱的尾分处理。

在支持 `SKILL.md` 的客户端中，将完整目录导入技能目录并启用；`scripts`、`references` 和 `assets` 都需要保留。启用后可以说：

> 用麦麦公平账演示四人拼餐，比较按金额比例和均分优惠，再导出账单。

## 能处理哪些账

- 一笔已付人民币订单，2—10 人，最多 30 个收费行，一人垫付整单。
- 独享餐品、按份数共享的小食，以及按约定比例拆开的套餐。
- 限定商品和人员的优惠，支持按金额比例、均分或指定金额；多项优惠依次处理，分配不会超过适用餐费。
- 配送等费用单独分摊，逐人金额与实付总额核对到分。
- 比较优惠分法，导出 JSON、Markdown 或离线 HTML，保存带版本号的本地账本。

退款、多人垫付、已有转账追踪和在线认领尚未支持。结算页提供转账建议，不执行付款或核验到账。

## 接入自己的订单

在 [麦当劳 MCP 平台](https://open.mcd.cn/mcp) 申请 Token，通过客户端的安全连接设置，或本地进程环境变量 `MCD_MCP_TOKEN` 提供。真实凭证不要写进仓库、命令行参数或分享材料。配置示例里的环境变量占位符需要客户端支持变量替换。

```bash
python3 scripts/cli.py mcp probe
python3 scripts/cli.py mcp orders --out private/orders.json
python3 scripts/cli.py mcp order --id YOUR_ORDER_ID --out private/snapshot.json
```

`probe` 只检查连接。需要找近期订单时使用 `orders`；已有订单号可直接使用 `order`。

导入前，分别核实总额和配送费字符串的单位。如果两者均已确认为元：

```bash
python3 scripts/cli.py draft private/snapshot.json --total-unit yuan --delivery-unit yuan --bill-id lunch-001 --out private/bill.json
```

确认是分的字段使用 `fen`。总额与配送费可以使用不同单位；省略 `--delivery-unit` 时配送费保持待确认。打包费和餐具费按官方字段声明的元转换。不要根据数值大小选择单位。

草稿还需要补齐餐品金额、认领和优惠范围。按 [账单格式](references/bill-format.md) 整理，经用户同意后设置 `rules_confirmed=true`、清空 `unresolved`，再运行：

```bash
python3 scripts/cli.py solve private/bill.json --json private/result.json --markdown private/bill.md --html private/bill.html
python3 scripts/cli.py save private/bill.json --db private/ledger.db --expected-version 0
python3 scripts/cli.py history lunch-001 --db private/ledger.db
```

如需比较优惠分法，在 `solve` 后加 `--compare equal` 或 `--compare proportional`。比较只生成候选结果，仍需重新确认。

`private` 目录用于存放自己的订单与账本，不要上传。导出前也请核对昵称是否适合分享。

## 金额为什么需要核对

订单接口中的商品价格是原价，没有给出可靠的逐项实付金额；套餐子项也没有独立成交价。因此，导入只生成待核对草稿。只有总额可靠时，可以约定权重拆分净餐费，结果会注明“官方总额 · 约定拆分”。

已经包含在行净额或实际配送费中的优惠不能重复扣减。优惠缺少适用范围时，需要参与者确认；谁提供了券，也不直接决定谁享受优惠。字段依据见 [MCP 集成说明](MCP_INTEGRATION.md)。

## 检查与验证

```bash
python3 scripts/test_fairsplit.py
```

测试覆盖金额封顶、商品适用范围、尾分、总额核对、版本冲突、私有字段过滤、文件导出和 MCP 响应处理，含 1000 组随机分配检查。

2026-10-23 已用真实 MCP 完成握手、工具列表、时间查询和近期订单查询。目标订单详情及导入使用合成响应测试，尚未完成真实已付订单的完整分账核对。HTML 已做内容、转义及离线资源检查，尚未完成浏览器视觉检查。

## 参赛文件

仓库已包含源码、`SKILL.md`、MCP 配置示例和官方原文 `CONTEST_DECLARATION.md`。官方声明保留原有条款，不扩展第三方素材授权。

本仓库已公开；[活动报名 Issue](https://github.com/M-China/mcd-developer-innovation-challenge) 尚未提交。申请 WorkBuddy 专项奖励还需要实际使用 WorkBuddy，并按活动要求导出记录。
