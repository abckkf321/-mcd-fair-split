# 麦麦公平账 MCP 集成说明

服务：麦当劳中国官方MCP，https://mcp.mcd.cn，Streamable HTTP，协议2025-06-18。凭证仅从进程环境或宿主安全连接提供；本项目不包含真实Token。

## 业务流程

明确订单号 → query-order读取订单会计候选 → 快照白名单脱敏 → 核实金额单位和支付状态 → 用户认领商品、确认拆分权重及优惠规则 → 本地确定性分账 → 对比、解释、导出。

用户请求查找近期订单时才增加order-list；有明确编号不额外枚举。需要当前参考套餐结构时由宿主使用query-meal-detail；当前参考价不充当历史成交价。

## 使用工具与本地价值

| 工具 | 用途 | 当前验证状态 |
|---|---|---|
| now-time-info | 连接自检中的真实只读调用 | 2026-10-23成功 |
| order-list | 用户查找近期订单的入口 | 2026-10-23真实调用成功，不保留原始账户数据 |
| query-order | 已指定订单的会计快照 | 客户端路径及白名单适配已实现；合成响应测试，未完成真实已付订单全链路 |
| query-meals / query-meal-detail / calculate-price | 可选当前参考数据 | 白名单允许，宿主可按需要调用；首版核心分账不依赖重新试算历史订单 |

initialize和tools/list不是业务查询；成功握手不证明实单分账。所有离线演示显式标synthetic_demo。

## 字段关系

query-order的realTotalAmount、realDeliveryPrice为字符串金额，需要单独确认单位；realPackingFeeTotalPrice与tablewarePrice的Schema明确元。orderProductList.price明确为原价、单位元，但未明确逐项实付或单件/整行语义。

订单子项没有独立成交金额；优惠列表没有完整的商品适用映射。因此，草稿金额和归属保持待核对，不能自动冒充逐商品实付。用户确认比例后标为agreed_weight，订单总额与内部协商拆分分别展示。

calculate-price的主体金额单位分，enjoyed.realDiscount另声明元；没有通用的全字段单位猜测规则。字段列表与限制见references/mcp-fields.md。

## 执行边界

MCP只提供真实订单和当前参考信息。本地Python引擎负责商品归属、优惠与费用拆分、封顶、最大余数、总额对账、结算建议和版本管理；这些并非麦当劳官方分账功能。

本项目没有写业务工具透传入口。create-order、cancel-order、auto-bind-coupons、mall-create-order在客户端调用前被拒绝。没有支付、催款、自动发消息或银行到账核验。

## 信息保护

订单读取后只按字段白名单保留会计候选，排除配送地址、电话、骑手、支付和取餐凭证。列表候选含私有订单号，仅保存到用户私有目录。分享导出只用经过求解的账单字段，额外原始字段不进入输出。

HTTP错误不输出原响应和请求头，不自动重试；固定官方HTTPS源、不跟随重定向。公开示例与测试仅使用合成数据。
