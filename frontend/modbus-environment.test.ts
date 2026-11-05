import assert from 'node:assert/strict';
import { test } from 'node:test';
import { withModbusDefaults } from './modbus-environment.ts';
import type { ModbusEnvironmentFields } from './api';

test('旧环境提供空白路径，资源确认保持未勾选', () => {
  const value = withModbusDefaults({});
  assert.equal(value.modbus_manager_address, '192.168.1.219:16000');
  assert.deepEqual(value.modbus_huawei.serial_devices, ['', '', '', '', '', '']);
  assert.equal(value.modbus_huawei.dedicated_simulator, false);
  assert.equal(value.modbus_basic.dedicated_test_resources, false);
});

test('回填保留零基地址、自定义通信参数与显式确认', () => {
  const value = withModbusDefaults({ modbus_manager_address: 'test-host:50051', modbus_baud_rate: 19200,
    modbus_basic: { bus: '总线甲', serial_device: '/dev/test', stations: [{ station_id: 7, address: 0 }, { station_id: 9, address: 65535 }], dedicated_test_resources: true },
    modbus_huawei: { serial_devices: ['/dev/a', '/dev/b', '/dev/c', '/dev/d', '/dev/e', '/dev/f'], dedicated_simulator: true } });
  assert.equal(value.modbus_manager_address, 'test-host:50051');
  assert.equal(withModbusDefaults({modbus_manager_address: ''}).modbus_manager_address, '');
  assert.equal(value.modbus_baud_rate, 19200);
  assert.equal(value.modbus_basic.stations[0].address, 0);
  assert.equal(value.modbus_basic.stations[1].station_id, 9);
  assert.equal(value.modbus_huawei.serial_devices[5], '/dev/f');
  assert.equal(value.modbus_huawei.dedicated_simulator, true);
});

test('默认值独立且兼容导入的空对象或null', () => {
  const first = withModbusDefaults({});
  const second = withModbusDefaults({ modbus_basic: null, modbus_huawei: null } as unknown as ModbusEnvironmentFields);
  first.modbus_basic.stations[0].station_id = 99;
  first.modbus_huawei.serial_devices[0] = '/dev/changed';
  assert.equal(second.modbus_basic.stations[0].station_id, 1);
  assert.equal(second.modbus_huawei.serial_devices[0], '');
});

test('显式清空的数字经JSON保存回填后仍为空白，不变成零地址或默认通信参数', () => {
  const draft = withModbusDefaults({ modbus_baud_rate: NaN, modbus_poll_interval_ms: NaN,
    modbus_acquisition_timeout: NaN, modbus_basic: { data_bits: NaN, stop_bits: NaN,
      stations: [{ station_id: NaN, address: NaN }, { station_id: 2, address: 0 }] } });
  const loaded = withModbusDefaults(JSON.parse(JSON.stringify(draft)) as ModbusEnvironmentFields);
  assert.equal(Number.isNaN(loaded.modbus_baud_rate), true);
  assert.equal(Number.isNaN(loaded.modbus_poll_interval_ms), true);
  assert.equal(Number.isNaN(loaded.modbus_acquisition_timeout), true);
  assert.equal(Number.isNaN(loaded.modbus_basic.data_bits), true);
  assert.equal(Number.isNaN(loaded.modbus_basic.stop_bits), true);
  assert.equal(Number.isNaN(loaded.modbus_basic.stations[0].station_id), true);
  assert.equal(Number.isNaN(loaded.modbus_basic.stations[0].address), true);
  assert.equal(loaded.modbus_basic.stations[1].address, 0);
});
