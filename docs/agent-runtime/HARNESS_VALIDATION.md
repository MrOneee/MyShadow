# DSH 升级验收（2026-09-09）

部署到现有服务器项目，服务 PID `791992`，健康检查报告 `dsh / 0.1.2rc1`，轮询及时、无 group_errors。发布备份：`/home/admin/zhouyihang/shadow/logs/dsh-backup-20260909-111320`。未更换微信账号或回滚活动数据库。

| 检查 | 实测结果 |
|---|---|
| Linux 全量回归 | 328 项通过；候选目录 `logs/dsh-candidate/tests-final.log` |
| 真 DSH + 本地确定性模型接口 | 无效提交收到校验错误后正确提交；原生 web 插件、压缩插件成功加载；模型无 Shell/编辑器工具 |
| 服务器真实模型与宿主工具 | 先调用 read_signal，再提交读取的 7391；峰值 DSH RSS 177808 KiB |
| 服务器真实原生 web_search | 检索得到官方 DeepSeek Harness GitHub 仓库；`web-live-result.json` passed=true |
| 服务器主持回放 | 开局 0%，提示后仍 0%，玩家完整解答后 100% 并结束；`game-check-result.json` passed=true |
| 在线题完整路径 | 公开检索得到《雨天乘电梯》，来源 `https://ask.chazidian.com/ask1763824/`；署名/资料与质量检查通过；取得的题再次回放，提示不加分，解答后 100% 揭晓 |
| 许二木案例 | 找到了作品候选；部分转录页面要求验证码或返回不完整材料，未取得能同时核实原作者、完整题面和汤底的材料。返回明确的 unavailable，而非“没搜到”或伪称开局；未替换作者 |
| 完成通知 | “灯 盏 堂”，reply_id=112，微信数据库确认 confirmed |

本次许二木测试不代表成功取得了许二木作品。Harness 的升级使缺少什么资料、搜索了什么、哪个页面访问失败均可追溯，并在预算内继续查找；它不能保证任意网站正文都可读取。完整成功路径另以公开材料充足的题进行了验证。

执行轨迹均保留在本机 `.remote-work/dsh-acceptance`、`.remote-work/dsh-positive-final`、`.remote-work/retrieved-game` 及服务器候选目录中；含私密汤底，不发到群里。

当前 2 GiB 容器只准入一个 DSH 运行实例。群会话、权限和队列保持独立，但不同群的 AI 执行可能等待；后续增加并发需先扩充并验证内存预算。被中断的模型任务从新尝试恢复，不声称支持无缝 token 流续接；已有结果与外部效果账本保留。
