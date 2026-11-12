import { useEffect, useState } from 'react';
import { wasNotRun } from './state';
import type { ExecutionTiming } from './state';

function RunningTime({ startedAt, labeled }: { startedAt: number; labeled: boolean }) {
  const [clockOffset] = useState(() => Date.now() - performance.now());
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(performance.now()), 100);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, clockOffset + now - startedAt) / 1000;
  return <span className="execution-time" title="实时耗时">{labeled && '已运行 '}{seconds.toFixed(1)} s</span>;
}

export function ExecutionTime({ timing, status, labeled = false }: {
  timing?: ExecutionTiming;
  status?: string;
  labeled?: boolean;
}) {
  if (status === 'running') {
    const startedAt = Date.parse(timing?.started_at ?? '');
    if (Number.isFinite(startedAt)) return <RunningTime startedAt={startedAt} labeled={labeled}/>;
  } else if (!wasNotRun(status) && typeof timing?.duration === 'number' && Number.isFinite(timing.duration) && timing.duration >= 0) {
    return <span className="execution-time" title="最终耗时">{labeled && '耗时 '}{timing.duration.toFixed(3)} s</span>;
  }
  return <span className="execution-time">—</span>;
}
