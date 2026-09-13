import { useEffect, useMemo, useRef, useState } from "react";
import {
  Archive, ArrowClockwise, Bell, BoxArrowDown, Camera, CaretDown, CaretRight, Check, CheckCircle,
  Clock, Cpu, Cube, DownloadSimple, FilmSlate, FolderOpen, Gear, Image, ListChecks,
  MagnifyingGlass, MonitorPlay, Package, PencilSimple, Play, Plus, Queue,
  ShieldCheck, SlidersHorizontal, Sparkle, SquaresFour, StopCircle, UploadSimple, User, VideoCamera, WarningCircle, X,
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
  ["assets", "素材库", Archive], ["fidelity", "保真审核", ShieldCheck],
  ["interaction", "人物互动", User], ["reference", "参考重演", VideoCamera], ["settings", "设置", Gear],
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
    FAILED: ["失败", "danger"], QA_REJECTED: ["质检驳回", "danger"],
    SUCCEEDED: ["已完成", "success"], VERIFICATION_PASSED: ["验证通过·不可发布", "warning"],
    NOT_VERIFIED: ["未核实", "warning"],
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
  const verifiedOnly = job?.status === "VERIFICATION_PASSED";
  const list = [
    ["1", "产品素材", asset ? "已选择" : "图片 / GLB", Image, !!asset],
    ["2", "生成分镜", plan ? "3 个 Shot" : "模板导演", FilmSlate, !!plan],
    ["3", "生成预演", job?.stage || "等待执行", Camera, job?.status === "SUCCEEDED" || verifiedOnly],
    ["4", "技术检查", job?.status === "SUCCEEDED" ? "已通过" : verifiedOnly ? "验证通过·不可发布" : "自动校验", ListChecks, job?.status === "SUCCEEDED" || verifiedOnly],
  ];
  return <div className="steps">{list.map(([n, title, sub, Icon, done], index) => <div className={done ? "done" : ""} key={title}><em>{done ? <Check /> : <Icon />}</em><p><b>{n} {title}</b><small>{sub}</small></p>{index < 3 && <CaretRight className="arrow" />}</div>)}</div>;
}

function AssetCard({ asset, assetUrl, onUpload, onDemo, busy }) {
  const input = useRef(null);
  return <section className="card asset-card">
    <div className="card-head"><div><h2>产品素材</h2><i>?</i></div><button onClick={() => input.current?.click()}>管理素材 <CaretRight /></button></div>
    <div className="asset-stage">
      {asset?.kind === "model"
        ? <ModelPreview url={assetUrl} />
        : asset?.kind === "video"
          ? <video src={assetUrl} controls muted playsInline />
          : <img src={assetUrl || demoImage} alt={asset ? asset.name : "演示产品"} />}
      <span>{asset ? (asset.kind === "model" ? "GLB · 3D" : asset.kind === "video" ? "视频 · MP4" : "图片 · 2D") : "演示素材"}</span>
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
  const verificationOnly = job?.status === "VERIFICATION_PASSED";
  const releaseReason = job?.release_status?.reason;
  return <section className="card render-card">
    <div className="card-head"><div><h2>执行与检查</h2><i>?</i></div>{job && <Pill status={job.status} />}</div>
    <div className="env">{[["Blender", health?.blender, Cube], ["FFmpeg", health?.ffmpeg, FilmSlate]].map(([name, value, Icon]) => <div key={name}><em className={value?.available ? "ok" : "off"}><Icon /></em><p><strong>{name}</strong><small>{value?.available ? "本机已就绪" : "未连接"}</small></p></div>)}</div>
    {job ? <div className="job-box"><div><span>{job.stage}</span><strong>{job.progress}%</strong></div><div className="progress"><i style={{ width: `${job.progress}%` }} /></div><small>{job.status === "FAILED" ? job.error : verificationOnly ? (releaseReason || "验证小样已完成，但不可发布或继承 Strict PASS") : job.status === "SUCCEEDED" ? "视频与 metadata 已完成基础检查" : "任务在后台执行，刷新页面也不会丢失。"}</small></div> : <div className="empty-job"><Clock /><span>确认分镜后创建第一条预演任务</span></div>}
    <div className="render-actions">
      {active ? <button className="danger" onClick={onCancel}><StopCircle />取消任务</button> : <button className="primary" disabled={!plan} onClick={onRender}><Play weight="fill" />确认计划并生成预演</button>}
      {retryable && <button onClick={onRetry}><ArrowClockwise />重试任务</button>}
      {(job?.status === "SUCCEEDED" || verificationOnly) && <><a href={`${API}/jobs/${job.id}/video`} target="_blank"><DownloadSimple />{verificationOnly ? "下载验证 MP4" : "下载 MP4"}</a><a className="icon-link" title={verificationOnly ? "下载验证小样 metadata（不可发布）" : "下载 metadata"} href={`${API}/jobs/${job.id}/manifest`} target="_blank"><BoxArrowDown /></a></>}
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
    <div className="library">{assets.length ? assets.map((asset) => <button key={asset.id} onClick={() => onSelect(asset)}><div>{asset.kind === "image" ? <img src={`${API}/assets/${asset.id}/content`} alt="" /> : asset.kind === "video" ? <video src={`${API}/assets/${asset.id}/content`} muted playsInline /> : <Cube />}</div><strong>{asset.name}</strong><span>{asset.kind === "model" ? "GLB 三维产品" : asset.kind === "video" ? "H3 生成视频" : "产品图片"} · {formatBytes(asset.size_bytes)}</span></button>) : <div className="library-empty"><Package /><strong>等待你的第一个产品</strong><span>支持 PNG、JPEG、WebP 或 GLB。</span></div>}</div>
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

const fidelityTabs = [["versions", "版本审核"], ["qa", "质检报告"], ["viewer", "通道查看"]];
const viewerChannels = [
  ["product", "产品层 product"], ["mask", "遮罩 mask"], ["background", "背景 background"],
  ["composite", "合成 composite"], ["passes_mask", "通道遮罩 passes/mask"],
];
const checkLabels = {
  frame_completeness: "帧完整性", color_core: "核心区颜色", edge: "边缘带", contour: "轮廓",
  logo: "Logo 保护", product_id: "产品身份", dimension: "尺寸", encoded_media: "编码成片",
  asset_hash: "资产哈希",
};

function FidelityPage({ jobs }) {
  const [tab, setTab] = useState("versions");
  const [versions, setVersions] = useState([]);
  const [selectedVersionId, setSelectedVersionId] = useState("");
  const [reviews, setReviews] = useState([]);
  const [policies, setPolicies] = useState([]);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [qaRows, setQaRows] = useState([]);
  const [report, setReport] = useState(null);
  const [channel, setChannel] = useState("product");
  const [frame, setFrame] = useState(1);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    apiRequest("/product-versions").then(async (response) => {
      if (response.ok) {
        const rows = await response.json();
        setVersions(rows);
        if (rows[0]) loadVersion(rows[0].id);
      }
    }).catch(() => {});
  }, []);
  useEffect(() => {
    if (notice) { const timer = setTimeout(() => setNotice(null), 5000); return () => clearTimeout(timer); }
  }, [notice]);

  async function loadVersion(id) {
    setSelectedVersionId(id); setReviews([]); setPolicies([]);
    const [rv, pl] = await Promise.all([
      apiRequest(`/product-versions/${id}/reviews`),
      apiRequest(`/product-versions/${id}/fidelity-policies`),
    ]);
    if (rv.ok) setReviews(await rv.json());
    if (pl.ok) setPolicies(await pl.json());
  }
  async function loadQa(jobId) {
    setSelectedJobId(jobId); setReport(null); setQaRows([]);
    const response = await apiRequest(`/qa-reports?job_id=${encodeURIComponent(jobId)}`);
    if (response.ok) setQaRows(await response.json());
  }
  async function openReport(id) {
    const response = await apiRequest(`/qa-reports/${id}`);
    if (response.ok) setReport(await response.json());
  }
  async function decide(decision) {
    if (!report) return; setBusy(true);
    try {
      const response = await apiRequest(`/qa-reports/${report.id}/decisions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, manifest_hash: report.manifest_sha256, notes: note }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "审批提交失败");
      setNotice(["success", `已提交：${decision === "APPROVED" ? "批准" : "拒绝"}（Manifest ${String(report.manifest_sha256).slice(0, 12)}…）`]);
      openReport(report.id);
      if (selectedJobId) loadQa(selectedJobId);
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  function jumpToProblem(problem) {
    setChannel("composite"); setFrame(problem.frame); setTab("viewer");
  }

  const selectedVersion = versions.find((item) => item.id === selectedVersionId);
  const review = reviews[0];
  const problems = report?.report?.problems || [];
  const reportChecks = report?.report?.checks || {};
  const canApprove = report?.status === "PASS";
  const frameMax = report?.report?.frame_count || 144;

  return <section className="page">
    <div className="page-head"><div><b>V3 PRODUCT FIDELITY</b><h1>产品保真审核</h1><p>版本审核与保真策略、质检报告、问题帧定位和通道查看。批准按钮始终注明所批 Manifest hash。</p></div></div>
    <div className="tabs">{fidelityTabs.map(([id, label]) => <button className={tab === id ? "active" : ""} onClick={() => setTab(id)} key={id}>{label}</button>)}</div>
    {notice && <div className={`notice ${notice[0]}`}><span>{notice[1]}</span><button onClick={() => setNotice(null)}><X /></button></div>}

    {tab === "versions" && <div className="fidelity-grid">
      <section className="card">
        <div className="card-head"><div><h2>产品版本</h2><span className="count">{versions.length}</span></div><small>批准后的版本不可改写；改动会生成新版本</small></div>
        {versions.length ? <div className="table-wrap"><table><thead><tr><th>版本</th><th>状态</th><th>创建时间</th><th /></tr></thead><tbody>{versions.map((item) => <tr className={selectedVersionId === item.id ? "selected" : ""} key={item.id}><td><strong>v{item.version}</strong> <small>{item.id.slice(0, 8)}</small></td><td><Pill status={item.status === "APPROVED" ? "SUCCEEDED" : "DRAFT"} /></td><td>{formatTime(item.created_at)}</td><td><button onClick={() => loadVersion(item.id)}>查看</button></td></tr>)}</tbody></table></div> : <div className="table-empty"><Cube /><strong>还没有产品版本</strong><span>批准计划后会生成产品版本。</span></div>}
      </section>
      <section className="card">
        <div className="card-head"><div><h2>版本审核</h2>{review && <Pill status={review.decision === "APPROVED" ? "SUCCEEDED" : "DRAFT"} />}</div><small>冻结记录 · 哈希 {review?.payload_sha256?.slice(0, 12) || "—"}…</small></div>
        {review ? <div className="kv-list">
          <p><b>审核结论</b><span>{review.decision === "APPROVED" ? "已批准" : review.decision}</span></p>
          <p><b>来源类型</b><span>{review.review.source_kind === "cad" ? "官方 CAD / 3D" : review.review.source_kind === "image" ? "单图重建（未观测面保持 unverified）" : review.review.source_kind || "—"}</span></p>
          <p><b>已核实尺寸</b><span>{Object.entries(review.review.verified_dimensions || {}).map(([k, v]) => `${k}: ${v}mm`).join(" · ") || "未提供"}</span></p>
          <p><b>视角覆盖</b><span>{(review.review.view_coverage || []).join("、") || "未声明"}</span></p>
          <p><b>未核实区域</b><span>{(review.review.unverified_regions || []).map((r) => `${r.region || r.label || JSON.stringify(r)}`).join("；") || "无"}</span></p>
          <p><b>Logo 区域</b><span>{(review.review.logo_regions || []).map((r) => `${r.label || "logo"} (${r.x}, ${r.y}, ${r.width} × ${r.height})`).join("；") || "未标记"}</span></p>
          <p><b>相机可见性限制</b><span>{(review.review.camera_visibility_constraints || []).join("；") || "无"}</span></p>
          {review.review.notes && <p><b>备注</b><span>{review.review.notes}</span></p>}
        </div> : <div className="table-empty"><ListChecks /><strong>该版本还没有冻结审核</strong><span>提交审核后核实结果会绑定到版本。</span></div>}
      </section>
      <section className="card">
        <div className="card-head"><div><h2>保真策略</h2><span className="count">{policies.length}</span></div><small>策略版本化、不可改写</small></div>
        {policies.length ? <div className="table-wrap"><table><thead><tr><th>版本</th><th>模式</th><th>保护区域</th><th>允许操作</th></tr></thead><tbody>{policies.map((item) => <tr key={item.id}><td><strong>v{item.version}</strong></td><td><Pill status={item.mode === "STRICT" ? "SUCCEEDED" : "DRAFT"} /></td><td>{(item.policy.protected_regions || []).map((r) => r.label || r.region || JSON.stringify(r)).join("、") || "—"}</td><td>{(item.policy.allowed_operations || []).join("、") || "—"}</td></tr>)}</tbody></table></div> : <div className="table-empty"><ShieldCheck /><strong>还没有保真策略</strong><span>STRICT 模式会保护已批准资产。</span></div>}
      </section>
    </div>}

    {tab === "qa" && <div className="fidelity-grid">
      <section className="card">
        <div className="card-head"><div><h2>任务与质检报告</h2><span className="count">{qaRows.length}</span></div><small>选择任务查看其 QA 报告</small></div>
        <div className="qa-pick">
          <select value={selectedJobId} onChange={(event) => loadQa(event.target.value)}>
            <option value="">选择渲染任务…</option>
            {jobs.map((item) => <option key={item.id} value={item.id}>任务 {item.id.slice(0, 8)} · {item.status}</option>)}
          </select>
        </div>
        {qaRows.length ? <div className="table-wrap"><table><thead><tr><th>报告</th><th>状态</th><th>阈值集</th><th>审批</th><th /></tr></thead><tbody>{qaRows.map((item) => <tr className={report?.id === item.id ? "selected" : ""} key={item.id}><td><strong>{item.id.slice(0, 8)}</strong><small> {formatTime(item.created_at)}</small></td><td><Pill status={item.status === "PASS" ? "SUCCEEDED" : item.status === "FAIL" ? "QA_REJECTED" : "NOT_VERIFIED"} /></td><td><small>{item.threshold_set_version}</small></td><td>{item.decision ? `${item.decision}${item.decision_valid === false ? " · 已失效" : ""}` : "待审批"}</td><td><button onClick={() => openReport(item.id)}>查看</button></td></tr>)}</tbody></table></div> : <div className="table-empty"><ListChecks /><strong>该任务还没有 QA 报告</strong><span>Strict 运行闭环会生成质检报告。</span></div>}
      </section>
      {report && <section className="card qa-detail">
        <div className="card-head"><div><h2>QA 报告 {report.id.slice(0, 8)}</h2><Pill status={report.status === "PASS" ? "SUCCEEDED" : report.status === "FAIL" ? "QA_REJECTED" : "NOT_VERIFIED"} /></div><small>阈值集 {report.threshold_set_id} · {report.threshold_set_version}</small></div>
        <div className="kv-list">
          <p><b>Manifest 哈希</b><span className="mono">{report.manifest_sha256}</span></p>
          <p><b>绑定哈希</b><span className="mono">{report.binding_sha256}</span></p>
          <p><b>当前审批</b><span>{report.effective_decision || "未审批"}{report.decision_valid === false ? ` · 已失效：${report.decision_invalid_reason || "输入已变化"}` : ""}</span></p>
          <p><b>未核实项</b><span>{(report.report.not_verified || []).join("；") || "无"}</span></p>
        </div>
        <h3>检查项</h3>
        <div className="table-wrap"><table><thead><tr><th>检查</th><th>状态</th><th>摘要</th></tr></thead><tbody>{Object.entries(reportChecks).map(([key, check]) => <tr key={key}><td>{checkLabels[key] || key}</td><td><Pill status={check.status === "PASS" ? "SUCCEEDED" : check.status === "FAIL" ? "QA_REJECTED" : "DRAFT"} /></td><td><small>{check.reason || Object.entries(check).filter(([k]) => k !== "status").map(([k, v]) => `${k}=${v}`).join(" · ")}</small></td></tr>)}</tbody></table></div>
        <h3>问题帧 {problems.length ? `（${problems.length}）` : ""}</h3>
        {problems.length ? <div className="table-wrap"><table><thead><tr><th>帧</th><th>镜头</th><th>镜内帧</th><th>检查</th><th>原因</th><th /></tr></thead><tbody>{problems.map((problem, index) => <tr key={index}><td><strong>#{problem.frame}</strong></td><td>{problem.shot_name || problem.shot || "—"}</td><td>{problem.shot_frame || "—"}</td><td>{checkLabels[problem.check] || problem.check}</td><td><small>{problem.reason}</small></td><td><button onClick={() => jumpToProblem(problem)}>定位</button></td></tr>)}</tbody></table></div> : <p className="qa-empty-note">没有帧级问题。</p>}
        <div className="qa-decision">
          <label><span>审批备注</span><input value={note} onChange={(event) => setNote(event.target.value)} placeholder="审批说明（可选）" /></label>
          <button className="primary" disabled={busy || !canApprove} title={canApprove ? `批准 Manifest ${report.manifest_sha256}` : "QA 状态不是 PASS，不能批准为 Strict 成果"} onClick={() => decide("APPROVED")}><Check weight="fill" />批准（Manifest {String(report.manifest_sha256).slice(0, 10)}…）</button>
          <button className="danger" disabled={busy} onClick={() => decide("REJECTED")}><X />拒绝</button>
        </div>
      </section>}
    </div>}

    {tab === "viewer" && <div className="fidelity-grid">
      <section className="card channel-viewer">
        <div className="card-head"><div><h2>通道查看器</h2></div><small>beauty/alpha/depth/normal 为 EXR 原始通道，当前只提供可在线预览的 PNG 通道</small></div>
        {report ? <div className="viewer-controls">
          <label><span>通道</span><select value={channel} onChange={(event) => setChannel(event.target.value)}>{viewerChannels.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label><span>帧（1–{frameMax}）</span><input type="number" min="1" max={frameMax} value={frame} onChange={(event) => setFrame(Math.max(1, Math.min(frameMax, Number(event.target.value) || 1)))} /></label>
          <span className="viewer-src">Run {report.run_id.slice(0, 8)}</span>
        </div> : <div className="table-empty"><MonitorPlay /><strong>先选择一份 QA 报告</strong><span>查看器按报告的 Run 定位 strict 产物目录。</span></div>}
        {report && <div className="viewer-stage"><img key={`${report.run_id}-${channel}-${frame}`} src={`${API}/runs/${report.run_id}/strict-artifacts/${channel}/${frame}`} alt={`${channel} 帧 ${frame}`} onError={(event) => { event.currentTarget.style.opacity = 0.25; }} /></div>}
        {report && problems.length > 0 && <div className="viewer-problems"><b>问题帧快捷跳转</b>{problems.slice(0, 12).map((problem, index) => <button key={index} onClick={() => { setChannel("composite"); setFrame(problem.frame); }}>#{problem.frame}</button>)}</div>}
      </section>
    </div>}
  </section>;
}

const interactionActions = [
  ["press_button", "按按钮"], ["single_punch_target", "击打指定靶点"],
  ["approach", "走近产品"], ["celebrate", "庆祝"],
];

function InteractionPage() {
  const [plans, setPlans] = useState([]);
  const [characters, setCharacters] = useState([]);
  const [selectedPlanId, setSelectedPlanId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [anchors, setAnchors] = useState([]);
  const [anchorSets, setAnchorSets] = useState([]);
  const [interactions, setInteractions] = useState([]);
  const [selectedInteraction, setSelectedInteraction] = useState(null);
  const [validation, setValidation] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    character_id: "", anchor_set_id: "", action: "press_button",
    prepare_frame: 10, contact_frame: 46, end_frame: 60, speed_scale: 1.0, notes: "",
  });

  useEffect(() => {
    Promise.all([apiRequest("/plans"), apiRequest("/characters")]).then(async ([p, c]) => {
      if (p.ok) setPlans(await p.json());
      if (c.ok) setCharacters(await c.json());
    }).catch(() => {});
  }, []);
  useEffect(() => {
    if (notice) { const timer = setTimeout(() => setNotice(null), 5000); return () => clearTimeout(timer); }
  }, [notice]);

  async function selectPlan(planId) {
    setSelectedPlanId(planId); setAnchors([]); setAnchorSets([]); setInteractions([]);
    setSelectedInteraction(null); setValidation(null); setTimeline(null);
    const contracts = await apiRequest(`/plans/${planId}/contracts`);
    if (!contracts.ok) return;
    const rows = await contracts.json();
    if (!rows[0]) return;
    setVersionId(rows[0].product_version_id);
    const [a, s, i] = await Promise.all([
      apiRequest(`/product-versions/${rows[0].product_version_id}/anchors`),
      apiRequest(`/product-versions/${rows[0].product_version_id}/anchor-sets`),
      apiRequest(`/plans/${planId}/interactions`),
    ]);
    if (a.ok) setAnchors(await a.json());
    if (s.ok) setAnchorSets(await s.json());
    if (i.ok) setInteractions(await i.json());
  }

  async function createInteraction() {
    if (!selectedPlanId || !form.character_id || !form.anchor_set_id) {
      setNotice(["danger", "请先选择计划、人物与已批准的锚点集。"]);
      return;
    }
    setBusy(true);
    try {
      const response = await apiRequest(`/plans/${selectedPlanId}/interactions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, prepare_frame: Number(form.prepare_frame), contact_frame: Number(form.contact_frame), end_frame: Number(form.end_frame), speed_scale: Number(form.speed_scale) }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "交互计划创建失败");
      setNotice(["success", `交互计划 v${body.version} 已创建。`]);
      await selectPlan(selectedPlanId);
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }

  async function validate(interaction) {
    setSelectedInteraction(interaction); setValidation(null); setTimeline(null);
    const [v, p] = await Promise.all([
      apiRequest(`/interaction-plans/${interaction.id}/validate`, { method: "POST" }),
      apiRequest(`/interaction-plans/${interaction.id}/previz`, { method: "POST" }),
    ]);
    if (v.ok) setValidation(await v.json());
    if (p.ok) setTimeline(await p.json());
  }

  const selectedVersionAnchors = anchors.filter((item) => item.revision === anchorSets[0]?.revision);
  const maxHandZ = timeline ? Math.max(...timeline.frames.map((f) => f.hand_z_m), 1) : 1;

  return <section className="page">
    <div className="page-head"><div><b>V4 HUMAN INTERACTION</b><h1>人物与互动</h1><p>人物规格、产品锚点与交互计划：校验接触/穿透/时序并生成预演时间线。真人感人物层需经批准的工作流或授权素材（能力页如实报告）。</p></div></div>
    {notice && <div className={`notice ${notice[0]}`}><span>{notice[1]}</span><button onClick={() => setNotice(null)}><X /></button></div>}
    <div className="fidelity-grid">
      <section className="card">
        <div className="card-head"><div><h2>计划与锚点</h2><span className="count">{anchors.length}</span></div><small>锚点绑定产品版本，版本升级后需重新映射</small></div>
        <div className="qa-pick">
          <select value={selectedPlanId} onChange={(event) => selectPlan(event.target.value)}>
            <option value="">选择计划…</option>
            {plans.map((item) => <option key={item.id} value={item.id}>计划 {item.id.slice(0, 8)} · {item.intent.slice(0, 24)}</option>)}
          </select>
        </div>
        {anchors.length ? <div className="table-wrap"><table><thead><tr><th>修订</th><th>名称</th><th>位置（bbox 归一化）</th><th>半径</th><th>允许动作</th><th>状态</th></tr></thead><tbody>{anchors.map((item) => <tr key={item.id}><td>r{item.revision}</td><td>{item.name}</td><td><small>{item.anchor.position_m.map((v) => v.toFixed(2)).join(", ")}</small></td><td>{item.anchor.radius_m}</td><td><small>{item.anchor.allowed_actions.join("、")}</small></td><td><Pill status={item.status === "APPROVED" ? "SUCCEEDED" : "DRAFT"} /></td></tr>)}</tbody></table></div> : <div className="table-empty"><ShieldCheck /><strong>还没有锚点</strong><span>选择计划后在「保真审核」创建并批准锚点集。</span></div>}
        {anchorSets.length > 0 && <p className="qa-empty-note">已冻结锚点集：revision {anchorSets.map((s) => s.revision).join("、")}</p>}
      </section>
      <section className="card">
        <div className="card-head"><div><h2>人物</h2><span className="count">{characters.length}</span></div><small>合成 Proxy 可用于预演；真人感成片需授权素材或批准工作流</small></div>
        {characters.length ? <div className="table-wrap"><table><thead><tr><th>名称</th><th>来源</th><th>身高</th><th>授权记录</th></tr></thead><tbody>{characters.map((item) => <tr key={item.id}><td>{item.name}</td><td>{item.character.source === "licensed_asset" ? "授权素材" : "合成 Proxy"}</td><td><small>{item.character.height_range_m.join("–")}m</small></td><td>{item.character.source === "licensed_asset" ? (item.character.license_record ? "已记录" : "缺记录") : "—"}</td></tr>)}</tbody></table></div> : <div className="table-empty"><User /><strong>还没有人物</strong><span>在 API 创建人物规格（/characters）。</span></div>}
      </section>
      <section className="card">
        <div className="card-head"><div><h2>新建交互计划</h2></div><small>动作帧数必须落在计划总时长内</small></div>
        <div className="interaction-form">
          <label><span>人物</span><select value={form.character_id} onChange={(e) => setForm({ ...form, character_id: e.target.value })}><option value="">选择人物…</option>{characters.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label><span>锚点集</span><select value={form.anchor_set_id} onChange={(e) => setForm({ ...form, anchor_set_id: e.target.value })}><option value="">选择已批准锚点集…</option>{anchorSets.map((item) => <option key={item.id} value={item.id}>revision {item.revision}</option>)}</select></label>
          <label><span>动作</span><select value={form.action} onChange={(e) => setForm({ ...form, action: e.target.value })}>{interactionActions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label><span>准备帧</span><input type="number" min="1" value={form.prepare_frame} onChange={(e) => setForm({ ...form, prepare_frame: e.target.value })} /></label>
          <label><span>接触帧</span><input type="number" min="1" value={form.contact_frame} onChange={(e) => setForm({ ...form, contact_frame: e.target.value })} /></label>
          <label><span>结束帧</span><input type="number" min="1" value={form.end_frame} onChange={(e) => setForm({ ...form, end_frame: e.target.value })} /></label>
          <label><span>变速</span><input type="number" min="0.5" max="1.5" step="0.05" value={form.speed_scale} onChange={(e) => setForm({ ...form, speed_scale: e.target.value })} /></label>
          <button className="primary" disabled={busy} onClick={createInteraction}><Plus />创建交互计划</button>
        </div>
      </section>
      <section className="card">
        <div className="card-head"><div><h2>交互计划</h2><span className="count">{interactions.length}</span></div><small>修改 = 新版本；计划更新后旧交互失效</small></div>
        {interactions.length ? <div className="table-wrap"><table><thead><tr><th>版本</th><th>动作</th><th>帧</th><th>创建时间</th><th /></tr></thead><tbody>{interactions.map((item) => <tr className={selectedInteraction?.id === item.id ? "selected" : ""} key={item.id}><td>v{item.version}</td><td>{interactionActions.find(([v]) => v === item.interaction.action)?.[1] || item.interaction.action}</td><td><small>{item.interaction.prepare_frame} / {item.interaction.contact_frame} / {item.interaction.end_frame}</small></td><td>{formatTime(item.created_at)}</td><td><button onClick={() => validate(item)}>校验</button></td></tr>)}</tbody></table></div> : <div className="table-empty"><FilmSlate /><strong>还没有交互计划</strong><span>选择计划与锚点集后创建。</span></div>}
        {validation && <div className="validation-report">
          <h3>校验 {validation.passed ? <Pill status="SUCCEEDED" /> : <Pill status="QA_REJECTED" />}</h3>
          {validation.failures.map((failure, index) => <p className="validation-fail" key={index}><WarningCircle weight="fill" />{failure}</p>)}
          {Object.entries(validation.metrics?.timing || {}).length > 0 && <p><b>时序</b> 偏差 {validation.metrics.timing.deviation_frames} 帧（容差 ±{validation.metrics.timing.tolerance_frames}）</p>}
          {Object.entries(validation.metrics?.contact || {}).length > 0 && <p><b>接触</b> 距离 {validation.metrics.contact.contact_distance_m}m（阈值 {validation.metrics.contact.threshold_m}m）</p>}
          {Object.entries(validation.metrics?.penetration || {}).length > 0 && <p><b>穿透</b> {validation.metrics.penetration.penetration_m}m（容差 {validation.metrics.penetration.tolerance_m}m）</p>}
          {validation.metrics?.capabilities && Object.entries(validation.metrics.capabilities).length > 0 && <p><b>能力</b> 可达带 {validation.metrics.capabilities.reach_low_m}–{validation.metrics.capabilities.reach_high_m}m</p>}
        </div>}
        {timeline && <div className="timeline">
          <h3>预演时间线（确定性代理手部高度）</h3>
          <div className="timeline-events">{timeline.events.map((event) => <span key={event.event}>● {event.event}@{event.frame}</span>)}</div>
          <div className="timeline-bars">{timeline.frames.map((frame) => <i key={frame.frame} className={frame.event ? "marked" : ""} style={{ height: `${Math.max(6, (frame.hand_z_m / maxHandZ) * 100)}%` }} title={`帧 ${frame.frame} · ${frame.hand_z_m}m${frame.event ? ` · ${frame.event}` : ""}`} />)}</div>
        </div>}
      </section>
    </div>
  </section>;
}

const reuseDimensions = [
  ["duration", "镜头长度"], ["shot_size", "景别"], ["composition", "构图"],
  ["motion_direction", "运动方向"], ["action_rhythm", "动作节奏"], ["transition", "转场"],
];
const cameraLabelsV5 = { static: "Static · 定格", side_track: "Side Track · 侧移", dolly_in: "Dolly In · 推近", hero_orbit: "Hero Orbit · 环绕" };

function ReferencePage() {
  const [references, setReferences] = useState([]);
  const [versions, setVersions] = useState([]);
  const [plans, setPlans] = useState([]);
  const [selectedRefId, setSelectedRefId] = useState("");
  const [analyses, setAnalyses] = useState([]);
  const [versionId, setVersionId] = useState("");
  const [dimensions, setDimensions] = useState(["duration", "motion_direction", "transition"]);
  const [mapping, setMapping] = useState(null);
  const [targetPlan, setTargetPlan] = useState(null);
  const [selectedPlanId, setSelectedPlanId] = useState("");
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([apiRequest("/references"), apiRequest("/product-versions"), apiRequest("/plans")]).then(async ([r, v, p]) => {
      if (r.ok) setReferences(await r.json());
      if (v.ok) setVersions(await v.json());
      if (p.ok) setPlans(await p.json());
    }).catch(() => {});
  }, []);
  useEffect(() => {
    if (notice) { const timer = setTimeout(() => setNotice(null), 5000); return () => clearTimeout(timer); }
  }, [notice]);

  async function selectReference(referenceId) {
    setSelectedRefId(referenceId); setAnalyses([]); setMapping(null); setTargetPlan(null);
    const response = await apiRequest(`/references/${referenceId}/analyses`);
    if (response.ok) setAnalyses(await response.json());
  }
  function toggleDimension(dimension) {
    setDimensions((value) => value.includes(dimension) ? value.filter((item) => item !== dimension) : [...value, dimension]);
  }
  async function createPlan() {
    const approved = analyses.find((item) => item.status === "APPROVED");
    if (!approved || !versionId) { setNotice(["danger", "请选择已批准的分析与产品版本。"]); return; }
    setBusy(true);
    try {
      const response = await apiRequest("/projects/project-default/plans/from-reference", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ analysis_id: approved.id, product_version_id: versionId, selected_dimensions: dimensions }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "计划生成失败");
      setMapping(body.reference_mapping);
      setTargetPlan({ id: body.id, shots: body.shots, output: body.output });
      setSelectedPlanId(body.id);
      setNotice(["success", `目标计划已生成（${body.shots.length} 个镜头，总时长误差 ${body.reference_mapping.total_duration_error_frames} 帧）。`]);
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function loadMapping(planId) {
    setSelectedPlanId(planId); setMapping(null); setTargetPlan(null);
    const [plan, mappingResponse] = await Promise.all([
      apiRequest(`/plans/${planId}`),
      apiRequest(`/plans/${planId}/reference-mapping`),
    ]);
    if (plan.ok) { const body = await plan.json(); setTargetPlan({ id: body.id, shots: body.payload?.shots || [], output: body.payload?.output || {} }); }
    if (mappingResponse.ok) setMapping((await mappingResponse.json()).mapping);
  }
  const selectedReference = references.find((item) => item.id === selectedRefId);
  const totalSource = mapping ? Math.max(...mapping.shots.map((shot) => shot.source_range_s[1] || 0), 1) : 1;
  const totalTarget = mapping ? mapping.total_frames : 1;

  return <section className="page">
    <div className="page-head"><div><b>V5 REFERENCE RE-ENACTMENT</b><h1>参考重演</h1><p>左栏参考视频与切镜观察，右栏目标分镜与对齐时间线。景别/动作节奏等无模型支持的维度如实标 unsupported，不悄悄复用。</p></div></div>
    {notice && <div className={`notice ${notice[0]}`}><span>{notice[1]}</span><button onClick={() => setNotice(null)}><X /></button></div>}
    <div className="reference-grid">
      <section className="card">
        <div className="card-head"><div><h2>参考视频</h2><span className="count">{references.length}</span></div><small>上传与摄取见 API（/references/upload）</small></div>
        <div className="qa-pick">
          <select value={selectedRefId} onChange={(event) => selectReference(event.target.value)}>
            <option value="">选择参考视频…</option>
            {references.map((item) => <option key={item.id} value={item.id}>参考 {item.id.slice(0, 8)} · {item.status}</option>)}
          </select>
        </div>
        {selectedReference?.status === "READY" && <video className="reference-player" src={`${API}/references/${selectedReference.id}/proxy`} controls muted playsInline />}
        {analyses.length > 0 && <div className="table-wrap"><table><thead><tr><th>修订</th><th>状态</th><th>切点数</th><th /></tr></thead><tbody>{analyses.map((item) => <tr key={item.id}><td>r{item.revision}{item.edited_from_revision ? `（改自 r${item.edited_from_revision}）` : ""}</td><td><Pill status={item.status === "APPROVED" ? "SUCCEEDED" : "DRAFT"} /></td><td>{item.analysis.cuts.length}</td><td><button onClick={() => selectReference(selectedRefId)}>刷新</button></td></tr>)}</tbody></table></div>}
        {analyses.length > 0 && <div className="segment-observations">
          <h3>切镜观察（观察与推断分离）</h3>
          {analyses[0].analysis.segments.map((segment) => <div className="segment-block" key={segment.index}>
            <b>段 {segment.index + 1} · {segment.start_s}s–{segment.end_s ?? "末"}s{segment.transition_in ? ` · ${segment.transition_in}` : ""}</b>
            {Object.entries(segment.observations || {}).map(([key, item]) => <p key={key}><span>{key}</span> {item.value === null ? "—（不可推断）" : String(item.value)}{item.confidence != null ? ` · 置信 ${Number(item.confidence).toFixed(2)}` : ""}<small> {item.method}</small></p>)}
          </div>)}
        </div>}
      </section>
      <section className="card">
        <div className="card-head"><div><h2>目标计划</h2>{mapping && <small>总时长误差 {mapping.total_duration_error_frames} 帧</small>}</div><small>仅已批准分析版本可生成</small></div>
        <div className="qa-pick">
          <select value={selectedPlanId} onChange={(event) => loadMapping(event.target.value)}>
            <option value="">选择已有计划…</option>
            {plans.map((item) => <option key={item.id} value={item.id}>计划 {item.id.slice(0, 8)} · {item.intent.slice(0, 20)}</option>)}
          </select>
        </div>
        <div className="reuse-dimensions">{reuseDimensions.map(([value, label]) => <label key={value}><input type="checkbox" checked={dimensions.includes(value)} onChange={() => toggleDimension(value)} />{label}</label>)}</div>
        <div className="qa-pick">
          <select value={versionId} onChange={(event) => setVersionId(event.target.value)}>
            <option value="">选择产品版本…</option>
            {versions.map((item) => <option key={item.id} value={item.id}>v{item.version} · {item.id.slice(0, 8)}</option>)}
          </select>
        </div>
        <button className="primary wide" disabled={busy || !selectedRefId} onClick={createPlan}><Sparkle weight="fill" />从参考生成目标计划</button>
        {targetPlan && <div className="table-wrap"><table><thead><tr><th>镜头</th><th>机位</th><th>帧数</th><th>时长误差</th></tr></thead><tbody>{targetPlan.shots.map((shot, index) => <tr key={shot.id}><td>SHOT {index + 1}</td><td>{cameraLabelsV5[shot.camera] || shot.camera}</td><td>{shot.duration_frames}</td><td>{mapping?.shots[index]?.duration_error_frames ?? "—"}</td></tr>)}</tbody></table></div>}
        {mapping && <div className="mapping-diff">
          <h3>映射差异（保留/修改/不支持）</h3>
          {mapping.shots.map((shot) => <p key={shot.target_shot_id}><b>{shot.target_shot_id}</b> 源 {shot.source_range_s[0]}s–{shot.source_range_s[1] ?? "末"}s → 帧 {shot.target_frames[0]}–{shot.target_frames[1]}<br /><small>保留: {shot.kept_dimensions.join("、") || "无"} · 修改: {shot.changed_dimensions.join("、")} · 不支持: {shot.unsupported_dimensions.join("、") || "无"}</small></p>)}
          {mapping.capability_notes.map((note, index) => <p className="capability-note" key={index}><WarningCircle weight="fill" />{note}</p>)}
        </div>}
      </section>
    </div>
    {mapping && <section className="card aligned-timeline">
      <div className="card-head"><div><h2>对齐时间线</h2></div><small>上：源片段（秒） · 下：目标镜头（帧）</small></div>
      <div className="timeline-row source">{mapping.shots.map((shot) => <i key={shot.source_segment_index} style={{ width: `${(((shot.source_range_s[1] || totalSource) - shot.source_range_s[0]) / totalSource) * 100}%` }} title={`源 ${shot.source_range_s[0]}–${shot.source_range_s[1]}s`} />)}</div>
      <div className="timeline-row target">{mapping.shots.map((shot) => <i key={shot.target_shot_id} style={{ width: `${((shot.target_frames[1] - shot.target_frames[0] + 1) / totalTarget) * 100}%` }} title={`${shot.target_shot_id} 帧 ${shot.target_frames[0]}–${shot.target_frames[1]}`} />)}</div>
    </section>}
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
      {["products", "assets"].includes(active) ? <Library assets={assets} onSelect={selectAsset} /> : active === "fidelity" ? <FidelityPage jobs={jobs} /> : active === "interaction" ? <InteractionPage /> : active === "reference" ? <ReferencePage /> : active === "settings" ? <Settings health={health} provider={provider} onSaveProvider={saveProvider} onTestProvider={testProvider} busy={busy} /> : <>
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
