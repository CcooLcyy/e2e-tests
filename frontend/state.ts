export type Status = 'pending' | 'running' | 'passed' | 'failed' | 'skipped' | 'cancelled';
export interface ExecutionTiming { started_at?: string; duration?: number }
export type PlanStepTimings = Record<string, ExecutionTiming>;
export interface Step extends ExecutionTiming { name: string; status: string; step_id?: number | string; expected?: unknown; actual?: unknown }
export interface CaseRecord extends ExecutionTiming { status: Status; steps: Step[]; message?: string; artifacts_dir?: string; plan_steps?: Record<string, string>; plan_step_timings?: PlanStepTimings; latest_plan_step_id?: string }
export interface BenchEvent extends ExecutionTiming { type: string; case_id?: string; status?: string; step_id?: number | string; plan_step_id?: string; name?: string; expected?: unknown; actual?: unknown; message?: string; artifacts_dir?: string; time?: string; plan_steps?: Record<string, string>; plan_step_timings?: PlanStepTimings }

export function wasNotRun(status?: string, message?: string) {
  return status === 'pending' || status === 'not_run' || (status === 'skipped' && message === '未执行');
}

function eventTiming(event: BenchEvent, running: boolean, previous?: ExecutionTiming): ExecutionTiming {
  return {
    started_at: event.started_at ?? (running ? event.time : previous?.started_at),
    duration: running ? undefined : event.duration ?? previous?.duration,
  };
}

export function initialRecords(ids: string[]): Record<string, CaseRecord> { return Object.fromEntries(ids.map(id => [id, {status: 'pending', steps: []}])); }
export function applyEvents(records: Record<string, CaseRecord>, events: BenchEvent[]): Record<string, CaseRecord> {
  const next = Object.fromEntries(Object.entries(records).map(([id, record]) => [id, { ...record, steps: [...record.steps] }]));
  for (const event of events) {
    const row = event.case_id ? next[event.case_id] : undefined;
    if (row) {
      if (event.type === 'case_started') Object.assign(row, eventTiming(event, true), { status: 'running' });
      if (event.type === 'case_finished') {
        const timing = eventTiming(event, false, row);
        if (wasNotRun(event.status, event.message)) timing.duration = undefined;
        Object.assign(row, event, timing, { status: event.status as Status });
      }
      if (event.type === 'plan_step' && event.plan_step_id && event.status) {
        const id = event.plan_step_id;
        row.plan_steps = { ...row.plan_steps, [id]: event.status };
        const timing = event.status === 'skipped' ? {} : eventTiming(event, event.status === 'running', row.plan_step_timings?.[id]);
        row.plan_step_timings = { ...row.plan_step_timings, [id]: timing };
        row.latest_plan_step_id = id;
      }
      if (event.type === 'step') {
        const step: Step = {name: event.name || '执行步骤', status: event.status || 'running', step_id: event.step_id, expected: event.expected, actual: event.actual};
        // 新事件用序号定位，避免同名嵌套或重复完成事件关联到其他步骤。
        const index = step.step_id !== undefined
          ? row.steps.findIndex(s => s.step_id === step.step_id)
          : row.steps.findLastIndex(s => s.step_id === undefined && s.name === step.name && s.status === 'running');
        if (index >= 0 && (step.step_id !== undefined || step.status !== 'running')) {
          row.steps[index] = { ...step, ...eventTiming(event, step.status === 'running', row.steps[index]) };
        } else {
          row.steps.push({ ...step, ...eventTiming(event, step.status === 'running') });
        }
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
