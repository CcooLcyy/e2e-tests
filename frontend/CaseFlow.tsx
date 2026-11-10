import type { CaseInfo } from './api';
import { labels } from './state';

const stepLabels: Record<string, string> = {
  skipped: '条件不满足',
  incomplete: '未完成',
  not_run: '未执行',
  pending: '待执行',
};

export function CaseFlow({ testCase, steps, caseStatus, finished = false }: {
  testCase?: Pick<CaseInfo, 'flow' | 'flow_blocked' | 'executable_flow'>;
  steps?: Record<string, string>;
  caseStatus?: string;
  finished?: boolean;
}) {
  if (!testCase?.flow?.length) {
    return <div className="empty"><span>{testCase ? '此用例尚未填写测试流程' : '选择用例查看测试流程'}</span></div>;
  }

  return <>
    {testCase.flow_blocked && <p className="notice info">{testCase.flow_blocked}</p>}
    {!testCase.executable_flow && finished && <p className="flow-record-note">无逐步记录</p>}
    <ol className="case-flow" aria-label="计划步骤">
      {testCase.flow.map(({id, action, expected}, index) => {
        const recorded = id ? steps?.[id] : undefined;
        const status = recorded === 'running' && finished
          ? caseStatus === 'cancelled' ? 'cancelled' : 'incomplete'
          : recorded || (finished ? 'not_run' : 'pending');
        const label = stepLabels[status] || labels[status] || status;
        return <li key={id ?? index} aria-current={status === 'running' ? 'step' : undefined}>
          <div className="flow-step-heading">
            <strong>{action}</strong>
            {testCase.executable_flow && <span className={`status ${status}`}><i />{label}</span>}
          </div>
          <p><span>预期结果</span>{expected}</p>
        </li>;
      })}
    </ol>
  </>;
}
