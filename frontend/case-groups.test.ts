import assert from 'node:assert/strict';
import { test } from 'node:test';
import { caseGroup, summarizeGroups, visibleCases, toggleCases, selectionState, selectedCaseIds, reconcileSelection, groupedSections, toggleExpandedGroup } from './case-groups.ts';
import type { GroupCase } from './case-groups.ts';
import type { CaseRecord } from './state.ts';

const cases: GroupCase[] = [
  { id: 'login', name: '正确账号登录', description: '进入配置工作台', group: '基础登录' },
  { id: 'layout', name: '展开连接设置', description: '展开和折叠', group: '启动与界面' },
  { id: 'remember', name: '记住密码', description: '关闭后重新打开程序', group: '密码记忆' },
  { id: 'wrong', name: '错误密码提示', description: '明确提示认证错误', group: '基础登录' },
];

test('旧用例归入未分组，显式组名去掉边缘空白', () => {
  assert.equal(caseGroup({ ...cases[0], group: undefined }), '未分组');
  assert.equal(caseGroup({ ...cases[0], group: '   ' }), '未分组');
  assert.equal(caseGroup({ ...cases[0], group: ' 基础登录 ' }), '基础登录');
});

test('分组按首次出现排序并合并不相邻用例，统计不改变原列表或结果', () => {
  const records: Record<string, CaseRecord> = {
    login: { status: 'passed', steps: [] },
    layout: { status: 'skipped', steps: [] },
    remember: { status: 'cancelled', steps: [] },
    wrong: { status: 'failed', steps: [] },
  };
  const snapshot = JSON.stringify({ cases, records });
  assert.deepEqual(summarizeGroups(cases, new Set(['wrong', 'layout', 'stale']), records), [
    { name: '基础登录', ids: ['login', 'wrong'], total: 2, selected: 1, passed: 1, failed: 1 },
    { name: '启动与界面', ids: ['layout'], total: 1, selected: 1, passed: 0, failed: 0 },
    { name: '密码记忆', ids: ['remember'], total: 1, selected: 0, passed: 0, failed: 0 },
  ]);
  assert.equal(JSON.stringify({ cases, records }), snapshot);
});

test('未执行或缺失结果不计通过失败，未分组旧用例可显示统计', () => {
  const old = { ...cases[0], group: undefined };
  const pending: Record<string, CaseRecord> = { login: { status: 'pending', steps: [] } };
  assert.deepEqual(summarizeGroups([old], new Set(), pending), [
    { name: '未分组', ids: ['login'], total: 1, selected: 0, passed: 0, failed: 0 },
  ]);
  assert.equal(summarizeGroups([old], new Set(), {})[0].passed, 0);
  assert.deepEqual(summarizeGroups([], new Set(['login']), pending), []);
});

test('切换分组与搜索保留组内原顺序，不修改完整列表', () => {
  assert.deepEqual(visibleCases(cases, '基础登录').map(item => item.id), ['login', 'wrong']);
  assert.deepEqual(visibleCases(cases).map(item => item.id), ['login', 'layout', 'remember', 'wrong']);
  assert.deepEqual(visibleCases(cases, '基础登录', ' 错误 ').map(item => item.id), ['wrong']);
  assert.deepEqual(visibleCases(cases, '基础登录', '关闭'), []);
  assert.deepEqual(visibleCases(cases, '不存在'), []);
  assert.deepEqual(cases.map(item => item.id), ['login', 'layout', 'remember', 'wrong']);
});

test('搜索同时覆盖分组、名称和说明并忽略大小写及首尾空格', () => {
  assert.equal(visibleCases(cases, '', '基础登录').length, 2);
  assert.equal(visibleCases(cases, '', '连接设置')[0].id, 'layout');
  assert.equal(visibleCases(cases, '', '重新打开')[0].id, 'remember');
  assert.equal(visibleCases([{ ...cases[0], description: 'Modbus TCP 连接' }], '', ' tcp ')[0].id, 'login');
  assert.equal(visibleCases(cases, '', '   ').length, 4);
});

test('选择当前可见项时保留隐藏选择，不变更原集合', () => {
  const selected = new Set(['remember']);
  const visible = visibleCases(cases, '基础登录', '错误');
  const next = toggleCases(selected, visible.map(item => item.id), true);
  assert.deepEqual([...selected], ['remember']);
  assert.deepEqual([...next], ['remember', 'wrong']);
  assert.deepEqual([...toggleCases(next, ['wrong'], false)], ['remember']);
  assert.notEqual(toggleCases(selected, [], true), selected);
});

test('整组选择不受搜索影响，取消整组只删除该组用例', () => {
  const group = summarizeGroups(cases, new Set(), {})[0];
  const selected = toggleCases(new Set(['remember']), group.ids, true);
  assert.deepEqual(selectedCaseIds(cases, selected), ['login', 'wrong', 'remember']);
  assert.deepEqual([...toggleCases(selected, group.ids, false)], ['remember']);
});

test('全选半选空组选中状态准确且不受其他组选择影响', () => {
  assert.deepEqual(selectionState(['login', 'wrong'], new Set(['remember'])), { checked: false, indeterminate: false });
  assert.deepEqual(selectionState(['login', 'wrong'], new Set(['wrong'])), { checked: false, indeterminate: true });
  assert.deepEqual(selectionState(['login', 'wrong'], new Set(['wrong', 'login'])), { checked: true, indeterminate: false });
  assert.deepEqual(selectionState([], new Set(['wrong'])), { checked: false, indeterminate: false });
});

test('按分组及组内顺序生成执行顺序，忽略勾选先后与陈旧标识', () => {
  assert.deepEqual(selectedCaseIds(cases, new Set(['wrong', 'remember', 'login', 'removed'])), ['login', 'wrong', 'remember']);
  assert.deepEqual(selectedCaseIds(cases, new Set(['remember', 'layout', 'wrong', 'login'])), ['login', 'wrong', 'layout', 'remember']);
  assert.deepEqual(selectedCaseIds(cases, new Set()), []);
});

test('刷新保留已有选择和未选状态，删除消失项并选中新用例', () => {
  const refreshed = [cases[1], cases[0], { ...cases[3], id: 'new' }];
  const selected = new Set(['login', 'remember', 'stale']);
  assert.deepEqual([...reconcileSelection(cases, refreshed, selected)], ['login', 'new']);
  assert.deepEqual([...selected], ['login', 'remember', 'stale']);
  assert.deepEqual([...reconcileSelection([], refreshed, new Set())], ['layout', 'login', 'new']);
  assert.deepEqual([...reconcileSelection(cases, [], selected)], []);
});

test('分组标题默认全部收起，执行顺序与分组展示顺序一致', () => {
  const sections = groupedSections(cases, new Set(['wrong', 'layout', 'login']), {}, new Set());
  assert.deepEqual(sections.map(group => [group.name, group.open]), [
    ['基础登录', false], ['启动与界面', false], ['密码记忆', false],
  ]);
  assert.deepEqual(sections[0].cases.map(item => item.id), ['login', 'wrong']);
  assert.deepEqual(selectedCaseIds(cases, new Set(['wrong', 'layout', 'login'])), ['login', 'wrong', 'layout']);
});

test('只展开指定分组，展开和收起操作不修改原状态集合', () => {
  const expanded = new Set(['启动与界面']);
  const next = toggleExpandedGroup(expanded, '基础登录');
  assert.deepEqual([...expanded], ['启动与界面']);
  assert.deepEqual(groupedSections(cases, new Set(), {}, next).map(group => group.open), [true, true, false]);
  assert.deepEqual([...toggleExpandedGroup(next, '基础登录')], ['启动与界面']);
});

test('搜索只显示匹配分组并自动展开，清空搜索恢复原先展开状态', () => {
  const expanded = new Set(['启动与界面']);
  const sections = groupedSections(cases, new Set(['remember']), {}, expanded, ' 错误 ');
  assert.equal(sections.length, 1);
  assert.equal(sections[0].name, '基础登录');
  assert.equal(sections[0].open, true);
  assert.deepEqual(sections[0].cases.map(item => item.id), ['wrong']);
  assert.deepEqual([...expanded], ['启动与界面']);
  assert.deepEqual(groupedSections(cases, new Set(), {}, expanded, '').map(group => group.open), [false, true, false]);
});

test('搜索期间可以独立折叠匹配分组，不改变搜索前展开状态', () => {
  const expanded = new Set(['启动与界面']);
  const searchCollapsed = toggleExpandedGroup(new Set(), '基础登录');
  const sections = groupedSections(cases, new Set(), {}, expanded, '基础登录', searchCollapsed);
  assert.equal(sections[0].open, false);
  assert.deepEqual(sections[0].cases.map(item => item.id), ['login', 'wrong']);
  assert.equal(groupedSections(cases, new Set(), {}, expanded, '', searchCollapsed)[1].open, true);
  assert.deepEqual([...searchCollapsed], ['基础登录']);
});

test('搜索和折叠不缩减整组勾选及统计范围，输入列表和状态原样保留', () => {
  const records: Record<string, CaseRecord> = {
    login: {status: 'passed', steps: []}, wrong: {status: 'failed', steps: []},
  };
  const selected = new Set(['login', 'remember']);
  const expanded = new Set<string>();
  const snapshot = JSON.stringify({cases, records, selected: [...selected], expanded: [...expanded]});
  const group = groupedSections(cases, selected, records, expanded, '错误', new Set(['基础登录']))[0];
  assert.deepEqual({ids: group.ids, total: group.total, selected: group.selected, passed: group.passed, failed: group.failed},
    {ids: ['login', 'wrong'], total: 2, selected: 1, passed: 1, failed: 1});
  assert.equal(group.open, false);
  assert.deepEqual(selectedCaseIds(cases, toggleCases(selected, group.ids, true)), ['login', 'wrong', 'remember']);
  assert.equal(JSON.stringify({cases, records, selected: [...selected], expanded: [...expanded]}), snapshot);
});

test('无匹配项和空列表不生成空分组，空白搜索沿用普通折叠状态', () => {
  assert.deepEqual(groupedSections(cases, new Set(), {}, new Set(), '不存在'), []);
  assert.deepEqual(groupedSections([], new Set(), {}, new Set()), []);
  assert.deepEqual(groupedSections(cases, new Set(), {}, new Set(['密码记忆']), '   ').map(group => group.open), [false, false, true]);
});
