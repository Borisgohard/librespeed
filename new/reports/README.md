# 本地运行报告目录

这个目录用于保存 `interactive_vps_pair.py` 和 `deploy_vps_pair.py` 每次执行后生成的本地报告。

报告文件会包含真实服务器地址、测试参数、健康检查结果、测速原始数据和令牌指纹。令牌指纹不是令牌明文，但仍然属于运行环境痕迹，因此默认不提交到公开 Git 仓库。

开源仓库只保留这个说明文件。实际运行后生成的 `interactive_report_*.md`、`interactive_report_*.json`、`pair_report_*.md` 和 `pair_report_*.json` 会继续留在本地，作为你自己的审计记录。
