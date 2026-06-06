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

**触发逻辑**:
1. launchd 每 30 分钟跑一次 watcher
2. watcher 调 `gh pr list --state open` 拿所有 open PR
3. 对每个 PR:查最近 15 分钟内是否有新 commit
4. 若有,检查 PR 评论/已记录 state 是否已有 Codex review
5. **若没有** → 飞书 DM 通知 master(`~/bin/feishu-dm.sh`)
6. watcher **不**调任何 LLM / 写 review / 调 Codex API

## master 决策后

master 看到通知后,可选:
- 手动触发 Codex review(用自己订阅的 `gpt-5.5` bot)
- 跳过(小修改/已知问题)
- 推迟(等下班后)

watcher 的状态文件 `/tmp/pr-reviewer-watch.state/<N>.last-sha` 记录
"已通知过哪个 SHA",同一 SHA 不重复通知。

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
