import { useEffect, useRef, useState } from 'react';

// Upload is storage; reconstruction is an explicit, crop-aware provider operation.
export default function ImageReconstruction({ asset, src, request, onModel }) {
  const [size, setSize] = useState(null);
  const [crop, setCrop] = useState(null);
  const [task, setTask] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const drag = useRef(null);
  const key = `pd-reconstruction:${asset.id}`;
  useEffect(() => {
    setSize(null); setCrop(null); setResult(null); setError('');
    const saved = localStorage.getItem(key);
    setTask(saved ? { id: saved, status: 'RUNNING' } : null);
  }, [key]);
  useEffect(() => {
    if (!task?.id || ['FAILED','CANCELLED','SUCCEEDED'].includes(task.status)) return;
    let stopped = false, timer;
    const poll = async () => {
      try {
        const response = await request(`/providers/h3/jobs/${task.id}`);
        const body = await response.json();
        if (stopped) return;
        if (!response.ok) throw new Error(body.detail || '无法读取3D生成进度');
        setTask({ ...body, id: task.id });
        if (body.status === 'SUCCEEDED') {
          const assetsResponse = await request('/assets');
          if (!assetsResponse.ok) throw new Error('模型已生成，素材列表读取失败，请打开产品库');
          const assets = await assetsResponse.json();
          if (!stopped) {
            const model = assets.find(a => a.id === body.artifact_asset_id) || null;
            setResult(model);
            if (model) onModel(model);
          }
          localStorage.removeItem(key);
        } else if (body.status === 'FAILED' || body.status === 'CANCELLED') {
          setError(body.error || '3D生成未完成'); localStorage.removeItem(key);
        } else timer = setTimeout(poll, 2500);
      } catch (err) {
        if (!stopped) { setError(String(err.message)); timer = setTimeout(poll, 5000); }
      }
    };
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [task?.id, key]);
  function point(event) {
    const rect = event.currentTarget.getBoundingClientRect();
    return [Math.round(Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width))*size.width),
      Math.round(Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))*size.height)];
  }
  function move(event) {
    if (!drag.current || !size) return;
    const [x,y] = point(event), [sx,sy] = drag.current;
    setCrop([Math.min(x,sx),Math.min(y,sy),Math.abs(x-sx),Math.abs(y-sy)]);
  }
  async function start() {
    setBusy(true); setError(''); setResult(null);
    try {
      const response = await request('/providers/h3/reconstruct', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({product_asset_id:asset.id,crop,steps:50,cfg:5})});
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail));
      localStorage.setItem(key,body.provider_job_id);
      setTask({id:body.provider_job_id,status:body.status});
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  const running = task && !['FAILED','CANCELLED','SUCCEEDED'].includes(task.status);
  return <section className="reconstruction-panel">
    <h3>图片转 3D</h3>
    <p>拖动框选一个完整产品，排除文字和其他视图。生成结果是几何模型，材质需另外核验；彩色场景视频使用原始产品图。</p>
    <div className="reconstruction-crop" onPointerDown={e => {if (!size || running) return; drag.current=point(e);e.currentTarget.setPointerCapture(e.pointerId);}}
      onPointerMove={move} onPointerUp={e => {move(e);drag.current=null;}} onPointerCancel={() => {drag.current=null;}}>
      <img src={src} alt="拖动选择3D重建区域" draggable="false" onLoad={e => {
        const width=e.target.naturalWidth,height=e.target.naturalHeight;setSize({width,height});setCrop([0,0,width,height]);
      }} />
      {size && crop && <span className="reconstruction-selection" style={{left:`${crop[0]/size.width*100}%`,top:`${crop[1]/size.height*100}%`,width:`${crop[2]/size.width*100}%`,height:`${crop[3]/size.height*100}%`}} />}
    </div>
    {crop && <div className="inline-fields">{['左侧像素','顶部像素','宽度像素','高度像素'].map((label,i) => <label key={label}>{label}<input type="number" aria-label={`3D裁切${label}`} min={i<2?0:64} value={crop[i]} disabled={running}
      onChange={e => setCrop(old=>old.map((v,j)=>j===i?Number(e.target.value):v))}/></label>)}</div>}
    <button className="primary" onClick={start} disabled={busy||running||!crop||crop[2]<64||crop[3]<64}>生成 3D 模型</button>
    {task && <p role="status">3D任务：{task.status} · {task.stage || '提交中'} · {task.id?.slice(0,8)}</p>}
    {error && <p role="alert">{error}</p>}
    {result && <div><p>3D模型已生成并加入产品库：{result.name}</p><button className="primary" onClick={()=>onModel(result)}>使用生成的 3D 模型</button></div>}
  </section>;
}
