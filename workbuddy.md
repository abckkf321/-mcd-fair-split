# WorkBuddy 开发对话记录

导出日期：2026-10-24（北京时间）

项目：麦麦公平账 McFairSplit

公开仓库：https://github.com/abckkf321/-mcd-fair-split

基线提交：4298a4077a8135878d889761373f7d5cff5bf4c0

WorkBuddy 网页任务 ID：2113950552247066624

## 导出范围

这是实际 WorkBuddy 会话的节选，不是平台原生全量日志。以下用户消息取自页面显示文字；WorkBuddy 输出通过该条消息的“复制”功能原样导出，包含过程消息及最终答复，不包含内部思考或完整工具日志。先前的网络失败、中断和测试断言修正由最终答复原文保留。附件为该基线的21个公开源文件。未加入登录信息、真实订单或Token。

## 用户任务原文

收敛到金额边界并直接运行代码复现：draft 使用 --total-unit fen 时输入5000位数字字符串；yuan('12.34') 在 decimal.localcontext() 的 prec=2 环境里转换。两者都应保持金额上限1,000,000,000分，异常输入应给 BillError 而不是未捕获异常，合法金额不应受全局 Decimal 上下文影响。请在现有工作空间复现、最小修复、增加回归测试并运行全套测试。只做这个问题，不再枚举其他候选。请生成 unified diff 补丁和修改后的必要文件，并给中文简短总结。真实记录里保留网络失败、取消和成功操作，不编造记录。

## WorkBuddy 输出原文

按您指定的两个边界问题复现。先写复现脚本：
两个问题均已实际复现：

1. **5000 位分字符串**：`int()` 触发 Python 3.11 整数转字符串/解析上限（4300 位），抛出未捕获的 `ValueError`；CLI 直接崩溃退出码 1 并打印 Traceback，而不是受控的 `BillError`（退出码 2）。
2. **`yuan('12.34')` 在 `prec=2` 上下文**：`Decimal(value)*100` 受全局精度影响，返回 1200 而非 1234，金额被静默算错。

现在做最小修复：
加回归测试：
我测试断言写错了：99999999.99 元 = 9,999,999,990 分，本身超过 10 亿分上限，应报 BillError。修正断言：
## 总结

**实际操作记录（含失败与中断）**
- ❌ `git clone` GitHub 两次失败（TLS 中断 / 443 连接超时，2 分 15 秒）；codeload tarball 曾下载成功但该轮被您中断。
- ✅ 改用您上传的 `mcd-fair-split-source.zip`（21 个文件，与基线一致），解压至 `/workspace/mcd-fair-split`，本地 `git init` 提交为基线。
- ✅ 基线测试：`python3 scripts/test_fairsplit.py` → **41 个测试全部通过**，环境可运行。

**问题复现（均实际跑出）**
1. `draft --total-unit fen` 输入 5000 位数字字符串：Python 3.11 的 int 解析上限（4300 位）触发**未捕获 `ValueError`**；CLI 崩溃、退出码 1 并打印 Traceback（契约要求受控 `BillError` + 退出码 2）。
2. `yuan('12.34')` 在 `decimal.localcontext(prec=2)` 下返回 **1200**（应为 1234）——`Decimal(value)*100` 受全局精度影响，金额被静默算错。

**最小修复（3 个文件，+31/−2 行，补丁见 `mcd-fair-split-fix.patch`）**
- `scripts/fairsplit.py`：`yuan()` 改为手工按位换算（`int(whole + frac.ljust(2,"0"))`），不再经过 Decimal 运算，并加整数位长度上限（≤11 位），删除不再使用的 `Decimal` 导入。
- `scripts/cli.py`：`draft()` 的分单位转换在 `int()` 前加位数上限（≤10 位，恰好容纳最大合法值 1,000,000,000 分），超长报 `BillError`。
- `scripts/test_fairsplit.py`：新增 3 个回归测试——窄精度上下文下 `yuan` 精确换算、超长元/分字符串报 `BillError`、10 亿分边界值仍合法。

**验证结果**
- 修复后全套测试 **44 个全部 OK**。
- 复现脚本复核：5000 位分字符串 → `BillError`；CLI 退出码 2、无 Traceback、错误信息为“分金额位数超出支持范围”；`prec=2` 下 `yuan('12.34') == 1234`。

**限制与说明**
- 仅做了您指定的这一个问题的修复，未枚举其他候选；未访问麦当劳账户，未使用真实订单/Token；`CONTEST_DECLARATION.md` 未改动；未推送 GitHub（提交仅在本地 git）。中途一次测试断言笔误（99999999.99 元 = 9,999,999,990 分本就超上限）已修正为正确边界断言，过程如实记录。
