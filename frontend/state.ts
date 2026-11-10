export type Status = 'pending' | 'running' | 'passed' | 'failed' | 'skipped' | 'cancelled';
export interface Step { name: string; status: string; step_id?: number | string; expected?: unknown; actual?: unknown; duration?: number }
export interface CaseRecord { status: Status; steps: Step[]; duration?: number; message?: string; artifacts_dir?: string; plan_steps?: Record<string, string>; latest_plan_step_id?: string }
export interface BenchEvent { type: string; case_id?: string; status?: string; step_id?: number | string; plan_step_id?: string; name?: string; expected?: unknown; actual?: unknown; duration?: number; message?: string; artifacts_dir?: string; time?: string }
export function initialRecords(ids: string[]): Record<string, CaseRecord> { return Object.fromEntries(ids.map(id => [id, {status: 'pending', steps: []}])); }
export function applyEvents(records: Record<string, CaseRecord>, events: BenchEvent[]): Record<string, CaseRecord> {
  const next = Object.fromEntries(Object.entries(records).map(([id, record]) => [id, { ...record, steps: [...record.steps] }]));
  for (const event of events) {
    const row = event.case_id ? next[event.case_id] : undefined;
    if (row) {
      if (event.type === 'case_started') row.status = 'running';
      if (event.type === 'case_finished') Object.assign(row, event, { status: event.status as Status });
      if (event.type === 'plan_step' && event.plan_step_id && event.status) {
        row.plan_steps = { ...row.plan_steps, [event.plan_step_id]: event.status };
        row.latest_plan_step_id = event.plan_step_id;
      }
      if (event.type === 'step') {
        const step: Step = {name: event.name || '执行步骤', status: event.status || 'running', step_id: event.step_id, expected: event.expected, actual: event.actual, duration: event.duration};
        // 新事件用序号定位，避免同名嵌套或重复完成事件关联到其他步骤。
        const index = step.step_id !== undefined
          ? row.steps.findIndex(s => s.step_id === step.step_id)
          : row.steps.findLastIndex(s => s.step_id === undefined && s.name === step.name && s.status === 'running');
        if (index >= 0 && (step.step_id !== undefined || step.status !== 'running')) row.steps[index] = step; else row.steps.push(step);
      }
    }
    if (event.type === 'task_finished') for (const record of Object.values(next)) {
      if (record.status === 'running') record.status = event.status === 'cancelled' ? 'cancelled' : 'failed';
    }
  }
  return next;
}
export function counts(records: Record<string, CaseRecord>, ids: string[]) {
  const rows = ids.map(id => records[id]).filter(Boolean);
  return { total: ids.length, done: rows.filter(r => !['pending', 'running'].includes(r.status)).length,
    passed: rows.filter(r => r.status === 'passed').length, failed: rows.filter(r => r.status === 'failed').length };
}

export function planProgress(flow: readonly {id?: string}[], steps?: Record<string, string>) {
  const processed = flow.filter(({id}) => {
    const status = id ? steps?.[id] : undefined;
    return status === 'passed' || status === 'failed' || status === 'skipped';
  }).length;
  return { processed, total: flow.length };
}

export const labels: Record<string, string> = {pending:'未执行', running:'运行中', passed:'通过', failed:'失败', skipped:'已跳过', cancelled:'已停止'};
