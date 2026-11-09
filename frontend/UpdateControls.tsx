import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { call, choose, desktop } from './api';

type UpdateInfo = { version: string; installed: boolean; enabled: boolean };
type Candidate = { version: string; notes: string | null };

export function UpdateControls({ locked, onMaintenance, onImport }: {
  locked: boolean;
  onMaintenance: (active: boolean) => void;
  onImport: () => Promise<void>;
}) {
  const [info, setInfo] = useState<UpdateInfo>();
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [operation, setOperation] = useState('');
  const [status, setStatus] = useState('');

  useEffect(() => {
    if (desktop) void invoke<UpdateInfo>('update_info').then(setInfo).catch(error => {
      console.error('读取测试上位机版本失败', error);
    });
  }, []);

  async function check() {
    setCandidate(null);
    setOperation('正在检查更新…');
    setStatus('');
    try {
      const available = await invoke<Candidate | null>('check_update');
      setCandidate(available);
      if (!available) setStatus('当前已是最新版本。');
    } catch (error) {
      setStatus(String(error));
    } finally {
      setOperation('');
    }
  }

  async function runMaintenance(message: string, run: () => Promise<void>) {
    setOperation(message);
    setStatus('');
    onMaintenance(true);
    try {
      await run();
    } catch (error) {
      setStatus(String(error));
    } finally {
      onMaintenance(false);
      setOperation('');
    }
  }

  if (!info) return null;
  if (!info.installed) return <span className="local-label">v{info.version} · 开发版</span>;

  return <details className="update-menu">
    <summary className="button">版本与更新 · v{info.version}</summary>
    <div className="update-panel">
      <button className="button" disabled={!info.enabled || !!operation} onClick={() => void check()}>
        检查更新
      </button>
      {!info.enabled && <p>当前版本未配置平台更新。</p>}
      {candidate && <div className="update-candidate">
        <strong>可安装 v{candidate.version}</strong>
        {candidate.notes && <p>{candidate.notes}</p>}
        <button className="button primary" disabled={locked || !!operation} onClick={() => {
          void runMaintenance('等待确认或正在下载并验证更新包…', async () => {
            const installed = await invoke<boolean>('install_update', { version: candidate.version });
            if (!installed) setStatus('已取消安装。');
          });
        }}>安装更新</button>
        {locked && !operation && <p>测试或其他操作完成后可以安装。</p>}
      </div>}
      <button className="button" disabled={locked || !!operation} onClick={() => {
        void runMaintenance('正在导入旧工作区…', async () => {
          const path = await choose('directory');
          if (!path) return;
          const result = await call<{ configs: number; reports: number; skipped: number }>('migrate_workspace', { path });
          await onImport();
          setStatus(`已导入 ${result.configs} 项配置、${result.reports} 份历史报告。已保留 ${result.skipped} 项现有数据。`);
        });
      }}>导入旧工作区</button>
      {(operation || status) && <p role="status">{operation || status}</p>}
    </div>
  </details>;
}
