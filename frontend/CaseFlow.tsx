import type { CaseInfo } from './api';
import { ExecutionTime } from './ExecutionTime';
import { labels } from './state';
import type { ExecutionTiming, PlanStepTimings, Step } from './state';

const stepLabels: Record<string, string> = {
  skipped: '条件不满足',
  incomplete: '未完成',
  not_run: '未执行',
  pending: '待执行',
};

function StepHeading({ action, status, timing }: { action: string; status?: string; timing?: ExecutionTiming }) {
  return <div className="flow-step-heading">
    <strong>{action}</strong>
    {status && <div className="flow-step-meta">
      <span className={`status ${status}`}><i />{stepLabels[status] || labels[status] || status}</span>
      <ExecutionTime timing={timing} status={status} labeled/>
    </div>}
  </div>;
}

function ExecutedSteps({ steps, finishedStatus }: { steps: Step[]; finishedStatus?: string }) {
  return <ol className="case-flow" aria-label="实际步骤">
    {steps.map((step, index) => {
      const status = step.status === 'running' && finishedStatus ? finishedStatus : step.status;
      return <li key={step.step_id ?? index} aria-current={status === 'running' ? 'step' : undefined}>
        <StepHeading action={step.name} status={status} timing={step}/>
      </li>;
    })}
  </ol>;
}

export function CaseFlow({ testCase, steps, timings, executedSteps, caseStatus, finished = false, followStepId }: {
  testCase?: Pick<CaseInfo, 'flow' | 'flow_blocked' | 'executable_flow'>;
  steps?: Record<string, string>;
  timings?: PlanStepTimings;
  executedSteps?: Step[];
  caseStatus?: string;
  finished?: boolean;
  followStepId?: string;
}) {
  const interruptedStatus = caseStatus === 'cancelled' ? 'cancelled' : 'incomplete';
  const recordedSteps = executedSteps?.length
    ? <ExecutedSteps steps={executedSteps} finishedStatus={finished ? interruptedStatus : undefined}/>
    : null;
  if (!testCase?.flow?.length) {
    return recordedSteps || <div className="empty"><span>{testCase ? '此用例尚未填写测试流程' : '选择用例查看测试流程'}</span></div>;
  }

  const showStepStatus = testCase.executable_flow || (testCase.flow_blocked && finished);
  return <>
    {testCase.flow_blocked && <p className="notice info">{testCase.flow_blocked}</p>}
    {!showStepStatus && finished && !recordedSteps && <p className="flow-record-note">无逐步记录</p>}
    <ol className="case-flow" aria-label="计划步骤">
      {testCase.flow.map(({id, action, expected, group}, index, flow) => {
        const recorded = id ? steps?.[id] : undefined;
        const status = recorded === 'running' && finished
          ? interruptedStatus
          : recorded || (finished ? 'not_run' : 'pending');
        return <li key={id ?? index} aria-current={status === 'running' ? 'step' : undefined}
          data-follow-step={id && id === followStepId ? true : undefined}>
          {group && group !== flow[index - 1]?.group && <h4 className="flow-step-group">{group}</h4>}
          <StepHeading action={action} status={showStepStatus ? status : undefined} timing={id ? timings?.[id] : undefined}/>
          <p><span>预期结果</span>{expected}</p>
        </li>;
      })}
    </ol>
    {recordedSteps && <details className="executed-steps" open={!testCase.executable_flow}>
      <summary>实际操作记录</summary>
      {recordedSteps}
    </details>}
  </>;
}
