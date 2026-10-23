# 麦麦公平账 · McFairSplit

**一起吃，每一分都算明白。**

把麦当劳订单整理成能核对的拼餐账单：谁吃了什么、共享小食怎么分、优惠适用谁、配送费谁承担。可以比较另一种优惠分法，生成每人的金额变化和转给垫付者的建议。

首版0.1.0，Python 3.10+，标准库，无需额外LLM密钥。独立开发，非麦当劳官方产品。所有随附示例为合成数据，价格、优惠、支付记录不代表实际交易。

## 已实现

- 单笔人民币订单，2—10人，最多30个收费行，一名垫付整单者。
- 餐品独享/按份数共享，套餐按明确约定权重拆分。
- 优惠同时限定适用人员和商品，比例、均分、明确指定三种规则。
- 多优惠按约定顺序处理剩余适用金额，封顶重分配，稳定尾分。
- 费用独立分配，整单金额校验，未知或对不齐就拒绝正式结算。
- 两种优惠规则比较，展示每人差额；比较不覆盖当前确认版本。
- JSON、Markdown和离线HTML账单，金额来源展开；导出字段白名单、HTML转义、无外部资源或脚本。
- SQLite不可变版本、expected-version冲突检查。
- 固定官方源的MCP只读客户端：连接测试、近期订单候选、指定订单会计快照、待核对草稿。

退款、多付款人、已有转账追踪、在线自主认领和支付核验未实现；非空相关输入会被拒绝，避免悄悄忽略。

## 安装

将整个本目录作为mcd-fair-split技能目录导入支持SKILL.md的客户端，或复制到该客户端的技能目录并启用。不同客户端安装入口不同。包含scripts、references和assets，不能只复制SKILL.md。

命令行也可独立使用，进入本目录：

```bash
python3 scripts/cli.py solve --demo four_people --compare equal --html exports/demo.html
python3 scripts/cli.py solve --demo bundle
python3 scripts/cli.py solve --demo one_cent
python3 scripts/test_fairsplit.py
```

打开exports/demo.html即可查看账单。无需联网，不含远程字体、资源和脚本；每人卡片可以展开查看金额来源。

在Skill宿主中可以说：“用麦麦公平账演示四人拼餐，比较按金额比例和均分优惠，再导出账单。”

## 实际订单接入

到https://open.mcd.cn/mcp申请Token，在宿主安全连接或本地进程环境设置MCD_MCP_TOKEN。不要把真实Token放到仓库、示例、命令行参数或聊天分享材料。

mcp-config.example.json仅使用环境变量占位符；只有客户端支持变量替换时才能直接使用。否则通过该客户端的安全凭证设置配置，不要公开含Token的配置。

```bash
python3 scripts/cli.py mcp probe
python3 scripts/cli.py mcp orders --out private/orders.json
python3 scripts/cli.py mcp order --id YOUR_ORDER_ID --out private/snapshot.json
python3 scripts/cli.py draft private/snapshot.json --total-unit yuan --bill-id lunch-001 --out private/bill.json
```

仅在用户要求查找订单时查询近期列表；有明确编号时只读该单。上面的yuan需要操作者已核实总额和配送字符串单位，不能照抄猜测。草稿保留未知费用、缺失逐项实付和未确认归属，不能直接结算。

按references/bill-format.md补充经用户确认的金额、归属和规则，解决unresolved并确认规则后：

```bash
python3 scripts/cli.py solve private/bill.json --compare equal --json private/result.json --markdown private/bill.md --html private/bill.html
python3 scripts/cli.py save private/bill.json --db private/ledger.db --expected-version 0
python3 scripts/cli.py history lunch-001 --db private/ledger.db
```

不要把private目录或真实账单上传到公开仓库。导出只含必要昵称和分账数据，公开前核对参与者昵称是否适合分享。结算建议不执行转账，不代表已到账。

## 为什么需要人工确认

query-order的商品价格标注原价，未明确逐项实付；套餐子项没有独立成交价。当前菜单价只能作为拆分权重建议。客户端不会编造历史成交价，用户确认的比例标为约定拆分。

优惠提供者和消费受益者分别处理；没有适用范围时不靠券名猜测。已含在行净额的优惠、实际运费中已经扣减的优惠，都不能再扣一次。引擎校验整单合计和每一分钱。

## 验证状态

- 自动测试：27项，含1000组随机封顶金额分配性质检查。
- 演示：四人账单、跨人套餐、一分钱尾分；四人88元合计精确对齐。
- 独立试用：三人46元手工确认账单，验证商品级优惠范围、共享食品排除、分法比较和未最终确认状态。
- HTML已完成内容、转义与无外部资源检查；当前环境没有可用浏览器，尚未完成浏览器视觉检查。
- 2026-10-23真实MCP：握手、工具列表（35个）、now-time-info和order-list已调用成功；未保存账户原始数据。
- 尚未完成真实已付订单的端到端归属/金额核对；目标详情与导入路径已有合成响应测试，不能把它描述为实单全链路已验证。

## 参赛准备

README.md、MCP_INTEGRATION.md、mcp-config.example.json、官方原样CONTEST_DECLARATION.md和可运行源码已包含。CONTEST_DECLARATION.md来源为官方活动仓库，独立第三方素材不因本项目获得额外授权。

公开GitHub仓库和在官方活动仓库提交报名Issue仍需单独执行；保存本技能不代表已经参赛。若申请WorkBuddy专项奖励，需真实使用WorkBuddy并导出真实workbuddy.md，本项目不伪造该文件。

官方活动：https://github.com/M-China/mcd-developer-innovation-challenge
