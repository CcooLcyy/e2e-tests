# RTU 模拟环境与观察配置

本文说明运行前的模拟环境准备。基础 RTU 和华为机群的 DSP 连接及点表由各用例创建。

## 运行前准备

1. 在测试之外准备专用 DSP、RTU 模块、空闲串口、slaveSim 总线和模型。专用设备的 RTU 模块全局 MQTT 配置须已初始化。
2. 环境页填写实际对接的 slaveSim 页面地址和采集等待时限。测试使用专用平台账号和独立浏览器观察会话。
3. 基础用例将 [独立 RTU 示例](examples/modbus-rtu-independent.example.json) 复制为 `local/modbus-rtu-independent.json`，填写专用下位机地址、总线、串口、站号和读保持寄存器零基地址。初始值及后续变值时序见 [基础 RTU 验收](Modbus%20RTU双端采集测试.md)。
4. 华为用例将 [机群示例](examples/huawei-rtu.example.json) 复制为 `local/huawei-rtu.json`。按顺序填写六路实际总线名称和串口，第 n 路预置站号 1 至 n。固定工况和点位见 [华为六路采集验收](华为21台六路RTU采集测试.md)。

## 运行边界

用例通过上位机正常界面维护本例连接及点表，通过 slaveSim 只读观察运行反馈。基础用例需要预先安排固定原始值和变化，机群用例需要预先安排七个可观测工况。RTU 到 104 品质另需事先配置路由和 104 链路，见 [104 品质验收](Modbus%20RTU到104品质验收.md)。
