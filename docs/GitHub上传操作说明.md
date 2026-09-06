# GitHub 上传操作说明

## 普通用户只需下载，不需要发布

想给自己的两台 VPS 测速，只需按根 README 下载、解压，运行 `start.cmd`（Windows）或 `bash start.sh`（Linux/macOS）。不需要 GitHub 账号、Git、提交代码或创建仓库。

本文是维护者的发布流程。先在私有仓库验证，最后才公开。仓库地址是 [Borisgohard/librespeed](https://github.com/Borisgohard/librespeed)，实时可见性以匿名访问结果为准，不能用某次历史“推送成功”推断当前已公开。

2026-09-06 的独立新仓库已完成五作业 CI、隐私历史隔离、匿名下载与启动检查，并已公开。具体证据和没有覆盖的边界见 [公开发布整改与验收](公开发布整改与验收.md)；以下保留为以后发布时可以重复执行的流程。

## 旧 Git 副本迁移提醒

本项目在 2026-09-06 为隔离旧隐私更换了仓库内部历史，对外地址不变。此前克隆过旧版本的维护者，应重新克隆干净仓库，再按需迁移自己检查过的源码修改；不要把旧分支合并到新仓库，也不要使用 `--allow-unrelated-histories`、`--mirror` 或强制推送把旧历史带回来。先在本地备份自己的令牌、节点配置和报告，它们不能随代码提交。

普通用户下载 ZIP 使用不受 Git 历史切换影响。已有 VPS 不需要因此重装；在新下载的项目里复用自己的私密配置后，仍可通过原来的日常检查和测速动作连接它们。

## 先分清哪些能发，哪些不能发

可以公开的是源码、许可证、中文匿名说明、使用虚构地址的测试和质量工作流。不能公开的是密码、令牌、真实节点配置、SSH 身份记录、实测原始报告、截图中的机器信息、个人绝对路径、私密审计备份及虚拟环境。

`.gitignore` 已覆盖 `.venv/`、`venv/`、`.private/`、`*.bundle`、节点配置、令牌和报告。但忽略规则不加密文件，也不能自动清除已经被 Git 跟踪的文件。不要用 `git add -f` 绕过保护，也不要把整个本地文件夹压缩后上传。

如果历史中已经有私人地址或邮箱，只改当前 README 不够。GitHub 的旧提交链接、分支、标签、拉取请求或工作流还可能保留原内容。不能一边清理一边公开；原始审计保存在本地私密目录，新发布版本需验证全部可达历史。历史清理方案与本轮结果见 [公开发布整改与验收](公开发布整改与验收.md)。

## 个人身份设置

在 GitHub 的 Settings → Emails 启用邮箱隐私，复制 GitHub 为你提供的 noreply 邮箱。在项目终端运行下面两条命令，替换引号中的占位说明：

```powershell
git config --local user.name "你的 GitHub 用户名"
git config --local user.email "从 GitHub 设置中复制的 noreply 邮箱"
```

这只设置今后的提交，不会修改旧提交。不要把登录令牌或密码写进仓库地址；使用系统凭据管理器或 GitHub CLI 完成认证。公开用户名、项目许可证和上游作者署名应保留，不要为了隐私删除他人的版权信息。

## 日常修改与推送

在项目文件夹打开终端，先检查：

```powershell
git status --short --ignored
git diff --check
python -B -m unittest discover -s tests -v
```

测试有跳过时要看具体原因。Windows 没有 PHP 不等于 PHP 已通过，需等待 Linux CI 补验。

然后只添加你确实修改并检查过的文件。下面只演示更新 README 和一个测试文件，实际修改其他文件时逐个补充路径：

```powershell
git add -- README.md tests/test_repository_integrity.py
git diff --cached --name-only
git diff --cached --check
git diff --cached
git commit -m "用中文概括本次改动"
git push origin main
```

不要直接复制一个“全目录添加”的命令；逐个检查暂存差异是否含真实 IP、SSH 端口、个人邮箱、凭据、日志和绝对路径。工作区干净也不是隐私保证，还需要查看提交历史与远端实际内容。

如果你要发布自己的副本，先在 GitHub 创建空的 **Private** 仓库，不自动添加 README 或许可证，再把它设为该副本的远端。不要对不熟悉的旧仓库执行强制推送、镜像推送或历史覆盖。

## 公开前的验收门槛

1. 对比待发布文件清单，保留原版源码、中文文档、启动器、部署脚本、测试和许可证，排除全部私密与运行文件。
2. 扫描待发布内容和提交元数据；检查所有分支、标签、附件、Pages、工作流日志及其他可能的公开出口，确认它们不带旧隐私。
3. 从干净克隆或下载 ZIP 中启动向导，不能依赖维护者已有的配置、令牌或全局 Paramiko。检查取消、不部署和首次环境准备。
4. 等待 [质量工作流](https://github.com/Borisgohard/librespeed/actions/workflows/quality.yml) 的 Python 3.10/3.14、Shell/PHP/镜像及浏览器作业全部完成；逐项检查实际运行步骤，而不只看绿色图标。
5. 完成以上步骤后才在仓库 Settings → General → Danger Zone 修改为 Public。公开是外部可见操作，不应先公开再补隐私检查。

## 公开后还要验证

使用未登录的浏览器或不带凭据的请求，检查仓库页、README、启动文件、关键部署脚本和文档能读到。下载当前 ZIP 并核对内容摘要，确认看到的是本次版本，不是缓存旧内容。

再确认 `new/.vps_token`、`new/.vps_pair.json`、`new/.ssh_known_hosts`、真实报告、`.private/` 和虚拟环境路径无法读取。只有正常文件能读取而私密路径返回 404，才能把这个 404 当作未发布证据。若仓库本来就是私有，所有匿名路径都 404，不能证明隐私检查通过。

最后把检查日期、提交摘要、工作流结论、匿名访问结果和未覆盖边界写进操作审计。发布记录应使用人类可理解的中文概括，原始日志留在本地，不把凭据或真实机器详情粘进公开文档。

## 相关官方说明

GitHub 说明了 [清理敏感数据的限制](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository) 和 [仓库改名的重定向行为](https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository)。只做强制推送不保证旧内容从所有入口消失；已有外部克隆也无法由本项目撤回。
