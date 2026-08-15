# 架构

## 结构

项目由通用核心和设备包组成：

```text
用户请求
  -> Planner                 生成结构化工具计划
  -> ToolRegistry            校验工具和 JSON Schema
  -> ExecutionPolicy         校验权限和设备参数
  -> DeviceAdapter           执行语义工具
  -> VISA / USB / LAN        访问设备
  -> Session / Report        保存结果和审计记录
```

设备协议不进入 Planner。Planner 只能看到当前设备包注册的语义工具。

## 核心模块

| 模块 | 职责 |
|---|---|
| `agent.py` | 计划执行、错误停止、会话结果 |
| `model_planner.py` | 模型调用、结构化输出校验、规则回退 |
| `planner.py` | 本地规则规划器 |
| `tools.py` | 工具 Schema 和统一执行入口 |
| `policy.py` | 风险等级、授权和参数边界 |
| `runtime.py` | 当前设备栈和原子切换 |
| `adaptive.py` | 示波器信号闭环分析和状态恢复 |
| `service.py` | 本地 HTTP API 和静态网页 |
| `reporting.py` | JSON、HTML 和审计输出 |
| `device_packs/` | 设备包契约、注册表和实现 |
| `src/` | VISA 连接、驱动和波形转换 |

## DevicePack

一个设备包包含：

| 字段 | 用途 |
|---|---|
| `pack_id` | 稳定的设备包标识 |
| `display_name` | 页面显示名称 |
| `device_class` | 设备类别 |
| `manufacturers`、`model_patterns` | 身份匹配范围 |
| `transports` | 支持的连接方式 |
| `tool_specs` | 工具、参数 Schema 和风险等级 |
| `adapter_factory` | 创建真实设备连接 |
| `simulator_factory` | 可选模拟器 |
| `argument_validator` | 设备特有参数约束 |
| `result_validator` | 返回数据契约 |
| `rule_planner_factory` | 本地规划器 |
| `adaptive` | 可选的设备类闭环控制器 |

`DevicePackRegistry` 是显式白名单。新增文件不会自动获得硬件访问权，必须在 `device_packs/registry.py` 注册并通过测试。

## 运行时切换

Web 页面从 `GET /device-packs` 读取设备列表，通过 `POST /device-packs/select` 选择设备。

切换顺序：

1. 查找已注册设备包；
2. 建立新 Adapter；
3. 构建工具、策略、规划器和闭环控制器；
4. 原子替换当前设备栈；
5. 关闭旧连接。

新设备构建失败时，当前设备栈保持不变。当前实现一次激活一个设备包。

## 权限

| 等级 | 规则 |
|---|---|
| `read_only` | 默认允许 |
| `reversible` | 需要 `--allow-changes` |
| `guarded` | 需要更高权限和明确用途 |
| `prohibited` | 不注册为模型工具 |

Schema 校验、权限校验和设备参数校验都在本地执行。模型输出不能绕过这些检查。

## 闭环分析

示波器设备包可启用专用信号分析控制器。控制器维护竞争假设、选择单变量实验、检查量化与采集质量，并在结束时恢复档位、时基、触发和运行状态。

任务结果分别记录：

- `execution_success`：通信和工具执行是否完成；
- `scientific_success`：证据是否支持结论；
- `restoration_status`：设备状态是否恢复。

三个状态互不替代。设备操作成功不代表测量结论可靠。

## 扩展范围

新增设备通常只需要新增设备包、Adapter、规则规划器和测试。电源、万用表、信号源等设备应提供自己的闭环控制器，不能直接复用示波器信号逻辑。
