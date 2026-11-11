import { useCallback, useEffect, useRef, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { ArrowDownToLine, Settings, X } from 'lucide-react';
import { call, choose, desktop } from './api';
import type { CasePackStatus, UpdateChannel } from './api';

type UpdateInfo = { version: string; installed: boolean; enabled: boolean; channel: UpdateChannel };
type CasePackUpdateInfo = { enabled: boolean; channel: UpdateChannel };
type Candidate = { id: string; version: string; notes: string | null; ready: boolean; downloadError?: string };
const channelLabels = { stable: 'Stable', ci: 'CI' };
const casePackSources = { downloaded: '平台下载', bundled: '安装包内置', checkout: '源码', legacy: '安装包内置' };
const updateCheckInterval = 60 * 1000;

export function UpdateControls({ locked, onMaintenance, onImport, casePack, casePackWorking }: {
  locked: boolean;
  onMaintenance: (active: boolean) => void;
  onImport: () => Promise<void>;
  casePack?: CasePackStatus;
  casePackWorking: boolean;
}) {
  const [info, setInfo] = useState<UpdateInfo>();
  const [casePackInfo, setCasePackInfo] = useState<CasePackUpdateInfo>();
  const [savingCasePackChannel, setSavingCasePackChannel] = useState(false);
  const [casePackSettingsStatus, setCasePackSettingsStatus] = useState('');
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [open, setOpen] = useState(false);
  const [operation, setOperation] = useState('');
  const [status, setStatus] = useState('');
  const [settingsStatus, setSettingsStatus] = useState('');
  const settings = useRef<HTMLDialogElement>(null);
  const updateButton = useRef<HTMLButtonElement>(null);
  const updating = useRef(false);
  const downloading = useRef(new Set<string>());
  const notifiedVersions = useRef(new Set<string>());
  const channel = info?.channel;
  const enabled = info?.installed && info.enabled;

  useEffect(() => {
    if (!desktop) return;
    void invoke<UpdateInfo>('update_info').then(setInfo).catch(error => {
      console.error('读取测试上位机版本失败', error);
    });
    void invoke<CasePackUpdateInfo>('case_pack_update_info').then(setCasePackInfo).catch(error => {
      console.error('读取用例包更新频道失败', error);
    });
  }, []);

  const check = useCallback(async (automatic: boolean) => {
    if (!channel || updating.current) return;
    updating.current = true;
    setOperation('正在检查更新…');
    if (!automatic) setSettingsStatus('');
    try {
      const available = await invoke<Candidate | null>('check_update');
      setCandidate(available);
      if (available) {
        const key = `${channel}:${available.version}`;
        if (!notifiedVersions.current.has(key)) {
          setOpen(true);
          setStatus('');
        }
        notifiedVersions.current.add(key);
        if (!automatic) setSettingsStatus(`发现新版 v${available.version}，可在侧栏查看。`);
        if (!available.ready && !downloading.current.has(available.id)) {
          setStatus('');
          downloading.current.add(available.id);
          void invoke<boolean>('download_update', { id: available.id }).then(ready => {
            if (ready) setCandidate(current => current?.id === available.id ? { ...current, ready } : current);
          }).catch(error => {
            setCandidate(current => current?.id === available.id ? { ...current, downloadError: String(error) } : current);
          }).finally(() => {
            downloading.current.delete(available.id);
          });
        }
      } else {
        setOpen(false);
        setStatus('');
        if (!automatic) setSettingsStatus('所选频道暂无比当前版本更新的安装包。');
      }
    } catch (error) {
      if (!automatic) setSettingsStatus(String(error));
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
    setSettingsStatus('');
    setOperation('正在保存更新频道…');
    try {
      await invoke('set_update_channel', { channel });
      setCandidate(null);
      setOpen(false);
      setStatus('');
      setInfo({ ...info, channel });
    } catch (error) {
      setSettingsStatus(String(error));
    } finally {
      updating.current = false;
      setOperation('');
    }
  }

  async function selectCasePackChannel(channel: string) {
    if (!casePackInfo?.enabled || casePackWorking || savingCasePackChannel ||
      (channel !== 'stable' && channel !== 'ci') || channel === casePackInfo.channel) return;
    setSavingCasePackChannel(true);
    setCasePackSettingsStatus('');
    try {
      await invoke('set_case_pack_update_channel', { channel });
      setCasePackInfo({ ...casePackInfo, channel });
      setCasePackSettingsStatus(`已选择 ${channelLabels[channel]}。点击「刷新用例」检查该频道。`);
    } catch (error) {
      setCasePackSettingsStatus(String(error));
    } finally {
      setSavingCasePackChannel(false);
    }
  }

  async function runMaintenance(message: string, run: () => Promise<void>, setResult = setStatus) {
    if (locked || updating.current) return;
    updating.current = true;
    setOperation(message);
    setResult('');
    onMaintenance(true);
    try {
      await run();
      return true;
    } catch (error) {
      setResult(String(error));
      return false;
    } finally {
      updating.current = false;
      onMaintenance(false);
      setOperation('');
    }
  }

  if (!info) return null;
  return <div className="sidebar-tools">
    <button type="button" className="nav-item" aria-label="设置" onClick={() => settings.current?.showModal()}>
      <Settings size={18}/>设置
    </button>
    <div className="update-menu">
      {candidate && <button ref={updateButton} type="button" className="nav-item" aria-label="版本与更新，有新版本"
        aria-expanded={open} onClick={() => setOpen(!open)}>
        <ArrowDownToLine size={18}/>
        <span className="update-label">版本与更新<small>v{candidate.version}</small></span>
        <span className="nav-count">新版</span>
      </button>}
      <span className="update-announcement" role="status">
        {open && candidate && (candidate.ready ? '更新包已下载，可以安装' : `发现新版 v${candidate.version}`)}
      </span>
      {open && candidate && <section className="update-panel" aria-label="版本与更新">
        <div className="update-heading">
          <h2>有新版本可更新</h2>
          <button type="button" className="icon-button" aria-label="关闭更新提示" onClick={() => {
            setOpen(false);
            updateButton.current?.focus();
          }}><X size={15}/></button>
        </div>
        <div className="update-candidate">
          <strong>{channelLabels[info.channel]} 频道 · v{candidate.version}</strong>
          {candidate.notes && <p>{candidate.notes}</p>}
          {candidate.ready ? <p>更新包已下载并验证，可以安装。</p> : <p>
            {candidate.downloadError ? `下载或校验失败，下次检查时重试。${candidate.downloadError}` : '正在后台准备更新包…'}
          </p>}
          <button className="button primary" disabled={!candidate.ready || locked || !!operation} onClick={() => {
            void runMaintenance('等待确认或正在启动安装…', async () => {
              const installed = await invoke<boolean>('install_update', { id: candidate.id });
              if (!installed) setStatus('已取消安装。');
            }).then(completed => {
              if (completed === false) void check(true);
            });
          }}>安装更新</button>
          {candidate.ready && locked && !operation && <p>测试或其他操作完成后可以安装。</p>}
        </div>
        {(operation || status) && <p role="status">{operation || status}</p>}
      </section>}
    </div>
    <dialog ref={settings} className="settings-dialog" aria-labelledby="settings-title">
      <div className="update-heading">
        <h2 id="settings-title">设置</h2>
        <button type="button" className="icon-button" aria-label="关闭设置" onClick={() => settings.current?.close()}><X size={15}/></button>
      </div>
      <p>当前版本 v{info.version}{!info.installed && ' · 开发版'}</p>
      {info.installed && <>
        <label className="field">
          <span>程序更新频道</span>
          <select value={info.channel} disabled={!enabled || !!operation}
            onChange={event => void selectChannel(event.currentTarget.value)}>
            <option value="stable">Stable（正式版）</option>
            <option value="ci">CI（持续集成版）</option>
          </select>
        </label>
        <div className="settings-actions">
          <button className="button" disabled={!enabled || !!operation} onClick={() => void check(false)}>
            检查更新
          </button>
          <button className="button" disabled={locked || !!operation} onClick={() => {
            void runMaintenance('正在导入旧工作区…', async () => {
              const path = await choose('directory');
              if (!path) return;
              const result = await call<{ configs: number; reports: number; skipped: number }>('migrate_workspace', { path });
              await onImport();
              setSettingsStatus(`已导入 ${result.configs} 项配置、${result.reports} 份历史报告。已保留 ${result.skipped} 项现有数据。`);
            }, setSettingsStatus);
          }}>导入旧工作区</button>
        </div>
        {!enabled && <p>当前版本未配置平台更新。</p>}
        {(operation || settingsStatus) && <p role="status">{operation || settingsStatus}</p>}
        {casePack && <p>当前用例包{casePack.version && ` v${casePack.version}`} · {casePackSources[casePack.source]}</p>}
        {casePackInfo && <label className="field">
          <span>用例包更新频道</span>
          <select value={casePackInfo.channel} disabled={!casePackInfo.enabled || locked || casePackWorking || savingCasePackChannel}
            onChange={event => void selectCasePackChannel(event.currentTarget.value)}>
            <option value="stable">Stable（正式用例包）</option>
            <option value="ci">CI（持续集成用例包）</option>
          </select>
        </label>}
        {casePackInfo && !casePackInfo.enabled && <p>当前版本未配置平台用例更新。</p>}
        {casePackSettingsStatus && <p role="status">{casePackSettingsStatus}</p>}
      </>}
    </dialog>
  </div>;
}
