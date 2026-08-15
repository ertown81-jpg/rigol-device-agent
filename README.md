# Device Agent

本地智能设备控制服务。通用核心负责模型规划、权限、审计和报告，具体设备通过 `DevicePack` 接入。

当前注册设备包：

| ID | 设备 | 连接 | 状态 |
|---|---|---|---|
| `rigol_ds1102ze` | RIGOL DS1102Z-E 示波器 | USBTMC/VISA | 已实机验证 |

## 功能

- 设备发现、身份核验和断线重连；
- 状态、自动测量、波形和屏幕截图读取；
- 严格工具 Schema、参数范围和权限等级；
- 可恢复的通道、档位、时基、触发和运行状态控制；
- 规则规划器，以及可选的 DeepSeek、豆包和 OpenAI 兼容模型；
- 本地 Web 控制台、会话记录、HTML 报告和审计日志；
- 显式注册的设备包列表和运行时设备切换。

模型只能调用设备包声明的语义工具，不能直接发送 SCPI、串口或其他底层协议命令。

## 安装

要求 Python 3.10 或更高版本。USB 连接建议安装 NI-VISA 或 RIGOL Ultra Sigma 提供的 VISA 驱动。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-agent.txt
Copy-Item config.example.json config.json
```

发现设备：

```powershell
python scripts\discover.py
```

将发现的 VISA 地址写入 `config.json` 的 `resource` 字段。配置文件不会被 Git 跟踪。

## 启动

```powershell
.\start_agent.ps1
```

控制台地址：<http://127.0.0.1:8765/>

页面顶部显示后端已注册设备列表、当前设备包、连接方式和能力数量。注册新设备包并重启服务后，设备会自动出现在列表中。

常用命令：

```powershell
python -m rigol_agent devices
python -m rigol_agent --device rigol_ds1102ze capabilities
python -m rigol_agent run "读取 CH1 的频率、峰峰值和有效值"
python -m rigol_agent --simulate --scenario sine run "分析 CH1 当前信号"
```

修改设备状态需要显式授权：

```powershell
python -m rigol_agent --allow-changes run "把 CH1 档位设置为 1 V/div"
```

RAW 波形读取和 `single` 等受保护操作还需要 guarded 权限。闭环分析的临时修改使用独立租约，并在结束时恢复完整状态快照。

## 模型配置

模型是可选项。未配置或调用失败时，服务使用本地规则规划器。

```powershell
.\configure_model.ps1
```

脚本将密钥保存在 Windows 当前账户的加密配置中，不写入项目目录。

## 测试

```powershell
python -m unittest discover -s tests -p test_*.py -v
```

测试覆盖设备包契约、权限、计划校验、HTTP API、设备切换、模拟信号、状态恢复和波形处理。实机验证范围见 [验证记录](docs/VALIDATION.md)。

## 目录

```text
rigol_agent/          Agent 核心、设备包、服务和网页
rigol_agent/device_packs/
                      设备包契约、注册表和具体实现
src/                  VISA 驱动与波形处理
scripts/              发现、查询、控制和采集工具
tests/                自动测试
docs/                 架构、能力、扩展和验证说明
```

## 文档

- [架构](docs/ARCHITECTURE.md)
- [能力边界](docs/CAPABILITIES.md)
- [新增设备](docs/ADDING_DEVICE.md)
- [验证记录](docs/VALIDATION.md)
