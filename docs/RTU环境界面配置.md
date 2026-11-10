# RTU 环境界面配置

在「测试环境」填写 DSP 上位机程序、EdgeDriver 和对接的 slaveSim 观察地址。「Modbus RTU」只配置采集等待时限，默认 300 秒，可填写 5～300 秒。旧环境导入时保留观察地址和等待时限。

五条基础 RTU 用例在上位机建立连接和点表。测试前准备专用下位机、空闲串口和模拟工况。之前使用 `local/modbus-rtu.json` 的场景，按新示例将总线、串口、站号和地址填入 `local/modbus-rtu-independent.json`，并补充专用下位机地址。 [独立 RTU 配置](Modbus%20RTU双端采集测试.md)

华为六路 21 台验收读取 `local/huawei-rtu.json`，根据预置的模拟机群创建本例连接和点表。RTU 到 104 品质使用预置的业务点、路由和主站。 [华为机群配置](examples/huawei-rtu.example.json) [104 品质准备](Modbus%20RTU到104品质验收.md)

界面更新需关闭旧测试上位机，再运行「启动测试上位机.cmd」。实际采集结果以本次用户运行的报告为准。
