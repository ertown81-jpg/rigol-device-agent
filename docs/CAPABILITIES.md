# 能力边界

## 当前设备包

`rigol_ds1102ze` 适配 RIGOL DS1102Z-E，已验证 USBTMC/NI-VISA。LAN/VISA 保留为未验证连接方式。

## 模型可用工具

| 工具 | 功能 | 风险 | 主要限制 |
|---|---|---|---|
| `get_device_status` | 身份、通道、时基、采集和触发状态 | `read_only` | 无参数 |
| `measure` | 自动测量 | `read_only` | CH1/CH2；限定测量项 |
| `capture_waveform` | 波形 CSV 和元数据 | `read_only` | NORMAL/RAW；RAW 需要 guarded 权限 |
| `capture_screen` | 屏幕 PNG | `read_only` | 无参数 |
| `set_channel_enabled` | 通道开关 | `reversible` | CH1/CH2 |
| `set_channel_scale` | 垂直档位 | `reversible` | 1 mV/div 至 10 V/div |
| `set_timebase_scale` | 主时基 | `reversible` | 5 ns/div 至 50 s/div |
| `set_trigger_level` | 边沿触发电平 | `reversible` | -100 V 至 100 V |
| `run` | 连续采集 | `reversible` | 改变运行状态 |
| `stop` | 停止采集 | `reversible` | 改变运行状态 |
| `single` | 单次采集 | `guarded` | 默认不授权 |

支持的自动测量项：

```text
FREQUENCY, PERIOD, VPP, VMAX, VMIN, VAVG, RMS,
PDUTY, NDUTY, RISE_TIME, FALL_TIME
```

设备返回的无效超大测量值会转换为 `null`，不会当成真实数值。

## 标准设备状态

所有设备包必须提供 `get_device_status`：

```json
{
  "online": true,
  "identity": {
    "manufacturer": "VENDOR",
    "model": "MODEL",
    "serial": "ABC***123",
    "firmware": "1.0"
  },
  "status": {},
  "errors": []
}
```

序列号必须脱敏。

## 闭环能力

当前示波器闭环控制器可：

- 获取状态、自动测量、波形和截图；
- 评估削顶、量化分辨率、周期性、直流、瞬态和噪声证据；
- 每轮最多调整一个变量；
- 在授权范围内调整垂直档位、时基或触发电平；
- 保存完整设置快照并验证恢复结果；
- 区分执行成功、结论可信和恢复成功。

## 不开放的能力

以下能力不注册为模型工具：

- 任意 SCPI `query` 或 `write`；
- 任意寄存器、串口或网络请求；
- 恢复出厂、校准和固件升级；
- 设备文件系统访问；
- 未声明的通道、参数和测量项；
- 未经验证的高级触发和总线配置写入。

底层驱动可以包含通用协议方法，但只能由经过评审的 Adapter 内部调用。

## HTTP API

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 服务、规划器和当前设备包 |
| GET | `/device-packs` | 已注册设备列表 |
| POST | `/device-packs/select` | 切换当前设备包 |
| GET | `/capabilities` | 当前工具和权限信息 |
| GET | `/device` | 当前设备状态 |
| POST | `/plan` | 只生成计划 |
| POST | `/tasks` | 执行任务 |
| GET | `/sessions` | 最近会话 |

服务只允许监听本机回环地址。
