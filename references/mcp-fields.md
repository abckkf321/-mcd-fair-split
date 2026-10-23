# 官方MCP接入边界与映射

官方入口：https://open.mcd.cn/mcp

服务地址：https://mcp.mcd.cn

官方工具说明：https://github.com/M-China/mcd-mcp-server

结构观察日期：2026-10-23；35个工具。服务能力可能更新，以实时tools/list为准。

## 连接

脚本使用Streamable HTTP、协议2025-06-18。Authorization只从进程环境MCD_MCP_TOKEN构造；固定HTTPS官方源，不跟随重定向。凭证不支持命令行值参数。配置示例中的环境占位符需要客户端真实支持变量替换，不能把占位符字符串原样当Token。

`python3 scripts/cli.py mcp probe`：握手、列工具、实际调用now-time-info；不读取账户订单。

`python3 scripts/cli.py mcp orders --out 私有目录/orders.json`：只有用户要求查找近期订单时才调用；输出仍含私有订单编号，不公开。

`python3 scripts/cli.py mcp order --id 用户明确订单编号 --out 私有目录/snapshot.json`：只读目标订单，白名单保留会计候选。原始电话、地址、骑手、支付、取餐码和二维码不落盘。

只读白名单：query-order、order-list、now-time-info、query-meals、query-meal-detail、calculate-price。未提供任意工具透传参数，写工具在发送网络请求前即拒绝。CLI只暴露常用的只读子命令，其余工具供宿主官方连接或受控Python适配器使用。

## 订单字段

query-order输入orderId。其Schema声明：

| 字段 | 用途 | 限制 |
|---|---|---|
| realTotalAmount | 实际支付总额候选 | 字符串；单位须核对 |
| orderStatus | 状态代码 | 已知已付候选2/4/6/8/10；1待付、7取消；未知不猜 |
| orderProductList.price | 商品原价 | 明确元，但未明确单件/整行，不自动计算行实付 |
| orderProductList.quantity | 数量候选 | 商品行金额语义核对后才使用 |
| comboItemList | 历史子项名称/数量 | 包含特调文字，不全是独立收费子项 |
| couponList | 券名与优惠金额候选 | 无完整商品适用映射；不与总优惠重复相加 |
| totalDiscountAmount | 总优惠候选 | 未明确哪些优惠已包含在行或费用净价中 |
| realDeliveryPrice | 实际配送费候选 | 字符串单位需核对 |
| realPackingFeeTotalPrice | 实际打包费 | Schema明确单位元 |
| tablewarePrice | 餐具费 | Schema明确单位元 |

实际响应可缺字段、不同状态可有不同语义。order-list的状态字段不应假定与详情完全相同；不要靠列表标签推断已付再据此结算。首版draft依据详情代码决定是否需要核对。

`draft snapshot.json --total-unit yuan --bill-id lunch-001 --out bill.json` 表示操作者已核实总额与配送字符串金额为元；如果已核实为分使用fen。明确元的打包/餐具字段独立转换。金额缺失继续unknown，不能置零。

draft不生成商品成交金额，保留原价和数量参考，amount_minor=null、claims=[]、规则未确认。由用户补充/认可拆分后才允许solve。只有总额可靠时可以按约定权重分净餐费，来源标agreed_weight。

## 当前参考价

query-meals和query-meal-detail需要门店、业务方式等上下文。详情代码必须来自可靠匹配，不能按名字猜；历史套餐的默认组成不能被当前默认组成替换。不同门店、渠道、规格和预约时段不能混配。

calculate-price主体金额是整数分，但enjoyed.realDiscount、enjoyable.realDiscount的Schema另声明元。没有通用“所有字段乘100”规则。当前试算不证明历史成交，也不能在原价和行净价之间重复减优惠。

## 错误与数据保护

401提示更新连接；429提示稍后按服务Retry-After重试；网络或解析异常不循环请求。不输出原始业务错误或请求头，以免暴露账户数据。当前客户端没有自动重试。

来源与单位确认属于适配过程，不是对所有历史订单语义的完整验证。联调记录要分别写明握手、时间工具、历史列表、目标订单和完整分账实际验证到哪一步。
