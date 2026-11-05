import type { ModbusEnvironmentFields, ModbusBasic, ModbusHuawei } from './api';

const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const string = (value: unknown) => typeof value === 'string' ? value : '';
// undefined表示旧配置未提供字段；null是用户清空后通过JSON保存的值，不能变成有效默认地址。
const number = (value: unknown, fallback: number) => value === undefined ? fallback : typeof value === 'number' ? value : NaN;

/** 缺少地址时使用默认下位机；保留显式输入，每次返回独立数组。 */
export function withModbusDefaults<T extends ModbusEnvironmentFields>(environment: T) {
  const basic = object(environment.modbus_basic);
  const huawei = object(environment.modbus_huawei);
  const stations = Array.isArray(basic.stations) ? basic.stations : [];
  const paths = Array.isArray(huawei.serial_devices) ? huawei.serial_devices : [];
  const normalizedBasic: Required<ModbusBasic> = {
    device_name: string(basic.device_name), bus: string(basic.bus), serial_device: string(basic.serial_device),
    stations: [0, 1].map(index => { const station = object(stations[index]); return { station_id: number(station.station_id, index + 1), address: number(station.address, 0) }; }),
    dedicated_test_resources: basic.dedicated_test_resources === true,
    data_bits: number(basic.data_bits, 8), stop_bits: number(basic.stop_bits, 1),
    parity: basic.parity === '奇校验' || basic.parity === '偶校验' ? basic.parity : '无校验',
  };
  const normalizedHuawei: Required<ModbusHuawei> = {
    serial_devices: Array.from({ length: 6 }, (_, index) => string(paths[index])),
    dedicated_simulator: huawei.dedicated_simulator === true,
  };
  return { ...environment, modbus_manager_address: environment.modbus_manager_address === undefined
      ? '192.168.1.219:17000' : string(environment.modbus_manager_address),
    modbus_baud_rate: number(environment.modbus_baud_rate, 9600),
    modbus_poll_interval_ms: number(environment.modbus_poll_interval_ms, 1000),
    modbus_acquisition_timeout: number(environment.modbus_acquisition_timeout, 30),
    modbus_basic: normalizedBasic, modbus_huawei: normalizedHuawei };
}
