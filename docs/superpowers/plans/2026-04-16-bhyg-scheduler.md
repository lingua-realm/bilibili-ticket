# 半自动 B 站票务调度器 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 `BHYG`、`Bili_Ticket_Monitor`、`bilibili-vipbuy` 的安全可复用部分，构建一个本机运行、单账号、配置驱动的半自动票务调度器：支持多演出并行监控、单演出内日期优先再票价优先、锁单成功后企业微信群机器人通知，支付由用户手动完成。

**Architecture:** 以 Python CLI 程序为主入口，启动前先人工二维码登录并保存会话；运行时从 `tasks.yaml` 加载多个演出任务，为每个演出创建独立 runner。底层 B 站客户端与下单逻辑参考 `BHYG` 的会话/订单内核，库存监控参考 `Bili_Ticket_Monitor` 的轮询接口，线性下单顺序参考 `bilibili-vipbuy`。任何验证码或高风险校验仅做检测、暂停和人工接管通知，不实现自动过码。

**Tech Stack:** Python 3.12、`httpx`、`PyYAML`、`pytest`、`respx`

---

## 已确认需求

- 仅支持 `单账号` 第一阶段落地，预留后续多账号扩展点，但本计划不实现多账号并发。
- 部署在 `用户本机`，提前人工登录一次，运行时长期复用登录态。
- 以 `tasks.yaml` 为唯一配置入口，不做运行时菜单/TUI。
- 支持 `多个演出并行` 监控与抢单。
- 单个演出内部采用 `日期优先级 + 票价优先级` 的笛卡尔组合。
- 未进入优先级白名单的日期或票价一律 `不抢`。
- 只要命中任一候选且当前有票，就立即尝试下单，不等待更高优先级回票。
- 同一演出一旦锁单成功，停止该演出的其他候选任务；不同演出继续运行。
- 锁单成功后发送 `企业微信群机器人 webhook` 通知。
- 命中 `-401 / 100044 / 412` 等验证码或风控场景时，任务进入 `paused_for_human`，通知用户人工接管。
- 不实现验证码自动求解，不搬运、不接线、不扩展 `BHYG` 的自动过码逻辑。

## 非目标

- 不实现支付自动化。
- 不实现多账号账号池。
- 不实现分布式部署、代理池、IP 轮换。
- 不实现任何验证码自动化、打码平台接入、风控绕过逻辑。

## 参考仓库职责边界

- `Simplxss/bilibili-vipbuy`
  - 参考其线性下单流程与最小依赖思路。
  - 不复用其人工输入 geetest 的处理方式。
- `TaiMiao/Bili_Ticket_Monitor`
  - 参考其 `project/getV2` / `stock/check` 监控接口与状态轮询节奏。
  - 不扩展为独立进程；只吸收监控思想与状态映射。
- `ZianTT/BHYG`
  - 复用目标：二维码登录、会话持久化、`prepare/createV2` 下单链路、成功后通知模式。
  - 禁止复用：验证码自动求解与任何风控绕过逻辑。

## 文件结构

### 根目录

- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `configs/tasks.yaml.example`

### 源码

- Create: `src/bilibili_ticket/__init__.py`
- Create: `src/bilibili_ticket/errors.py`
- Create: `src/bilibili_ticket/models.py`
- Create: `src/bilibili_ticket/config.py`
- Create: `src/bilibili_ticket/app.py`
- Create: `src/bilibili_ticket/bilibili/__init__.py`
- Create: `src/bilibili_ticket/bilibili/client.py`
- Create: `src/bilibili_ticket/bilibili/login.py`
- Create: `src/bilibili_ticket/bilibili/order_service.py`
- Create: `src/bilibili_ticket/notifier/__init__.py`
- Create: `src/bilibili_ticket/notifier/wecom.py`
- Create: `src/bilibili_ticket/scheduler/__init__.py`
- Create: `src/bilibili_ticket/scheduler/priority.py`
- Create: `src/bilibili_ticket/scheduler/show_runner.py`
- Create: `src/bilibili_ticket/scheduler/manager.py`

### 测试

- Create: `tests/test_app_smoke.py`
- Create: `tests/test_config.py`
- Create: `tests/test_priority.py`
- Create: `tests/bilibili/test_login.py`
- Create: `tests/bilibili/test_order_service.py`
- Create: `tests/notifier/test_wecom.py`
- Create: `tests/scheduler/test_show_runner.py`
- Create: `tests/scheduler/test_manager.py`

### 设计约束

- `client.py` 只保留安全的 HTTP 会话、请求包装、cookie 读写和二维码登录所需能力。
- `order_service.py` 只负责项目信息、库存检查、下单准备、创建订单、错误码标准化。
- `show_runner.py` 只负责一个演出的优先级执行与状态机。
- `manager.py` 只负责多演出并行调度，不直接发请求。
- `wecom.py` 只负责 webhook 负载拼装与发送。

## Chunk 1: 项目骨架与配置

### Task 1: 搭建最小 Python 项目与测试骨架

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/bilibili_ticket/__init__.py`
- Test: `tests/test_app_smoke.py`

- [ ] **Step 1: 写一个最小失败测试，确认包尚不存在**

```python
def test_import_package():
    import bilibili_ticket

    assert bilibili_ticket.__all__ == []
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/test_app_smoke.py::test_import_package -v`
Expected: FAIL，报 `ModuleNotFoundError: No module named 'bilibili_ticket'`

- [ ] **Step 3: 创建 `src` 布局、依赖和测试配置**

```toml
[project]
name = "bilibili-ticket"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["httpx", "PyYAML"]

[project.optional-dependencies]
dev = ["pytest", "respx"]
```

- [ ] **Step 4: 再次运行测试确认通过**

Run: `pytest tests/test_app_smoke.py::test_import_package -v`
Expected: PASS

- [ ] **Step 5: 运行当前全部测试，确认基线稳定**

Run: `pytest -q`
Expected: `1 passed`

### Task 2: 定义配置模型和示例 `tasks.yaml`

**Files:**
- Create: `configs/tasks.yaml.example`
- Create: `src/bilibili_ticket/models.py`
- Create: `src/bilibili_ticket/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: 写失败测试，覆盖配置解析和白名单校验**

```python
def test_load_config_with_date_and_price_priority(tmp_path):
    config_file = tmp_path / "tasks.yaml"
    config_file.write_text(
        """
account:
  session_file: data/session.json
notifier:
  type: wecom_webhook
  webhook: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test
shows:
  - show_id: bw-2026
    project_id: 123456
    date_priority: [2026-05-01, 2026-05-02]
    price_priority: [680, 480]
    allowed_skus: [680, 480]
""",
        encoding="utf-8",
    )

    app = load_app_config(config_file)

    assert app.shows[0].date_priority == ["2026-05-01", "2026-05-02"]
    assert app.shows[0].price_priority == [680, 480]
```

- [ ] **Step 2: 运行单测确认失败**

Run: `pytest tests/test_config.py::test_load_config_with_date_and_price_priority -v`
Expected: FAIL，提示 `load_app_config` 未定义

- [ ] **Step 3: 实现配置解析与验证**

```python
@dataclass(slots=True)
class ShowTaskConfig:
    show_id: str
    project_id: int
    date_priority: list[str]
    price_priority: list[int]
    allowed_skus: list[int]
```

- [ ] **Step 4: 增加非法配置测试并补足实现**

```python
def test_reject_empty_priority_lists(tmp_path):
    ...
    with pytest.raises(ConfigError):
        load_app_config(config_file)
```

- [ ] **Step 5: 运行配置相关测试**

Run: `pytest tests/test_config.py -q`
Expected: 全绿

## Chunk 2: 安全下单内核抽离

### Task 3: 实现二维码登录与会话持久化

**Files:**
- Create: `src/bilibili_ticket/errors.py`
- Create: `src/bilibili_ticket/bilibili/client.py`
- Create: `src/bilibili_ticket/bilibili/login.py`
- Test: `tests/bilibili/test_login.py`

- [ ] **Step 1: 写失败测试，覆盖会话保存和恢复**

```python
def test_save_and_load_session(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    store.save({"SESSDATA": "abc", "bili_jct": "csrf"})

    assert store.load()["SESSDATA"] == "abc"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/bilibili/test_login.py::test_save_and_load_session -v`
Expected: FAIL，提示 `SessionStore` 未定义

- [ ] **Step 3: 实现最小会话存取与二维码登录入口**

```python
class SessionStore:
    def save(self, cookies: dict[str, str]) -> None: ...
    def load(self) -> dict[str, str]: ...
```

- [ ] **Step 4: 增加“缺失会话文件”和“空 cookie”测试**

Run: `pytest tests/bilibili/test_login.py -q`
Expected: 全绿

- [ ] **Step 5: 人工登录命令只做一次性准备，不进入热路径**

Run: `python -m bilibili_ticket.app login --session-file data/session.json`
Expected: 能启动二维码登录流程并在成功后写入会话文件

### Task 4: 抽离项目信息、库存检查、下单准备、创建订单

**Files:**
- Create: `src/bilibili_ticket/bilibili/order_service.py`
- Modify: `src/bilibili_ticket/models.py`
- Test: `tests/bilibili/test_order_service.py`

- [ ] **Step 1: 写失败测试，覆盖库存检查与下单结果标准化**

```python
def test_check_stock_returns_true_when_stock_status_is_3(order_service, respx_mock):
    respx_mock.post("https://show.bilibili.com/api/ticket/stock/check").respond(
        json={"code": 0, "data": {"stockStatus": 3}}
    )

    assert order_service.check_stock(project_id=1, screen_id=2, sku_id=3) is True
```

- [ ] **Step 2: 写失败测试，覆盖风险/验证码转人工接管**

```python
def test_prepare_order_pauses_for_human_on_risk_code(order_service, respx_mock):
    respx_mock.post("https://show.bilibili.com/api/ticket/order/prepare?project_id=1").respond(
        json={"code": -401, "data": {"ga_data": {"riskParams": {}}}}
    )

    with pytest.raises(HumanInterventionRequired):
        order_service.prepare_order(...)
```

- [ ] **Step 3: 运行两条测试确认失败**

Run: `pytest tests/bilibili/test_order_service.py -q`
Expected: FAIL，提示 `OrderService` 或 `HumanInterventionRequired` 未定义

- [ ] **Step 4: 实现最小订单服务**

```python
class OrderService:
    def fetch_project(self, project_id: int) -> dict: ...
    def check_stock(self, project_id: int, screen_id: int, sku_id: int) -> bool: ...
    def prepare_order(self, ...) -> PreparedOrder: ...
    def create_order(self, prepared: PreparedOrder) -> OrderResult: ...
```

- [ ] **Step 5: 明确风险边界，不复制任何自动过码逻辑**

```python
if resp_code in {-401, 100044, 412}:
    raise HumanInterventionRequired(code=resp_code, message=message)
```

- [ ] **Step 6: 增加成功锁单、库存不足、同演出停止信号相关测试**

Run: `pytest tests/bilibili/test_order_service.py -q`
Expected: 全绿

## Chunk 3: 优先级与调度器

### Task 5: 实现单演出候选优先级引擎

**Files:**
- Create: `src/bilibili_ticket/scheduler/priority.py`
- Test: `tests/test_priority.py`

- [ ] **Step 1: 写失败测试，验证日期优先再票价优先的笛卡尔展开**

```python
def test_expand_candidates_by_date_then_price():
    candidates = expand_candidates(
        date_priority=["2026-05-01", "2026-05-02"],
        price_priority=[680, 480],
    )

    assert candidates == [
        ("2026-05-01", 680),
        ("2026-05-01", 480),
        ("2026-05-02", 680),
        ("2026-05-02", 480),
    ]
```

- [ ] **Step 2: 写失败测试，验证白名单外日期/票价不参与抢单**

```python
def test_filter_out_non_whitelisted_candidates():
    ...
    assert ("2026-05-03", 680) not in filtered
```

- [ ] **Step 3: 运行优先级测试确认失败**

Run: `pytest tests/test_priority.py -q`
Expected: FAIL

- [ ] **Step 4: 实现优先级展开和过滤**

```python
def expand_candidates(date_priority: list[str], price_priority: list[int]) -> list[Candidate]:
    ...
```

- [ ] **Step 5: 运行优先级测试确认通过**

Run: `pytest tests/test_priority.py -q`
Expected: 全绿

### Task 6: 实现单演出 runner

**Files:**
- Create: `src/bilibili_ticket/scheduler/show_runner.py`
- Modify: `src/bilibili_ticket/models.py`
- Test: `tests/scheduler/test_show_runner.py`

- [ ] **Step 1: 写失败测试，验证同一演出锁单后停止其他候选**

```python
def test_stop_same_show_after_first_lock_success(fake_order_service):
    runner = ShowRunner(...)

    result = runner.run_once()

    assert result.locked_candidate == ("2026-05-01", 680)
    assert result.stopped_remaining_candidates is True
```

- [ ] **Step 2: 写失败测试，验证命中人工接管时 runner 进入 paused 状态**

```python
def test_pause_show_when_human_intervention_is_required(fake_order_service):
    ...
    assert runner.state.name == "PAUSED_FOR_HUMAN"
```

- [ ] **Step 3: 运行 runner 测试确认失败**

Run: `pytest tests/scheduler/test_show_runner.py -q`
Expected: FAIL

- [ ] **Step 4: 实现 `ShowRunner` 最小状态机**

```python
class ShowRunner:
    def run_once(self) -> ShowRunResult: ...
```

- [ ] **Step 5: 运行 runner 测试确认通过**

Run: `pytest tests/scheduler/test_show_runner.py -q`
Expected: 全绿

### Task 7: 实现多演出并行 manager

**Files:**
- Create: `src/bilibili_ticket/scheduler/manager.py`
- Test: `tests/scheduler/test_manager.py`

- [ ] **Step 1: 写失败测试，验证不同演出互不影响**

```python
def test_keep_other_shows_running_after_one_show_locks(fake_runner_factory):
    manager = ScheduleManager(...)

    result = manager.run_iteration()

    assert result["bw_day1"].state == "LOCKED"
    assert result["other_show"].state == "RUNNING"
```

- [ ] **Step 2: 写失败测试，验证单账号共享会话快照但不共享运行时对象**

```python
def test_create_separate_runners_from_same_session_snapshot(...):
    ...
    assert runner_a.client is not runner_b.client
```

- [ ] **Step 3: 运行 manager 测试确认失败**

Run: `pytest tests/scheduler/test_manager.py -q`
Expected: FAIL

- [ ] **Step 4: 实现 manager，使用线程或顺序循环均可，但接口必须稳定**

```python
class ScheduleManager:
    def run_forever(self) -> None: ...
```

- [ ] **Step 5: 运行 manager 测试确认通过**

Run: `pytest tests/scheduler/test_manager.py -q`
Expected: 全绿

## Chunk 4: 通知、CLI 与最终验证

### Task 8: 实现企业微信群机器人通知

**Files:**
- Create: `src/bilibili_ticket/notifier/wecom.py`
- Test: `tests/notifier/test_wecom.py`

- [ ] **Step 1: 写失败测试，验证锁单成功通知内容**

```python
def test_send_lock_success_message(respx_mock):
    ...
    assert "锁单成功" in sent_payload["markdown"]["content"]
```

- [ ] **Step 2: 写失败测试，验证人工接管通知内容**

```python
def test_send_human_takeover_message(respx_mock):
    ...
    assert "人工接管" in sent_payload["markdown"]["content"]
```

- [ ] **Step 3: 运行 notifier 测试确认失败**

Run: `pytest tests/notifier/test_wecom.py -q`
Expected: FAIL

- [ ] **Step 4: 实现企业微信群机器人 markdown webhook 发送器**

```python
class WeComNotifier:
    def send_lock_success(self, event: LockSuccessEvent) -> None: ...
    def send_human_takeover(self, event: HumanInterventionEvent) -> None: ...
```

- [ ] **Step 5: 运行 notifier 测试确认通过**

Run: `pytest tests/notifier/test_wecom.py -q`
Expected: 全绿

### Task 9: 实现 CLI 入口与 `dry-run`

**Files:**
- Create: `src/bilibili_ticket/app.py`
- Modify: `README.md`
- Test: `tests/test_app_smoke.py`

- [ ] **Step 1: 写失败测试，验证 CLI 帮助和 `dry-run`**

```python
def test_cli_help(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
```

```python
def test_cli_dry_run_uses_example_config(tmp_path, monkeypatch):
    ...
    assert exit_code == 0
```

- [ ] **Step 2: 运行 CLI 测试确认失败**

Run: `pytest tests/test_app_smoke.py -q`
Expected: FAIL

- [ ] **Step 3: 实现最小 CLI**

```python
def main(argv: list[str] | None = None) -> int:
    ...
```

- [ ] **Step 4: 在 README 中补充使用说明**

```bash
python -m bilibili_ticket.app login --session-file data/session.json
python -m bilibili_ticket.app run --config configs/tasks.yaml
python -m bilibili_ticket.app run --config configs/tasks.yaml --dry-run
```

- [ ] **Step 5: 运行 CLI 测试确认通过**

Run: `pytest tests/test_app_smoke.py -q`
Expected: 全绿

### Task 10: 运行完整验证矩阵

**Files:**
- Modify: `README.md`（如测试命令或示例需补充）

- [ ] **Step 1: 跑所有单元测试**

Run: `pytest -q`
Expected: 全绿

- [ ] **Step 2: 跑关键子集，验证模块边界**

Run: `pytest tests/bilibili/test_order_service.py tests/scheduler/test_show_runner.py tests/scheduler/test_manager.py -q`
Expected: 全绿

- [ ] **Step 3: 跑 CLI 冒烟验证**

Run: `python -m bilibili_ticket.app --help`
Expected: 输出 `login`、`run`、`--dry-run` 等帮助信息

- [ ] **Step 4: 跑示例配置 dry-run**

Run: `python -m bilibili_ticket.app run --config configs/tasks.yaml.example --dry-run`
Expected: 不发真实请求，只打印已加载的演出任务、优先级候选和通知目标

- [ ] **Step 5: 人工验收边界**

Checklist:
- 同一演出锁单后，其余候选停止
- 不同演出继续运行
- 白名单外日期/票价不会被下单
- 命中 `-401 / 100044 / 412` 时任务暂停并通知，不自动过码
- 企业微信群机器人能收到“锁单成功”和“人工接管”两类消息

## 实施顺序建议

1. 先做 `Chunk 1`，把项目骨架、依赖、配置模型稳定住。
2. 再做 `Chunk 2`，把 `BHYG` 可复用的安全内核剥离出来。
3. 再做 `Chunk 3`，实现优先级与多演出调度。
4. 最后做 `Chunk 4`，打通通知、CLI 和完整验证。

## 关键实现提醒

- 不要把 `BHYG` 的菜单式交互直接搬进新项目；热路径必须是配置驱动。
- 不要复制 `BHYG` 的验证码自动求解逻辑；风险码只允许触发暂停和通知。
- `get_token()` / `prepare_token()` 路径需要优先审查，避免无意义重复准备。
- 运行态每个演出 runner 使用独立客户端实例，但都从同一个会话快照初始化，避免共享可变运行状态。
- 测试一律先 mock HTTP，再做本地 dry-run；不要把真实票务接口作为测试前提。

## 完成定义

- `pytest -q` 全绿。
- `python -m bilibili_ticket.app run --config configs/tasks.yaml.example --dry-run` 可运行。
- README 能指导用户完成一次人工登录、配置任务、启动监控、接收企业微信通知。
- 已明确验证：本实现不包含验证码自动求解，仅支持人工接管。
