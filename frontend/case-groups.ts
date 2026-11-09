import type { CaseRecord } from './state.ts';

export interface GroupCase { id: string; name: string; description: string; group?: string; opt_in?: boolean }
export interface GroupSummary { name: string; ids: string[]; total: number; selected: number; passed: number; failed: number }
export interface CaseGroupSection<T extends GroupCase> extends GroupSummary { cases: T[]; open: boolean }

export function caseGroup(item: GroupCase): string {
  return item.group?.trim() || '未分组';
}

/** 分组顺序来自后端顺序清单，组内用例保持清单中的相对顺序。 */
export function summarizeGroups(
  cases: readonly GroupCase[], selected: ReadonlySet<string>, records: Record<string, CaseRecord>,
): GroupSummary[] {
  const groups = new Map<string, GroupSummary>();
  for (const item of cases) {
    const name = caseGroup(item);
    let group = groups.get(name);
    if (!group) {
      group = { name, ids: [], total: 0, selected: 0, passed: 0, failed: 0 };
      groups.set(name, group);
    }
    group.ids.push(item.id);
    group.total += 1;
    if (selected.has(item.id)) group.selected += 1;
    const status = records[item.id]?.status;
    if (status === 'passed') group.passed += 1;
    if (status === 'failed') group.failed += 1;
  }
  return [...groups.values()];
}

export function visibleCases<T extends GroupCase>(cases: readonly T[], group = '', search = ''): T[] {
  const query = search.trim().toLowerCase();
  return cases.filter(item => (!group || caseGroup(item) === group)
    && (!query || `${caseGroup(item)} ${item.name} ${item.description}`.toLowerCase().includes(query)));
}

/** 仅更改目标标识的勾选状态，保留所有隐藏项和原集合。 */
export function toggleCases(selected: ReadonlySet<string>, ids: readonly string[], checked: boolean): Set<string> {
  const next = new Set(selected);
  for (const id of ids) {
    if (checked) next.add(id); else next.delete(id);
  }
  return next;
}

export function selectionState(ids: readonly string[], selected: ReadonlySet<string>): { checked: boolean; indeterminate: boolean } {
  const count = ids.filter(id => selected.has(id)).length;
  return { checked: ids.length > 0 && count === ids.length, indeterminate: count > 0 && count < ids.length };
}

/** 执行顺序与分组展示一致：先分组，再组内顺序，忽略勾选先后。 */
export function selectedCaseIds(cases: readonly GroupCase[], selected: ReadonlySet<string>): string[] {
  return summarizeGroups(cases, selected, {}).flatMap(group => group.ids.filter(id => selected.has(id)));
}

export function reconcileSelection(
  previousCases: readonly GroupCase[], refreshedCases: readonly GroupCase[], selected: ReadonlySet<string>,
): Set<string> {
  const previousIds = new Set(previousCases.map(item => item.id));
  return new Set(refreshedCases.filter(item => selected.has(item.id) || (!item.opt_in && !previousIds.has(item.id))).map(item => item.id));
}

/** 搜索只缩减组内展示项，完整分组的选择范围与结果统计不随折叠改变。 */
export function groupedSections<T extends GroupCase>(
  cases: readonly T[], selected: ReadonlySet<string>, records: Record<string, CaseRecord>,
  expanded: ReadonlySet<string>, search = '', searchCollapsed: ReadonlySet<string> = new Set(),
): CaseGroupSection<T>[] {
  const matching = visibleCases(cases, '', search);
  const searching = Boolean(search.trim());
  return summarizeGroups(cases, selected, records).flatMap(group => {
    const members = matching.filter(item => caseGroup(item) === group.name);
    if (!members.length) return [];
    return [{ ...group, cases: members, open: searching ? !searchCollapsed.has(group.name) : expanded.has(group.name) }];
  });
}

/** 搜索期间与普通列表各自保存展开状态，切换时不修改原集合。 */
export function toggleExpandedGroup(current: ReadonlySet<string>, name: string): Set<string> {
  const next = new Set(current);
  if (next.has(name)) next.delete(name); else next.add(name);
  return next;
}
