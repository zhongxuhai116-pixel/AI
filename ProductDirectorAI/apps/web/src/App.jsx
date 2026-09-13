import { useEffect, useMemo, useRef, useState } from "react";
import {
  Archive, ArrowClockwise, Bell, BoxArrowDown, Camera, CaretDown, CaretRight, Check, CheckCircle,
  Clock, Coins, Cpu, Cube, DownloadSimple, FilmSlate, FolderOpen, Gauge, Gear, Image, ListChecks,
  MagnifyingGlass, MonitorPlay, Package, PencilSimple, Play, Plug, Plus, Queue,
  ShieldCheck, SlidersHorizontal, Sparkle, SquaresFour, StopCircle, UploadSimple, User, VideoCamera, WarningCircle, X,
} from "@phosphor-icons/react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

const API_BASE = (import.meta.env.VITE_API_BASE || "").trim().replace(/\/$/, "");
const API = `${API_BASE}/api/v1`;
let sessionCsrfToken = "";
const demoImage = "/assets/boxing-trainer.png";
const navItems = [
  ["console", "总控台", Gauge], ["project", "项目", SquaresFour], ["products", "产品库", Cube],
  ["director", "导演台", SlidersHorizontal], ["storyboard", "分镜", FilmSlate],
  ["preview", "3D 预演", MonitorPlay], ["jobs", "渲染任务", Queue],
  ["assets", "素材库", Archive], ["fidelity", "保真审核", ShieldCheck],
  ["interaction", "人物互动", User], ["reference", "参考重演", VideoCamera],
  ["profiles", "平台配置", SquaresFour], ["batch", "批次生产", Queue],
  ["audio", "音频与音乐", MonitorPlay], ["packages", "发布包", Package], ["costs", "成本与用量", Coins], ["automation", "自动化接入", Plug], ["webhooks", "事件与 Webhook", Bell], ["settings", "设置", Gear],
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

function ProfilePage() {
  const [profiles, setProfiles] = useState([]);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [versions, setVersions] = useState([]);
  const [validation, setValidation] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [output, setOutput] = useState({ width: 540, height: 960, fps: 24, frame_count: 144 });

  useEffect(() => { refresh(); }, []);
  useEffect(() => { if (selected) loadDetail(selected); }, [selected]);
  useEffect(() => { if (notice) { const timer = setTimeout(() => setNotice(null), 6000); return () => clearTimeout(timer); } }, [notice]);

  async function refresh() {
    const response = await apiRequest("/platform-profiles");
    if (response.ok) {
      const body = await response.json();
      setProfiles(body);
      if (!selected && body.length) setSelected(body[0].profile_key);
    }
  }
  async function loadDetail(profileId) {
    const [detailResponse, versionsResponse] = await Promise.all([
      apiRequest(`/platform-profiles/${profileId}`),
      apiRequest(`/platform-profiles/${profileId}/versions`),
    ]);
    setValidation(null);
    if (detailResponse.ok) setDetail(await detailResponse.json());
    if (versionsResponse.ok) setVersions(await versionsResponse.json());
  }
  async function validateWithOutput() {
    setBusy(true);
    try {
      const response = await apiRequest(`/platform-profiles/${selected}/validate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ output }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "校验失败");
      setValidation(body);
      setNotice([body.valid ? "success" : "danger",
        body.valid ? "规格与成片兼容性校验通过" : `存在 ${body.blocking.length} 个阻断项`]);
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function validateAccount() {
    setBusy(true);
    try {
      const response = await apiRequest(`/platform-profiles/${selected}/validate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_id: "demo-account" }),
      });
      const body = await response.json();
      setValidation(body);
      setNotice(["warning", `账号能力：${body.account.status}（${body.account.reason}）`]);
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }

  return <section className="page">
    <div className="page-head"><div><b>V6 PLATFORM PROFILES</b><h1>平台配置</h1><p>Profile 是生产与导出的版本化配置；选择它不代表账号存在，也不代表已核验平台规则。</p></div></div>
    {notice && <div className={`notice ${notice[0]}`}><span>{notice[1]}</span><button onClick={() => setNotice(null)}><X /></button></div>}
    <div className="two-col">
      <section className="card">
        <div className="card-head"><div><h2>导出 Profile</h2><span className="count">{profiles.length}</span></div><button onClick={refresh}><ArrowClockwise /></button></div>
        <div className="table-wrap"><table><thead><tr><th>名称</th><th>平台</th><th>比例 / 分辨率</th><th>语言</th><th>规则</th><th>校验</th></tr></thead>
          <tbody>{profiles.map((item) => <tr key={item.id} className={selected === item.profile_key ? "selected" : ""}
            onClick={() => setSelected(item.profile_key)} style={{ cursor: "pointer" }}>
            <td>{item.name}<small>v{item.version}</small></td>
            <td>{item.platform}</td>
            <td>{item.aspect_ratio} · {item.resolution}</td>
            <td>{item.locale}</td>
            <td><code>{item.rules_status.status}</code></td>
            <td>{item.valid ? <Pill status="SUCCEEDED" /> : <Pill status="QA_REJECTED" />}
              {item.warning_count > 0 && <small> {item.warning_count} 警告</small>}</td>
          </tr>)}</tbody></table></div>
      </section>
      <section className="card">
        <div className="card-head"><div><h2>规格与校验</h2>{detail && <small>{detail.profile.profile_key}</small>}</div></div>
        {detail && <div className="profile-detail">
          <dl>
            <dt>市场</dt><dd>{detail.spec.market.region} · {detail.spec.market.locale} · {detail.spec.market.timezone}</dd>
            <dt>视频</dt><dd>{detail.spec.video.width}×{detail.spec.video.height} · {detail.spec.video.fps}fps · {detail.spec.video.duration_min_seconds}–{detail.spec.video.duration_max_seconds}s</dd>
            <dt>构图</dt><dd>{detail.spec.composition.aspect_ratio} · {detail.spec.composition.crop_policy}</dd>
            <dt>字幕</dt><dd>{detail.spec.subtitles.enabled ? `${detail.spec.subtitles.sidecar_formats.join("/")} · ${detail.spec.subtitles.font_ref}` : "未启用"}</dd>
            <dt>配音</dt><dd>{detail.spec.voice.enabled ? `${detail.spec.voice.locale} · ${detail.spec.voice.voice_ref}` : "未启用（无 Provider 时显式关闭）"}</dd>
            <dt>BGM</dt><dd>{detail.spec.music.enabled ? `许可：${detail.spec.music.license_ref}` : "未启用"}</dd>
            <dt>硬限制</dt><dd>{detail.spec.video.hard_limits.length ? detail.spec.video.hard_limits.length : "未核验（不写成平台事实）"}</dd>
          </dl>
          <div className="qa-pick">
            <label>成片兼容性校验（用真实输出规格）</label>
            <div className="inline-fields">
              <input type="number" value={output.width} onChange={(event) => setOutput({ ...output, width: Number(event.target.value) })} />
              <input type="number" value={output.height} onChange={(event) => setOutput({ ...output, height: Number(event.target.value) })} />
              <input type="number" value={output.frame_count} onChange={(event) => setOutput({ ...output, frame_count: Number(event.target.value) })} />
            </div>
          </div>
          <div className="action-row">
            <button className="primary" disabled={busy} onClick={validateWithOutput}><ShieldCheck />校验规格 + 成片</button>
            <button disabled={busy} onClick={validateAccount}>查询账号能力</button>
          </div>
          {validation && <div className="validation">
            <p><b>{validation.valid ? "通过" : "阻断"}</b> · 阻断 {validation.blocking.length} · 警告 {validation.warnings.length}</p>
            {validation.blocking.map((item, index) => <p className="validation-fail" key={index}><WarningCircle weight="fill" />{item.message}</p>)}
            {validation.warnings.map((item, index) => <p className="capability-note" key={index}><WarningCircle weight="fill" />{item.message}</p>)}
            {validation.account && <p className="capability-note"><WarningCircle weight="fill" />账号：{validation.account.status} · {validation.account.reason}</p>}
          </div>}
          <h3>版本历史</h3>
          <div className="table-wrap"><table><thead><tr><th>版本</th><th>hash</th><th>创建时间</th></tr></thead>
            <tbody>{versions.map((item) => <tr key={item.id}><td>v{item.version}</td><td><code>{item.payload_sha256.slice(0, 16)}…</code></td><td>{formatTime(item.created_at)}</td></tr>)}</tbody></table></div>
        </div>}
      </section>
    </div>
  </section>;
}

function BatchPage() {
  const [plans, setPlans] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [batches, setBatches] = useState([]);
  const [form, setForm] = useState({ plan_id: "", profile_ids: [], variations: 2, max_concurrent: 2 });
  const [preview, setPreview] = useState(null);
  const [selected, setSelected] = useState(null);
  const [items, setItems] = useState([]);
  const [summary, setSummary] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([apiRequest("/plans"), apiRequest("/platform-profiles"), apiRequest("/batches")]).then(async ([p, f, b]) => {
      if (p.ok) { const body = await p.json(); setPlans(body.filter((item) => item.approved)); }
      if (f.ok) setProfiles(await f.json());
      if (b.ok) setBatches(await b.json());
    });
  }, []);
  useEffect(() => { if (selected) loadItems(selected); }, [selected]);
  useEffect(() => { if (notice) { const timer = setTimeout(() => setNotice(null), 6000); return () => clearTimeout(timer); } }, [notice]);
  useEffect(() => {
    if (!selected) return;
    const timer = setInterval(() => loadItems(selected), 8000);
    return () => clearInterval(timer);
  }, [selected]);

  async function refreshBatches() {
    const response = await apiRequest("/batches");
    if (response.ok) setBatches(await response.json());
  }
  async function loadItems(batchId) {
    const [itemsResponse, batchResponse] = await Promise.all([
      apiRequest(`/batches/${batchId}/items`),
      apiRequest(`/batches/${batchId}`),
    ]);
    if (itemsResponse.ok) { const body = await itemsResponse.json(); setItems(body.items); setSummary(body.summary); }
    if (batchResponse.ok) {
      const body = await batchResponse.json();
      setBatches((list) => list.map((item) => item.id === body.id ? body : item));
    }
  }
  function matrixPayload() {
    return {
      product_version_ids: form.product_version_ids,
      plan_ids: form.plan_id ? [form.plan_id] : [],
      profile_ids: form.profile_ids,
      variations_per_combination: form.variations,
    };
  }
  async function runPreview() {
    setBusy(true);
    try {
      const response = await apiRequest("/batches/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ matrix: matrixPayload(), max_concurrent: form.max_concurrent }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "预览失败");
      setPreview(body);
      setNotice(["success", `将展开 ${body.expanded_count} 项（服务端计算）`]);
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function createBatch() {
    setBusy(true);
    try {
      const response = await apiRequest("/batches", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: `批次 ${new Date().toLocaleString("zh-CN")}`, matrix: matrixPayload(),
          max_concurrent: form.max_concurrent,
          idempotency_key: `ui-${Date.now()}`,
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "创建失败");
      setNotice(["success", `批次已创建（${body.batch.summary.total} 项）`]);
      setSelected(body.batch.id);
      refreshBatches();
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function batchAction(action, extra = {}) {
    if (!selected) return;
    setBusy(true);
    try {
      const response = await apiRequest(`/batches/${selected}/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "控制台操作", ...extra }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "操作失败");
      setNotice(["success", `${action} 完成`]);
      loadItems(selected);
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function packageSelectedPlanVersion() {
    // 取计划绑定的产品版本用于矩阵（避免让用户手填 UUID）
    const response = await apiRequest(`/plans/${form.plan_id}/contracts`);
    if (!response.ok) return;
    const contracts = await response.json();
    if (contracts.length) setForm({ ...form, product_version_ids: [contracts[0].product_version_id] });
  }
  useEffect(() => { if (form.plan_id && !form.product_version_ids?.length) packageSelectedPlanVersion(); }, [form.plan_id]);

  const active = batches.find((item) => item.id === selected);

  return <section className="page">
    <div className="page-head"><div><b>V6 BATCH PRODUCTION</b><h1>批次生产</h1><p>矩阵展开数量由服务端给出；每项独立 Run，单项失败不影响其他项；暂停只停新调度。</p></div></div>
    {notice && <div className={`notice ${notice[0]}`}><span>{notice[1]}</span><button onClick={() => setNotice(null)}><X /></button></div>}
    <div className="two-col">
      <section className="card">
        <div className="card-head"><div><h2>新建批次</h2></div></div>
        <div className="qa-pick">
          <label>已批准计划</label>
          <select value={form.plan_id} onChange={(event) => setForm({ ...form, plan_id: event.target.value })}>
            <option value="">选择计划…</option>
            {plans.map((item) => <option key={item.id} value={item.id}>{item.intent?.slice(0, 34)} · {item.id.slice(0, 8)}</option>)}
          </select>
        </div>
        <div className="qa-pick">
          <label>Profile（可多选）</label>
          <div className="reuse-dimensions">{profiles.map((item) => <label key={item.id}>
            <input type="checkbox" checked={form.profile_ids.includes(item.profile_key)}
              onChange={() => setForm({
                ...form,
                profile_ids: form.profile_ids.includes(item.profile_key)
                  ? form.profile_ids.filter((key) => key !== item.profile_key)
                  : [...form.profile_ids, item.profile_key],
              })} />{item.aspect_ratio} · {item.locale}</label>)}</div>
        </div>
        <div className="qa-pick"><label>每组合变体数 / 并发上限</label>
          <div className="inline-fields">
            <input type="number" min="1" max="20" value={form.variations}
              onChange={(event) => setForm({ ...form, variations: Number(event.target.value) })} />
            <input type="number" min="1" max="8" value={form.max_concurrent}
              onChange={(event) => setForm({ ...form, max_concurrent: Number(event.target.value) })} />
          </div>
        </div>
        <div className="action-row">
          <button disabled={busy || !form.plan_id || !form.profile_ids.length} onClick={runPreview}><MagnifyingGlass />预览展开</button>
          <button className="primary" disabled={busy || !preview} onClick={createBatch}><Play weight="fill" />创建并开始</button>
        </div>
        {preview && <div className="mapping-diff">
          <h3>预览结果（无副作用）</h3>
          <p><b>展开 {preview.expanded_count} 项</b> · 上限 {preview.limits.max_expanded_items} · 并发 {preview.limits.max_concurrent}</p>
          {preview.duplicates.length > 0 && preview.duplicates.map((item, index) =>
            <p className="capability-note" key={index}><WarningCircle weight="fill" />第 {item.index + 1} 项与第 {item.duplicate_of + 1} 项重复：{item.detail}</p>)}
          <div className="table-wrap"><table><thead><tr><th>#</th><th>Profile</th><th>变体</th><th>种子</th></tr></thead>
            <tbody>{preview.items.slice(0, 12).map((item) => <tr key={item.index}><td>{item.index + 1}</td><td>{item.profile_id}</td><td>#{item.variation}</td><td><code>{item.seed}</code></td></tr>)}</tbody></table></div>
        </div>}
      </section>
      <section className="card">
        <div className="card-head"><div><h2>批次列表</h2><span className="count">{batches.length}</span></div><button onClick={refreshBatches}><ArrowClockwise /></button></div>
        <div className="table-wrap"><table><thead><tr><th>批次</th><th>状态</th><th>总数</th><th>成功/失败</th></tr></thead>
          <tbody>{batches.map((item) => <tr key={item.id} className={selected === item.id ? "selected" : ""} onClick={() => setSelected(item.id)} style={{ cursor: "pointer" }}>
            <td>{item.name}<small>{item.id.slice(0, 8)}</small></td>
            <td><Pill status={batchPill(item.status)} /></td>
            <td>{item.summary.total}</td>
            <td>{item.summary.succeeded} / {item.summary.failed}</td>
          </tr>)}</tbody></table></div>
        {active && <div className="batch-detail">
          <div className="action-row">
            <button disabled={busy || active.paused} onClick={() => batchAction("pause")}>暂停新调度</button>
            <button disabled={busy || !active.paused} onClick={() => batchAction("resume")}>恢复</button>
            <button disabled={busy} onClick={() => batchAction("cancel", { scope: "not_started" })}>取消未开始</button>
            <button disabled={busy} onClick={() => batchAction("cancel", { scope: "all_unfinished" })}>取消全部未完成</button>
            <button disabled={busy} onClick={() => batchAction("retry-failed")}>重试失败项</button>
          </div>
          {summary && <p>总数 {summary.total} · 成功 {summary.succeeded} · 失败 {summary.failed} · 取消 {summary.cancelled} · 进行 {summary.active} · 待开始 {summary.pending}
            {summary.production_complete && " · 生产完成"}</p>}
          <div className="table-wrap"><table><thead><tr><th>#</th><th>状态</th><th>Profile</th><th>变体</th><th>Run</th><th>错误</th></tr></thead>
            <tbody>{items.map((item) => <tr key={item.id}><td>{item.index + 1}</td><td><Pill status={itemPill(item.status)} /></td>
              <td>{item.profile_id}</td><td>#{item.variation}</td>
              <td>{item.run_id ? <code>{String(item.run_id).slice(0, 8)}</code> : "—"}</td>
              <td>{item.error ? <small className="validation-fail">{item.error}</small> : "—"}</td></tr>)}</tbody></table></div>
        </div>}
      </section>
    </div>
  </section>;
}

function batchPill(status) {
  return { COMPLETED: "SUCCEEDED", RUNNING: "RUNNING", QUEUED: "QUEUED", PAUSED: "CANCELLED",
    PARTIAL_FAILED: "FAILED", FAILED: "FAILED", CANCELLED: "CANCELLED", DRAFT: "DRAFT",
    WAITING_REVIEW: "VERIFICATION_PASSED" }[status] || "DRAFT";
}
function itemPill(status) {
  return { SUCCEEDED: "SUCCEEDED", FAILED: "FAILED", RUNNING: "RUNNING", QUEUED: "QUEUED",
    CANCELLED: "CANCELLED", PENDING: "DRAFT", SKIPPED: "DRAFT" }[status] || "DRAFT";
}

function AudioPage() {
  const [engines, setEngines] = useState(null);
  const [music, setMusic] = useState([]);
  const [runs, setRuns] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [runId, setRunId] = useState("");
  const [voiceovers, setVoiceovers] = useState([]);
  const [text, setText] = useState("Haz de cada noche una experiencia mágica.");
  const [preview, setPreview] = useState(null);
  const [mix, setMix] = useState(null);
  const [mixForm, setMixForm] = useState({ voiceover_id: "", music_asset_id: "", duration_s: 15, music_gain_db: -14, ducking: true });
  const [musicForm, setMusicForm] = useState({ name: "", license_ref: "", commercial_use_allowed: false });
  const [file, setFile] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([apiRequest("/audio/engines"), apiRequest("/music-assets"), apiRequest("/jobs")]).then(async ([e, m, j]) => {
      if (e.ok) setEngines(await e.json());
      if (m.ok) setMusic(await m.json());
      if (j.ok) {
        const body = await j.json();
        setJobs(body);
        const runs = body.filter((item) => item.run_id).slice(0, 30);
        setRuns(runs);
        if (runs.length && !runId) setRunId(runs[0].run_id);
      }
    });
  }, []);
  useEffect(() => { if (runId) loadVoiceovers(runId); }, [runId]);
  useEffect(() => { if (notice) { const timer = setTimeout(() => setNotice(null), 6000); return () => clearTimeout(timer); } }, [notice]);

  async function loadVoiceovers(id) {
    const response = await apiRequest(`/runs/${id}/voiceovers`);
    if (response.ok) setVoiceovers(await response.json());
  }
  async function synthesize() {
    setBusy(true);
    try {
      const response = await apiRequest("/audio/previews", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, locale: "es-MX", rate: 1.0, max_seconds: 120 }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "合成失败");
      setPreview(body);
      setNotice(["success", `试听已生成：${body.engine} · ${body.voice} · ${body.duration_s}s`]);
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function uploadMusic() {
    if (!file) { setNotice(["danger", "请选择音频文件"]); return; }
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("name", musicForm.name || file.name);
      form.append("license_ref", musicForm.license_ref);
      form.append("commercial_use_allowed", String(musicForm.commercial_use_allowed));
      const response = await apiRequest("/music-assets", { method: "POST", body: form });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "上传失败");
      setNotice(["success", "BGM 已入库（含许可引用）"]);
      const list = await apiRequest("/music-assets");
      if (list.ok) setMusic(await list.json());
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function runMix() {
    setBusy(true);
    try {
      const response = await apiRequest(`/runs/${runId}/audio/mix`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          voiceover_id: mixForm.voiceover_id, music_asset_id: mixForm.music_asset_id,
          duration_s: Number(mixForm.duration_s), music_gain_db: Number(mixForm.music_gain_db),
          ducking: { enabled: mixForm.ducking, ratio: 6 },
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "混音失败");
      setMix(body);
      setNotice([body.verdict.passed ? "success" : "warning",
        `混音完成：${body.measurement.integrated_lufs} LUFS / ${body.measurement.true_peak_dbtp} dBTP`]);
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }

  return <section className="page">
    <div className="page-head"><div><b>V6 AUDIO POST</b><h1>音频与音乐</h1><p>响度目标 −16 LUFS ±1.5 / 真峰值 ≤ −1 dBTP 是本产品默认，不是平台官方要求；BGM 必须有许可引用。</p></div></div>
    {notice && <div className={`notice ${notice[0]}`}><span>{notice[1]}</span><button onClick={() => setNotice(null)}><X /></button></div>}
    <div className="two-col">
      <section className="card">
        <div className="card-head"><div><h2>配音与试听</h2>{engines && <small>{engines.status} · {engines.engine || "无引擎"}</small>}</div></div>
        {engines && <p className="capability-note"><WarningCircle weight="fill" />{engines.note || engines.reason}
          {engines.fallbacks && <small>（回退：{engines.fallbacks.join("；")}）</small>}</p>}
        <div className="qa-pick"><label>配音文本（es-MX）</label>
          <textarea rows="4" value={text} onChange={(event) => setText(event.target.value)} /></div>
        <div className="action-row">
          <button className="primary" disabled={busy || !text} onClick={synthesize}><Play weight="fill" />生成试听</button>
        </div>
        {preview && <div className="qa-pick">
          <audio controls src={preview.download_url} style={{ width: "100%" }} />
          <p>{preview.engine} · {preview.voice} · {preview.duration_s}s · {preview.characters} 字符
            <small>（{preview.note}）</small></p>
        </div>}
        <div className="card-head"><div><h2>BGM 素材</h2><span className="count">{music.length}</span></div></div>
        <div className="qa-pick"><label>许可引用（必填）</label>
          <input value={musicForm.license_ref} onChange={(event) => setMusicForm({ ...musicForm, license_ref: event.target.value })}
            placeholder="例如 CC0-… / 授权合同号" />
          <label className="checkbox-line"><input type="checkbox" checked={musicForm.commercial_use_allowed}
            onChange={(event) => setMusicForm({ ...musicForm, commercial_use_allowed: event.target.checked })} />允许商业使用</label>
          <input type="file" accept="audio/*" onChange={(event) => setFile(event.target.files?.[0] || null)} />
          <button disabled={busy} onClick={uploadMusic}><UploadSimple />上传 BGM</button>
        </div>
        <div className="table-wrap"><table><thead><tr><th>名称</th><th>许可</th><th>商用</th><th>时长</th><th /></tr></thead>
          <tbody>{music.map((item) => <tr key={item.id}><td>{item.name}</td><td><code>{item.license_ref}</code></td>
            <td>{item.commercial_use_allowed ? "是" : "否"}</td><td>{item.duration_s}s</td>
            <td><a href={item.download_url} target="_blank" rel="noreferrer">试听</a></td></tr>)}</tbody></table></div>
      </section>
      <section className="card">
        <div className="card-head"><div><h2>混音（旁白优先）</h2></div></div>
        <div className="qa-pick"><label>目标 Run</label>
          <select value={runId} onChange={(event) => setRunId(event.target.value)}>
            <option value="">选择 Run…</option>
            {runs.map((item) => <option key={item.run_id} value={item.run_id}>{item.run_id?.slice(0, 8)} · {item.plan_id?.slice(0, 8)}</option>)}
          </select></div>
        <div className="qa-pick"><label>配音</label>
          <select value={mixForm.voiceover_id} onChange={(event) => setMixForm({ ...mixForm, voiceover_id: event.target.value })}>
            <option value="">关闭配音（仅 BGM 需显式选择）</option>
            {voiceovers.map((item) => <option key={item.id} value={item.id}>{item.locale} · {item.duration_s}s</option>)}
          </select></div>
        <div className="qa-pick"><label>BGM</label>
          <select value={mixForm.music_asset_id} onChange={(event) => setMixForm({ ...mixForm, music_asset_id: event.target.value })}>
            <option value="">关闭 BGM</option>
            {music.map((item) => <option key={item.id} value={item.id}>{item.name}（{item.license_ref}）</option>)}
          </select></div>
        <div className="qa-pick"><label>时长（秒）/ BGM 增益（dB）</label>
          <div className="inline-fields">
            <input type="number" value={mixForm.duration_s} onChange={(event) => setMixForm({ ...mixForm, duration_s: event.target.value })} />
            <input type="number" value={mixForm.music_gain_db} onChange={(event) => setMixForm({ ...mixForm, music_gain_db: event.target.value })} />
          </div>
          <label className="checkbox-line"><input type="checkbox" checked={mixForm.ducking}
            onChange={(event) => setMixForm({ ...mixForm, ducking: event.target.checked })} />旁白优先 ducking</label>
        </div>
        <div className="action-row"><button className="primary" disabled={busy || !runId} onClick={runMix}><MonitorPlay />生成混音</button></div>
        {mix && <div className="mapping-diff">
          <h3>响度实测</h3>
          <p>综合 <b>{mix.measurement.integrated_lufs} LUFS</b> · 真峰值 <b>{mix.measurement.true_peak_dbtp} dBTP</b> · 判定
            {mix.verdict.passed ? "通过" : `未通过：${mix.verdict.failures.join("；")}`}</p>
          <p>ducking：{mix.ducking.enabled ? `开（阈值 ${mix.ducking.threshold_db} dB，来源 ${mix.ducking.threshold_source}）` : "关"}</p>
          <audio controls src={mix.download_url} style={{ width: "100%" }} />
        </div>}
      </section>
    </div>
  </section>;
}

function PackagePage() {
  const [jobs, setJobs] = useState([]);
  const [runId, setRunId] = useState("");
  const [localizations, setLocalizations] = useState([]);
  const [subtitleTracks, setSubtitleTracks] = useState([]);
  const [mixes, setMixes] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [packages, setPackages] = useState([]);
  const [form, setForm] = useState({ profile_id: "tiktok-mx-9x16-esmx", localization_id: "", subtitle_track_id: "", audio_mix_id: "", include_mixed_audio: true });
  const [result, setResult] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([apiRequest("/jobs"), apiRequest("/platform-profiles"), apiRequest("/packages")]).then(async ([j, p, k]) => {
      if (j.ok) {
        const body = await j.json();
        setJobs(body);
        const withRun = body.filter((item) => item.run_id);
        if (withRun.length && !runId) setRunId(withRun[0].run_id);
      }
      if (p.ok) setProfiles(await p.json());
      if (k.ok) setPackages(await k.json());
    });
  }, []);
  useEffect(() => { if (runId) loadRunContext(runId); }, [runId]);
  useEffect(() => { if (notice) { const timer = setTimeout(() => setNotice(null), 7000); return () => clearTimeout(timer); } }, [notice]);

  async function loadRunContext(id) {
    const [localizationResponse, mixesResponse] = await Promise.all([
      apiRequest(`/runs/${id}/localizations`),
      apiRequest(`/audio/mixes?run_id=${id}`),
    ]);
    if (localizationResponse.ok) {
      const body = await localizationResponse.json();
      setLocalizations(body);
      if (body.length) setForm((value) => ({ ...value, localization_id: body[0].id }));
    }
    if (mixesResponse.ok) {
      const body = await mixesResponse.json();
      setMixes(Array.isArray(body) ? body : []);
      if (Array.isArray(body) && body.length) setForm((value) => ({ ...value, audio_mix_id: body[0].id }));
    }
  }
  async function loadDetail(localizationId) {
    const response = await apiRequest(`/localizations/${localizationId}`);
    if (!response.ok) return;
    const body = await response.json();
    setSubtitleTracks(body.subtitle_tracks || []);
    if (body.subtitle_tracks?.length) setForm((value) => ({ ...value, subtitle_track_id: body.subtitle_tracks.at(-1).id }));
  }
  useEffect(() => { if (form.localization_id) loadDetail(form.localization_id); }, [form.localization_id]);

  async function build() {
    setBusy(true);
    try {
      const response = await apiRequest(`/runs/${runId}/packages`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "打包失败");
      setResult(body);
      setNotice([body.status === "BUILT" ? "success" : "danger",
        body.status === "BUILT" ? `包已生成（${body.manifest.files.length} 个文件）` : `校验未通过：${body.verification.join("；")}`]);
      const list = await apiRequest("/packages");
      if (list.ok) setPackages(await list.json());
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function approve() {
    if (!result) return;
    setBusy(true);
    try {
      const response = await apiRequest(`/packages/${result.id}/approve`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content_hash: result.content_hash, reason: "控制台审批" }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "审批失败");
      setNotice(["success", "包已审批（绑定内容哈希，之后改动即失效）"]);
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }

  return <section className="page">
    <div className="page-head"><div><b>V6 PUBLISH PACKAGE</b><h1>发布包</h1><p>先 build → verify → approve；审批绑定内容哈希。包内不含任何 API Key 或平台 token。</p></div></div>
    {notice && <div className={`notice ${notice[0]}`}><span>{notice[1]}</span><button onClick={() => setNotice(null)}><X /></button></div>}
    <div className="two-col">
      <section className="card">
        <div className="card-head"><div><h2>打包</h2></div></div>
        <div className="qa-pick"><label>目标 Run</label>
          <select value={runId} onChange={(event) => setRunId(event.target.value)}>
            <option value="">选择 Run…</option>
            {jobs.filter((item) => item.run_id).map((item) => <option key={item.run_id} value={item.run_id}>{item.run_id.slice(0, 8)} · {formatTime(item.created_at)}</option>)}
          </select></div>
        <div className="qa-pick"><label>Profile</label>
          <select value={form.profile_id} onChange={(event) => setForm({ ...form, profile_id: event.target.value })}>
            {profiles.map((item) => <option key={item.id} value={item.profile_key}>{item.name}</option>)}
          </select></div>
        <div className="qa-pick"><label>本地化文案</label>
          <select value={form.localization_id} onChange={(event) => setForm({ ...form, localization_id: event.target.value })}>
            <option value="">选择本地化…</option>
            {localizations.map((item) => <option key={item.id} value={item.id}>{item.locale} · r{item.latest?.revision}</option>)}
          </select></div>
        <div className="qa-pick"><label>字幕轨</label>
          <select value={form.subtitle_track_id} onChange={(event) => setForm({ ...form, subtitle_track_id: event.target.value })}>
            <option value="">不含字幕（清单标 disabled）</option>
            {subtitleTracks.map((item) => <option key={item.id} value={item.id}>{item.locale} · rev{item.revision}</option>)}
          </select></div>
        <div className="qa-pick"><label>混音</label>
          <select value={form.audio_mix_id} onChange={(event) => setForm({ ...form, audio_mix_id: event.target.value })}>
            <option value="">不含音轨</option>
            {mixes.map((item) => <option key={item.id} value={item.id}>{item.id.slice(0, 8)} · {item.duration_s}s</option>)}
          </select>
          <label className="checkbox-line"><input type="checkbox" checked={form.include_mixed_audio}
            onChange={(event) => setForm({ ...form, include_mixed_audio: event.target.checked })} />把混音放入包（music/voice 原文件不入包）</label>
        </div>
        <div className="action-row">
          <button className="primary" disabled={busy || !runId} onClick={build}><Package />生成发布包</button>
          <button disabled={busy || !result || result.status !== "BUILT"} onClick={approve}><CheckCircle />审批</button>
          {result && <a className="icon-link" href={result.download_url} target="_blank" rel="noreferrer"><DownloadSimple />下载 ZIP</a>}
        </div>
        {result && <div className="mapping-diff">
          <h3>包内容（{result.manifest.files.length} 个文件）</h3>
          <p>状态 {result.status} · 内容哈希 <code>{result.content_hash.slice(0, 16)}…</code> · QA 来源 {result.manifest.qa.source}</p>
          <p>字幕 {result.manifest.subtitles.status || (result.manifest.subtitles.enabled ? "enabled" : "disabled")} ·
            配音 {result.manifest.voice.status} · BGM {result.manifest.music.status}</p>
          <div className="table-wrap"><table><thead><tr><th>路径</th><th>角色</th><th>大小</th></tr></thead>
            <tbody>{result.manifest.files.map((item) => <tr key={item.path}><td><small>{item.path.replace(/^publish-package_[^/]+\//, "")}</small></td>
              <td>{item.role}</td><td>{Math.round(item.size_bytes / 1024)} KB</td></tr>)}</tbody></table></div>
        </div>}
      </section>
      <section className="card">
        <div className="card-head"><div><h2>已生成包</h2><span className="count">{packages.length}</span></div></div>
        <div className="table-wrap"><table><thead><tr><th>包</th><th>语言</th><th>状态</th><th>审批时间</th><th /></tr></thead>
          <tbody>{packages.map((item) => <tr key={item.id}><td>{item.id.slice(0, 8)}<small>v{item.version}</small></td>
            <td>{item.locale}</td><td><Pill status={item.status === "APPROVED" ? "SUCCEEDED" : item.status === "INVALID" ? "FAILED" : "DRAFT"} /></td>
            <td>{formatTime(item.approved_at)}</td>
            <td><a href={item.download_url} target="_blank" rel="noreferrer">下载</a></td></tr>)}</tbody></table></div>
        <p className="capability-note"><WarningCircle weight="fill" />未审批的包不会进入批次归档；下载时会再校验 ZIP 哈希。</p>
      </section>
    </div>
  </section>;
}

function CostPage() {
  const [budgets, setBudgets] = useState([]);
  const [usage, setUsage] = useState(null);
  const [costs, setCosts] = useState(null);
  const [rateCard, setRateCard] = useState(null);
  const [runs, setRuns] = useState([]);
  const [runId, setRunId] = useState("");
  const [budgetForm, setBudgetForm] = useState({ name: "月度生产预算", currency: "USD", limit_amount: 20 });
  const [editForm, setEditForm] = useState({ budget_id: "", revision: "", limit_amount: "" });
  const [reserveForm, setReserveForm] = useState({ estimate_upper_bound: 1.5, note: "" });
  const [usageForm, setUsageForm] = useState({
    provider: "h3-comfyui", operation_id: "", unit: "video_generation_request", quantity: 1,
    explicit_price: "", currency: "USD", note: "",
  });
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { load(); }, [runId]);
  useEffect(() => { if (notice) { const timer = setTimeout(() => setNotice(null), 8000); return () => clearTimeout(timer); } }, [notice]);

  const money = (value, currency) => (value === null || value === undefined ? "—" : `${value} ${currency || ""}`.trim());

  async function load() {
    const query = runId ? `?run_id=${runId}` : "";
    const [b, r, u, c, j] = await Promise.all([
      apiRequest("/budgets"), apiRequest("/rate-card"), apiRequest(`/usage${query}`),
      apiRequest(`/costs${query}`), apiRequest("/jobs"),
    ]);
    if (b.ok) {
      const body = await b.json();
      setBudgets(body);
      setEditForm((value) => (value.budget_id ? value : {
        budget_id: body[0]?.id || "", revision: body[0]?.revision ?? "", limit_amount: body[0]?.limit_amount ?? "",
      }));
    }
    if (r.ok) setRateCard(await r.json());
    if (u.ok) setUsage(await u.json());
    if (c.ok) setCosts(await c.json());
    if (j.ok) setRuns((await j.json()).filter((item) => item.run_id));
  }
  function pickBudget(budget) {
    setEditForm({ budget_id: budget.id, revision: budget.revision, limit_amount: budget.limit_amount ?? "" });
  }
  async function createBudget() {
    setBusy(true);
    try {
      const response = await apiRequest("/budgets", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: budgetForm.name, currency: budgetForm.currency,
          limit_amount: budgetForm.limit_amount === "" ? null : Number(budgetForm.limit_amount),
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "建立预算失败");
      setNotice(["success", `预算已建立（revision ${body.revision}）${body.limit_amount === null ? "：未设上限，付费路线会被阻断" : ""}`]);
      await load();
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function updateBudget() {
    if (!editForm.budget_id) { setNotice(["danger", "请先选择预算账户"]); return; }
    setBusy(true);
    try {
      const response = await apiRequest(`/budgets/${editForm.budget_id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          revision: Number(editForm.revision),
          limit_amount: editForm.limit_amount === "" ? null : Number(editForm.limit_amount),
          reason: "控制台调整",
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "调整失败");
      setNotice(["success", `上限已更新为 ${money(body.limit_amount, body.currency)}（revision ${body.revision}，不追溯已发生金额）`]);
      await load();
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function reserve() {
    if (!editForm.budget_id) { setNotice(["danger", "请先选择预算账户"]); return; }
    setBusy(true);
    try {
      const response = await apiRequest(`/budgets/${editForm.budget_id}/reserve`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ estimate_upper_bound: Number(reserveForm.estimate_upper_bound), note: reserveForm.note }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "预留失败");
      setNotice(["success", `预留成功：可用额度剩余 ${money(body.snapshot.available, body.currency)}`]);
      await load();
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function settle(reservation) {
    const actual = window.prompt(`对账实际金额（预留 ${reservation.amount} ${reservation.currency}）`,
      String(reservation.amount));
    if (actual === null) return;
    setBusy(true);
    try {
      const response = await apiRequest(`/budgets/${reservation.budget_id}/settle`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reservation_id: reservation.id, actual_amount: Number(actual), note: "控制台对账" }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "对账失败");
      setNotice([body.overrun ? "warning" : "success",
        body.overrun
          ? `对账完成：实际超出预留 ${money(body.additional_accrual, body.currency)}（预估不是封顶，差异已如实记录）`
          : `对账完成：释放预留 ${money(body.release, body.currency)}`]);
      await load();
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function registerUsage() {
    setBusy(true);
    try {
      const response = await apiRequest("/usage-events", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: usageForm.provider, operation_id: usageForm.operation_id || `manual-${Date.now()}`,
          unit: usageForm.unit, quantity: Number(usageForm.quantity),
          explicit_price: usageForm.explicit_price === "" ? null : Number(usageForm.explicit_price),
          currency: usageForm.currency, capability: "manual_entry", note: usageForm.note,
          run_id: runId || null,
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "登记失败");
      setNotice([body.priced ? "success" : "warning",
        body.priced
          ? `用量已登记：${money(body.amount, body.currency)}（${body.amount_source}${body.internal_estimate ? " · 内部估算" : ""}）`
          : `用量已登记但未定价：${body.unpriced_reason}`]);
      await load();
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }

  const reservations = (costs?.entries || []).filter((item) => item.kind === "reserved" && item.amount > 0 && !item.settled_at);
  const totals = costs?.by_kind || {};

  return <section className="page">
    <div className="page-head"><div><b>V6 COST LEDGER</b><h1>成本与用量</h1>
      <p>可用额度 = 上限 − 已确认 − 未结预留 − 未预留已发生；预留是承诺不是支出，控制台不会把两者相加。</p></div></div>
    {notice && <div className={`notice ${notice[0]}`}><span>{notice[1]}</span><button onClick={() => setNotice(null)}><X /></button></div>}
    <div className="two-col">
      <section className="card">
        <div className="card-head"><div><h2>预算账户</h2><small>{costs?.note}</small></div></div>
        <table className="table"><thead><tr><th>预算</th><th>上限</th><th>已确认</th><th>未结预留</th><th>未预留已发生</th><th>可用</th></tr></thead>
          <tbody>{budgets.map((item) => <tr key={item.id} onClick={() => pickBudget(item)} style={{ cursor: "pointer" }}>
            <td>{item.name}<small>rev {item.revision} · {item.currency}</small></td>
            <td>{money(item.limit_amount, item.currency)}</td>
            <td>{item.settled}</td>
            <td>{item.reserved_outstanding}</td>
            <td>{item.accrued_unreserved}</td>
            <td><b>{money(item.available, item.currency)}</b></td></tr>)}</tbody></table>
        {!budgets.length && <p className="capability-note"><WarningCircle weight="fill" />还没有预算账户：付费路线必须先建立预算，系统不会假设 0 成本。</p>}
        <div className="action-row">
          <div className="inline-fields">
            <label>名称<input value={budgetForm.name} onChange={(event) => setBudgetForm({ ...budgetForm, name: event.target.value })} /></label>
            <label>币种<select value={budgetForm.currency} onChange={(event) => setBudgetForm({ ...budgetForm, currency: event.target.value })}>
              <option value="USD">USD</option><option value="MXN">MXN</option><option value="CNY">CNY</option></select></label>
            <label>上限<input value={budgetForm.limit_amount} onChange={(event) => setBudgetForm({ ...budgetForm, limit_amount: event.target.value })} /></label>
          </div>
          <button className="primary" onClick={createBudget} disabled={busy}><Plus />建立预算</button>
        </div>
        <div className="action-row">
          <div className="inline-fields">
            <label>选中预算<input value={editForm.budget_id} onChange={(event) => setEditForm({ ...editForm, budget_id: event.target.value })} /></label>
            <label>revision<input value={editForm.revision} onChange={(event) => setEditForm({ ...editForm, revision: event.target.value })} /></label>
            <label>新上限<input value={editForm.limit_amount} onChange={(event) => setEditForm({ ...editForm, limit_amount: event.target.value })} /></label>
          </div>
          <button onClick={updateBudget} disabled={busy}><PencilSimple />调整上限</button>
        </div>
        <p className="capability-note"><WarningCircle weight="fill" />上限调整需要当前 revision（乐观锁）；过期的 revision 会被拒绝，避免覆盖他人的预算改动。</p>
      </section>

      <section className="card">
        <div className="card-head"><div><h2>预留与对账</h2><small>预估不是强制封顶</small></div></div>
        <div className="inline-fields">
          <label>预估上界<input value={reserveForm.estimate_upper_bound} onChange={(event) => setReserveForm({ ...reserveForm, estimate_upper_bound: event.target.value })} /></label>
          <label>说明<input value={reserveForm.note} onChange={(event) => setReserveForm({ ...reserveForm, note: event.target.value })} /></label>
        </div>
        <div className="action-row"><button className="primary" onClick={reserve} disabled={busy}><CheckCircle />提交前预留</button></div>
        <table className="table"><thead><tr><th>预留</th><th>金额</th><th>关联</th><th>操作</th></tr></thead>
          <tbody>{reservations.map((item) => <tr key={item.id}>
            <td>{item.id.slice(0, 8)}<small>{formatTime(item.created_at)}</small></td>
            <td>{money(item.amount, item.currency)}</td>
            <td><small>{item.run_id ? `run ${item.run_id.slice(0, 8)}` : "未绑定运行"}</small></td>
            <td><button onClick={() => settle(item)} disabled={busy}>对账</button></td></tr>)}</tbody></table>
        {!reservations.length && <p className="capability-note">当前没有未结预留（已对账的预留会保留 settled_at 与结算记录，无法重复结算）。</p>}
      </section>
    </div>

    <div className="two-col">
      <section className="card">
        <div className="card-head"><div><h2>用量登记</h2><small>{usage?.note}</small></div></div>
        <div className="inline-fields">
          <label>运行<select value={runId} onChange={(event) => setRunId(event.target.value)}>
            <option value="">全部运行</option>
            {runs.map((item) => <option value={item.run_id} key={item.run_id}>{item.run_id.slice(0, 8)}</option>)}</select></label>
          <label>Provider<input value={usageForm.provider} onChange={(event) => setUsageForm({ ...usageForm, provider: event.target.value })} /></label>
          <label>单位<select value={usageForm.unit} onChange={(event) => setUsageForm({ ...usageForm, unit: event.target.value })}>
            {(usage?.summary?.units_supported || ["gpu_second", "cpu_second", "video_generation_request", "tts_character"]).map((unit) =>
              <option value={unit} key={unit}>{unit}</option>)}</select></label>
          <label>数量<input value={usageForm.quantity} onChange={(event) => setUsageForm({ ...usageForm, quantity: event.target.value })} /></label>
          <label>显式单价（可空）<input value={usageForm.explicit_price} onChange={(event) => setUsageForm({ ...usageForm, explicit_price: event.target.value })} /></label>
          <label>operation_id<input value={usageForm.operation_id} onChange={(event) => setUsageForm({ ...usageForm, operation_id: event.target.value })} placeholder="上游操作 ID" /></label>
        </div>
        <div className="action-row"><button className="primary" onClick={registerUsage} disabled={busy}><Plus />登记用量</button></div>
        <p className="capability-note"><WarningCircle weight="fill" />同一 Provider 的同一次操作重复回调不会重复计费；没有已知报价时金额留空并标注原因，不按 0 计入。</p>
        {rateCard && <div className="profile-detail">
          <dl>
            <dt>费率卡</dt><dd>{rateCard.kind}</dd>
            <dt>说明</dt><dd>{rateCard.note}</dd>
            {Object.entries(rateCard.rates).flatMap(([provider, rate]) => [
              <dt key={`${provider}-k`}>{provider}</dt>,
              <dd key={`${provider}-v`}>{rate.amount_per_unit} {rate.currency} / {rate.unit}</dd>,
            ])}
          </dl>
        </div>}
      </section>

      <section className="card">
        <div className="card-head"><div><h2>用量与成本汇总</h2><small>{usage?.summary?.event_count || 0} 条用量事件</small></div></div>
        <div className="profile-detail">
          <dl>
            <dt>已确认 settled</dt><dd>{totals.settled ?? 0}</dd>
            <dt>未结预留 reserved</dt><dd>{totals.reserved ?? 0}</dd>
            <dt>未预留已发生 accrued</dt><dd>{totals.accrued ?? 0}</dd>
            <dt>已定价合计</dt><dd>{usage?.priced_total ?? 0}</dd>
          </dl>
        </div>
        <table className="table"><thead><tr><th>Provider</th><th>单位</th><th>数量</th><th>事件</th><th>口径</th></tr></thead>
          <tbody>{Object.entries(usage?.summary?.by_provider || {}).map(([provider, item]) => <tr key={provider}>
            <td>{provider}</td><td>{item.unit}</td><td>{item.quantity}</td><td>{item.events}</td>
            <td>{item.internal_estimate ? <Pill status="DRAFT" /> : <small>Provider 报价</small>}</td></tr>)}</tbody></table>
        {!!usage?.unpriced_event_ids?.length && <p className="capability-note danger">
          <WarningCircle weight="fill" />{usage.unpriced_event_ids.length} 条用量没有已知报价：不按 0 计费，需要管理员给出上限或阻断该付费路线。</p>}
        <table className="table"><thead><tr><th>账本</th><th>类型</th><th>金额</th><th>标的</th><th>时间</th></tr></thead>
          <tbody>{(costs?.entries || []).slice(0, 25).map((item) => <tr key={item.id}>
            <td>{item.id.slice(0, 8)}<small>{item.note}</small></td>
            <td>{item.kind}{item.settled_at ? <small>已对账</small> : null}</td>
            <td>{money(item.amount, item.currency)}</td>
            <td><small>{item.run_id ? `run ${item.run_id.slice(0, 8)}` : item.batch_id ? `batch ${item.batch_id.slice(0, 8)}` : "—"}</small></td>
            <td>{formatTime(item.created_at)}</td></tr>)}</tbody></table>
        {!costs?.entries?.length && <p className="capability-note">账本为空：真实渲染、混音、配音与 H3 生成都会自动登记为内部估算用量。</p>}
      </section>
    </div>
  </section>;
}

function AutomationPage() {
  const [keys, setKeys] = useState([]);
  const [surface, setSurface] = useState(null);
  const [created, setCreated] = useState(null);
  const [form, setForm] = useState({
    name: "外部系统", scopes: ["projects:read", "jobs:read", "generate:write"],
    ip_allowlist: "", rate_limit_per_minute: 60, budget_limit_amount: "", expires_in_days: "",
  });
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const allScopes = surface?.scopes?.scopes || [
    "projects:read", "assets:write", "generate:write", "jobs:read", "packages:read", "publish:write", "webhooks:manage",
  ];

  useEffect(() => { load(); }, []);
  useEffect(() => { if (notice) { const timer = setTimeout(() => setNotice(null), 9000); return () => clearTimeout(timer); } }, [notice]);

  async function load() {
    const [k, s] = await Promise.all([apiRequest("/automation-keys"), apiRequest("/automation/openapi")]);
    if (k.ok) setKeys(await k.json());
    if (s.ok) setSurface(await s.json());
  }
  function toggleScope(scope) {
    setForm((value) => ({
      ...value,
      scopes: value.scopes.includes(scope) ? value.scopes.filter((item) => item !== scope) : [...value.scopes, scope],
    }));
  }
  async function createKey() {
    setBusy(true);
    try {
      const response = await apiRequest("/automation-keys", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.name, scopes: form.scopes, ip_allowlist: form.ip_allowlist,
          rate_limit_per_minute: Number(form.rate_limit_per_minute),
          budget_limit_amount: form.budget_limit_amount === "" ? null : Number(form.budget_limit_amount),
          expires_in_days: form.expires_in_days === "" ? null : Number(form.expires_in_days),
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "创建失败");
      setCreated(body);
      setNotice(["success", "Key 已创建：密钥只显示这一次，请立即保存到调用方的密钥管理里"]);
      await load();
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function revoke(key) {
    if (!window.confirm(`撤销 Key ${key.prefix}？撤销后该 Key 的新调用会立即返回 401。`)) return;
    setBusy(true);
    try {
      const response = await apiRequest(`/automation-keys/${key.id}`, {
        method: "DELETE", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "控制台撤销" }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "撤销失败");
      setNotice(["warning", `已撤销 ${key.prefix}：新调用立即 401（撤销幂等）`]);
      await load();
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  function copyKey() {
    if (!created?.key) return;
    navigator.clipboard?.writeText(created.key);
    setNotice(["success", "密钥已复制到剪贴板（离开本页后无法再次读取）"]);
  }

  return <section className="page">
    <div className="page-head"><div><b>V6 AUTOMATION</b><h1>自动化接入</h1>
      <p>外部系统用受限 Key 调用；未登记的接口不放行，缺 scope 一律 403，创建任务/发布类调用必须带 Idempotency-Key。</p></div></div>
    {notice && <div className={`notice ${notice[0]}`}><span>{notice[1]}</span><button onClick={() => setNotice(null)}><X /></button></div>}
    {created?.key && <section className="card">
      <div className="card-head"><div><h2>新 Key（只显示一次）</h2><small>{created.name} · {created.prefix}</small></div>
        <button onClick={copyKey}><DownloadSimple />复制</button></div>
      <pre className="key-once">{created.key}</pre>
      <p className="capability-note"><WarningCircle weight="fill" />服务端只保存哈希；页面刷新后无法再次读取。
        请放到调用方的密钥管理（环境变量/密钥库），不要提交到 Git、日志或前端代码。</p>
    </section>}
    <div className="two-col">
      <section className="card">
        <div className="card-head"><div><h2>Key 列表</h2><small>{keys.length} 个</small></div></div>
        <table className="table"><thead><tr><th>Key</th><th>Scope</th><th>限额</th><th>状态</th><th>调用</th><th /></tr></thead>
          <tbody>{keys.map((item) => <tr key={item.id}>
            <td>{item.name}<small>{item.prefix}</small></td>
            <td><small>{item.scopes.join(", ")}</small></td>
            <td><small>{item.rate_limit_per_minute}/分钟{item.budget_limit_amount !== null ? ` · ≤${item.budget_limit_amount}/${item.budget_period}` : ""}</small></td>
            <td>{item.revoked_at ? <Pill status="FAILED" /> : <Pill status="SUCCEEDED" />}<small>{item.revoked_at ? formatTime(item.revoked_at) : item.expires_at ? `至 ${Math.round((item.expires_at * 1000 - Date.now()) / 86400000)} 天` : "长期"}</small></td>
            <td><small>{item.call_count} 次<small>{item.last_used_at ? formatTime(item.last_used_at) : "未使用"}</small></small></td>
            <td>{item.revoked_at ? null : <button onClick={() => revoke(item)} disabled={busy}>撤销</button>}</td></tr>)}</tbody></table>
        {!keys.length && <p className="capability-note">还没有 Automation Key：外部系统目前无法调用任何接口。</p>}
      </section>

      <section className="card">
        <div className="card-head"><div><h2>创建 Key</h2><small>最小权限原则：只勾选需要的 scope</small></div></div>
        <div className="inline-fields">
          <label>名称<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
          <label>每分钟限额<input value={form.rate_limit_per_minute} onChange={(event) => setForm({ ...form, rate_limit_per_minute: event.target.value })} /></label>
          <label>IP 允许列表<input value={form.ip_allowlist} onChange={(event) => setForm({ ...form, ip_allowlist: event.target.value })} placeholder="留空=不限；10.0.0.*" /></label>
          <label>周期预算上限<input value={form.budget_limit_amount} onChange={(event) => setForm({ ...form, budget_limit_amount: event.target.value })} placeholder="留空=不限" /></label>
          <label>有效期（天）<input value={form.expires_in_days} onChange={(event) => setForm({ ...form, expires_in_days: event.target.value })} placeholder="留空=长期" /></label>
        </div>
        <div className="scope-grid">{allScopes.map((scope) => <label className="checkbox-line" key={scope}>
          <input type="checkbox" checked={form.scopes.includes(scope)} onChange={() => toggleScope(scope)} /><span>{scope}</span></label>)}</div>
        <div className="action-row"><button className="primary" onClick={createKey} disabled={busy}><Plus />创建 Key</button></div>
        <p className="capability-note"><WarningCircle weight="fill" />Key 不能管理 Key：创建/撤销只允许 Owner 会话（本控制台）。</p>
      </section>
    </div>

    {surface && <section className="card">
      <div className="card-head"><div><h2>调用面与限额</h2><small>{surface.limits.note}</small></div></div>
      <div className="profile-detail"><dl>
        <dt>认证</dt><dd>{surface.auth}</dd>
        <dt>幂等</dt><dd>{surface.idempotency.header} · {surface.idempotency.scope_isolation}；{surface.idempotency.replay}</dd>
        <dt>每 Key</dt><dd>{surface.limits.requests_per_minute.per_key} 请求/分钟</dd>
        <dt>每工作区</dt><dd>{surface.limits.requests_per_minute.per_workspace} 请求/分钟</dd>
        <dt>并发</dt><dd>{surface.limits.default_concurrency.gpu_jobs} GPU Job / {surface.limits.default_concurrency.remote_generation_operations} 远程生成 operation</dd>
        <dt>错误码</dt><dd>{Object.entries(surface.error_codes).map(([code, items]) => `${code}: ${[].concat(items).join(" / ")}`).join("；")}</dd>
      </dl></div>
      <div className="two-col">
        <div><h3>路由 → scope</h3>
          <table className="table"><thead><tr><th>方法</th><th>路径</th><th>Scope</th></tr></thead>
            <tbody>{surface.scopes.route_scopes.map((item) => <tr key={`${item.method}${item.path_regex}`}>
              <td>{item.method}</td><td><code>{item.path_regex}</code></td><td>{item.scope}</td></tr>)}</tbody></table></div>
        <div><h3>强制幂等键</h3>
          <table className="table"><thead><tr><th>方法</th><th>路径</th></tr></thead>
            <tbody>{surface.scopes.requires_idempotency_key.map((item) => <tr key={`${item.method}${item.path_regex}`}>
              <td>{item.method}</td><td><code>{item.path_regex}</code></td></tr>)}</tbody></table>
          <p className="capability-note"><WarningCircle weight="fill" />{surface.scopes.note}</p></div>
      </div>
    </section>}
  </section>;
}

function WebhookPage() {
  const [endpoints, setEndpoints] = useState([]);
  const [catalog, setCatalog] = useState(null);
  const [created, setCreated] = useState(null);
  const [selected, setSelected] = useState(null);
  const [history, setHistory] = useState(null);
  const [form, setForm] = useState({
    name: "生产事件接收端", url: "",
    event_types: ["batch.completed", "item.completed", "item.failed", "package.ready", "budget.blocked"],
  });
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { load(); }, []);
  useEffect(() => { if (notice) { const timer = setTimeout(() => setNotice(null), 9000); return () => clearTimeout(timer); } }, [notice]);

  async function load() {
    const [list, meta] = await Promise.all([apiRequest("/webhooks"), apiRequest("/webhooks/catalog")]);
    if (list.ok) setEndpoints(await list.json());
    if (meta.ok) setCatalog(await meta.json());
  }
  function toggleEvent(eventType) {
    setForm((value) => ({
      ...value,
      event_types: value.event_types.includes(eventType)
        ? value.event_types.filter((item) => item !== eventType)
        : [...value.event_types, eventType],
    }));
  }
  async function createEndpoint() {
    setBusy(true);
    try {
      const response = await apiRequest("/webhooks", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: form.name, url: form.url, event_types: form.event_types }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.detail || body.detail || "注册失败");
      setCreated(body);
      setNotice(["success", "目标已注册：签名密钥只显示这一次，请保存到接收端"]);
      await load();
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function act(endpoint, action, body = {}) {
    setBusy(true);
    try {
      const response = await apiRequest(`/webhooks/${endpoint.id}/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail?.detail || payload.detail || "操作失败");
      if (action === "rotate-secret") setCreated({ ...payload, name: `${endpoint.name}（轮换后的新密钥）` });
      if (action === "test") {
        const delivery = payload.delivery;
        setNotice([delivery?.status === "DELIVERED" ? "success" : "warning",
          `测试投递：${delivery?.status || "无结果"}${delivery?.response_status ? ` · HTTP ${delivery.response_status}` : ""}`]);
      } else {
        setNotice(["success", `${endpoint.name}：${action} 完成`]);
      }
      await load();
      if (selected?.id === endpoint.id) await loadHistory(endpoint.id);
    } catch (error) { setNotice(["danger", error.message]); } finally { setBusy(false); }
  }
  async function loadHistory(endpointId) {
    setSelected(endpoints.find((item) => item.id === endpointId) || { id: endpointId });
    const response = await apiRequest(`/webhooks/${endpointId}/deliveries`);
    if (response.ok) setHistory(await response.json());
  }
  function copySecret() {
    if (!created?.secret) return;
    navigator.clipboard?.writeText(created.secret);
    setNotice(["success", "密钥已复制（刷新后无法再次读取）"]);
  }

  return <section className="page">
    <div className="page-head"><div><b>V6 EVENT OUTBOX</b><h1>事件与 Webhook</h1>
      <p>业务状态与事件同事务写入 Outbox，投递 Worker 异步发送；交付语义是至少一次，接收方按 event_id 去重。</p></div></div>
    {notice && <div className={`notice ${notice[0]}`}><span>{notice[1]}</span><button onClick={() => setNotice(null)}><X /></button></div>}
    {created?.secret && <section className="card">
      <div className="card-head"><div><h2>签名密钥（只显示一次）</h2><small>{created.name} · {created.url}</small></div>
        <button onClick={copySecret}><DownloadSimple />复制</button></div>
      <pre className="key-once">{created.secret}</pre>
      <p className="capability-note"><WarningCircle weight="fill" />验签：HMAC-SHA256(secret, X-PDA-Timestamp + "." + 原始请求体)；
        时间戳容差 {catalog?.tolerance_seconds ?? 300} 秒，重投使用新时间戳但 event_id 不变。</p>
    </section>}
    <div className="two-col">
      <section className="card">
        <div className="card-head"><div><h2>Webhook 目标</h2><small>{endpoints.length} 个</small></div></div>
        <table className="table"><thead><tr><th>目标</th><th>订阅</th><th>投递</th><th>状态</th><th /></tr></thead>
          <tbody>{endpoints.map((item) => <tr key={item.id}>
            <td>{item.name}<small>{item.url}</small></td>
            <td><small>{item.event_types.join(", ")}</small></td>
            <td><small>成功 {item.stats.by_status.DELIVERED || 0} · 重试 {item.stats.pending_retry} · 死信 {item.stats.dead_letter}</small>
              {item.stats.last_delivery && <small>最近 {item.stats.last_delivery.status} {item.stats.last_delivery.response_status || ""}</small>}</td>
            <td>{item.paused_at ? <Pill status="FAILED" /> : item.enabled ? <Pill status="SUCCEEDED" /> : <Pill status="DRAFT" />}</td>
            <td><div className="action-row">
              <button onClick={() => loadHistory(item.id)} disabled={busy}>投递记录</button>
              <button onClick={() => act(item, "test")} disabled={busy}>测试</button>
              {item.paused_at ? <button onClick={() => act(item, "resume")} disabled={busy}>恢复</button>
                : <button onClick={() => act(item, "pause", { reason: "控制台暂停" })} disabled={busy}>暂停</button>}
              <button onClick={() => act(item, "rotate-secret", { grace_seconds: 86400 })} disabled={busy}>轮换密钥</button>
            </div></td></tr>)}</tbody></table>
        {!endpoints.length && <p className="capability-note">还没有目标：事件会写入 Outbox 但没有接收方，控制台可随时补注册（历史事件不会补投）。</p>}
      </section>

      <section className="card">
        <div className="card-head"><div><h2>注册目标</h2><small>只接受 HTTPS（本机回环联调可用 http://127.0.0.1）</small></div></div>
        <div className="inline-fields">
          <label>名称<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
          <label>接收地址<input value={form.url} onChange={(event) => setForm({ ...form, url: event.target.value })} placeholder="https://example.com/hooks/pda" /></label>
        </div>
        <div className="scope-grid">{(catalog?.event_types || []).map((eventType) => <label className="checkbox-line" key={eventType}>
          <input type="checkbox" checked={form.event_types.includes(eventType)} onChange={() => toggleEvent(eventType)} />
          <span>{eventType}</span></label>)}</div>
        <div className="action-row"><button className="primary" onClick={createEndpoint} disabled={busy}><Plus />注册目标</button></div>
        {catalog && <div className="profile-detail"><dl>
          <dt>签名方案</dt><dd>{catalog.signature_scheme}</dd>
          <dt>请求头</dt><dd>{Object.entries(catalog.headers).map(([key, value]) => `${value}（${key}）`).join("；")}</dd>
          <dt>重试计划</dt><dd>{catalog.retry_schedule_seconds.join("s / ")}s（共 {catalog.max_attempts} 次尝试）</dd>
          <dt>死信</dt><dd>{catalog.dead_letter}</dd>
          <dt>顺序</dt><dd>{catalog.note}</dd>
        </dl></div>}
      </section>
    </div>

    {history && <section className="card">
      <div className="card-head"><div><h2>投递记录（脱敏）</h2><small>{selected?.name || selected?.id}</small></div></div>
      <table className="table"><thead><tr><th>事件</th><th>尝试</th><th>状态</th><th>响应</th><th>时间</th></tr></thead>
        <tbody>{history.deliveries.map((item) => <tr key={item.id}>
          <td>{item.event_id.slice(0, 8)}<small>{item.event_type}</small></td>
          <td>#{item.attempt}</td>
          <td>{item.status}</td>
          <td><small>{item.response_status || item.error || "—"}</small><small>{item.response_excerpt?.slice(0, 60)}</small></td>
          <td>{formatTime(item.created_at)}</td></tr>)}</tbody></table>
      {!!history.dead_letters.length && <div>
        <h3>死信（修复后可重放，event_id 不变）</h3>
        <table className="table"><thead><tr><th>事件</th><th>次数</th><th>原因</th><th /></tr></thead>
          <tbody>{history.dead_letters.map((item) => <tr key={item.event_id}>
            <td>{item.event_id.slice(0, 8)}<small>{item.event_type}</small></td>
            <td>{item.attempts}</td>
            <td><small>{item.reason}</small><small>{item.last_error?.slice(0, 60)}</small></td>
            <td>{item.replayed_at ? <small>已重放 {formatTime(item.replayed_at)}</small>
              : <button onClick={() => act(selected, `dead-letters/${item.event_id}/replay`)} disabled={busy}>重放</button>}</td></tr>)}</tbody></table>
      </div>}
      {!history.deliveries.length && <p className="capability-note">还没有投递记录。</p>}
    </section>}
  </section>;
}

function ConsolePage() {
  const [overview, setOverview] = useState(null);
  const [window, setWindow] = useState(60);
  const [auto, setAuto] = useState(true);
  const [notice, setNotice] = useState(null);

  useEffect(() => { load(); }, [window]);
  useEffect(() => {
    if (!auto) return undefined;
    const timer = setInterval(() => { load(); }, 5000);
    return () => clearInterval(timer);
  }, [auto, window]);
  useEffect(() => { if (notice) { const timer = setTimeout(() => setNotice(null), 6000); return () => clearTimeout(timer); } }, [notice]);

  async function load() {
    const response = await apiRequest(`/console/overview?window_minutes=${window}`);
    if (response.ok) setOverview(await response.json());
    else setNotice(["danger", "总控台数据读取失败"]);
  }
  const metric = (value) => (value === null || value === undefined ? "—" : value);

  return <section className="page">
    <div className="page-head"><div><b>V6 CONSOLE</b><h1>总控台</h1>
      <p>全部数值来自数据库实时聚合（作业/队列/批次/账本/事件/投递）；页面每 5 秒刷新一次。</p></div>
      <div className="action-row">
        <label className="checkbox-line"><input type="checkbox" checked={auto} onChange={() => setAuto(!auto)} /><span>自动刷新</span></label>
        <select value={window} onChange={(event) => setWindow(Number(event.target.value))}>
          <option value={15}>近 15 分钟</option><option value={60}>近 1 小时</option>
          <option value={180}>近 3 小时</option><option value={1440}>近 24 小时</option></select>
        <button onClick={load}><ArrowClockwise />刷新</button>
      </div>
    </div>
    {notice && <div className={`notice ${notice[0]}`}><span>{notice[1]}</span><button onClick={() => setNotice(null)}><X /></button></div>}
    {!overview && <p className="capability-note">正在读取总控台数据…</p>}
    {overview && <>
      <section className="metric-grid">
        <div className="metric"><span>队列中</span><b>{metric(overview.queue.queued)}</b><small>运行中 {overview.queue.running}</small></div>
        <div className="metric"><span>作业成功率</span><b>{Math.round((overview.jobs.success_rate || 0) * 100)}%</b>
          <small>终态作业 {overview.jobs.total} 个</small></div>
        <div className="metric"><span>批次项</span><b>{Object.values(overview.batches.items_by_status).reduce((sum, value) => sum + value, 0)}</b>
          <small>{Object.entries(overview.batches.items_by_status).map(([key, value]) => `${key} ${value}`).join(" · ") || "无"}</small></div>
        <div className="metric"><span>未投递事件</span><b>{overview.events.undelivered}</b>
          <small>窗口内新增 {overview.events.window}</small></div>
        <div className="metric"><span>投递延迟 p50 / p95</span>
          <b>{metric(overview.webhooks.delivery_latency_ms.p50)} / {metric(overview.webhooks.delivery_latency_ms.p95)} ms</b>
          <small>样本 {overview.webhooks.delivery_latency_ms.sample} · 死信 {overview.webhooks.dead_letters_open}</small></div>
        <div className="metric"><span>成本（已确认）</span>
          <b>{(overview.cost.by_kind_currency.filter((item) => item.kind === "settled")[0]?.total) ?? 0}</b>
          <small>已发生未对账 {(overview.cost.by_kind_currency.filter((item) => item.kind === "accrued")[0]?.total) ?? 0} · 未定价用量 {overview.cost.unpriced_usage_events}</small></div>
      </section>
      {overview.queue.jobs_without_queue_row > 0 && <p className="capability-note danger">
        <WarningCircle weight="fill" />有 {overview.queue.jobs_without_queue_row} 个队列中的作业缺少 run_jobs 队列行：
        它们不会被 Worker 领取；读取批次会触发对账补建（V6-07 修复），如需立即处理可打开对应批次页。</p>}
      <div className="two-col">
        <section className="card">
          <div className="card-head"><div><h2>作业与运行</h2><small>测量时间 {formatTime(overview.measured_at)}</small></div></div>
          <table className="table"><thead><tr><th>状态</th><th>作业</th><th>窗口内</th><th>运行</th></tr></thead>
            <tbody>{Object.entries(overview.jobs.by_status).map(([status, value]) => <tr key={status}>
              <td>{status}</td><td>{value}</td><td>{overview.jobs.window[status] ?? 0}</td>
              <td>{overview.runs[status] ?? 0}</td></tr>)}</tbody></table>
          <h3>失败原因（前 5）</h3>
          <table className="table"><thead><tr><th>状态</th><th>原因</th><th>次数</th><th>最近</th></tr></thead>
            <tbody>{overview.failures.map((item, index) => <tr key={`${item.status}-${index}`}>
              <td>{item.status}</td><td><small>{item.error || "（无错误文本）"}</small></td>
              <td>{item.count}</td><td>{formatTime(item.last_at)}</td></tr>)}</tbody></table>
          {!overview.failures.length && <p className="capability-note">没有失败或 QA 拒绝的作业。</p>}
        </section>
        <section className="card">
          <div className="card-head"><div><h2>批次、事件与自动化</h2><small>{overview.window_minutes} 分钟窗口</small></div></div>
          <div className="profile-detail"><dl>
            <dt>批次状态</dt><dd>{Object.entries(overview.batches.by_status).map(([key, value]) => `${key} ${value}`).join(" · ") || "无"}</dd>
            <dt>项窗口内</dt><dd>{Object.entries(overview.batches.recent_items).map(([key, value]) => `${key} ${value}`).join(" · ") || "无"}</dd>
            <dt>投递（窗口）</dt><dd>尝试 {overview.webhooks.attempts} · 成功 {overview.webhooks.delivered} · 重试 {overview.webhooks.retry} · 死信 {overview.webhooks.dead}</dd>
            <dt>暂停目标</dt><dd>{overview.webhooks.paused_endpoints}</dd>
            <dt>自动化 Key</dt><dd>有效 {overview.automation_keys.active} · 已撤销 {overview.automation_keys.revoked} · 累计调用 {overview.automation_keys.calls_window}</dd>
            <dt>用量事件</dt><dd>{overview.cost.usage_events}</dd>
          </dl></div>
          <h3>最近事件</h3>
          <table className="table"><thead><tr><th>事件</th><th>类型</th><th>投递</th><th>时间</th></tr></thead>
            <tbody>{overview.events.latest.map((item) => <tr key={item.event_id}>
              <td>{item.event_id.slice(0, 8)}</td><td><small>{item.event_type}</small></td>
              <td>{item.delivered > 0 ? <Pill status="SUCCEEDED" /> : <small>尝试 {item.attempts}</small>}</td>
              <td>{formatTime(item.created_at)}</td></tr>)}</tbody></table>
          {!overview.events.latest.length && <p className="capability-note">还没有事件：注册 Webhook 目标并产生批次/发布动作后会出现在这里。</p>}
          <ul className="notes">{overview.notes.map((text) => <li key={text}>{text}</li>)}</ul>
        </section>
      </div>
    </>}
  </section>;
}

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
      {["products", "assets"].includes(active) ? <Library assets={assets} onSelect={selectAsset} /> : active === "fidelity" ? <FidelityPage jobs={jobs} /> : active === "interaction" ? <InteractionPage /> : active === "reference" ? <ReferencePage /> : active === "profiles" ? <ProfilePage /> : active === "batch" ? <BatchPage /> : active === "audio" ? <AudioPage /> : active === "packages" ? <PackagePage /> : active === "console" ? <ConsolePage /> : active === "costs" ? <CostPage /> : active === "automation" ? <AutomationPage /> : active === "webhooks" ? <WebhookPage /> : active === "settings" ? <Settings health={health} provider={provider} onSaveProvider={saveProvider} onTestProvider={testProvider} busy={busy} /> : <>
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
