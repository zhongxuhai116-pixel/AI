# ADR-001：保留 V1 原型，显式记录与目标架构的差距

日期：2026-09-10  
状态：现状记录；不表示用户批准降低验收标准。

## 背景

主规划定义 TypeScript、PostgreSQL、独立 Worker、版本合同与远程渲染。现有可用核心采用 React JSX、SQLite、FastAPI BackgroundTasks 和本机 Blender/FFmpeg。
历史验收描述“PASS”不能覆盖所有主规格退出门；换电脑时若直接照总规划重建，会丢失已有实现并掩盖差距。

## 决定

- 保留现有原型；本轮只整理文档，不迁移数据库、不重写代码。
- CURRENT_PHASE 标为 V1 本机核心已有实现、完整验收未完成；远程部署 BLOCKED。
- 实现事实以代码和实际测试为准；目标规格保留，缺口集中列入 V1_IMPLEMENTATION_GAPS。
- 旧本机测试为历史结果，不延伸为新电脑、Linux 或 GPU 云端通过。
- 用户已授权现有云节点上的 Blender 部署；不等于解锁 V2 或自动采购/云平台管理 API。
- 商品类别保持通用；拳击靶只作为可替换素材，不作为必需主场景。
- 已购资源 RTX 4090 24GB 优先复用；购买前的 5090/48GB 比较不作为当前运行配置。

## 后果

需要后续按真实风险补齐计划执行、可靠队列、远程鉴权、Linux 凭证适配和验收。结构变更须有备份、迁移、回滚与回归证据；不能仅修改文档把缺口变成通过。

## 关联

- [实现差距](../V1_IMPLEMENTATION_GAPS.md)
- [新电脑交接](../HANDOFF_NEW_COMPUTER.md)
- [云端状态](../CLOUD_SERVER_HANDOFF.md)

