import type { CaseInfo } from './api';

export function CaseFlow({ testCase }: {testCase?: CaseInfo}) {
  if (!testCase?.flow?.length) {
    return <div className="empty"><span>{testCase ? '此用例尚未填写测试流程' : '选择用例查看测试流程'}</span></div>;
  }

  return <>
    {testCase.flow_blocked && <p className="notice info">{testCase.flow_blocked}</p>}
    <ol className="case-flow" aria-label="计划步骤">
      {testCase.flow.map(({action, expected}, index) => <li key={index}>
        <strong>{action}</strong>
        <p><span>预期结果</span>{expected}</p>
      </li>)}
    </ol>
  </>;
}
