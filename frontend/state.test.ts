import assert from 'node:assert/strict';
import { test } from 'node:test';
import { applyEvents, initialRecords, counts } from './state.ts';

test('双端同名同编号步骤按来源分别完成', () => {
  const state = applyEvents(initialRecords(['a']), [
    { type: 'step', case_id: 'a', step_id: 1, name: '读取状态', status: 'running' },
    { type: 'step', case_id: 'a', step_id: 'master104:1', name: '[104主站] 读取状态', status: 'running' },
    { type: 'step', case_id: 'a', step_id: 'master104:1', name: '[104主站] 读取状态', status: 'failed', actual: false },
    { type: 'step', case_id: 'a', step_id: 1, name: '读取状态', status: 'passed', actual: 0 },
  ]);
  assert.deepEqual(state.a.steps.map(step => [step.step_id, step.status, step.actual]), [
    [1, 'passed', 0], ['master104:1', 'failed', false],
  ]);
});

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

test('同名嵌套步骤按序号完成，不会把外层结果写到内层', () => {
  const state = applyEvents(initialRecords(['a']), [
    { type: 'step', case_id: 'a', step_id: 1, name: '检查', status: 'running' },
    { type: 'step', case_id: 'a', step_id: 2, name: '检查', status: 'running' },
    { type: 'step', case_id: 'a', step_id: 1, name: '检查', status: 'failed', actual: '外层失败' },
  ]);
  assert.deepEqual(state.a.steps.map(step => [step.step_id, step.status, step.actual]), [
    [1, 'failed', '外层失败'], [2, 'running', undefined],
  ]);
});

test('重复步骤事件按序号更新且保持开始顺序，旧快照不被修改', () => {
  const original = applyEvents(initialRecords(['a']), [
    { type: 'step', case_id: 'a', step_id: 1, name: '点击', status: 'running' },
    { type: 'step', case_id: 'a', step_id: 2, name: '点击', status: 'running' },
  ]);
  const state = applyEvents(original, [
    { type: 'step', case_id: 'a', step_id: 2, name: '点击', status: 'passed', actual: false },
    { type: 'step', case_id: 'a', step_id: 1, name: '点击', status: 'passed', actual: 0 },
    { type: 'step', case_id: 'a', step_id: 1, name: '点击', status: 'passed', actual: 0 },
  ]);
  assert.deepEqual(state.a.steps.map(step => [step.step_id, step.status, step.actual]), [
    [1, 'passed', 0], [2, 'passed', false],
  ]);
  assert.deepEqual(original.a.steps.map(step => step.status), ['running', 'running']);
});

test('带序号事件不合并到无序号的旧步骤，同一序号按用例隔离', () => {
  const state = applyEvents(initialRecords(['a', 'b']), [
    { type: 'step', case_id: 'a', name: '检查', status: 'running' },
    { type: 'step', case_id: 'a', step_id: 1, name: '检查', status: 'passed' },
    { type: 'step', case_id: 'b', step_id: 1, name: '检查', status: 'failed' },
  ]);
  assert.equal(state.a.steps.length, 2);
  assert.equal(state.a.steps[0].status, 'running');
  assert.equal(state.a.steps[1].status, 'passed');
  assert.equal(state.b.steps.length, 1);
  assert.equal(state.b.steps[0].status, 'failed');
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

test('跟随各用例最后一条计划步骤事件，同批完成的步骤仍可定位', () => {
  const state = applyEvents(initialRecords(['a', 'b']), [
    { type: 'plan_step', case_id: 'a', plan_step_id: 'first', status: 'passed' },
    { type: 'plan_step', case_id: 'a', plan_step_id: 'next', status: 'running' },
    { type: 'plan_step', case_id: 'a', plan_step_id: 'next', status: 'passed' },
    { type: 'case_finished', case_id: 'a', status: 'passed' },
    { type: 'plan_step', case_id: 'b', plan_step_id: 'first', status: 'running' },
  ]);
  assert.equal(state.a.latest_plan_step_id, 'next');
  assert.equal(state.a.plan_steps?.next, 'passed');
  assert.equal(state.b.latest_plan_step_id, 'first');
});
