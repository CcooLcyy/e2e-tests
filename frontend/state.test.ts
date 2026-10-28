import assert from 'node:assert/strict';
import { test } from 'node:test';
import { applyEvents, initialRecords, counts } from './state.ts';

test('步骤事件开始和完成合并，零和false不丢失', () => {
  const state = applyEvents(initialRecords(['a']), [
    { type: 'case_started', case_id: 'a' },
    { type: 'step', case_id: 'a', name: '检查', status: 'running' },
    { type: 'step', case_id: 'a', name: '检查', status: 'passed', expected: 0, actual: false },
  ]);
  assert.equal(state.a.status, 'running');
  assert.equal(state.a.steps.length, 1);
  assert.equal(state.a.steps[0].actual, false);
  assert.equal(state.a.steps[0].expected, 0);
});
test('停止后的当前项取消、后续未执行，不能误计通过', () => {
  const state = applyEvents(initialRecords(['a', 'b']), [
    { type: 'case_started', case_id: 'a' }, { type: 'task_finished', status: 'cancelled' },
  ]);
  assert.equal(state.a.status, 'cancelled');
  assert.equal(state.b.status, 'pending');
  assert.deepEqual(counts(state, ['a', 'b']), { total: 2, done: 1, passed: 0, failed: 0 });
});
test('清理阶段失败应覆盖用例通过，保留错误供详情查看', () => {
  const state = applyEvents(initialRecords(['a']), [
    { type: 'case_finished', case_id: 'a', status: 'failed', message: '清理失败', duration: 2 },
  ]);
  assert.equal(state.a.status, 'failed');
  assert.equal(state.a.message, '清理失败');
  assert.equal(counts(state, ['a']).failed, 1);
});
