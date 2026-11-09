import type { Environment } from './api';
import { withModbusDefaults } from './modbus-environment';

export function ModbusEnvironment({ environment, onChange }: { environment: Environment; onChange: (environment: Environment) => void }) {
  const timeout = withModbusDefaults(environment).modbus_acquisition_timeout;
  return <details className="form-section modbus-section collapsible-section" id="environment-modbus" tabIndex={-1} data-testid="environment-modbus" onToggle={event => console.info('测试环境分区：', event.currentTarget.id, event.currentTarget.open ? '展开' : '收起')}>
    <summary className="section-title"><span>06</span><div><h3>Modbus RTU</h3><p>设置观察上位机采集反馈的等待时间。</p></div></summary>
    <div className="field">
      <label htmlFor="environment-modbus-acquisition-timeout">采集等待时限</label>
      <div className="number-input">
        <input id="environment-modbus-acquisition-timeout" data-testid="environment-modbus-acquisition-timeout" type="number" min={5} max={300}
          value={Number.isFinite(timeout) ? timeout : ''}
          onChange={event => onChange({ ...environment, modbus_acquisition_timeout: event.target.valueAsNumber })} />
        <span>秒</span>
      </div>
    </div>
    <p className="protocol-note">下位机采集链路、点表和 slaveSim 工况须在运行前准备。用例读取上位机和 slaveSim 的可观察反馈。</p>
  </details>;
}
