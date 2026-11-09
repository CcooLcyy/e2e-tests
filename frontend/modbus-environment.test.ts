import assert from 'node:assert/strict';
import test from 'node:test';
import { withModbusDefaults } from './modbus-environment.ts';

test('RTU observation has a five-minute default and preserves a custom wait', () => {
  assert.equal(withModbusDefaults({}).modbus_acquisition_timeout, 300);
  const value = withModbusDefaults({ timeout: 45, modbus_acquisition_timeout: 90 });
  assert.equal(value.timeout, 45);
  assert.equal(value.modbus_acquisition_timeout, 90);
});

test('an explicitly cleared observation wait remains empty after JSON round trip', () => {
  const draft = withModbusDefaults({ modbus_acquisition_timeout: NaN });
  const loaded = withModbusDefaults(JSON.parse(JSON.stringify(draft)));
  assert.equal(Number.isNaN(loaded.modbus_acquisition_timeout), true);
});
