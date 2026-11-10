# RTU 环境界面配置

在「测试环境」填写 DSP 上位机程序、EdgeDriver 和对接的 slaveSim 观察地址。「Modbus RTU」只配置采集等待时限，默认 300 秒，可填写 5～300 秒。旧环境导入时保留观察地址和等待时限。

保存重读和初始采集由用例在上位机建立连接和点表，测试前准备专用下位机、空闲串口和模拟工况。专项配置见 [独立 RTU 配置](Modbus%20RTU双端采集测试.md)。

其他基础采集用例和六路 21 台验收提前准备连接、点表和模拟工况，分别读取 `local/modbus-rtu.json` 和 `local/huawei-rtu.json`。专项配置样例见 [预置基础采集](examples/modbus-rtu.example.json)和[华为机群](examples/huawei-rtu.example.json)。

界面更新需关闭旧测试上位机，再运行「启动测试上位机.cmd」。实际采集结果以本次用户运行的报告为准。
