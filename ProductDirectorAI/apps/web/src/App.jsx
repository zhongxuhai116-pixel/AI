import { useEffect, useMemo, useRef, useState } from "react";
import {
  Archive, ArrowClockwise, Bell, BoxArrowDown, Camera, CaretDown, CaretRight, Check, CheckCircle,
  Clock, Cpu, Cube, DownloadSimple, FilmSlate, FolderOpen, Gear, Image, ListChecks,
  MagnifyingGlass, MonitorPlay, Package, PencilSimple, Play, Plus, Queue,
  SlidersHorizontal, Sparkle, SquaresFour, StopCircle, UploadSimple, WarningCircle, X,
} from "@phosphor-icons/react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

const API_BASE = (import.meta.env.VITE_API_BASE || "").trim().replace(/\/$/, "");
const API = `${API_BASE}/api/v1`;
let sessionCsrfToken = "";
const demoImage = "/assets/boxing-trainer.png";
const navItems = [
  ["project", "项目", SquaresFour], ["products", "产品库", Cube],
  ["director", "导演台", SlidersHorizontal], ["storyboard", "分镜", FilmSlate],
  ["preview", "3D 预演", MonitorPlay], ["jobs", "渲染任务", Queue],
  ["assets", "素材库", Archive], ["settings", "设置", Gear],
];
const defaultShots = [
  { id: "shot_01", name: "正面推近", camera: "dolly_in", focal_length_mm: 35, duration_frames: 48 },
  { id: "shot_02", name: "侧向观察", camera: "side_track", focal_length_mm: 35, duration_frames: 48 },
  { id: "shot_03", name: "细节定格", camera: "static", focal_length_mm: 50, duration_frames: 48 },
];
const outputPresets = [
  { label: "540 × 960（快出）", width: 540, height: 960 },
  { label: "1080 × 1920（高清）", width: 1080, height: 1920 },
];
const cropAnchorLabels = [
  ["center", "居中"], ["top", "顶部"], ["bottom", "底部"], ["left", "左侧"], ["right", "右侧"],
];
const cameraLabels = {
  dolly_in: "Dolly In · 推近", side_track: "Side Track · 侧移",
  hero_orbit: "Hero Orbit · 环绕", static: "Static · 定格",
};

function formatBytes(bytes = 0) {
  return bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
}
function formatTime(value) {
  return value ? new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "—";
}
function Pill({ status = "DRAFT" }) {
  const map = {
    DRAFT: ["草稿", "neutral"], QUEUED: ["排队中", "queued"], RUNNING: ["生成中", "running"],
    CANCEL_REQUESTED: ["取消中", "warning"], CANCELLED: ["已取消", "neutral"],
    FAILED: ["失败", "danger"], SUCCEEDED: ["已完成", "success"],
  };
  const [text, tone] = map[status] || [status, "neutral"];
  return <span className={`pill ${tone}`}><i />{text}</span>;
}

function ModelPreview({ url }) {
  const host = useRef(null);
  useEffect(() => {
    if (!url || !host.current) return;
    const mount = host.current;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#f3f5f7");
    const camera = new THREE.PerspectiveCamera(36, mount.clientWidth / mount.clientHeight, .01, 100);
    camera.position.set(2.5, -3.2, 2);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    mount.replaceChildren(renderer.domElement);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x475569, 2.5));
    const key = new THREE.DirectionalLight(0xffffff, 4); key.position.set(-2, -3, 5); scene.add(key);
    scene.add(new THREE.GridHelper(8, 16, 0xd7dce3, 0xe4e7eb));
    let object; let frame; let down = false; let previous = 0;
    new GLTFLoader().setWithCredentials(true).load(url, (gltf) => {
      object = gltf.scene;
      const box = new THREE.Box3().setFromObject(object);
      const size = box.getSize(new THREE.Vector3());
      const center = box.getCenter(new THREE.Vector3());
      object.position.sub(center);
      object.scale.setScalar(1.8 / (Math.max(size.x, size.y, size.z) || 1));
      scene.add(object);
    });
    const canvas = renderer.domElement;
    const start = (e) => { down = true; previous = e.clientX; };
    const move = (e) => { if (down && object) { object.rotation.z += (e.clientX - previous) * .01; previous = e.clientX; } };
    const stop = () => { down = false; };
    canvas.addEventListener("pointerdown", start); window.addEventListener("pointermove", move); window.addEventListener("pointerup", stop);
    const animate = () => { frame = requestAnimationFrame(animate); camera.lookAt(0, 0, .4); renderer.render(scene, camera); };
    animate();
    return () => { cancelAnimationFrame(frame); renderer.dispose(); window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); };
  }, [url]);
  return <div className="model-preview" ref={host}><span>拖动查看模型</span></div>;
}

function Sidebar({ active, onSelect }) {
  return <aside className="sidebar">
    <div className="brand"><img src="/assets/productdirector-mark.png" alt="" /><div><strong>ProductDirector<span>AI</span></strong><small>From product to story</small></div></div>
    <nav>{navItems.map(([id, label, Icon]) => <button className={active === id ? "active" : ""} onClick={() => onSelect(id)} key={id}><Icon weight={active === id ? "fill" : "regular"} /><span>{label}</span></button>)}</nav>
    <div className="version"><Cube /><div><strong>ProductDirectorAI</strong><small>V1 · 3D Director MVP</small></div></div>
  </aside>;
}

function Header({ connected, onSearch }) {
  return <header className="topbar">
    <div className="crumb"><span>项目</span><CaretRight /><strong>通用产品导演</strong></div>
    <label className="search"><MagnifyingGlass /><input placeholder="搜索项目、素材或任务..." onChange={(e) => onSearch(e.target.value)} /></label>
    <button className="icon-btn" aria-label="通知"><Bell /><i /></button>
    <div className="account"><b>L</b><div><strong>Leo</strong><small>{connected ? "本地工作区" : "服务未连接"}</small></div><CaretDown /></div>
  </header>;
}

function Steps({ asset, plan, job }) {
  const list = [
    ["1", "产品素材", asset ? "已选择" : "图片 / GLB", Image, !!asset],
    ["2", "生成分镜", plan ? "3 个 Shot" : "模板导演", FilmSlate, !!plan],
    ["3", "生成预演", job?.stage || "等待执行", Camera, job?.status === "SUCCEEDED"],
    ["4", "技术检查", job?.status === "SUCCEEDED" ? "已通过" : "自动校验", ListChecks, job?.status === "SUCCEEDED"],
  ];
  return <div className="steps">{list.map(([n, title, sub, Icon, done], index) => <div className={done ? "done" : ""} key={title}><em>{done ? <Check /> : <Icon />}</em><p><b>{n} {title}</b><small>{sub}</small></p>{index < 3 && <CaretRight className="arrow" />}</div>)}</div>;
}

function AssetCard({ asset, assetUrl, onUpload, onDemo, busy }) {
  const input = useRef(null);
  return <section className="card asset-card">
    <div className="card-head"><div><h2>产品素材</h2><i>?</i></div><button onClick={() => input.current?.click()}>管理素材 <CaretRight /></button></div>
    <div className="asset-stage">
      {asset?.kind === "model" ? <ModelPreview url={assetUrl} /> : <img src={assetUrl || demoImage} alt={asset ? asset.name : "演示产品"} />}
      <span>{asset ? (asset.kind === "model" ? "GLB · 3D" : "图片 · 2D") : "演示素材"}</span>
    </div>
    <div className="asset-info"><p><strong>{asset?.name || "还没有上传你的产品"}</strong><small>{asset ? `${asset.kind === "model" ? "可执行三维环绕" : "图片平移 / 推近预演"} · ${formatBytes(asset.size_bytes)}` : "可上传任意品类；示例不会成为产品规则"}</small></p><button onClick={() => input.current?.click()}><UploadSimple />上传</button></div>
    <div className="asset-actions"><button disabled={busy} onClick={() => input.current?.click()}><Plus />上传图片或 GLB</button>{!asset && <button disabled={busy} onClick={onDemo}>先用演示素材</button>}</div>
    <input ref={input} hidden type="file" accept=".png,.jpg,.jpeg,.webp,.glb" onChange={(e) => onUpload(e.target.files?.[0])} />
  </section>;
}

function DirectorCard({ intent, setIntent, plan, output, onOutputChange, cropAnchor, onCropAnchorChange, assetUrl, onGenerate, onAiGenerate, busy }) {
  return <section className="card director-card">
    <div className="card-head"><div><h2>导演描述</h2><span>V1 模板导演</span></div><button>高级设置 <CaretRight /></button></div>
    <textarea value={intent} onChange={(e) => setIntent(e.target.value)} />
    <div className="counter"><span>{intent.length} / 4000</span><span>AI 导演：本地生成 + 服务端校验</span></div>
    <div className="director-actions">
      <button className="primary" disabled={busy || !intent.trim()} onClick={onAiGenerate}><Sparkle weight="fill" />用描述生成分镜</button>
    </div>
    <div className="outputs">
      {[["视频比例", "9:16 竖屏"], ["总时长", "6 秒"], ["帧率", "24 fps"]].map(([label, value]) => <label key={label}><span>{label}</span><button>{value}<CaretDown /></button></label>)}
      <label>
        <span>输出分辨率</span>
        <select value={`${output.width}x${output.height}`} onChange={(event) => onOutputChange(event.target.value)}>
          {outputPresets.map((item) => <option key={`${item.width}x${item.height}`} value={`${item.width}x${item.height}`}>{item.label}</option>)}
        </select>
      </label>
      <label>
        <span>裁切锚点（图片预演：横/竖素材进 9:16 保留哪一侧）</span>
        <select value={cropAnchor} onChange={(event) => onCropAnchorChange(event.target.value)}>
          {cropAnchorLabels.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <div className="crop-preview">
        <div className="crop-frame">
          {assetUrl ? <img src={assetUrl} alt="裁切区域预览" style={{ objectPosition: cropAnchor }} /> : <Cube weight="duotone" />}
        </div>
        <small>9:16 裁切区域预览 —— 成片会在这个区域内做推近/侧移，不会用到框外内容。</small>
      </div>
    </div>
    <button className="primary wide" disabled={busy} onClick={onGenerate}><Sparkle weight="fill" />{busy ? "正在生成..." : plan ? "重新生成三镜头" : "生成三镜头计划"}</button>
  </section>;
}

function RenderCard({ health, plan, job, onRender, onCancel, onRetry }) {
  const active = ["QUEUED", "RUNNING", "CANCEL_REQUESTED"].includes(job?.status);
  const retryable = ["FAILED", "CANCELLED", "CANCEL_REQUESTED"].includes(job?.status);
  return <section className="card render-card">
    <div className="card-head"><div><h2>执行与检查</h2><i>?</i></div>{job && <Pill status={job.status} />}</div>
    <div className="env">{[["Blender", health?.blender, Cube], ["FFmpeg", health?.ffmpeg, FilmSlate]].map(([name, value, Icon]) => <div key={name}><em className={value?.available ? "ok" : "off"}><Icon /></em><p><strong>{name}</strong><small>{value?.available ? "本机已就绪" : "未连接"}</small></p></div>)}</div>
    {job ? <div className="job-box"><div><span>{job.stage}</span><strong>{job.progress}%</strong></div><div className="progress"><i style={{ width: `${job.progress}%` }} /></div><small>{job.status === "FAILED" ? job.error : job.status === "SUCCEEDED" ? "视频与 metadata 已完成基础检查" : "任务在后台执行，刷新页面也不会丢失。"}</small></div> : <div className="empty-job"><Clock /><span>确认分镜后创建第一条预演任务</span></div>}
    <div className="render-actions">
      {active ? <button className="danger" onClick={onCancel}><StopCircle />取消任务</button> : <button className="primary" disabled={!plan} onClick={onRender}><Play weight="fill" />确认计划并生成预演</button>}
      {retryable && <button onClick={onRetry}><ArrowClockwise />重试任务</button>}
      {job?.status === "SUCCEEDED" && <><a href={`${API}/jobs/${job.id}/video`} target="_blank"><DownloadSimple />下载 MP4</a><a className="icon-link" title="下载 metadata" href={`${API}/jobs/${job.id}/manifest`} target="_blank"><BoxArrowDown /></a></>}
    </div>
  </section>;
}

function Shots({ plan, setPlan, assetUrl }) {
  const shots = plan?.shots || defaultShots;
  function update(index, field, value) {
    if (!plan) return;
    setPlan({ ...plan, shots: plan.shots.map((shot, i) => i === index ? { ...shot, [field]: value } : shot) });
  }
  const totalFrames = shots.reduce((sum, shot) => sum + Number(shot.duration_frames || 0), 0);
  return <section className="card shots">
    <div className="card-head"><div><h2>分镜计划</h2><span className="count">3 SHOTS · {totalFrames} / 144 帧</span></div><small>{plan ? (totalFrames === 144 ? "计划草稿" : "总时长必须为 144 帧") : "等待生成"}</small></div>
    <div className="shot-grid">{shots.map((shot, index) => <article className={!plan ? "muted" : ""} key={shot.id}><div className="shot-img">{assetUrl ? <img src={assetUrl} alt="当前产品" /> : <div className="model-thumb"><Cube weight="duotone" /></div>}<b>SHOT 0{index + 1}</b><button><Play weight="fill" /></button></div><div className="shot-body"><input value={shot.name} readOnly={!plan} onChange={(e) => update(index, "name", e.target.value)} /><select disabled={!plan} value={shot.camera} onChange={(e) => update(index, "camera", e.target.value)}>{Object.entries(cameraLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><p><label>焦距 <input aria-label={`SHOT ${index + 1} 焦距`} disabled={!plan} type="number" min="15" max="120" value={shot.focal_length_mm} onChange={(e) => update(index, "focal_length_mm", Number(e.target.value))} /> mm</label><label>时长 <input aria-label={`SHOT ${index + 1} 时长`} disabled={!plan} type="number" min="24" max="144" value={shot.duration_frames} onChange={(e) => update(index, "duration_frames", Number(e.target.value))} /> 帧 · {(Number(shot.duration_frames || 0) / 24).toFixed(1)}s</label></p></div></article>)}</div>
  </section>;
}

const providerOperationLabels = { RECONSTRUCT_3D: "3D 重建", GENERATE_VIDEO: "H3 视频生成" };

function ProviderJobs({ jobs, onCancel }) {
  return <section className="card provider-jobs">
    <div className="card-head"><div><h2>H3 生成任务</h2><span className="count">{jobs.length} 条</span></div><small>自托管 MiniMax H3 · 单卡串行</small></div>
    {jobs.length === 0
      ? <div className="empty-job"><Clock /><span>还没有云端生成任务</span></div>
      : <div className="table-wrap"><table>
        <thead><tr><th>类型</th><th>状态</th><th>阶段</th><th>产物</th><th>创建时间</th><th /></tr></thead>
        <tbody>{jobs.map((item) => <tr key={item.id}>
          <td><span>{providerOperationLabels[item.operation] || item.operation}</span></td>
          <td><Pill status={item.status} /></td>
          <td>{item.status === "RUNNING" && item.cancel_requested ? "已记录取消请求" : item.stage}</td>
          <td>{item.artifact_ready ? (item.artifact_asset_id ? "已入库" : "可下载") : "—"}</td>
          <td>{new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })}</td>
          <td>{["RUNNING", "QUEUED"].includes(item.status) && <button onClick={() => onCancel(item)}>取消</button>}</td>
        </tr>)}</tbody>
      </table></div>}
  </section>;
}

function Jobs({ jobs, selected, onOpen }) {
  return <section className="card jobs">
    <div className="card-head"><div><h2>最近任务</h2><span className="count">{jobs.length}</span></div><button>查看全部 <CaretRight /></button></div>
    {jobs.length ? <div className="table-wrap"><table><thead><tr><th>任务</th><th>素材类型</th><th>状态</th><th>进度</th><th>阶段</th><th>创建时间</th><th /></tr></thead><tbody>{jobs.map((item, index) => <tr className={selected === item.id ? "selected" : ""} key={item.id}><td><span>{index + 1}</span><img src={demoImage} alt="" /><strong>产品预演 {item.id.slice(0, 6)}</strong></td><td>{item.kind === "model" ? "GLB · 3D" : "图片 · 2D"}</td><td><Pill status={item.status} /></td><td><div className="mini-progress"><i style={{ width: `${item.progress}%` }} /></div><small>{item.progress}%</small></td><td>{item.stage}</td><td>{formatTime(item.created_at)}</td><td><button onClick={() => onOpen(item)}>打开</button></td></tr>)}</tbody></table></div> : <div className="table-empty"><Queue /><strong>还没有任务</strong><span>上传产品并生成第一个三镜头计划。</span></div>}
  </section>;
}

function Library({ assets, onSelect }) {
  return <section className="page">
    <div className="page-head"><div><b>V1 PRODUCT LIBRARY</b><h1>产品素材库</h1><p>每个上传文件都是独立产品素材。产品可以是任意品类，演示拳击机不会写入业务规则。</p></div><button className="primary"><UploadSimple />上传新产品</button></div>
    <div className="library">{assets.length ? assets.map((asset) => <button key={asset.id} onClick={() => onSelect(asset)}><div>{asset.kind === "image" ? <img src={`${API}/assets/${asset.id}/content`} alt="" /> : <Cube />}</div><strong>{asset.name}</strong><span>{asset.kind === "model" ? "GLB 三维产品" : "产品图片"} · {formatBytes(asset.size_bytes)}</span></button>) : <div className="library-empty"><Package /><strong>等待你的第一个产品</strong><span>支持 PNG、JPEG、WebP 或 GLB。</span></div>}</div>
  </section>;
}

function Settings({ health, provider, onSaveProvider, onTestProvider, busy }) {
  const [key, setKey] = useState("");
  const tone = ["GENERATION_READY", "AUTHENTICATED"].includes(provider?.last_status) ? "SUCCEEDED" : provider?.last_status === "QUOTA_LIMITED" ? "CANCEL_REQUESTED" : "FAILED";
  async function submit(event) { event.preventDefault(); if (key.trim()) { await onSaveProvider(key.trim()); setKey(""); } }
  const gpuProfiles = [
    { name: "开发节省", gpu: "RTX 3090 · 24GB", price: "¥1.19/小时", use: "低成本开发与基础预演" },
    { name: "当前推荐", gpu: "RTX 5090 · 32GB", price: "¥3.20/小时", use: "V1、Blender 与普通 ComfyUI", recommended: true },
    { name: "视频稳妥", gpu: "RTX 4090 · 48GB", price: "¥3.30/小时", use: "大模型视频与显存敏感工作流" },
  ];
  return <section className="page">
    <div className="page-head"><div><b>RUNTIME & PROVIDERS</b><h1>执行环境</h1><p>本机运行时、MiniMax 中国区连接与云端 GPU 选型。V1 不会自动购买或创建云资源。</p></div></div>
    <div className="settings">
      {[["Blender", health?.blender, Cube], ["FFmpeg", health?.ffmpeg, FilmSlate], ["本地存储", { available: !!health, path: health?.storage }, FolderOpen]].map(([name, value, Icon]) => <div key={name}><em className={value?.available ? "ok" : "off"}><Icon /></em><p><strong>{name}</strong><small>{value?.path || "尚未连接"}</small></p><Pill status={value?.available ? "SUCCEEDED" : "FAILED"} /></div>)}
    </div>
    <section className="provider-card"><div className="provider-head"><div><Sparkle weight="fill" /><p><strong>MiniMax · 中国大陆</strong><small>https://api.minimaxi.com/v1</small></p></div><Pill status={tone} /></div><p className="provider-message">{provider?.last_message || "尚未配置 MiniMax API Key"}</p><form onSubmit={submit}><label><span>API Key</span><input aria-label="MiniMax API Key" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={provider?.configured ? "已加密保存；输入新密钥可替换" : "输入 sk-cp-…"} /></label><button className="primary" disabled={busy || !key.trim()} type="submit">保存并验证</button><button type="button" disabled={busy || !provider?.configured} onClick={onTestProvider}>测试文本生成</button></form><small className="provider-note">不会把密钥返回给浏览器、写入前端存储或提交到 GitHub。</small></section>
    <section className="provider-card gpu-card">
      <div className="provider-head"><div><Cpu weight="duotone" /><p><strong>优云智算 · 云 GPU 方案</strong><small>2026-09-10 选型快照 · 实际库存与结算价以控制台为准</small></p></div><span className="recommend-badge">推荐 5090 32G</span></div>
      <div className="gpu-profile-grid">{gpuProfiles.map((profile) => <article className={profile.recommended ? "recommended" : ""} key={profile.name}><div><span>{profile.name}</span>{profile.recommended && <CheckCircle weight="fill" />}</div><strong>{profile.gpu}</strong><b>{profile.price}</b><small>{profile.use}</small></article>)}</div>
      <div className="gpu-guidance"><WarningCircle weight="fill" /><p><strong>你截图里的 5090 配置可以开。</strong><span>单卡、14 核 64GB、按量计费；系统盘从 50GB 调到至少 100GB，ComfyUI/视频模型建议 200GB 或独立云盘。先跑 2–5 小时基准，不先买包月。</span></p></div>
      <div className="gpu-card-footer"><small>V1 仅展示已核对的选型，不保存云账号、不创建实例、不产生费用。</small><a href="https://compshare.cn/price-list" target="_blank" rel="noreferrer">查看官方价格 <CaretRight /></a></div>
    </section>
  </section>;
}

export function App() {
  const [session, setSession] = useState(null);
  const [accessKey, setAccessKey] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);
  const [active, setActive] = useState("project");
  const [health, setHealth] = useState(null);
  const [assets, setAssets] = useState([]);
  const [asset, setAsset] = useState(null);
  const [assetUrl, setAssetUrl] = useState("");
  const [intent, setIntent] = useState("在干净的现代工作室中，用三个清晰镜头展示产品外观、侧面结构与整体比例。");
  const [output, setOutput] = useState(outputPresets[0]);
  const [cropAnchor, setCropAnchor] = useState("center");
  const [plan, setPlan] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [provider, setProvider] = useState(null);
  const [providerJobs, setProviderJobs] = useState([]);
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const [search, setSearch] = useState("");

  async function refresh() {
    try {
      const [h, a, j, p] = await Promise.all([
        apiRequest("/health"),
        apiRequest("/assets"),
        apiRequest("/jobs"),
        apiRequest("/providers/minimax/status"),
      ]);
      const providerJobsResponse = await apiRequest("/providers/h3/jobs").catch(() => null);
      if ([h, a, j, p].some((response) => response.status === 401)) {
        sessionCsrfToken = ""; setSession(null); return;
      }
      if (!h.ok || !a.ok || !j.ok) throw new Error();
      const nextHealth = await h.json(); const nextAssets = await a.json(); const nextJobs = await j.json(); const nextProvider = p.ok ? await p.json() : null;
      setProvider(nextProvider);
      setHealth(nextHealth); setAssets(nextAssets); setJobs(nextJobs);
      if (providerJobsResponse && providerJobsResponse.ok) setProviderJobs(await providerJobsResponse.json());
      if (!asset && nextAssets[0]) { setAsset(nextAssets[0]); setAssetUrl(`${API}/assets/${nextAssets[0].id}/content`); }
      if (job) { const current = nextJobs.find((item) => item.id === job.id); if (current) setJob(current); }
    } catch { setHealth(null); }
  }
  useEffect(() => {
    apiRequest("/session").then(async (response) => {
      if (response.ok) {
        const data = await response.json(); sessionCsrfToken = data.csrf_token || ""; setSession(data);
      }
    }).catch(() => setLoginError("暂时无法连接服务，请检查连接"));
  }, []);
  useEffect(() => {
    if (!session) return;
    refresh(); const timer = setInterval(refresh, 1800); return () => clearInterval(timer);
  }, [session, job?.id, asset?.id]);
  useEffect(() => { if (!toast) return; const timer = setTimeout(() => setToast(null), 4000); return () => clearTimeout(timer); }, [toast]);

  async function upload(file) {
    if (!file) return; setBusy(true);
    const form = new FormData(); form.append("file", file);
    try {
      const response = await apiRequest("/assets", { method: "POST", body: form });
      if (!response.ok) throw new Error((await response.json()).detail || "上传失败");
      const uploaded = await response.json();
      setAsset(uploaded); setAssetUrl(uploaded.kind === "image" ? URL.createObjectURL(file) : `${API}/assets/${uploaded.id}/content`);
      setPlan(null); setJob(null); setToast(["success", `${file.name} 已成为当前产品素材`]); refresh();
    } catch (error) { setToast(["danger", error.message]); } finally { setBusy(false); }
  }
  async function useDemo() {
    const blob = await fetch(demoImage).then((r) => r.blob());
    upload(new File([blob], "demo-product.png", { type: "image/png" }));
  }
  async function generatePlan() {
    if (!asset) return setToast(["danger", "请先上传产品图片或 GLB。"]);
    setBusy(true);
    try {
      const response = await apiRequest("/plans/template", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product_asset_id: asset.id, intent, ratio: "9:16", duration_seconds: 6, output, crop_anchor: cropAnchor }),
      });
      if (!response.ok) throw new Error((await response.json()).detail || "计划生成失败");
      const created = await response.json();
      setPlan(created); setCropAnchor(created.crop_anchor || "center");
      setToast(["success", "三镜头计划已生成，可以继续调整。"]);
    } catch (error) { setToast(["danger", error.message]); } finally { setBusy(false); }
  }
  async function run() {
    setBusy(true);
    try {
      const saved = await apiRequest(`/plans/${plan.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ intent, shots: plan.shots, crop_anchor: cropAnchor }),
      });
      if (!saved.ok) throw new Error("分镜保存失败");
      const approval = await apiRequest(`/plans/${plan.id}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved: true }),
      });
      if (!approval.ok) throw new Error("分镜确认失败");
      const response = await apiRequest("/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_id: plan.id }),
      });
      if (!response.ok) throw new Error((await response.json()).detail || "任务创建失败");
      const created = await response.json();
      const next = { id: created.job_id, status: "QUEUED", stage: "PREPARE", progress: 0, created_at: new Date().toISOString(), kind: asset.kind };
      setJob(next); setJobs((value) => [next, ...value]); setToast(["success", "预演任务已提交，正在后台执行。"]);
    } catch (error) { setToast(["danger", error.message]); } finally { setBusy(false); }
  }
  function updateOutput(eventValue) {
    const [width, height] = eventValue.split("x").map((value) => Number(value));
    setOutput(outputPresets.find((item) => item.width === width && item.height === height) || outputPresets[0]);
  }
  async function cancel() { if (job) { await apiRequest(`/jobs/${job.id}/cancel`, { method: "POST" }); refresh(); } }
  async function retry() { if (job) { await apiRequest(`/jobs/${job.id}/retry`, { method: "POST" }); refresh(); } }
  async function aiGenerate() {
    if (!asset) return setToast(["danger", "请先上传产品图片或 GLB。"]);
    setBusy(true);
    try {
      const draft = await apiRequest("/director/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ intent: intent || "展示产品外观与细节", product_asset_id: asset.id }),
      });
      if (!draft.ok) throw new Error((await draft.json()).detail || "AI 导演生成失败");
      const generated = await draft.json();
      const created = await apiRequest("/plans/template", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          product_asset_id: asset.id,
          intent: generated.plan.intent,
          ratio: "9:16",
          duration_seconds: 6,
          output,
          crop_anchor: cropAnchor,
        }),
      });
      if (!created.ok) throw new Error((await created.json()).detail || "计划创建失败");
      const base = await created.json();
      const saved = await apiRequest(`/plans/${base.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ intent: generated.plan.intent, shots: generated.plan.shots, crop_anchor: cropAnchor }),
      });
      if (!saved.ok) throw new Error((await saved.json()).detail || "分镜保存失败");
      const planResult = await saved.json();
      setIntent(planResult.intent);
      setPlan(planResult);
      setCropAnchor(planResult.crop_anchor || cropAnchor);
      setToast(["success", `AI 导演已生成分镜（${generated.provider}，尝试 ${generated.attempts} 次${generated.repairs.length ? "，含修复" : ""}），可直接编辑。`]);
    } catch (error) {
      setToast(["danger", error.message]);
    } finally {
      setBusy(false);
    }
  }
  async function cancelProviderJob(item) {
    try {
      const response = await apiRequest(`/providers/h3/jobs/${item.id}/cancel`, { method: "POST" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "取消失败");
      setToast(["success", `任务已取消（${providerOperationLabels[item.operation] || item.operation}）。`]);
    } catch (error) {
      setToast(["danger", error.message]);
    } finally {
      refresh();
    }
  }
  async function saveProvider(apiKey) {
    setBusy(true);
    try {
      const response = await apiRequest("/providers/minimax/credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "MiniMax 密钥验证失败");
      setProvider(result); setToast(["success", result.last_message]);
    } catch (error) { setToast(["danger", error.message]); } finally { setBusy(false); }
  }
  async function testProvider() {
    setBusy(true);
    try {
      const response = await apiRequest("/providers/minimax/test", { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "MiniMax 测试失败");
      setProvider(result); setToast([result.last_status === "GENERATION_READY" ? "success" : "danger", result.last_message]);
    } catch (error) { setToast(["danger", error.message]); } finally { setBusy(false); }
  }
  const filtered = useMemo(() => jobs.filter((item) => !search || JSON.stringify(item).toLowerCase().includes(search.toLowerCase())).slice(0, 6), [jobs, search]);
  function selectAsset(next) { setAsset(next); setAssetUrl(`${API}/assets/${next.id}/content`); setPlan(null); setJob(null); setActive("project"); }

  if (!session) return <main style={{ maxWidth: 440, margin: "12vh auto", padding: 24 }}>
    <h1>登录 ProductDirectorAI</h1>
    <p>输入此服务的访问密钥，继续你的产品项目。</p>
    <form onSubmit={async (event) => {
      event.preventDefault(); setLoggingIn(true); setLoginError("");
      try {
        const response = await apiRequest("/session", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: accessKey }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "登录失败");
        sessionCsrfToken = data.csrf_token; setSession(data);
      } catch (error) { setLoginError(error.message || "暂时无法连接服务"); }
      finally { setAccessKey(""); setLoggingIn(false); }
    }}>
      <label htmlFor="access-key">访问密钥</label>
      <input id="access-key" type="password" autoComplete="off" required value={accessKey}
        onChange={(event) => setAccessKey(event.target.value)} style={{ display: "block", width: "100%", margin: "12px 0", padding: 12 }} />
      <button type="submit" disabled={loggingIn}>{loggingIn ? "登录中…" : "进入工作台"}</button>
      {loginError && <p role="alert">{loginError}</p>}
    </form>
  </main>;

  return <div className="shell">
    <Sidebar active={active} onSelect={setActive} />
    <div className="main"><Header connected={!!health} onSearch={setSearch} /><main className="workspace">
      {["products", "assets"].includes(active) ? <Library assets={assets} onSelect={selectAsset} /> : active === "settings" ? <Settings health={health} provider={provider} onSaveProvider={saveProvider} onTestProvider={testProvider} busy={busy} /> : <>
        <div className="title-row"><div><div><h1>通用产品导演</h1><button><PencilSimple /></button><span>V1 Director MVP</span></div><p>上传任意产品图片或 GLB，确认三个镜头，然后在本机生成可追踪的预演视频。</p></div><small><CheckCircle weight="fill" />本地保存</small></div>
        <Steps asset={asset} plan={plan} job={job} />
        <div className="grid">
          <AssetCard asset={asset} assetUrl={assetUrl} onUpload={upload} onDemo={useDemo} busy={busy} />
          <DirectorCard
            intent={intent}
            setIntent={setIntent}
            plan={plan}
            output={output}
            onOutputChange={updateOutput}
            cropAnchor={cropAnchor}
            onCropAnchorChange={setCropAnchor}
            assetUrl={asset?.kind === "image" ? assetUrl : ""}
            onGenerate={generatePlan}
            onAiGenerate={aiGenerate}
            busy={busy}
          />
          <RenderCard health={health} plan={plan} job={job} onRender={run} onCancel={cancel} onRetry={retry} />
          <ProviderJobs jobs={providerJobs} onCancel={cancelProviderJob} />
          <Shots plan={plan} setPlan={setPlan} assetUrl={asset?.kind === "image" ? assetUrl : ""} />
          <Jobs jobs={filtered} selected={job?.id} onOpen={setJob} />
        </div>
      </>}
    </main></div>
    {toast && <div className={`toast ${toast[0]}`}>{toast[0] === "success" ? <CheckCircle weight="fill" /> : <WarningCircle weight="fill" />}<span>{toast[1]}</span><button onClick={() => setToast(null)}><X /></button></div>}
  </div>;
}
async function apiRequest(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (sessionCsrfToken && !["GET", "HEAD"].includes((options.method || "GET").toUpperCase())) {
    headers["X-CSRF-Token"] = sessionCsrfToken;
  }
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return fetch(`${API}${normalizedPath}`, { ...options, headers, credentials: "include" });
}
