# Observer Ladder V1+ — 事件风险融合研究版

这是**影子研究层**，不是已证明更准的新交易模型。原版 V1 / V2 保持不变。

## 文件路径

- 仓库根目录：`observer_v1_plus.py`、`test_observer_v1_plus.py`、`README_V1_PLUS.md`
- `.github/workflows/`：`observer-v1-plus.yml`

依赖此前安装的 `event_risk_alert.py`，该模块从 BLS 官方 iCal 日历读取重大经济事件，失败时仅用明确标注且固定日期的备份。

## 行为

1. 读取最新 `observer_early_latest.json`（V1）。预测超过75分钟则报错，禁止旧数据冒充现在。
2. 核对未来120分钟 BLS CPI / PPI / Employment 预定事件。
3. 若原 V1 的60分钟窗口跨越事件，标注 `PREDICTION_CROSSES_EVENT`，将**综合置信度降级**；原方向和评分不变。
4. 若事件虽在120分钟内但不在原预测窗口，标注 `UPCOMING_WITHIN_120_MIN`。
5. 生成 `observer_v1_plus_latest.json`、去重历史 `observer_v1_plus_history.jsonl`。
6. 预测窗口跨越事件时只发送一次 Telegram 综合风险警报；状态在 `observer_v1_plus_sent.json` 中去重。

**限制：** 无实时官方结果与预期差值判断，绝不伪称预测重大事件的涨跌方向。不覆盖 FOMC、PCE 等其他机构事件；这些必须以后添加独立官方数据源。没有事件不代表上涨/下跌预测可靠；V1+ 尚未建立单独命中率。GitHub Actions 绿勾只证明执行，不能证明已触发 Telegram 时间窗口提醒。

## 手动测试

GitHub Actions -> Observer Ladder V1 Plus Event Overlay -> Run workflow。测试会运行单元测试并生成一次 V1+ 输出，正常情况下没有即将到来的重合事件则不会发送 Telegram。

## 改进研究

下一阶段应按事前时间截点保存所有候选风险状态，比较原始 V1 与事件门控 V1+ 的错过率、重大事件前假阳性、事件风险覆盖率，并加入官方结果后续对照（数据发布时间必须晚于事件）。
