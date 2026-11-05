import { useEffect, useRef, useState } from 'react';
import { Activity, ArrowDownToLine, ArrowRight, Check, CheckCheck, ChevronRight, CircleHelp, Clock3, FileText, FolderOpen, History as HistoryIcon, Layers3, ListChecks, LoaderCircle, Play, RefreshCw, Search, Settings2, ShieldCheck, Square, Terminal, TestTube2, X } from 'lucide-react';
import { call, choose, desktop, openArtifact } from './api';
import type { Bootstrap, CaseInfo, Detail, Environment, History, Master104Check, Poll } from './api';
import { applyEvents, counts, initialRecords, labels } from './state';
import type { BenchEvent, CaseRecord } from './state';
import { BusinessCaseDetails } from './BusinessCaseDetails';
import { ModbusEnvironment } from './ModbusEnvironment';
import { withModbusDefaults } from './modbus-environment';
import { caseGroup, summarizeGroups, visibleCases, toggleCases, selectionState, selectedCaseIds, reconcileSelection, groupedSections, toggleExpandedGroup } from './case-groups';

type Page = 'execution' | 'environment' | 'history';
const environmentSections = [
  ['environment-basics','基础信息','程序与驱动'],
  ['environment-isolation','运行与隔离','工作区与等待时间'],
  ['environment-annotation','截图标注','操作位置与说明'],
  ['environment-slavesim','slaveSim 模拟器','管理地址与连接检查'],
  ['environment-master104','104 模拟主站','按用例需要启用'],
  ['environment-modbus','Modbus RTU','运行时跟随模拟器配置'],
] as const;
const emptyEnvironment: Environment = withModbusDefaults({name:'本机测试环境',app_path:'',driver_path:'',mode:'fresh',workspace_path:'',baseline_path:'',timeout:60,close_app:true,slavesim_url:'http://192.168.1.219:16000',annotation_enabled:true,master104_enabled:false,master104_path:'',master104_backend:'win32',master104_timeout:10} as Environment);
const duration = (value?: number) => value === undefined ? '—' : `${value.toFixed(2)} s`;
function Status({ status, text = true }: {status: string; text?: boolean}) { return <span className={`status ${status}`}><i />{text && (labels[status] || status)}{status === 'running' && <LoaderCircle size={12} className="spin"/>}</span>; }

function SelectionCheckbox({ids, selected, disabled, label, onChange}: {
  ids: string[]; selected: ReadonlySet<string>; disabled: boolean; label: string; onChange: (checked: boolean) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const state = selectionState(ids, selected);
  useEffect(() => { if (input.current) input.current.indeterminate = state.indeterminate; }, [state.indeterminate]);
  return <input ref={input} type="checkbox" aria-label={label} aria-checked={state.indeterminate ? 'mixed' : state.checked}
    checked={state.checked} disabled={disabled || !ids.length} onChange={event => onChange(event.target.checked)}/>;
}

export default function App() {
  const [page,setPage] = useState<Page>('execution');
  const [ready,setReady] = useState(false);
  const [loading,setLoading] = useState(false);
  const [error,setError] = useState('');
  const [notice,setNotice] = useState('');
  const [environmentTarget,setEnvironmentTarget] = useState('');
  const [environment,setEnvironment] = useState<Environment>(emptyEnvironment);
  const [cases,setCases] = useState<CaseInfo[]>([]);
  const [history,setHistory] = useState<History[]>([]);
  const [records,setRecords] = useState<Record<string,CaseRecord>>({});
  const [selected,setSelected] = useState<Set<string>>(new Set());
  const [focused,setFocused] = useState('');
  const [search,setSearch] = useState('');
  const [expandedGroups,setExpandedGroups] = useState<Set<string>>(new Set());
  const [searchCollapsed,setSearchCollapsed] = useState<Set<string>>(new Set());
  const [active,setActive] = useState(false);
  const [stopping,setStopping] = useState(false);
  const [busy,setBusy] = useState(false);
  const [master104Checking,setMaster104Checking] = useState(false);
  const [master104Snapshot,setMaster104Snapshot] = useState<Record<string,unknown>|null>(null);
  const [runDir,setRunDir] = useState('');
  const [taskIds,setTaskIds] = useState<string[]>([]);
  const [taskStatus,setTaskStatus] = useState('pending');
  const [logs,setLogs] = useState<BenchEvent[]>([]);
  const [tab,setTab] = useState<'steps'|'logs'>('steps');
  const [detail,setDetail] = useState<Detail|null>(null);
  const [historyFocus,setHistoryFocus] = useState('');
  const [historyDetail,setHistoryDetail] = useState<{summary: Record<string,unknown>; task: {environment?:Environment}; cases: Array<Record<string,unknown> & Partial<Detail>>; diagnostics_path?: string; business_report_path?: string}|null>(null);
  const polling = useRef(false);
  const starting = useRef(false);
  const checkingMaster104 = useRef(false);
  const master104Polling = useRef(false);
  const disabled = active || busy;

  function goToEnvironment(id:string) {
    setPage('environment');
    setEnvironmentTarget(id);
    console.info('定位测试环境配置：',id);
  }
  useEffect(()=>{
    if(page!=='environment'||!environmentTarget)return;
    const target=document.getElementById(environmentTarget);
    if(target){
      let group=target.closest('details');
      while(group){
        group.open=true;
        group=group.parentElement?.closest('details')||null;
      }
      target.scrollIntoView({block:'center'});
      target.focus({preventScroll:true});
    }
    setEnvironmentTarget('');
  },[page,environmentTarget]);

  async function refreshHistory() { const result = await call<History[]>('history'); setHistory(result); }
  async function bootstrap() {
    if (!desktop) return;
    setLoading(true); setError('');
    try { const data = await call<Bootstrap>('bootstrap'); setEnvironment(withModbusDefaults({...emptyEnvironment,...data.environment}));setCases(data.cases);setHistory(data.history);setRecords(initialRecords(data.cases.map(c=>c.id)));setSelected(new Set(data.cases.map(c=>c.id)));setFocused(data.cases[0]?.id || '');setReady(true); }
    catch(e) {setError(String(e));} finally {setLoading(false);}
  }
  useEffect(()=>{void bootstrap();},[]);
  useEffect(()=>{
    if (!ready) return;
    const timer=setInterval(async()=>{
      if(polling.current || starting.current) return;
      polling.current=true;
      try {
        const result=await call<Poll>('poll');setActive(result.active);
        if(result.run_dir) setRunDir(result.run_dir);
        if(result.events.length) {
          setRecords(old=>applyEvents(old,result.events));setLogs(old=>[...old,...result.events].slice(-1000));
          for(const event of result.events) {
            if(event.type==='case_started' && event.case_id) setFocused(event.case_id);
            if(event.type==='task_finished') {setTaskStatus(event.status || 'failed');setStopping(false);void refreshHistory().catch(e=>setError(String(e)));}
            if(event.type==='error') {
              if(event.message==='用户停止了测试任务') setNotice('测试已停止，已产生的结果和现场资料会保留。');
              else setError('测试执行遇到异常，详细记录保存在诊断资料中。');
            }
          }
        }
      }catch(e){setError(String(e));}finally{polling.current=false;}
    },400);
    return()=>clearInterval(timer);
  },[ready]);
  const record=records[focused];
  useEffect(()=>{
    setDetail(null);setTab('steps');
    const path=record?.artifacts_dir;
    if(!path)return;
    let valid=true;
    call<Detail>('case_detail',{artifacts_dir:path}).then(data=>{if(valid)setDetail(data);}).catch(e=>{if(valid)setError(String(e));});
    return()=>{valid=false;};
  },[focused,record?.artifacts_dir]);
  useEffect(()=>{
    setHistoryDetail(null);
    if(!historyFocus)return;
    let valid=true;
    call<NonNullable<typeof historyDetail>>('history_detail',{run_dir:historyFocus}).then(data=>{if(valid)setHistoryDetail(data);}).catch(e=>{if(valid)setError(String(e));});
    return()=>{valid=false;};
  },[historyFocus]);

  async function refreshCases(){setLoading(true);try{const list=await call<CaseInfo[]>('discover');setSelected(old=>reconcileSelection(cases,list,old));setCases(list);setExpandedGroups(old=>new Set(list.map(caseGroup).filter(name=>old.has(name))));setSearchCollapsed(new Set());setRecords(initialRecords(list.map(c=>c.id)));setTaskIds([]);setTaskStatus('pending');setFocused(list[0]?.id||'');}catch(e){setError(String(e));}finally{setLoading(false);}}
  async function start(){
    if(disabled || !selected.size)return;
    setBusy(true);starting.current=true;setError('');setNotice('');
    const ids=selectedCaseIds(cases,selected);
    try{await call('save_environment',{environment});const result=await call<{run_dir:string}>('start',{environment,case_ids:ids});setRecords(initialRecords(cases.map(c=>c.id)));setLogs([]);setDetail(null);setTaskIds(ids);setRunDir(result.run_dir);setTaskStatus('running');setActive(true);setStopping(false);setPage('execution');}
    catch(e){setError(String(e));}finally{starting.current=false;setBusy(false);}
  }
  async function stop(){setStopping(true);try{await call('stop');}catch(e){setError(String(e));setStopping(false);}}
  async function save(){setBusy(true);try{setEnvironment(withModbusDefaults({...emptyEnvironment,...await call<Environment>('save_environment',{environment})}));setNotice('测试环境已保存，下次启动会自动加载。');}catch(e){setError(String(e));}finally{setBusy(false);}}
  async function load(){try{const path=await choose('environment');if(path){setEnvironment(withModbusDefaults({...emptyEnvironment,...await call<Environment>('load_environment',{path})}));setNotice('环境配置已载入，保存后生效。');}}catch(e){setError(String(e));}}
  async function pick(key:'app_path'|'driver_path'|'workspace_path'|'baseline_path'|'master104_path'){try{const path=await choose(key==='app_path'||key==='driver_path'||key==='master104_path'?'exe':'directory');if(path){setEnvironment(old=>({...old,[key]:path}));if(key==='master104_path')setMaster104Snapshot(null);}}catch(e){setError(String(e));}}
  async function open(path:string){try{await openArtifact(path);}catch(e){setError(String(e));}}
  async function slavesimAction(method: 'open_slavesim' | 'check_slavesim') {
    if(disabled || !environment.slavesim_url.trim())return;
    setBusy(true);setError('');setNotice('');
    try {
      const result=await call<{message:string}>(method,{url:environment.slavesim_url});
      setNotice(result.message);
      console.info(method==='open_slavesim'?'已请求打开 slaveSim 管理页':'已完成 slaveSim 连接检查');
    } catch(e) {setError(String(e));} finally {setBusy(false);}
  }
  async function checkMaster104() {
    if(disabled || checkingMaster104.current || !environment.master104_enabled)return;
    checkingMaster104.current=true;setBusy(true);setMaster104Snapshot(null);setError('');setNotice('');
    try {
      await call<Master104Check>('check_master104',{environment});
      setMaster104Checking(true);
    } catch(e) {setError(String(e));checkingMaster104.current=false;setBusy(false);}
  }
  useEffect(()=>{
    if(!master104Checking)return;
    const timer=setInterval(async()=>{
      if(master104Polling.current)return;
      master104Polling.current=true;
      try {
        const result=await call<Master104Check>('poll_master104');
        if(!result.checking){
          setMaster104Snapshot(result.snapshot||null);
          setNotice(result.message||'已读取主站窗口；尚未验证 IEC104 协议连接或业务效果。');
          console.info('已完成 104 主站只读窗口检查');
          setMaster104Checking(false);checkingMaster104.current=false;setBusy(false);
        }
      } catch(e) {setError(String(e));setMaster104Checking(false);checkingMaster104.current=false;setBusy(false);}
      finally {master104Polling.current=false;}
    },300);
    return()=>clearInterval(timer);
  },[master104Checking]);
  const stats=counts(records,taskIds.length?taskIds:cases.map(c=>c.id));
  const progress=stats.total?Math.round(stats.done/stats.total*100):0;
  const currentCase=cases.find(c=>c.id===focused);
  const visible=visibleCases(cases,'',search);
  const groups=summarizeGroups(cases,selected,records);
  const sections=groupedSections(cases,selected,records,expandedGroups,search,searchCollapsed);
  function toggleGroup(name: string) {
    if(search.trim()) setSearchCollapsed(old=>toggleExpandedGroup(old,name));
    else setExpandedGroups(old=>toggleExpandedGroup(old,name));
    console.info('切换用例组展开状态：',name);
  }
  function expandAll(open: boolean) {
    if(search.trim()) setSearchCollapsed(new Set(open?[]:groups.map(item=>item.name)));
    else setExpandedGroups(new Set(open?groups.map(item=>item.name):[]));
    console.info(open?'展开全部用例组':'折叠全部用例组');
  }
  function chooseCases(ids: string[], checked: boolean) { setSelected(old=>toggleCases(old,ids,checked)); console.info('批量选择用例：', ids.length, checked ? '选中' : '取消'); }

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-symbol"><TestTube2 size={22}/></span><div>测试上位机<small>MSKDSP TESTBENCH</small></div></div>
      <div className="nav-caption">工作空间</div>
      <nav>{([
        ['execution','测试执行',ListChecks],['environment','测试环境',Settings2],['history','历史报告',HistoryIcon],
      ] as const).map(([id,title,Icon])=><button key={id} data-testid={`nav-${id}`} className={page===id?'nav-item selected':'nav-item'} onClick={()=>setPage(id)}><Icon size={18}/>{title}{id==='execution'&&<span className="nav-count">{cases.length}</span>}</button>)}</nav>
      <div className="sidebar-bottom"><span className={`connection-dot ${ready?'online':''}`}/><div>{ready?'本地执行内核已连接':'等待本地连接'}<small>所有测试数据保存在本机</small></div><ShieldCheck size={17}/></div>
      <div className="version">DESKTOP EDITION <span>v0.1.0</span></div>
    </aside>

    <main className="workspace">
      <header className="topbar"><div className="breadcrumb">工作空间 <ChevronRight size={14}/><span>{{execution:'测试执行',environment:'测试环境',history:'历史报告'}[page]}</span></div><div className="topbar-right"><span className="local-label"><span className="tiny-dot"/>本地工作区</span><span className="avatar">M</span></div></header>
      <div className={page==='environment'?'page-scroll environment-page-scroll':'page-scroll'}>
        <section className="page-title"><div><div className="eyebrow">{page==='execution'?'TEST RUNNER':page==='environment'?'ENVIRONMENT':'REPORTS'}</div><h1>{{execution:'测试执行',environment:'测试环境',history:'历史报告'}[page]}</h1><p>{{execution:'选择测试用例，验证系统的每一个关键流程。',environment:'管理上位机运行环境，保持每次测试可控、可重复。',history:'回溯每次测试结果，查看执行记录与问题证据。'}[page]}</p></div>{page==='execution'&&<div className="title-actions"><button className="button" disabled={!ready||disabled||loading} onClick={()=>void refreshCases()} data-testid="cases-refresh"><RefreshCw size={15} className={loading?'spin':''}/>刷新用例</button>{active?<button className="button danger" data-testid="run-stop" disabled={stopping} onClick={()=>void stop()}>{stopping?<LoaderCircle size={15} className="spin"/>:<Square size={13}/>} {stopping?'正在停止…':'停止测试'}</button>:<button className="button primary" data-testid="run-start" disabled={!ready||disabled||loading||!selected.size} onClick={()=>void start()}>{busy?<LoaderCircle size={16} className="spin"/>:<Play size={15} fill="currentColor"/>}开始测试<span className="button-count">{selected.size}</span></button>}</div>}</section>
        {!desktop&&<div className="notice info"><CircleHelp size={18}/><div><strong>请使用桌面版测试上位机</strong><p>浏览器仅用于界面开发。连接测试内核、执行用例和选择本机文件需要通过 Tauri 桌面窗口。</p></div></div>}
        {error&&<div className="notice error" role="alert" data-testid="error-message"><Activity size={17}/><div className="notice-content"><strong>操作未完成</strong><p>{error}</p></div><button onClick={()=>setError('')} aria-label="关闭错误提示"><X size={16}/></button></div>}
        {notice&&<div className="notice success" role="status"><CheckCheck size={17}/><span>{notice}</span><button onClick={()=>setNotice('')} aria-label="关闭提示"><X size={16}/></button></div>}
        {loading&&!ready&&<div className="loading-state"><LoaderCircle className="spin"/>正在连接本地测试内核并发现用例…</div>}

        {page==='execution'&&<>
          <div className="environment-strip"><div className="environment-icon"><Layers3 size={20}/></div><div className="strip-name"><span>当前测试环境</span><strong>{environment.name}</strong></div><span className="soft-tag">{environment.mode==='fresh'?'全新独立副本':'固定测试工作区'}</span><div className="strip-divider"/><span className="strip-hint"><Clock3 size={14}/>等待 {environment.timeout} 秒</span><button className="text-button" onClick={()=>setPage('environment')}>环境设置<ArrowRight size={14}/></button></div>
          <div className="stat-grid"><Stat label="用例总数" value={stats.total} icon={<ListChecks size={18}/>} hint="本次测试范围"/><Stat label="已完成" value={stats.done} icon={<Activity size={18}/>} hint={`${progress}% 已完成`} accent="blue"/><Stat label="通过" value={stats.passed} icon={<CheckCheck size={18}/>} hint="符合预期结果" accent="green"/><Stat label="失败" value={stats.failed} icon={<X size={18}/>} hint="需要进一步检查" accent="red"/></div>
          <div className="execution-grid">
            <section className="panel case-panel"><div className="panel-heading"><div><h2>测试用例 <span>{cases.length}</span></h2><p>按顺序执行所选用例</p></div><span className={`task-badge ${taskStatus}`} data-testid="task-status">{stopping?'正在停止':labels[taskStatus]||taskStatus}</span></div>
              <div className="case-toolbar"><label className="search"><Search size={15}/><input value={search} onChange={e=>{setSearch(e.target.value);setSearchCollapsed(new Set());}} placeholder="搜索分组、用例名称或说明" aria-label="搜索测试用例"/></label><span className="selection-count">匹配 {visible.length} 项 · 已选 {selected.size} 项</span></div>
              <div className="case-list-actions"><span>{sections.length} 个分组</span><button className="text-button" disabled={!sections.length} data-testid="groups-expand-all" onClick={()=>expandAll(true)}>全部展开</button><button className="text-button" disabled={!sections.length} data-testid="groups-collapse-all" onClick={()=>expandAll(false)}>全部折叠</button></div>
              <div className="case-table"><div className="table-head"><label><SelectionCheckbox ids={visible.map(item=>item.id)} selected={selected} disabled={disabled} label="选择当前搜索结果" onChange={checked=>chooseCases(visible.map(item=>item.id),checked)}/></label><span>用例名称</span><span>执行状态</span><span>耗时</span></div>
              {sections.map(section=>{const contentId=`group-cases-${cases.findIndex(item=>caseGroup(item)===section.name)}`;return <section className="case-group-section" key={section.name}>
                <div className="case-group-heading" data-testid="case-group" data-group={section.name}>
                  <SelectionCheckbox ids={section.ids} selected={selected} disabled={disabled} label={`选择整组${section.name}`} onChange={checked=>chooseCases(section.ids,checked)}/>
                  <button className="case-group-toggle" type="button" data-testid="case-group-toggle" data-group={section.name} aria-expanded={section.open} aria-controls={contentId} onClick={()=>toggleGroup(section.name)}>
                    <ChevronRight size={15}/><span className="group-title">{section.name}<strong>{section.total}</strong></span>
                    <span className="group-summary">已选 {section.selected}/{section.total}<span className="group-passed">通过 {section.passed}</span><span className="group-failed">失败 {section.failed}</span></span>
                  </button>
                </div>
                <div id={contentId} hidden={!section.open} data-testid="case-group-content">
                  {section.open&&section.cases.map(item=><div key={item.id} role="button" tabIndex={0} data-testid="case-row" data-case-id={item.id} data-status={records[item.id]?.status||'pending'} className={`case-row ${focused===item.id?'focused':''}`} onClick={()=>setFocused(item.id)} onKeyDown={e=>{if(e.key==='Enter')setFocused(item.id);}}><label onClick={e=>e.stopPropagation()}><input aria-label={`选择${item.name}`} type="checkbox" disabled={disabled} checked={selected.has(item.id)} onChange={e=>chooseCases([item.id],e.target.checked)}/></label><div className="case-name"><span className="case-number">{String(cases.findIndex(entry=>entry.id===item.id)+1).padStart(2,'0')}</span><span><strong>{item.name.replace(/。$/,'')}</strong><small>{item.description===item.name?'桌面端功能验证':item.description}</small></span></div><Status status={records[item.id]?.status||'pending'}/><span className="elapsed">{duration(records[item.id]?.duration)}</span></div>)}
                </div>
              </section>;})}
              {!visible.length&&<div className="empty"><ListChecks size={30}/><strong>{search?'没有匹配的用例':'暂无测试用例'}</strong><span>{search?'尝试其他关键词':'在 tests 目录添加 Python 用例，然后点击刷新。'}</span></div>}</div>
              <div className="case-footer"><div className="legend"><Status status="pending"/><Status status="passed"/><Status status="failed"/></div><span>{stats.done} / {stats.total} 已完成</span></div><div className="progress-track"><div style={{width:`${progress}%`}}/></div>
            </section>

            <section className="panel detail-panel"><div className="panel-heading"><div><h2>执行详情</h2><p>{currentCase?.name || '选择用例查看执行详情'}</p></div><button className="icon-button" aria-label="打开报告资料" disabled={!record?.artifacts_dir} onClick={()=>void open(detail?.business_report_path||detail?.diagnostics_path||record!.artifacts_dir!)}><FolderOpen size={17}/></button></div>
              <div className="tabs"><button className={tab==='steps'?'active':''} onClick={()=>setTab('steps')}><ListChecks size={14}/>测试结果</button><button className={tab==='logs'?'active':''} onClick={()=>setTab('logs')}><Terminal size={14}/>诊断记录</button></div>
              {tab==='steps'?<div className="detail-scroll">{record&&<div className="detail-overview"><Status status={record.status}/><span><Clock3 size={13}/>{duration(record.duration)}</span></div>}<BusinessCaseDetails key={focused} detail={detail} status={record?.status} onOpen={path=>void open(path)}/></div>:<div className="log-scroll">{logs.length?logs.map((event,index)=><div className="log-line" key={index}><time>{event.time?.slice(11,19)||'--:--:--'}</time><span>{event.message||event.name||(event.type==='case_started'?'开始执行用例':event.type==='task_finished'?'任务执行结束':event.type==='case_finished'?`用例${labels[event.status||'']||'结束'}`:'准备测试任务')}{event.type==='step'&&` · ${labels[event.status||'']||''}`}</span></div>):<div className="empty"><Terminal size={26}/><span>执行日志将在任务开始后显示</span></div>}</div>}
            </section>
          </div>
          <footer className="page-footer"><span><ShieldCheck size={14}/>测试在本地执行，日志与截图仅保存在本机</span><button className="text-button" disabled={!runDir} onClick={()=>void open(runDir)}><FolderOpen size={14}/>打开本次报告目录</button></footer>
        </>}

        {page==='environment'&&<div className="environment-page"><div className="panel form-panel"><div className="panel-heading"><div><h2>环境配置</h2><p>配置程序、运行方式及设备连接入口。</p></div><span className="soft-tag">需点击保存</span></div><fieldset disabled={disabled||!ready}>
          <details className="form-section collapsible-section environment-basics" id="environment-basics" tabIndex={-1} open onToggle={event=>console.info('测试环境分区：',event.currentTarget.id,event.currentTarget.open?'展开':'收起')}><summary className="section-title"><span>01</span><div><h3>基础信息</h3><p>选择需要测试的上位机及自动化驱动</p></div></summary><Field label="环境名称" hint="用于区分不同测试环境"><input data-testid="environment-name" value={environment.name} onChange={e=>setEnvironment({...environment,name:e.target.value})}/></Field><Field label="上位机程序" hint="选择已安装或编译完成的 Release 可执行程序"><PathInput value={environment.app_path} onChange={v=>setEnvironment({...environment,app_path:v})} onBrowse={()=>void pick('app_path')} testId="environment-app"/></Field><Field label="WebView2 驱动" hint="驱动版本应与本机 WebView2 Runtime 匹配"><PathInput value={environment.driver_path} onChange={v=>setEnvironment({...environment,driver_path:v})} onBrowse={()=>void pick('driver_path')} testId="environment-driver"/></Field></details>
          <details className="form-section collapsible-section" id="environment-isolation" tabIndex={-1} onToggle={event=>console.info('测试环境分区：',event.currentTarget.id,event.currentTarget.open?'展开':'收起')}><summary className="section-title"><span>02</span><div><h3>运行与隔离</h3><p>根据测试需求选择干净环境或保留配置</p></div></summary><div className="mode-options">{(['fresh','workspace'] as const).map(mode=><button type="button" data-testid={`mode-${mode}`} className={environment.mode===mode?'mode-card chosen':'mode-card'} key={mode} onClick={()=>setEnvironment({...environment,mode})}><span className="radio-dot"/><div><strong>{mode==='fresh'?'全新独立副本':'固定测试工作区'}</strong><p>{mode==='fresh'?'每项用例使用新的程序副本和数据':'复用专用工作区，保留已有测试配置'}</p></div>{environment.mode===mode&&<Check size={16}/>}</button>)}</div>{environment.mode==='workspace'&&<Field label="固定工作区" hint="请选择专用测试目录，不使用原上位机安装目录"><PathInput value={environment.workspace_path} onChange={v=>setEnvironment({...environment,workspace_path:v})} onBrowse={()=>void pick('workspace_path')} testId="environment-workspace"/></Field>}<Field label="初始配置基线（可选）" hint="仅导入顶层 JSON 测试配置，登录配置不会导入"><PathInput value={environment.baseline_path} onChange={v=>setEnvironment({...environment,baseline_path:v})} onBrowse={()=>void pick('baseline_path')}/></Field><div className="form-split"><Field label="等待时间"><div className="number-input"><input data-testid="environment-timeout" type="number" min="1" value={Number.isFinite(environment.timeout)?environment.timeout:''} onChange={e=>setEnvironment({...environment,timeout:Number(e.target.value)})}/><span>秒</span></div></Field><label className="cleanup-toggle"><input type="checkbox" checked={environment.close_app} onChange={e=>setEnvironment({...environment,close_app:e.target.checked})}/><span>测试结束后关闭本次上位机实例<small>仅清理本次测试创建的实例</small></span></label></div></details>
          <details className="form-section collapsible-section" id="environment-annotation" tabIndex={-1} onToggle={event=>console.info('测试环境分区：',event.currentTarget.id,event.currentTarget.open?'展开':'收起')}><summary className="section-title"><span>03</span><div><h3>截图标注</h3><p>统一为步骤截图添加操作位置和简短说明</p></div></summary><label className="cleanup-toggle"><input data-testid="environment-annotation-enabled" type="checkbox" checked={environment.annotation_enabled} onChange={e=>setEnvironment({...environment,annotation_enabled:e.target.checked})}/><span>自动生成标注图<small>保留原图；查看截图时可切换原图和标注图</small></span></label></details>
          <details className="form-section collapsible-section" id="environment-slavesim" tabIndex={-1} onToggle={event=>console.info('测试环境分区：',event.currentTarget.id,event.currentTarget.open?'展开':'收起')}><summary className="section-title"><span>04</span><div><h3>slaveSim 模拟器</h3><p>统一保存管理页地址，随时打开查看模拟器状态</p></div></summary>
            <Field label="管理页地址（可选）" hint="填写完整的 HTTP 或 HTTPS 地址；修改后保存环境，下次打开会自动回填">
              <input data-testid="environment-slavesim-url" type="url" placeholder="http://192.168.1.219:16000" value={environment.slavesim_url} onChange={e=>{setEnvironment({...environment,slavesim_url:e.target.value});setNotice('');setError('');}}/>
            </Field>
            <div className="slavesim-actions"><button className="button" data-testid="slavesim-open" disabled={!environment.slavesim_url.trim()} onClick={()=>void slavesimAction('open_slavesim')}><ArrowRight size={15}/>打开管理页</button><button className="button" data-testid="slavesim-check" disabled={!environment.slavesim_url.trim()} onClick={()=>void slavesimAction('check_slavesim')}><Activity size={15}/>检查连接</button><span>使用当前填写的地址，在默认浏览器中打开</span></div>
          </details>
          <details className="form-section collapsible-section" id="environment-master104" tabIndex={-1} onToggle={event=>console.info('测试环境分区：',event.currentTarget.id,event.currentTarget.open?'展开':'收起')}><summary className="section-title"><span>05</span><div><h3>104 模拟主站</h3><p>使用已打开的 ProIEC104Client；检查只读取窗口，不自动连接或发送命令</p></div></summary>
            <label className="cleanup-toggle"><input data-testid="environment-master104-enabled" type="checkbox" checked={environment.master104_enabled} onChange={e=>{setEnvironment({...environment,master104_enabled:e.target.checked});setMaster104Snapshot(null);}}/><span>启用 104 主站<small>仅供需要主站的用例使用，既有用例保持原行为</small></span></label>
            <fieldset disabled={!environment.master104_enabled}>
              <Field label="主站程序" hint="选择已打开实例对应的 ProIEC104Client.exe；检查无需先保存环境">
                <PathInput value={environment.master104_path} onChange={v=>{setEnvironment({...environment,master104_path:v});setMaster104Snapshot(null);}} onBrowse={()=>void pick('master104_path')} testId="environment-master104-path"/>
              </Field>
              <div className="form-split"><Field label="窗口读取方式"><select data-testid="environment-master104-backend" value={environment.master104_backend} onChange={e=>{setEnvironment({...environment,master104_backend:e.target.value as Environment['master104_backend']});setMaster104Snapshot(null);}}><option value="win32">Windows 原生控件（win32）</option><option value="uia">UI Automation（uia）</option></select></Field>
                <Field label="主站等待时间"><div className="number-input"><input data-testid="environment-master104-timeout" type="number" min="0.1" step="0.1" value={Number.isFinite(environment.master104_timeout)?environment.master104_timeout:''} onChange={e=>setEnvironment({...environment,master104_timeout:Number(e.target.value)})}/><span>秒</span></div></Field></div>
              <div className="slavesim-actions"><button className="button" data-testid="master104-check" disabled={!ready||disabled||!environment.master104_path.trim()} onClick={()=>void checkMaster104()}>{master104Checking?<LoaderCircle size={15} className="spin"/>:<Activity size={15}/>} {master104Checking?'正在检查…':'检查主站'}</button><span>检查窗口可读取性；协议连接及业务效果由后续用例验证</span></div>
            </fieldset>
            {master104Snapshot&&<details className="master104-snapshot" data-testid="master104-snapshot"><summary>本次主站窗口快照</summary><pre>{JSON.stringify(master104Snapshot,null,2)}</pre></details>}
          </details>
          <ModbusEnvironment environment={environment} onChange={setEnvironment}/>
        </fieldset><div className="form-actions"><span role="status">{active?'任务运行中，环境配置已锁定':busy?'正在处理，请稍候…':'修改后请保存环境'}</span><button className="button" disabled={disabled||!ready} onClick={()=>void load()}><ArrowDownToLine size={15}/>载入配置</button><button className="button primary" data-testid="environment-save" disabled={disabled||!ready} onClick={()=>void save()}><Check size={16}/>保存环境</button></div></div><aside className="environment-help" aria-label="环境配置导航">
          <div className="environment-directory"><h3>配置导航</h3><p>点击定位到对应配置</p><nav aria-label="配置分区">{environmentSections.map(([id,title,hint],index)=><button type="button" key={id} onClick={()=>goToEnvironment(id)}><span>{String(index+1).padStart(2,'0')}</span><div><strong>{title}</strong><small>{hint}</small></div><ChevronRight size={14}/></button>)}</nav></div>
          <details className="environment-guide"><summary><ShieldCheck size={16}/>配置说明与使用边界</summary><h4>保留已有配置</h4><p>固定工作区会保留测试配置；全新副本适合首次启动及默认状态验证。</p><h4>运行中的任务</h4><p>执行期间配置锁定，停止并完成清理后可再次修改。</p><h4>模拟器连接</h4><p>连接检查仅确认状态接口可访问，控制效果由业务用例验证。</p><h4>104 主站</h4><p>只接管已打开实例，不启动或关闭主站，不修改主站配置。</p><h4>Modbus RTU</h4><p>用例执行时读取 slaveSim 当前配置，必要时补充模拟资源，并记录配置前后及验收结果。</p></details>
        </aside></div>}

        {page==='history'&&<>
          <div className="history-top"><span>{history.length} 条本地测试记录</span><button className="button" disabled={!ready} onClick={()=>void refreshHistory().catch(e=>setError(String(e)))}><RefreshCw size={14}/>刷新记录</button></div>
          <div className="history-layout"><section className="panel history-list">
            <div className="history-table-heading"><span>测试任务 / 环境</span><span>结果</span></div>
            {history.map(item=><button className={`history-row ${historyFocus===item.run_dir?'focused':''}`} key={item.id} data-testid="history-row" onClick={()=>setHistoryFocus(item.run_dir)}>
              <div className="history-icon"><FileText size={19}/></div><div><strong>{item.environment_name||'测试任务'}</strong><small>{item.created_at?.replace('T',' ').slice(0,19) || item.id}</small>
              <span className="history-count">{item.case_count} 项 · {item.passed} 通过 · {item.failed} 失败 · {item.cancelled||0} 停止 · {item.skipped||0} 未执行</span></div><Status status={item.status}/><ChevronRight size={16}/>
            </button>)}
            {!history.length&&<div className="empty"><HistoryIcon size={30}/><strong>还没有测试记录</strong><span>运行一次测试后，可在这里查看历史结果。</span></div>}
          </section><section className="panel history-detail">
            <div className="panel-heading"><div><h2>任务详情</h2><p>{historyDetail?.task.environment?.name || '选择一条历史记录'}</p></div>
              <div className="business-actions">
                {historyDetail?.business_report_path&&<button className="button" onClick={()=>void open(historyDetail.business_report_path!)}><FileText size={14}/>业务报告</button>}
                {historyDetail?.diagnostics_path&&<button className="button" onClick={()=>void open(historyDetail.diagnostics_path!)}><FolderOpen size={14}/>诊断资料</button>}
              </div>
            </div>
            {historyDetail?<div className="history-cases">
              <div className="history-summary"><Status status={String(historyDetail.summary.status||'pending')}/><span>{historyDetail.cases.length} 项测试用例</span></div>
              {historyDetail.cases.map((item,index)=><details key={`${historyFocus}-${index}`} className="history-case" open={index===0}>
                <summary><Status status={String(item.status||'pending')} text={false}/><strong>{item.business?.title||cases.find(c=>c.id===item.case_id)?.name||'测试用例'}</strong><span>{duration(item.duration as number|undefined)}</span><ChevronRight size={14}/></summary>
                <BusinessCaseDetails detail={item} status={String(item.status||'pending')} onOpen={path=>void open(path)}/>
              </details>)}
            </div>:<div className="empty"><FileText size={30}/><strong>查看历史测试详情</strong><span>选择左侧任务，查看结果、业务检查和关键截图。</span></div>}
          </section></div>
        </>}
      </div>
    </main>
  </div>;
}

function Stat({label,value,icon,hint,accent='default'}:{label:string;value:number;icon:React.ReactNode;hint:string;accent?:string}){return <div className={`stat-card ${accent}`}><div className="stat-top"><span>{label}</span><span className="stat-icon">{icon}</span></div><strong>{value}<small>项</small></strong><p>{hint}</p></div>;}
function Field({label,hint,children}:{label:string;hint?:string;children:React.ReactNode}){return <div className="field"><label>{label}</label>{children}{hint&&<p>{hint}</p>}</div>;}
function PathInput({value,onChange,onBrowse,testId}:{value:string;onChange:(v:string)=>void;onBrowse:()=>void;testId?:string}){return <div className="path-input"><input value={value} onChange={e=>onChange(e.target.value)} data-testid={testId}/><button type="button" onClick={onBrowse} aria-label="浏览路径"><FolderOpen size={16}/></button></div>;}
