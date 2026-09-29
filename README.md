# 冷链物流温控管理平台

面向冷链运输的车辆调度、温控监测、冷库作业、签收回单、异常报警与冷链断链追溯的综合物流管理后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 发运单管理 | `shipment` | 发运单 | 运单编号、发货方、收货方 |
| 温控监测 | `temp_monitor` | 温度记录 | 记录编号、运单编号、当前温度 |
| 车辆调度 | `vehicle` | 冷藏车辆 | 车辆编号、车牌号、车型类别 |
| 司机管理 | `driver` | 驾驶人员 | 司机编号、姓名、驾驶证号 |
| 冷库运营 | `cold_storage` | 冷库库区 | 库区编号、库区名称、设定温度 |
| 装卸作业 | `loading` | 装卸记录 | 记录编号、运单编号、装卸类型 |
| 报警管理 | `alert` | 报警记录 | 报警编号、报警类型、关联设备 |
| 线路规划 | `route` | 运输线路 | 线路编号、始发地、到达地 |
| 制冷机组 | `reefer_unit` | 制冷设备 | 机组编号、所属车辆、机组型号 |
| 油料管理 | `fuel` | 加油记录 | 记录编号、车辆编号、油料类型 |
| 签收回单 | `delivery` | 签收记录 | 签收编号、运单编号、签收人 |
| 断链追溯 | `break_chain` | 断链事件 | 事件编号、运单编号、断链环节 |
| 月台管理 | `dock` | 装卸月台 | 月台编号、月台类型、温层分区 |
| 包装管理 | `package` | 保温包装 | 包装编号、包装类型、保温材料 |
| 通行费用 | `toll` | 过路记录 | 记录编号、车辆编号、收费站名称、通行方向、收费金额 |
| 车辆消杀 | `sanitation` | 消杀记录 | 消杀编号、车辆编号、消杀方式 |
| 承运合同 | `contract` | 运输合同 | 合同编号、托运方、承运方 |
| 货运保险 | `insurance` | 保险单 | 保单编号、运单编号、投保险种 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 通行费用统一口径

通行费用围绕过路记录、收费站、通行方向、收费金额，把金额阈值、例外路线、判定依据
集中在 `backend/app/services/toll_policy.py`，全平台只有这一个判定出口：

- **同一结论**：车辆列表（通行费列）、过路明细、费用汇总（`/api/toll/summary`）
  都调用同一个 `TollPolicy.evaluate`，不允许各页面再算一套。
- **阈值版本化**：金额阈值按版本生效期存放在 `toll_policy_versions` 表，
  判定时按通行日期回溯当时版本；规则改版不改变历史记录的结论（历史费用按当时规则留痕）。
- **例外路线优先级**：多条规则同时命中先取优先级最高档；同档位处置结论仍冲突的，
  判「异常待核」，以业务确认凭证为准。
- **异常待核**：金额越界、金额无法识别、规则冲突一律标待核；业务确认时判异记录
  必须填写凭证编号，且每条记录只允许落一个最终结论。
- **并发补录**：同一业务键（车辆 + 收费站 + 方向 + 日期）经写锁去重，
  并发补录只保留第一条及其最终结论，重复提交直接拦截。
- **留痕**：登记、业务确认、冲销都追加到 `判定历史`，只追加不改写；
  冲销记录金额不计入汇总但记录保留。
- 口径查询：`GET /api/toll/rules`（阈值版本 + 按优先级排序的例外路线）。
- 口径校验脚本（仅依赖标准库）：`cd backend && python3 tests/test_toll_rules.py`。
