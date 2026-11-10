import { useCallback, useEffect, useRef, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { ArrowDownToLine, Settings, X } from 'lucide-react';
import { call, choose, desktop } from './api';

type UpdateChannel = 'stable' | 'ci';
type UpdateInfo = { version: string; installed: boolean; enabled: boolean; channel: UpdateChannel };
type Candidate = { version: string; notes: string | null };
const channelLabels = { stable: 'Stable', ci: 'CI' };
const updateCheckInterval = 60 * 1000;

export function UpdateControls({ locked, onMaintenance, onImport }: {
  locked: boolean;
  onMaintenance: (active: boolean) => void;
  onImport: () => Promise<void>;
}) {
  const [info, setInfo] = useState<UpdateInfo>();
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [open, setOpen] = useState(false);
  const [operation, setOperation] = useState('');
  const [status, setStatus] = useState('');
  const [settingsError, setSettingsError] = useState('');
  const settings = useRef<HTMLDialogElement>(null);
  const updateButton = useRef<HTMLButtonElement>(null);
  const updating = useRef(false);
  const notifiedVersions = useRef(new Set<string>());
  const channel = info?.channel;
  const enabled = info?.installed && info.enabled;

  useEffect(() => {
    if (desktop) void invoke<UpdateInfo>('update_info').then(setInfo).catch(error => {
      console.error('读取测试上位机版本失败', error);
    });
  }, []);

  const check = useCallback(async (automatic: boolean) => {
    if (!channel || updating.current) return;
    updating.current = true;
    setOperation('正在检查更新…');
    if (!automatic) setStatus('');
    try {
      const available = await invoke<Candidate | null>('check_update');
      setCandidate(available);
      if (available) {
        const key = `${channel}:${available.version}`;
        if (!notifiedVersions.current.has(key)) setOpen(true);
        notifiedVersions.current.add(key);
        setStatus('');
      } else if (!automatic) {
        setStatus('所选频道暂无比当前版本更新的安装包。');
      }
    } catch (error) {
      setCandidate(null);
      if (!automatic) setStatus(String(error));
    } finally {
      updating.current = false;
      setOperation('');
    }
  }, [channel]);

  useEffect(() => {
    if (!enabled) return;
    void check(true);
    const timer = window.setInterval(() => void check(true), updateCheckInterval);
    return () => window.clearInterval(timer);
  }, [check, enabled]);

  async function selectChannel(channel: string) {
    if (!info || updating.current || (channel !== 'stable' && channel !== 'ci') || channel === info.channel) return;
    updating.current = true;
    setSettingsError('');
    setOperation('正在保存更新频道…');
    try {
      await invoke('set_update_channel', { channel });
      setCandidate(null);
      setStatus('');
      setInfo({ ...info, channel });
    } catch (error) {
      setSettingsError(String(error));
    } finally {
      updating.current = false;
      setOperation('');
    }
  }

  async function runMaintenance(message: string, run: () => Promise<void>) {
    if (locked || updating.current) return;
    updating.current = true;
    setOperation(message);
    setStatus('');
    onMaintenance(true);
    try {
      await run();
    } catch (error) {
      setStatus(String(error));
    } finally {
      updating.current = false;
      onMaintenance(false);
      setOperation('');
    }
  }

  if (!info) return null;
  if (!info.installed) return <div className="sidebar-tools"><span className="version">v{info.version} · 开发版</span></div>;

  return <div className="sidebar-tools">
    <button type="button" className="nav-item" aria-label="设置" onClick={() => settings.current?.showModal()}>
      <Settings size={18}/>设置
    </button>
    <div className="update-menu">
      <button ref={updateButton} type="button" className="nav-item" aria-label={candidate ? '版本与更新，有新版本' : '版本与更新'}
        aria-expanded={open} onClick={() => setOpen(!open)}>
        <ArrowDownToLine size={18}/>
        <span className="update-label">版本与更新<small>v{info.version} · {channelLabels[info.channel]}</small></span>
        {candidate && <span className="nav-count">新版</span>}
      </button>
      <span className="update-announcement" role="status">
        {open && candidate && `有新版本可更新 v${candidate.version}`}
      </span>
      {open && <section className="update-panel" aria-label="版本与更新">
        <div className="update-heading">
          <h2>{candidate ? '有新版本可更新' : '版本与更新'}</h2>
          <button type="button" className="icon-button" aria-label="关闭更新提示" onClick={() => {
            setOpen(false);
            updateButton.current?.focus();
          }}><X size={15}/></button>
        </div>
        <p>当前版本 v{info.version} · {channelLabels[info.channel]}</p>
        <button className="button" disabled={!enabled || !!operation} onClick={() => void check(false)}>
          检查更新
        </button>
        {!info.enabled && <p>当前版本未配置平台更新。</p>}
        {candidate && <div className="update-candidate">
          <strong>{channelLabels[info.channel]} 频道可安装 v{candidate.version}</strong>
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
      </section>}
    </div>
    <dialog ref={settings} className="settings-dialog" aria-labelledby="settings-title">
      <div className="update-heading">
        <h2 id="settings-title">设置</h2>
        <button type="button" className="icon-button" aria-label="关闭设置" onClick={() => settings.current?.close()}><X size={15}/></button>
      </div>
      <label className="field">
        <span>更新频道</span>
        <select value={info.channel} disabled={!enabled || !!operation}
          onChange={event => void selectChannel(event.currentTarget.value)}>
          <option value="stable">Stable（正式版）</option>
          <option value="ci">CI（持续集成版）</option>
        </select>
      </label>
      {!enabled && <p>当前版本未配置平台更新。</p>}
      {(operation || settingsError) && <p role="status">{operation || settingsError}</p>}
    </dialog>
  </div>;
}
