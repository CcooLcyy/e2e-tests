import type { Environment } from './api';
import { withModbusDefaults } from './modbus-environment';

function NumberField({label, value, onChange, id, min, max, unit}: {label:string;value:number;onChange:(value:number)=>void;id:string;min:number;max:number;unit:string}) {
  return <div className="field"><label htmlFor={id}>{label}</label><div className="number-input"><input id={id} data-testid={id} type="number" min={min} max={max} value={Number.isFinite(value)?value:''} onChange={event=>onChange(event.target.value===''?NaN:Number(event.target.value))}/><span>{unit}</span></div></div>;
}

export function ModbusEnvironment({environment, onChange}: {environment:Environment;onChange:(environment:Environment)=>void}) {
  const current = withModbusDefaults(environment);
  return <details className="form-section modbus-section collapsible-section" id="environment-modbus" tabIndex={-1} data-testid="environment-modbus" onToggle={event=>console.info('测试环境分区：',event.currentTarget.id,event.currentTarget.open?'展开':'收起')}>
    <summary className="section-title"><span>06</span><div><h3>Modbus RTU</h3><p>用例执行时读取当前 DSP 对接的 slaveSim 配置，按需准备模拟设备及采集连接。</p></div></summary>
    <div className="field"><label htmlFor="environment-modbus-manager-address">下位机 ModuleManager 地址</label>
      <input id="environment-modbus-manager-address" data-testid="environment-modbus-manager-address" value={current.modbus_manager_address} placeholder="主机:端口" onChange={event=>onChange({...environment,modbus_manager_address:event.target.value})}/>
      <p>填写实际现场直连地址；上方 slaveSim 管理地址须指向这个 DSP 对接的模拟器。</p>
    </div>
    <div className="form-split">
      <NumberField label="轮询周期" id="environment-modbus-poll-interval" value={current.modbus_poll_interval_ms} min={100} max={60000} unit="毫秒" onChange={value=>onChange({...environment,modbus_poll_interval_ms:value})}/>
      <NumberField label="采集等待时限" id="environment-modbus-acquisition-timeout" value={current.modbus_acquisition_timeout} min={5} max={300} unit="秒" onChange={value=>onChange({...environment,modbus_acquisition_timeout:value})}/>
    </div>
    <p className="protocol-note">采集匹配默认最多等待300秒（5分钟），匹配成功即继续，不固定等待满5分钟。</p>
    <p className="protocol-note">总线、串口路径、站号、寄存器及模型在执行时读取，无需在这里重复配置。资源不足时，用例会通过 slaveSim 管理页面补充配置，确认保存生效后执行验收。</p>
    <p className="protocol-note">请使用测试环境及全新上位机副本。保存模拟器配置会重建运行状态，配置前后及本次调整会保留在执行记录中；华为用例新增的模型和连接保留供检查。</p>
    <label className="cleanup-toggle">
      <input type="checkbox" checked={environment.agc_rebalance_dedicated === true} onChange={event=>onChange({...environment,agc_rebalance_dedicated:event.target.checked})}/>
      <span>确认下位机与 slaveSim 专供 AGC 通信重分配测试<small>单独运行该用例，轮询周期最多 10000 毫秒。开始时检查连接和模拟器总线为空，随后自动配置并保留现场。</small></span>
    </label>
    <NumberField label="AGC 通信重分配 104 监听端口" id="environment-agc-rebalance-104-port"
      value={environment.agc_rebalance_iec104_port === undefined ? 2404 : Number.isFinite(environment.agc_rebalance_iec104_port) ? environment.agc_rebalance_iec104_port : NaN}
      min={1} max={65535} unit="端口" onChange={value=>onChange({...environment,agc_rebalance_iec104_port:value})}/>
  </details>;
}
