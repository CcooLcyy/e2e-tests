import type { Environment, ModbusBasic } from './api';
import { withModbusDefaults } from './modbus-environment';

type TextProps = { label: string; value: string; onChange: (value: string) => void; id: string; placeholder?: string; hint?: string };
function TextField({label, value, onChange, id, placeholder, hint}: TextProps) {
  return <div className="field"><label htmlFor={id}>{label}</label><input id={id} data-testid={id} value={value} placeholder={placeholder} onChange={event=>onChange(event.target.value)}/>{hint&&<p>{hint}</p>}</div>;
}
function NumberField({label, value, onChange, id, min, max, step=1, unit}: {label:string;value:number;onChange:(value:number)=>void;id:string;min:number;max?:number;step?:number;unit?:string}) {
  return <div className="field"><label htmlFor={id}>{label}</label><div className="number-input"><input id={id} data-testid={id} type="number" min={min} max={max} step={step} value={Number.isFinite(value)?value:''} onChange={event=>onChange(event.target.value===''?NaN:Number(event.target.value))}/>{unit&&<span>{unit}</span>}</div></div>;
}

export function ModbusEnvironment({environment, onChange}: {environment:Environment;onChange:(environment:Environment)=>void}) {
  const current = withModbusDefaults(environment);
  const basic = current.modbus_basic;
  const huawei = current.modbus_huawei;
  const setBasic = (patch:ModbusBasic) => onChange({...environment, modbus_basic:{...basic,...patch}});
  const setStation = (index:number, field:'station_id'|'address', value:number) => {
    const stations = basic.stations.map((station, position)=>position===index?{...station,[field]:value}:{...station});
    setBasic({stations});
  };
  const setPath = (index:number, value:string) => onChange({...environment,modbus_huawei:{...huawei,serial_devices:huawei.serial_devices.map((path,position)=>position===index?value:path)}});
  return <div className="form-section" data-testid="environment-modbus">
    <div className="section-title"><span>06</span><div><h3>Modbus RTU</h3><p>填写所选用例需要的参数；保存环境后自动回填，开始测试前检查完整性。</p></div></div>
    <TextField label="下位机 ModuleManager 地址" id="environment-modbus-manager-address" value={current.modbus_manager_address}
      placeholder="主机:端口" hint="基础采集和华为六路用例共用，填写实际现场直连地址。" onChange={value=>onChange({...environment,modbus_manager_address:value})}/>
    <div className="form-split">
      <NumberField label="波特率" id="environment-modbus-baud-rate" value={current.modbus_baud_rate} min={1} onChange={value=>onChange({...environment,modbus_baud_rate:value})}/>
      <NumberField label="轮询周期" id="environment-modbus-poll-interval" value={current.modbus_poll_interval_ms} min={100} max={60000} unit="毫秒" onChange={value=>onChange({...environment,modbus_poll_interval_ms:value})}/>
    </div>
    <NumberField label="采集等待时限" id="environment-modbus-acquisition-timeout" value={current.modbus_acquisition_timeout} min={5} max={120} unit="秒" onChange={value=>onChange({...environment,modbus_acquisition_timeout:value})}/>
    <details open>
      <summary><strong>基础采集：原5个 RTU 用例</strong></summary>
      <p>使用提前准备好的两个普通模拟从站，各有一个手动来源 UINT16 保持寄存器。运行华为机群用例时无需填写本区。</p>
      <TextField label="下位机名称" id="environment-modbus-basic-device-name" value={basic.device_name} hint="用于报告中标识目标设备。" onChange={value=>setBasic({device_name:value})}/>
      <TextField label="slaveSim 总线名称" id="environment-modbus-basic-bus" value={basic.bus} onChange={value=>setBasic({bus:value})}/>
      <TextField label="基础采集串口路径" id="environment-modbus-basic-serial" value={basic.serial_device} placeholder="/dev/专用串口" hint="填写下位机和模拟器共用的实际串口路径。" onChange={value=>setBasic({serial_device:value})}/>
      <div className="form-split">
        <NumberField label="数据位" id="environment-modbus-basic-data-bits" value={basic.data_bits} min={5} max={8} onChange={value=>setBasic({data_bits:value})}/>
        <div className="field"><label htmlFor="environment-modbus-basic-parity">校验位</label><select id="environment-modbus-basic-parity" data-testid="environment-modbus-basic-parity" value={basic.parity} onChange={event=>setBasic({parity:event.target.value as Required<ModbusBasic>['parity']})}>{(['无校验','奇校验','偶校验'] as const).map(value=><option key={value} value={value}>{value}</option>)}</select></div>
      </div>
      <div className="field"><label htmlFor="environment-modbus-basic-stop-bits">停止位</label><select id="environment-modbus-basic-stop-bits" data-testid="environment-modbus-basic-stop-bits" value={basic.stop_bits} onChange={event=>setBasic({stop_bits:Number(event.target.value)})}><option value={1}>1</option><option value={2}>2</option></select></div>
      {basic.stations.map((station,index)=><div className="form-split" key={index}>
        <NumberField label={`从站${index+1}站号`} id={`environment-modbus-basic-station-${index+1}`} value={station.station_id} min={1} max={247} onChange={value=>setStation(index,'station_id',value)}/>
        <NumberField label={`从站${index+1}寄存器地址（零基）`} id={`environment-modbus-basic-address-${index+1}`} value={station.address} min={0} max={65535} onChange={value=>setStation(index,'address',value)}/>
      </div>)}
      <label className="cleanup-toggle"><input data-testid="environment-modbus-basic-dedicated" type="checkbox" checked={basic.dedicated_test_resources} onChange={event=>{setBasic({dedicated_test_resources:event.target.checked});console.info('已更新基础RTU专用资源确认');}}/><span>确认串口和模拟点为专用测试资源<small>用例会设值并恢复原值，新建和清理本例连接。</small></span></label>
    </details>
    <details open>
      <summary><strong>华为六路21台：自动创建与采集匹配</strong></summary>
      <p>六路分别创建1至6台行为模型，自动配置连接及点表。基础采集用例无需填写本区。当前用例使用8数据位、无校验、1停止位。</p>
      {huawei.serial_devices.map((path,index)=><TextField key={index} label={`第${index+1}路485共享串口路径`} id={`environment-modbus-huawei-serial-${index+1}`} value={path} placeholder="Linux绝对路径，例如 /srv/serial-sim/tty0" hint="必须同时对模拟器和下位机可见，六路路径不同且未被已有连接占用。" onChange={value=>setPath(index,value)}/>)}
      <label className="cleanup-toggle"><input data-testid="environment-modbus-huawei-dedicated" type="checkbox" checked={huawei.dedicated_simulator} onChange={event=>{onChange({...environment,modbus_huawei:{...huawei,dedicated_simulator:event.target.checked}});console.info('已更新华为机群专用模拟器确认');}}/><span>确认使用专用 slaveSim 模拟器<small>保存模型配置会重建模拟器运行状态；用例结束后保留新增模型和连接供检查。</small></span></label>
    </details>
    <p>两类用例共用上方 slaveSim 管理页地址。未选择 RTU 用例时，无需填写本节；这里的参数统一随测试环境保存，无需另建配置文件。</p>
  </div>;
}
