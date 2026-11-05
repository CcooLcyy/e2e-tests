/** 仅通过和失败构成有效判定，未执行、运行、跳过及停止不计入分母。 */
export function formatPassRate(passed: number, failed: number): string {
  const evaluated = passed + failed;
  return evaluated > 0 ? `${(passed / evaluated * 100).toFixed(1)}%` : '—';
}
