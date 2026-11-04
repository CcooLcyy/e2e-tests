import { useEffect, useState } from 'react';
import { FileText, FolderOpen, X } from 'lucide-react';
import type { Detail, Shot } from './api';
import { businessScreenshots, resultSummary } from './business-report';
import { hasAnnotation, screenshotSource } from './screenshot';

export function BusinessCaseDetails({detail,status,onOpen}:{
  detail?: Partial<Detail> | null; status?: string; onOpen:(path:string)=>void;
}) {
  const [preview,setPreview] = useState<Shot|null>(null);
  const [original,setOriginal] = useState(false);
  const view = detail?.business;
  const shots = businessScreenshots({business:view,screenshots:detail?.screenshots||[]});
  useEffect(()=>{
    if(!preview)return;
    const listener=(event:KeyboardEvent)=>{if(event.key==='Escape')setPreview(null);};
    window.addEventListener('keydown',listener);
    return()=>window.removeEventListener('keydown',listener);
  },[preview]);
  function show(shot:Shot){setPreview(shot);setOriginal(false);}
  return <div className="business-case-details">
    {view?<>
      <p className="business-goal"><strong>测试目标</strong>{view.goal}</p>
      <p className="business-summary"><strong>结果：{view.result}</strong>{view.summary}</p>
      {view.checks.length>0&&<div className="business-checks"><table>
        <thead><tr><th>检查内容</th><th>预期表现</th><th>实际表现</th><th>结果</th></tr></thead>
        <tbody>{view.checks.map((check,index)=><tr key={index}>
          <td>{check.name}</td><td>{check.expected}</td><td>{check.actual}</td><td>{check.status}</td>
        </tr>)}</tbody>
      </table></div>}
    </>:<p className="business-summary">{resultSummary(status)}</p>}
    <div className="business-actions">
      {detail?.business_report_path&&<button className="button" onClick={()=>onOpen(detail.business_report_path!)}><FileText size={14}/>打开业务报告</button>}
      {detail?.diagnostics_path&&<button className="button" onClick={()=>onOpen(detail.diagnostics_path!)}><FolderOpen size={14}/>诊断资料</button>}
    </div>
    {shots.length>0&&<div className="evidence"><h3>关键截图 <span>{shots.length}</span></h3><div className="screenshots">
      {shots.map((shot,index)=><article className="screenshot-card" key={shot.name}>
        {shot.capture_status!=='missing'&&shot.data_url?<button className="screenshot-preview" onClick={()=>show(shot)} aria-label={`查看关键截图${index+1}`}>
          <img src={screenshotSource(shot)} alt={shot.description}/><span>点击放大{hasAnnotation(shot)&&'，可切换原图'}</span>
        </button>:<div className="screenshot-missing">本项关键截图未保存，相关记录见诊断资料。</div>}
        <div className="screenshot-explanation"><p>{shot.description}</p><small>图片用于记录现场，用例结果以测试报告为准。</small></div>
      </article>)}
    </div></div>}
    {preview&&<div className="image-modal" role="dialog" aria-modal="true" aria-label="关键截图预览" onClick={()=>setPreview(null)}>
      <div className="image-modal-content" onClick={event=>event.stopPropagation()}>
        <div className="screenshot-toolbar"><strong>{preview.description||'界面截图'}</strong>
          {hasAnnotation(preview)&&<button className="button" aria-pressed={original} onClick={()=>setOriginal(value=>!value)}>{original?'查看标注图':'查看原图'}</button>}
          <button className="icon-button" aria-label="关闭截图" onClick={()=>setPreview(null)}><X size={20}/></button>
        </div>
        <img src={screenshotSource(preview,original)} alt={preview.description}/>
        <div className="screenshot-explanation"><p>{preview.description}</p><small>用例结果：{preview.business_result}。本图用于记录界面现场。</small></div>
      </div>
    </div>}
  </div>;
}
