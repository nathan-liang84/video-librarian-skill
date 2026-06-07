# Review 触发机制(2026-06)

## 状态

PR review 自动触发**当前依赖仓库外 watcher**(`方案 C`,最低风险),
不依赖 GitHub Actions workflow,也不依赖中转 API。

**历史**:
- PR #41 (`feat(ci): review-relay workflow`) 在 2026-06-05 CLOSED,没真合到仓库
- 原因:依赖"中转 API" 实际不存在 → 方案 A 不可行
- Codex 自身的 OpenAI 兼容方案需 master 提供 API key → 方案 B 等 master 决定

## 当前方案(`ci/review-relay-fix`)

**仓库内**(本 PR):
- `.github/review-queue/.gitkeep` 占位(PR #41 设计的入站路径,保留)
- 本文档

**仓库外**:
- `~/bin/pr-reviewer-watch.sh` —— watcher 脚本
- `~/Library/LaunchAgents/io.video-librarian.pr-reviewer-watch.plist` —— launchd 配置

**触发逻辑**(P1 修复:覆盖度按 head.sha 精确匹配):
1. launchd 每 30 分钟跑一次 watcher
2. watcher 调 `gh pr list --state open --json number,headRefOid` 拿**所有 open PR** 的最新 head SHA
3. 对每个 PR:拉取其**所有评论**(`comments`)和**正式 review body**(`reviews`),逐条 grep
4. 命中**精确双 token**(同一条评论里**同时**含):
   - `Role: GPT-5.5 Reviewer`(精确字串)
   - `Reviewed head: <headRefOid>`(精确全 40 字符 SHA,不是短 SHA)
   → 算覆盖,跳过
5. **不满足** → 飞书 DM 通知 master(`~/bin/feishu-dm.sh`)
6. watcher **不**调任何 LLM / 写 review / 调 Codex API

**抑制规则强约束**(P1-2 修复,以下都不算覆盖):
- ❌ 本地 state 文件 `/tmp/pr-reviewer-watch.state/<N>.last-sha` —— 不参与抑制(只防重发,不防漏)
- ❌ 泛泛 "review" / "codex" / "gpt-5.5" / "LGTM" / "looks good" 提及 —— 不算
- ❌ 短 SHA 匹配(`Reviewed head: 656da94`)—— 不算(必须全 40 字符)
- ❌ 跨条评论聚合(`Role` 在评论 A,`Reviewed head` 在评论 B)—— 不算(必须同一条)
- ❌ 上一次 review 的 head SHA 与本次 head.sha 不一致 —— 不算(每次 push 都需要重审)

**为什么不留 state 文件**:watcher/API/推送断链时长于 15 分钟 → open PR 永久无 review 漏检。
按 head.sha 精确匹配可保证:**只要 head 变 + 没有精确 review → 必通知**(每次 push 都会变 SHA)。

## master 决策后

master 看到通知后,可选:
- 手动触发 Codex review(用自己订阅的 `gpt-5.5` bot)
- 跳过(小修改/已知问题)
- 推迟(等下班后)

## 如何 dry-run

```bash
~/bin/pr-reviewer-watch.sh --dry-run    # 默认:只输出判断
~/bin/pr-reviewer-watch.sh --notify     # 触发飞书 DM
```

`--dry-run` 适合:验证 watcher 逻辑、新 PR 刚 push 后想看判断、不打扰 master。

## 如何上线

master 决定上线后:
```bash
# 1) 验证 plist 语法
plutil -lint ~/Library/LaunchAgents/io.video-librarian.pr-reviewer-watch.plist

# 2) 加载到 launchd(用户态)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.video-librarian.pr-reviewer-watch.plist

# 3) 启动一次(测试)
launchctl kickstart -k gui/$(id -u)/io.video-librarian.pr-reviewer-watch

# 4) 验证已注册
launchctl print gui/$(id -u)/io.video-librarian.pr-reviewer-watch | head -10
```

## 未来可能性(等 master 决策)

- **方案 B**:`pr-reviewer-watch.sh --auto` 调 OpenAI 兼容 API(MiniMax / DeepSeek
  之类)直接生成 review 并写 PR。需 master 提供 API key + 选定模型。
- **方案 A 复活**:补一个真中转 API + Actions workflow。`/review-queue/` 目录
  已就位,只需写 workflow yml。
- **GitHub 原生**:GitHub Copilot / OpenAI review 机器人(收费,master 决定)。

## 不做什么

- ❌ **不**自动调 LLM 写 review(章程红线:不擅自做关键决策)
- ❌ **不**自动 merge(那是 `~/bin/pr-poll.sh` 的事,已存在)
- ❌ **不**写 Codex API key 到仓库(GitHub Secret 也不行,master 没主动给)
