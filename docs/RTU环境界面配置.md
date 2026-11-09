# RTU 环境界面配置

在「测试环境」填写 DSP 上位机程序、EdgeDriver 和对接的 slaveSim 观察地址。「Modbus RTU」只配置采集等待时限，默认 300 秒，可填写 5～300 秒。旧环境导入时保留观察地址和等待时限。

测试前准备并启用专用采集链路、点表和模拟工况，确认 DSP 上位机能从现有工作区或配置基线看到目标连接。基础采集读取 `local/modbus-rtu.json`，六路 21 台验收读取 `local/huawei-rtu.json`，从专项配置确定两端观测点和固定预期。专项配置样例分别见 [基础采集](examples/modbus-rtu.example.json)和[华为机群](examples/huawei-rtu.example.json)。

开始任务前检查 slaveSim 观察地址和等待时限。测试在两端读取现象，资源或反馈缺失时记录前置失败。下位机管理地址、轮询周期和 104 监听设置属于环境准备。

界面更新需关闭旧测试上位机，再运行「启动测试上位机.cmd」。实际采集结果以本次用户运行的报告为准。
