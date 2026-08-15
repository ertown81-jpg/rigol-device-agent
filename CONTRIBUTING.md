# 参与开发

## 分支和提交

从 `dev` 创建独立分支：

```text
feat/device-pack-dp832
fix/raw-waveform-restore
```

提交前缀使用 `feat:`、`fix:`、`test:`、`docs:` 或 `refactor:`。

## 基本要求

- 不修改任务范围外的设备包；
- 新能力使用严格参数 Schema，不开放原始协议命令；
- 写操作声明风险等级、回读方式和恢复方式；
- 没有实机时标记为“文档支持”或“模拟验证”；
- 不提交 `config.json`、API 密钥、真实序列号、日志、截图或虚拟环境。

## 提交前检查

```powershell
python -m unittest discover -s tests -p test_*.py -v
python -m rigol_agent devices
python -m rigol_agent --simulate --device rigol_ds1102ze capabilities
```

Pull Request 需要说明影响的设备包、权限变化、测试条件、恢复结果和未验证项。新增设备按 [新增设备](docs/ADDING_DEVICE.md) 执行。
