import type { ModbusEnvironmentFields } from './api';

export function withModbusDefaults<T extends ModbusEnvironmentFields>(environment: T) {
  const timeout = environment.modbus_acquisition_timeout;
  // 旧配置缺少字段时采用默认值，显式清空后保存的 null 仍表示未填写。
  return { ...environment, modbus_acquisition_timeout: timeout === undefined ? 300 : typeof timeout === 'number' ? timeout : NaN };
}
