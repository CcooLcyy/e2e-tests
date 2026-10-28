import { useEffect, useRef, useState } from 'react';
import { Activity, ArrowDownToLine, ArrowRight, Check, CheckCheck, ChevronRight, CircleHelp, Clock3, FileText, FolderOpen, History as HistoryIcon, Layers3, ListChecks, LoaderCircle, Play, RefreshCw, Search, Settings2, ShieldCheck, Square, Terminal, TestTube2, X } from 'lucide-react';
import { call, choose, desktop, openArtifact } from './api';
import type { Bootstrap, CaseInfo, Detail, Environment, History, Poll, Shot } from './api';
import { applyEvents, counts, initialRecords, labels } from './state';
import type { BenchEvent, CaseRecord, Step } from './state';
import { caseGroup, summarizeGroups, visibleCases, toggleCases, selectionState, selectedCaseIds, reconcileSelection, groupedSections, toggleExpandedGroup } from './case-groups';

type Page = 'execution' | 'environment' | 'history';
const emptyEnvironment: Environment = {name:'本机测试环境',app_path:'',driver_path:'',mode:'fresh',workspace_path:'',baseline_path:'',timeout:20,close_app:true};
const duration = (value?: number) => value === undefined ? '—' : `${value.toFixed(2)} s`;
const valueText = (value: unknown) => value === undefined || value === null ? '—' : typeof value === 'string' ? value || '空字符串' : JSON.stringify(value);
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
  const [runDir,setRunDir] = useState('');
  const [taskIds,setTaskIds] = useState<string[]>([]);
  const [taskStatus,setTaskStatus] = useState('pending');
  const [logs,setLogs] = useState<BenchEvent[]>([]);
  const [tab,setTab] = useState<'steps'|'logs'>('steps');
  const [detail,setDetail] = useState<Detail|null>(null);
  const [preview,setPreview] = useState<Shot|null>(null);
  const [historyFocus,setHistoryFocus] = useState('');
  const [historyDetail,setHistoryDetail] = useState<{summary: Record<string,unknown>; task: {environment?:Environment}; cases: Array<Record<string,unknown> & {report?:Record<string,unknown>;screenshots?:Shot[]}>}|null>(null);
  const polling = useRef(false);
  const starting = useRef(false);
  const disabled = active || busy;

  async function refreshHistory() { const result = await call<History[]>('history'); setHistory(result); }
  async function bootstrap() {
    if (!desktop) return;
    setLoading(true); setError('');
    try { const data = await call<Bootstrap>('bootstrap'); setEnvironment(data.environment);setCases(data.cases);setHistory(data.history);setRecords(initialRecords(data.cases.map(c=>c.id)));setSelected(new Set(data.cases.map(c=>c.id)));setFocused(data.cases[0]?.id || '');setReady(true); }
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
            if(event.type==='error') setError(event.message || '测试任务发生错误');
          }
        }
      }catch(e){setError(String(e));}finally{polling.current=false;}
    },400);
    return()=>clearInterval(timer);
  },[ready]);
  const record=records[focused];
  useEffect(()=>{
    setDetail(null);
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
  useEffect(()=>{if(!preview)return;const handler=(event:KeyboardEvent)=>{if(event.key==='Escape')setPreview(null);};window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler);},[preview]);

  async function refreshCases(){setLoading(true);try{const list=await call<CaseInfo[]>('discover');setSelected(old=>reconcileSelection(cases,list,old));setCases(list);setExpandedGroups(old=>new Set(list.map(caseGroup).filter(name=>old.has(name))));setSearchCollapsed(new Set());setRecords(initialRecords(list.map(c=>c.id)));setTaskIds([]);setTaskStatus('pending');setFocused(list[0]?.id||'');}catch(e){setError(String(e));}finally{setLoading(false);}}
  async function start(){
    if(disabled || !selected.size)return;
    setBusy(true);starting.current=true;setError('');setNotice('');
    const ids=selectedCaseIds(cases,selected);
    try{await call('save_environment',{environment});const result=await call<{run_dir:string}>('start',{environment,case_ids:ids});setRecords(initialRecords(cases.map(c=>c.id)));setLogs([]);setDetail(null);setTaskIds(ids);setRunDir(result.run_dir);setTaskStatus('running');setActive(true);setStopping(false);setPage('execution');}
    catch(e){setError(String(e));}finally{starting.current=false;setBusy(false);}
  }
  async function stop(){setStopping(true);try{await call('stop');}catch(e){setError(String(e));setStopping(false);}}
  async function save(){setBusy(true);try{setEnvironment(await call<Environment>('save_environment',{environment}));setNotice('测试环境已保存，下次启动会自动加载。');}catch(e){setError(String(e));}finally{setBusy(false);}}
  async function load(){try{const path=await choose('environment');if(path){setEnvironment(await call<Environment>('load_environment',{path}));setNotice('环境配置已载入，保存后生效。');}}catch(e){setError(String(e));}}
  async function pick(key:'app_path'|'driver_path'|'workspace_path'|'baseline_path'){try{const path=await choose(key==='app_path'||key==='driver_path'?'exe':'directory');if(path)setEnvironment(old=>({...old,[key]:path}));}catch(e){setError(String(e));}}
  async function open(path:string){try{await openArtifact(path);}catch(e){setError(String(e));}}
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
  const screens=detail?.screenshots||[];
  const steps=record?.steps||[];

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
      <div className="page-scroll">
        <section className="page-title"><div><div className="eyebrow">{page==='execution'?'TEST RUNNER':page==='environment'?'ENVIRONMENT':'REPORTS'}</div><h1>{{execution:'测试执行',environment:'测试环境',history:'历史报告'}[page]}</h1><p>{{execution:'选择测试用例，验证系统的每一个关键流程。',environment:'管理上位机运行环境，保持每次测试可控、可重复。',history:'回溯每次测试结果，查看执行记录与问题证据。'}[page]}</p></div>{page==='execution'&&<div className="title-actions"><button className="button" disabled={!ready||disabled||loading} onClick={()=>void refreshCases()} data-testid="cases-refresh"><RefreshCw size={15} className={loading?'spin':''}/>刷新用例</button>{active?<button className="button danger" data-testid="run-stop" disabled={stopping} onClick={()=>void stop()}>{stopping?<LoaderCircle size={15} className="spin"/>:<Square size={13}/>} {stopping?'正在停止…':'停止测试'}</button>:<button className="button primary" data-testid="run-start" disabled={!ready||disabled||loading||!selected.size} onClick={()=>void start()}>{busy?<LoaderCircle size={16} className="spin"/>:<Play size={15} fill="currentColor"/>}开始测试<span className="button-count">{selected.size}</span></button>}</div>}</section>
        {!desktop&&<div className="notice info"><CircleHelp size={18}/><div><strong>请使用桌面版测试上位机</strong><p>浏览器仅用于界面开发。连接测试内核、执行用例和选择本机文件需要通过 Tauri 桌面窗口。</p></div></div>}
        {error&&<div className="notice error" role="alert" data-testid="error-message"><Activity size={17}/><span>{error}</span><button onClick={()=>setError('')} aria-label="关闭错误提示"><X size={16}/></button></div>}
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

            <section className="panel detail-panel"><div className="panel-heading"><div><h2>执行详情</h2><p>{currentCase?.name || '选择用例查看执行详情'}</p></div><button className="icon-button" aria-label="打开用例报告" disabled={!record?.artifacts_dir} onClick={()=>void open(`${record!.artifacts_dir}/report.md`)}><FolderOpen size={17}/></button></div>
              <div className="tabs"><button className={tab==='steps'?'active':''} onClick={()=>setTab('steps')}><ListChecks size={14}/>步骤记录 {steps.length>0&&<span>{steps.length}</span>}</button><button className={tab==='logs'?'active':''} onClick={()=>setTab('logs')}><Terminal size={14}/>运行日志</button></div>
              {tab==='steps'?<div className="detail-scroll">{record&&<div className="detail-overview"><Status status={record.status}/><span><Clock3 size={13}/>{duration(record.duration)}</span></div>}{record?.message&&<div className="case-error">{record.message}</div>}{steps.length?<div className="timeline">{steps.map((step,index)=><StepView key={index} step={step} index={index}/>)}</div>:<div className="empty detail-empty"><span className="empty-icon"><Play size={25}/></span><strong>等待开始测试</strong><span>开始后，步骤与检查结果将在这里实时显示。</span></div>}{screens.length>0&&<div className="evidence"><h3>测试截图 <span>{screens.length}</span></h3><div className="screenshots">{screens.map(shot=><button key={shot.path} onClick={()=>setPreview(shot)}><img src={shot.data_url} alt={shot.name}/><span>{shot.name}</span></button>)}</div></div>}</div>:<div className="log-scroll">{logs.length?logs.map((event,index)=><div className="log-line" key={index}><time>{event.time?.slice(11,19)||'--:--:--'}</time><span>{event.message||event.name||(event.type==='case_started'?'开始执行用例':event.type==='task_finished'?'任务执行结束':event.type==='case_finished'?`用例${labels[event.status||'']||'结束'}`:'准备测试任务')}{event.type==='step'&&` · ${labels[event.status||'']||''}`}</span></div>):<div className="empty"><Terminal size={26}/><span>执行日志将在任务开始后显示</span></div>}</div>}
            </section>
          </div>
          <footer className="page-footer"><span><ShieldCheck size={14}/>测试在本地执行，日志与截图仅保存在本机</span><button className="text-button" disabled={!runDir} onClick={()=>void open(runDir)}><FolderOpen size={14}/>打开本次报告目录</button></footer>
        </>}

        {page==='environment'&&<div className="environment-page"><div className="panel form-panel"><div className="panel-heading"><div><h2>环境配置</h2><p>配置一次，即可重复运行测试用例。</p></div><span className="soft-tag">本地保存</span></div><fieldset disabled={disabled||!ready}>
          <div className="form-section"><div className="section-title"><span>01</span><div><h3>基础信息</h3><p>选择需要测试的上位机及自动化驱动</p></div></div><Field label="环境名称" hint="用于区分不同测试环境"><input data-testid="environment-name" value={environment.name} onChange={e=>setEnvironment({...environment,name:e.target.value})}/></Field><Field label="上位机程序" hint="选择已安装或编译完成的 Release 可执行程序"><PathInput value={environment.app_path} onChange={v=>setEnvironment({...environment,app_path:v})} onBrowse={()=>void pick('app_path')} testId="environment-app"/></Field><Field label="WebView2 驱动" hint="驱动版本应与本机 WebView2 Runtime 匹配"><PathInput value={environment.driver_path} onChange={v=>setEnvironment({...environment,driver_path:v})} onBrowse={()=>void pick('driver_path')} testId="environment-driver"/></Field></div>
          <div className="form-section"><div className="section-title"><span>02</span><div><h3>运行与隔离</h3><p>根据测试需求选择干净环境或保留配置</p></div></div><div className="mode-options">{(['fresh','workspace'] as const).map(mode=><button type="button" data-testid={`mode-${mode}`} className={environment.mode===mode?'mode-card chosen':'mode-card'} key={mode} onClick={()=>setEnvironment({...environment,mode})}><span className="radio-dot"/><div><strong>{mode==='fresh'?'全新独立副本':'固定测试工作区'}</strong><p>{mode==='fresh'?'每项用例使用新的程序副本和数据':'复用专用工作区，保留已有测试配置'}</p></div>{environment.mode===mode&&<Check size={16}/>}</button>)}</div>{environment.mode==='workspace'&&<Field label="固定工作区" hint="请选择专用测试目录，不使用原上位机安装目录"><PathInput value={environment.workspace_path} onChange={v=>setEnvironment({...environment,workspace_path:v})} onBrowse={()=>void pick('workspace_path')} testId="environment-workspace"/></Field>}<Field label="初始配置基线（可选）" hint="仅导入顶层 JSON 测试配置，登录配置不会导入"><PathInput value={environment.baseline_path} onChange={v=>setEnvironment({...environment,baseline_path:v})} onBrowse={()=>void pick('baseline_path')}/></Field><div className="form-split"><Field label="等待时间"><div className="number-input"><input data-testid="environment-timeout" type="number" min="1" value={Number.isFinite(environment.timeout)?environment.timeout:''} onChange={e=>setEnvironment({...environment,timeout:Number(e.target.value)})}/><span>秒</span></div></Field><label className="cleanup-toggle"><input type="checkbox" checked={environment.close_app} onChange={e=>setEnvironment({...environment,close_app:e.target.checked})}/><span>测试结束后关闭本次上位机实例<small>仅清理本次测试创建的实例</small></span></label></div></div>
        </fieldset><div className="form-actions"><button className="button" disabled={disabled||!ready} onClick={()=>void load()}><ArrowDownToLine size={15}/>载入配置</button><button className="button primary" data-testid="environment-save" disabled={disabled||!ready} onClick={()=>void save()}><Check size={16}/>保存环境</button></div></div><aside className="environment-help"><div className="help-symbol"><ShieldCheck size={27}/></div><h3>可重复的测试环境</h3><p>程序、工作区与初始状态共同决定测试条件。建议为不同版本保留独立的测试工作区。</p><div className="help-divider"/><h4>关于已有配置</h4><p>固定工作区会保留测试配置；全新副本适合首次启动及默认状态验证。</p><h4>关于运行中的任务</h4><p>任务执行期间环境配置会锁定，停止并完成清理后可再次修改。</p></aside></div>}

        {page==='history'&&<><div className="history-top"><span>{history.length} 条本地测试记录</span><button className="button" disabled={!ready} onClick={()=>void refreshHistory().catch(e=>setError(String(e)))}><RefreshCw size={14}/>刷新记录</button></div><div className="history-layout"><section className="panel history-list"><div className="history-table-heading"><span>测试任务 / 环境</span><span>结果</span></div>{history.map(item=><button className={`history-row ${historyFocus===item.run_dir?'focused':''}`} key={item.id} data-testid="history-row" onClick={()=>setHistoryFocus(item.run_dir)}><div className="history-icon"><FileText size={19}/></div><div><strong>{item.environment_name||'测试任务'}</strong><small>{item.created_at?.replace('T',' ').slice(0,19) || item.id}</small><span className="history-count">{item.case_count} 项用例 · {item.passed} 通过 · {item.failed} 失败</span></div><Status status={item.status}/><ChevronRight size={16}/></button>)}{!history.length&&<div className="empty"><HistoryIcon size={30}/><strong>还没有测试记录</strong><span>运行一次测试后，可在这里查看历史结果。</span></div>}</section><section className="panel history-detail"><div className="panel-heading"><div><h2>任务详情</h2><p>{historyDetail?.task.environment?.name || '选择一条历史记录'}</p></div><button className="icon-button" disabled={!historyFocus} aria-label="打开历史报告" onClick={()=>void open(historyFocus)}><FolderOpen size={18}/></button></div>{historyDetail?<div className="history-cases"><div className="history-summary"><Status status={String(historyDetail.summary.status||'pending')}/><span>{historyDetail.cases.length} 项测试用例</span></div>{historyDetail.cases.map((item,index)=><details key={index} className="history-case" open={index===0}><summary><Status status={String(item.status||'pending')} text={false}/><strong>{cases.find(c=>c.id===item.case_id)?.name||String(item.case_id||'测试用例')}</strong><span>{duration(item.duration as number|undefined)}</span><ChevronRight size={14}/></summary>{Boolean(item.message)&&<pre className="case-error">{String(item.message)}</pre>}<SavedSteps report={item.report}/>{item.screenshots&&item.screenshots.length>0&&<div className="screenshots history-shots">{item.screenshots.map(shot=><button key={shot.path} onClick={()=>setPreview(shot)}><img src={shot.data_url} alt={shot.name}/><span>{shot.name}</span></button>)}</div>}</details>)}</div>:<div className="empty"><FileText size={30}/><strong>查看历史测试详情</strong><span>选择左侧任务，查看结果、步骤和测试截图。</span></div>}</section></div></>}
      </div>
    </main>
    {preview&&<div className="image-modal" role="dialog" aria-modal="true" aria-label="测试截图预览" onClick={()=>setPreview(null)}><div className="image-modal-content" onClick={e=>e.stopPropagation()}><div><strong>{preview.name}</strong><button className="icon-button" aria-label="关闭截图" onClick={()=>setPreview(null)}><X size={20}/></button></div><img src={preview.data_url} alt={preview.name}/></div></div>}
  </div>;
}

function Stat({label,value,icon,hint,accent='default'}:{label:string;value:number;icon:React.ReactNode;hint:string;accent?:string}){return <div className={`stat-card ${accent}`}><div className="stat-top"><span>{label}</span><span className="stat-icon">{icon}</span></div><strong>{value}<small>项</small></strong><p>{hint}</p></div>;}
function StepView({step,index}:{step:Step;index:number}){return <div className={`timeline-step ${step.status}`}><div className="step-marker">{step.status==='passed'?<Check size={12}/>:step.status==='running'?<LoaderCircle size={12} className="spin"/>:<span>{index+1}</span>}</div><div className="step-body"><div className="step-title"><strong>{step.name}</strong><span>{duration(step.duration)}</span></div>{(step.expected!==undefined||step.actual!==undefined)&&<div className="expectation"><div><span>预期</span><code>{valueText(step.expected)}</code></div><div><span>实际</span><code>{valueText(step.actual)}</code></div></div>}</div></div>;}
function Field({label,hint,children}:{label:string;hint?:string;children:React.ReactNode}){return <div className="field"><label>{label}</label>{children}{hint&&<p>{hint}</p>}</div>;}
function PathInput({value,onChange,onBrowse,testId}:{value:string;onChange:(v:string)=>void;onBrowse:()=>void;testId?:string}){return <div className="path-input"><input value={value} onChange={e=>onChange(e.target.value)} data-testid={testId}/><button type="button" onClick={onBrowse} aria-label="浏览路径"><FolderOpen size={16}/></button></div>;}
function SavedSteps({report}:{report?:Record<string,unknown>}){const steps=report?.['步骤'] as Array<Record<string,unknown>>|undefined;return steps?<div className="timeline saved-timeline">{steps.map((step,index)=><StepView key={index} index={index} step={{name:String(step['步骤']),status:step['状态']==='通过'?'passed':'failed',expected:step['预期'],actual:step['实际'],duration:step['耗时秒'] as number}}/>)}</div>:null;}
