import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatPassRate } from './pass-rate.ts';
import { counts, initialRecords } from './state.ts';

test('通过率以通过和失败为分母，保留一位小数', () => {
  assert.equal(formatPassRate(8, 2), '80.0%');
  assert.equal(formatPassRate(1, 2), '33.3%');
  assert.equal(formatPassRate(2, 1), '66.7%');
  assert.equal(formatPassRate(5, 0), '100.0%');
  assert.equal(formatPassRate(0, 5), '0.0%');
});

test('没有通过或失败结果显示破折号', () => {
  assert.equal(formatPassRate(0, 0), '—');
});

test('混合状态只统计本次任务的已判定结果', () => {
  const records = initialRecords(['pass', 'fail', 'pending', 'running', 'skip', 'stop', 'outside']);
  records.pass.status = 'passed';
  records.fail.status = 'failed';
  records.running.status = 'running';
  records.skip.status = 'skipped';
  records.stop.status = 'cancelled';
  records.outside.status = 'passed';
  const stats = counts(records, ['pass', 'fail', 'pending', 'running', 'skip', 'stop']);
  assert.equal(formatPassRate(stats.passed, stats.failed), '50.0%');
  const unfinished = counts(records, ['pending', 'running', 'skip', 'stop']);
  assert.equal(formatPassRate(unfinished.passed, unfinished.failed), '—');
});
