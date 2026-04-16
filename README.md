# bilibili-ticket

基于 `BHYG`、`Bili_Ticket_Monitor`、`bilibili-vipbuy` 的安全可复用部分整理出的半自动 B 站票务调度器。

当前实现聚焦单账号、本机运行、配置驱动：

- 支持多个演出并行监控
- 单演出内按 `date_priority` 再按 `price_priority` 抢票
- 白名单外日期和票价不会参与下单
- 同一演出一旦锁单成功，立即停止该演出的其他候选
- 锁单成功后通过企业微信群机器人通知
- 支持提前人工二维码登录并长期复用会话

明确不做的事情：

- 不自动支付
- 不实现多账号池
- 不自动绕过验证码或风控

命中 `-401`、`100044`、`412` 等风险码时，程序只会暂停该演出任务并发送人工接管通知。

## 开发

```bash
uv run --extra dev pytest -q
```

## 使用

### 1. 准备本地配置

复制示例文件到本地私有配置：

```bash
cp configs/tasks.yaml.example configs/tasks.local.yaml
```

`configs/tasks.local.yaml` 已被 `.gitignore` 忽略，真实 webhook、真实项目 ID、联系人和会话文件路径只放这里，不要写回示例文件。

示例：

```yaml
account:
  session_file: data/session.json

notifier:
  type: wecom_webhook
  webhook: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=replace-me

shows:
  - show_id: may-day-1
    project_id: 123456
    date_priority:
      - 2026-05-01
      - 2026-05-02
    price_priority:
      - 680
      - 480
    allowed_skus:
      - 680
      - 480
    count: 1
    buyer_names:
      - 张三
    contact_name: 张三
    contact_phone: 13800000000
```

优先级规则：

- 先按 `date_priority`
- 再按 `price_priority`
- 只有同时出现在优先级白名单里的候选才会尝试下单

### 2. 登录并保存会话

运行一次二维码登录：

```bash
uv run python -m bilibili_ticket.app login --session-file data/session.json
```

CLI 会持续刷新二维码，并把最新图片写到 `data/login-qr.png`；终端也会输出最新登录链接。二维码过期后会自动换新，不需要重新启动命令。扫码成功后会把当前 cookie 保存到 `session_file`，并打印当前登录用户名。

如果希望远程通过企业微信 bot 完成登录，带上配置文件启动：

```bash
uv run python -m bilibili_ticket.app login --session-file data/session.json --config configs/tasks.local.yaml
```

这样每次二维码刷新时，bot 都会收到当前有效的点击链接和二维码图片。

### 3. 先做 dry-run

```bash
uv run python -m bilibili_ticket.app run --config configs/tasks.local.yaml --dry-run
```

`dry-run` 只验证配置加载和候选展开顺序，不会发真实下单请求。

### 4. 单轮真实测试

挑一场演出先做单轮实测：

```bash
uv run python -m bilibili_ticket.app run --config configs/tasks.local.yaml --once
```

`--once` 只跑一轮监控与尝试，适合验证配置、登录态和通知链路。  
如果当前没有登录态，`run` 会自动进入同一条二维码登录等待流程；你可以直接在本地扫 `data/login-qr.png`，或者通过企业微信 bot 收到的当前有效链接/二维码远程登录。登录成功后程序会自动继续执行，不需要重新启动。

### 5. 持续监控回流

```bash
uv run python -m bilibili_ticket.app run --config configs/tasks.local.yaml
```

不带 `--once` 时，程序会持续轮询并监控回流。若启动时缺少登录态，也会先自动等待登录成功，再进入正式监控。

## 通知

企业微信群机器人有两类通知：

- 锁单成功：包含演出 ID、日期、票价、订单号
- 人工接管：包含演出 ID、日期、票价、暂停原因；如果是需要重新登录，会额外附带最新登录链接和二维码图片

锁单成功后，你需要自己打开订单页面完成支付。

## 模块说明

- `bilibili_ticket.bilibili.login`
  - 二维码登录与会话持久化
- `bilibili_ticket.bilibili.order_service`
  - 项目信息获取、库存检查、`prepare/createV2` 下单链路
- `bilibili_ticket.scheduler.show_runner`
  - 单演出状态机与优先级执行
- `bilibili_ticket.scheduler.manager`
  - 多演出 runner 管理
- `bilibili_ticket.runtime`
  - 调度循环与事件去重通知
- `bilibili_ticket.notifier.wecom`
  - 企业微信群机器人通知
