import type { Detail, Shot } from './api';

// 业务截图说明只取已经整理的业务视图，原始异常和全部证据留在诊断资料。
export function businessScreenshots(detail: Pick<Detail,'business'|'screenshots'> | null | undefined): Shot[] {
  const view = detail?.business;
  if (!view) return [];
  return view.key_screenshots.flatMap(name => {
    const shot = detail?.screenshots.find(item => item.name === name);
    if (!shot) return [];
    const check = view.checks.find(item => item.step_id != null && shot.step_id != null && item.step_id === shot.step_id);
    return [{...shot,description:check?.name || view.goal,expected:check?.expected || '未记录预期现象',
      actual:check?.actual || '本图用于记录界面现场',business_result:view.result,
      error:'',annotation_error:'',annotations:[],
      annotated_data_url:shot.annotation_format==='business-v1'?shot.annotated_data_url:''}];
  });
}

export function resultSummary(status?: string): string {
  if (status === 'running') return '正在执行测试，结束后展示结果和关键截图。';
  if (status === 'failed') return '本项测试未通过，具体原因请查看诊断资料。';
  if (status === 'cancelled') return '本项测试已停止，已产生的证据仍保存在诊断资料中。';
  if (status === 'passed') return '本项测试已通过。';
  if (status === 'skipped') return '本项测试未执行。';
  return '选择用例并开始测试后，可在这里查看结果。';
}
