import { useRef, useState } from 'react';
import { Trash2, X } from 'lucide-react';
import { call } from './api';

interface SkippedRun {
  id: string;
  reason: string;
}

interface CleanupPreview {
  token: string;
  count: number;
  bytes: number;
  skipped: SkippedRun[];
}

interface CleanupResult {
  deleted: number;
  deleted_ids: string[];
  failures: SkippedRun[];
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KiB', 'MiB', 'GiB', 'TiB'];
  const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length);
  return `${(bytes / 1024 ** unit).toLocaleString('zh-CN', {maximumFractionDigits: 1})} ${units[unit - 1]}`;
}

export function HistoryCleanup({disabled, onCleaned}: {
  disabled: boolean;
  onCleaned: (deletedIds: string[]) => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [days, setDays] = useState(30);
  const [preview, setPreview] = useState<CleanupPreview>();
  const [result, setResult] = useState<CleanupResult>();
  const [operation, setOperation] = useState<'preview' | 'delete'>();
  const [error, setError] = useState('');

  function open() {
    setPreview(undefined);
    setResult(undefined);
    setError('');
    dialog.current?.showModal();
  }

  async function scan() {
    setPreview(undefined);
    setResult(undefined);
    setError('');
    setOperation('preview');
    try {
      setPreview(await call<CleanupPreview>('preview_history_cleanup', {days}));
    } catch (failure) {
      setError(String(failure));
    } finally {
      setOperation(undefined);
    }
  }

  async function remove() {
    if (!preview?.count) return;
    const {token} = preview;
    setOperation('delete');
    setError('');
    setPreview(undefined);
    try {
      const cleaned = await call<CleanupResult>('apply_history_cleanup', {token});
      setResult(cleaned);
      await onCleaned(cleaned.deleted_ids);
    } catch (failure) {
      setError(String(failure));
    } finally {
      setOperation(undefined);
    }
  }

  const skipped = result?.failures ?? preview?.skipped ?? [];
  return <>
    <button className="button" type="button" disabled={disabled} onClick={open}>
      <Trash2 size={14}/>清理测试记录
    </button>
    <dialog ref={dialog} className="settings-dialog" aria-labelledby="history-cleanup-title"
      onCancel={event => { if (operation) event.preventDefault(); }}>
      <div className="update-heading">
        <h2 id="history-cleanup-title">清理测试记录</h2>
        <button type="button" className="icon-button" aria-label="关闭" disabled={!!operation}
          onClick={() => dialog.current?.close()}><X size={15}/></button>
      </div>
      <label className="field">
        <span>清理范围</span>
        <select value={days} disabled={!!operation} onChange={event => {
          setDays(Number(event.target.value));
          setPreview(undefined);
          setResult(undefined);
          setError('');
        }}>
          <option value={7}>7 天前</option>
          <option value={30}>30 天前</option>
          <option value={90}>90 天前</option>
          <option value={0}>全部已结束任务</option>
        </select>
      </label>
      <p>将永久删除所选任务的报告、截图、日志和测试副本。</p>
      {operation && <p role="status">{operation === 'preview' ? '正在统计测试记录…' : '正在删除测试记录…'}</p>}
      {preview && <p role="status">{preview.count ? `可清理 ${preview.count} 条记录，约 ${formatSize(preview.bytes)}。` : '没有可清理的测试记录。'}</p>}
      {result && <p role="status">已删除 {result.deleted} 条记录。</p>}
      {skipped.length > 0 && <details className="history-cleanup-skipped">
        <summary>未清理 {skipped.length} 条记录</summary>
        <ul>{skipped.map(item => <li key={item.id}>{item.id} · {item.reason}</li>)}</ul>
      </details>}
      {error && <p role="alert" className="case-error">{error}</p>}
      <div className="settings-actions">
        <button type="button" className="button" disabled={disabled || !!operation} onClick={() => void scan()}>预览</button>
        {preview && preview.count > 0 && <button type="button" className="button danger"
          disabled={disabled || !!operation} onClick={() => void remove()}>永久删除 {preview.count} 条记录</button>}
      </div>
    </dialog>
  </>;
}
