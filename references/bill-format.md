# 内部账单格式与计算规则

本格式是 McFairSplit 内部输入，不是麦当劳官方 API。完整可运行示例见 assets/examples/four_people.json。脚本运行位置为技能根目录。

## 顶层必需字段

| 字段 | 类型 | 含义 |
|---|---|---|
| schema_version | 整数1 | 当前仅支持1 |
| bill_id | 稳定ASCII标识 | 1—64字符，字母数字下划线连字符；不要使用原始订单凭证 |
| version | 正整数 | 新账单1；保存下一版本须匹配CAS |
| currency | CNY | 只支持人民币 |
| data_mode | 枚举 | synthetic_demo / user_confirmed / official_total_agreed_weights |
| payment_status | paid | 待付或未知状态不能正式结算 |
| paid_total_minor | 非负整数分 | 实际支付总额 |
| payer_id / payer_paid_minor | 人员ID / 整数分 | 一名有效垫付者，已垫付整单 |
| participants | 对象数组 | 2—10个{id,name}，ID不重复，昵称最多120字符 |
| items | 对象数组 | 1—30个实际收费行 |
| discounts / fees | 对象数组 | 各最多30个金额池；没有时显式为空数组 |
| rules_confirmed | true | 用户已认可归属、优惠范围及分配顺序等约定 |
| organizer_confirmed | 布尔，可省略 | 是否确认当前版本；默认false |
| unresolved | 空数组 | 有待确认项时放字符串数组，引擎拒绝正式结算 |

支持的金额来源 source：synthetic_demo、user_confirmed、official_order、agreed_weight。正式账单不能混用演示来源。source 是来源说明，不是数字签名，不代表引擎独立验证官方真实性。

## 商品行

每行包含 id、name、amount_minor、source、claims。amount_minor 表示该行总金额；用户写“2份”不意味着脚本还要乘2。让两个人分别认领两份相同商品时拆成不同 id。

claims 为 [{participant_id,weight}]。weight 必须是正整数；1:2就是1和2。每人每行最多一个认领。可选 portions 用于实际份数分配，提供时必须等于权重之和；没提供时视为比例。

一个套餐整行3000分，汉堡和饮料分给两人并约定2:1，可保留一个套餐行，以weight 2和1分配。不要同时计入套餐父行和子行。若有确定升级金额，明确拆成单独收费行，保证所有行合计对账。不要用未计入金额的隐含“赠品价格”补账。

例：

```json
{"id":"bundle-1","name":"套餐：按约定2比1分配","amount_minor":3000,"source":"agreed_weight","claims":[{"participant_id":"A","weight":2},{"participant_id":"B","weight":1}]}
```

## 金额池

每个池包含 id、name、amount_minor、source、eligible、policy。仅优惠可选 eligible_items，列出可用优惠的商品行ID；费用设置该字段会报错。省略表示全部收费行在适用人员中的份额。eligible 为明确参与的人员ID数组，不从名称自动推断。

- proportional：优惠按剩余适用商品金额分；费用按参与人的优惠前餐费比例分。
- equal：适用人员等额分；优惠仍受每人的适用商品余额上限限制，超出份额会重新分配。
- specified：必须提供 allocations，对每名 eligible 人员指定非负整数分（包括0），合计必须等于池额。优惠仍不能超过适用余额。

优惠按 discounts 数组顺序逐个作用，脚本保留商品上的剩余可抵扣金额。先前分配会影响后续比例和上限。用户需明确同意该顺序，此顺序不是模型推定的官方券叠加顺序。

例：

```json
{"id":"coupon-1","name":"仅汉堡适用的优惠","amount_minor":500,"source":"user_confirmed","eligible":["A","B"],"eligible_items":["burger-1","burger-2"],"policy":"proportional"}
```

对账式：sum(items.amount_minor) − sum(discounts.amount_minor) + sum(fees.amount_minor) = paid_total_minor。

使用行净额时已包含的优惠不能再列到 discounts；使用实际运费时已包含的运费减免不能再次扣除。无法确认包含关系时停留草稿。手工总额模式也需用户明确认可权重：可以把净餐费作为一个 agreed_weight 行，真实附加费用单列，discounts 留空；解释中不能称其为官方逐餐品成交明细。

## 结果与比较

solve 输出 people 的 basis_minor、discount_minor、fee_minor、total_minor、prepaid_minor；items 和 pools 含逐人的 shares。所有输出金额均为整数分。转账建议仅供核对，不执行付款或验证到账。

compare 返回 base、alternate、delta_minor。alternate 总额不变，组织者确认状态清空；不自动替换原账单。多项指定分配不能被一键规则比较覆盖；通过 --pool 只比较用户指定的非指定优惠。

## 版本账本

`save bill.json --db 私有目录/ledger.db --expected-version 0` 保存version=1。修改已确认数据时复制账单，version=2，再以expected-version=1保存。保存不会覆盖旧版本；并发冲突返回错误。账本仅保留计算字段及有效的单位确认记录、快照摘要，输入中的其他附加字段不保存。`history bill_id --db ...` 返回私有明细，不公开运行输出。

首版不支持退款、多人垫付、已有组内转账和其他调整；这些非空字段会被拒绝。不要删掉这些输入后宣称账单已完整处理。
