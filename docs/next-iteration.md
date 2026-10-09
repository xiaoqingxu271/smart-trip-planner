# 下一轮业务迭代方案

> 状态：待实施，不包含在当前 main 交付范围内。  
> 原则：把「生成一份好看的行程 JSON」做成「一份能对照着出门的日程」；继续用「真实数据进 → 确定性规则拦 → 修不掉就警告」，不新开聊天型 Agent，不上实时订票订房。

本文只覆盖**下一轮建议落地的三批改动**。工程侧已完成的异步 Job、MCP 白名单、景点校验、局部重规划不在此重复。

---

## 0. 本轮目标与边界

**要解决的用户体感**

1. 数字和店名看起来像事实，一对导航/官网就穿帮。
2. 默认「人已经在目的地、全天可玩」，首末日排满。
3. 行程是景点清单，不是带钟点的日程。
4. 酒店不知道景点簇在哪；餐饮从未被搜索或核验。
5. 首页标签（美食、亲子、节奏）几乎不改变候选池和密度。

**本轮明确不做**

| 不做 | 原因 |
|---|---|
| 实时订票 / 订房 / 查余票 | 个人高德 Key 无 OTA 库存；继续用「用户反馈 → 约束重规划」 |
| 多城市复杂图搜索（杭州+乌镇一等座时刻表） | 超出单机配额与当前流水线；单城接驳先做 |
| 社交分享、方案 A/B 自动比价 | 体验增强，不解决可信度 |
| 换 LangGraph / 加自治多 Agent | 流程仍是固定流水线，编排形态不需要改 |

**建议切片**：先完整做完批次 1 再开批次 2。批次 3 可与 2 并行一半（表单字段），规则落地依赖 2 的候选池。

---

## 批次 1 · 可执行（优先，改动面小）

目标：不增加 Agent，让现有行程「能走、对得上、不装精确」。

### 1.1 首末日时段，避免满日误排

**问题**

`TripRequest` 只有 `destination + start_date + days`。Planner 提示词要求每天 2–3 个景点，第一天默认上午就能逛。早班机抵达仍可能排上午故宫；返程日前一晚仍可能住远郊。

**现状**

- 模型：`backend/app/models/schemas.py` → `TripRequest`
- 表单：`frontend/src/views/Home.vue`（出发日期、天数，无抵达/离开）
- 规划输入：`TripPlannerAgent._format_request()` 不包含接驳信息
- 校验：`validator.py` 无首末日密度特例

**方案**

1. `TripRequest` 增加可选字段（向后兼容，老请求视为「全天可玩」）：
   - `arrival_slot: Literal["morning","afternoon","evening",""] = ""`
   - `departure_slot: Literal["morning","afternoon","evening",""] = ""`
   - 暂不强制出发城市（批次 3 再收）；本批只收时段，用 notes 仍可写出发地。
2. 首页两个时段选择器（芯片即可，不必时间选择器）。
3. `_format_request()` 写入：「第 1 天 {arrival_slot} 抵达，当日只排下午/晚上；最后一天 {departure_slot} 离开，当日只排离开前能完成的点」。
4. `PLANNER_AGENT_PROMPT` 增加硬规则：
   - `afternoon` 抵达：Day1 最多 1–2 个点，且不排上午场。
   - `evening` 抵达：Day1 只排晚餐+入住，0 个景点。
   - `morning` 离开：末日 0–1 个近酒店/车站的点，不住「再订一晚远郊」。
5. Validator 增加弱/强规则（可先弱提醒）：
   - Day1 在 `evening` 抵达时若 `len(attractions) > 0` → 强，触发一次修复（清空当日景点或移到次日）。
   - 末日 `morning` 离开时若当日景点 > 1 或相邻距离仍按 15km 算 → 强。

**验收**

- 选「下午抵达 + 上午离开」的 3 日行程：Day1 无上午场、Day3 至多 1 个点。
- 不填时段时行为与现在一致（回归）。
- 前端 `types/index.ts` 与 Schema 同步；旧历史 JSON 反序列化不报错。

---

### 1.2 景点时间轴：用开园 + 游览 + 通勤生成 start/end

**问题**

Attraction 只有文案 `duration`（如 `"2小时"`）。结果页时间线按列表渲染，iCal（`ical_service.py`）从每天 09:00 起顺延，三餐写死 08:00 / 12:00 / 18:00，与真实开园无关。闭馆校验只看「星期几关门」，不管当天下午是否已停票。

**现状**

- iCal 已有「从 09:00 按 duration + 30 分钟空隙顺延」的雏形，但：
  - 不考虑首日抵达时段；
  - 通勤固定 30 分钟，不用 `route_service` 的真实 `duration_s`；
  - 结果页不展示钟点，用户看不到这套时间。
- POI 详情里已能取到 `open_time` / `opentime2`（校验器 `_get_poi_info`），但没用到「几点开/最晚入园」。

**方案**

1. Schema 为 `Attraction` 增加可选：
   - `start_time: str = ""`  # `"14:30"`
   - `end_time: str = ""`
   - `open_time_text: str = ""`  # 高德原文，展示用
   规划器**不要**让 LLM 编钟点。钟点由规划后的确定性调度器填写。
2. 新增 `backend/app/services/schedule_service.py`（纯函数，便于单测）：
   - 输入：某日景点列表（已有坐标与 `duration`）、当日 `DayRoute`（已有 legs 的 `duration_s`）、`arrival_slot` / 是否末日、当天 `open_time` 缓存。
   - 日开始时刻：`morning` 09:00；`afternoon` 13:30；`evening` 不排景点。
   - 解析 `duration` 复用 `_parse_duration`（可抽到 `app/utils/duration.py`，iCal 共用）。
   - 点与点之间用对应 leg 的 `duration_s`，无路线时 fallback 30 分钟。
   - 若某点 `end_time` 超过解析出的最晚入园/闭园：写入 warning，不在调度器里改点位（改点位交给现有 Validator 修复；本批先警告）。
3. 调用位置：`main.py` 在 `compute_day_routes` 之后、入库之前跑调度器，写回 `plan.daily_plans[*].attractions[*].start_time/end_time`。
4. 前端 `Result.vue` 时间线每条景点显示 `14:30–16:30`；无字段的历史行程隐藏钟点。
5. `ical_service.py` 改为优先用 `start_time/end_time`，没有则保留旧的 09:00 顺延。三餐仍可用固定槽，直到批次 2 有餐厅坐标后再夹在景点间隙。

开园文案解析（`9:00-16:30` / `08:30-17:00（16:00停止入园）`）规则要写死并单测，解析失败则当天不卡闭园，只排钟点。

**验收**

- 有路线的天：第二点开始时间 ≈ 第一点结束 + 该 leg 车程，误差允许取整到 5 分钟。
- FakeClient 单测：给定 duration 与 legs，输出的 start/end 稳定。
- 导出 iCal 的 DTSTART 与页面钟点一致。

---

### 1.3 用真实路线回写市内交通费

**问题**

`route_service.compute_day_routes` 已拿高德 `taxi_cost`，结果页地图旁展示「打车约 ¥x」。`Budget.transport_total` 仍是 Planner 估算，经常和地图对不上。提示词还要求预算贴近用户总额，模型会拿交通费当调节阀。

**现状**

- 路线：`backend/app/services/route_service.py`
- 预算模型：`Budget.transport_total` + `calc_grand_total`（分项 > 0 时重算总计）
- 调用：规划成功后 `main.py` 里算路线，**没有写回 Budget**

**方案**

1. 规划 / 重规划 / 手动编辑保存后，凡是重新算了 `DayRoute` 的路径：
   - `transport_total = round(sum(day.taxi_cost or 0), 2)`
   - 若用户选择将来会有「公交/步行」偏好（批次 3），本批仍按打车合计，但在 Budget 旁标注「按高德打车估价」。
2. Planner 提示词改为：`transport_total` 填 0，由系统回写；不要为凑总预算改门票/餐费。
3. 门票合计改为：对 `ticket_price` 求和（确定性），酒店合计：`price_per_night * 住宿晚数`（见 1.4 晚数）。餐费仍暂用模型数，批次 2 再核。
4. `grand_total` 继续走现有 `calc_grand_total`。
5. 前端交通分项加小字：「来自高德驾车路线打车估价，非公交/地铁票价」。

**验收**

- 同一行程：预算「交通」= 各天地图打车费之和。
- 演示模式无路线时保持模型原值或 0，不报错。

---

### 1.4 价格全部标明「估算」，禁止为凑预算编数

**问题**

门票、房价、餐标都来自 LLM，页面用 `¥` 展示，用户会当成报价。`PLANNER_AGENT_PROMPT` 第 6 条要求分项合计匹配用户预算，会反向激励幻觉。

**方案**

1. 文案：结果页预算区标题改为「费用估算」；门票/住宿/餐饮标签加「估算」。交通在 1.3 之后改为「路线估价」。
2. Schema 不强制加 `is_estimate` 也可以先靠 UI；若希望导出 PDF 也带说明，在 `TripPlan` 增加 `budget_note: str = "门票/住宿/餐饮为模型估算，交通为高德打车估价"`，导出与页面共用。
3. 提示词删除「合计需与用户预算匹配」；改为「门票未知填 0，禁止编造精确票价；若用户给了总预算，只在 tips 里说明是否可能超支」。
4. Validator 弱提醒：`ticket_price > 0` 但高德详情无票价字段时，不必当错误（高德常无票价）。
5. 用户总预算仅用于：tips 超支提示 + 酒店搜索关键词档次（经济/舒适），不反向改数字。

**验收**

- 页面与 PDF 均可见「估算」说明。
- 给 3000 元预算时，不再出现「交通费被调到刚好凑满」的规律性凑数（可用同一目的地跑两次看 transport 是否等于路线合计）。

---

## 批次 2 · 少幻觉（流水线改为有依赖）

目标：餐饮、酒店进入「真 POI + 校验」路径；酒店等景点簇确定后再搜。

### 2.1 餐饮走 MCP，纳入真实性校验

**问题**

无餐饮 Agent。餐厅名由 Planner 生成，Validator 只核景点。`Meal.location` 注释写明「Planner 不产出餐厅坐标，图片增强时顺带富化」——这是把事实发现推迟到配图，配图失败则导航也失败。

**方案**

1. 新增 `MealSearchAgent`（仍是 `SimpleAgent` + 白名单），或**更小的做法**：不新增 Agent，在景点搜索完成后由编排器**确定性**调用 `maps_around_search`：
   - 每个「需要正餐的时段」用当日景点簇中心搜 `keywords=美食/餐厅`，`radius=1500`。
   - 把候选表（名称|地址|坐标|品类）拼进 Planner 输入，与景点表同等地位。
   - 推荐采用**确定性搜索、不新增 Agent**：省一轮 LLM、配额可控、候选可单测。若坚持对称的四+一 Agent，再包一层 `MealAgent`，白名单仅 `maps_around_search` + `maps_text_search`。
2. 早餐默认不搜店：`Meal.type=breakfast` 允许 `restaurant="酒店早餐"` / `"自行解决"`，无坐标，前端不显示导航。
3. Planner 约束：午餐/晚餐的 `restaurant/address/location` 必须来自候选表原文；禁止候选外的店名。
4. Validator 扩展：
   - 对 `lunch/dinner` 走与景点相同的 `_get_poi_info` + 坐标偏差 1.5km；
   - 核不到 → 弱提醒（店名难对齐）；核到但坐标偏差 → 强，带周边候选修复。
5. 图片增强继续可以补图，但**不得作为唯一的坐标来源**；Planner 输出就必须带 location。
6. 测试：`test_validator.py` 增加餐厅坐标偏差用例（FakeClient）。

**验收**

- 正餐带坐标，结果页可导航；早餐无店也可通过校验。
- 手工把餐厅改成「张三私房菜」且坐标乱填，校验给出强问题或 warning。

---

### 2.2 酒店改为「景点聚类后再搜」，默认一订到底

**问题**

`TripPlannerAgent.plan()` 中景点 / 天气 / 酒店**串行但无数据依赖**：酒店 prompt 只用 `destination` 与用户需求，不看景点结果。HotelAgent 搜全市「酒店」。Schema 每天一个 `hotel`，提示词写「可每晚相同」，模型仍常换店。

**方案**

1. 调整 `plan()` 顺序：
   ```
   景点 Agent →（可选）确定性聚类 → 酒店搜索 → 天气（仍可并行于酒店，互不依赖）
   ```
   天气与酒店可在景点之后用线程并行，以节省墙钟时间；共享 MCP 已有 `_io_lock`，并行只会排队，不会坏消息，墙钟收益有限。**更稳妥：保持串行，先景点后酒店。**
2. 聚类（确定性，`app/services/geo.py`）：
   - 对景点候选坐标算简单质心，或按天数还没有分配时用全部候选的中位经纬度。
   - 酒店搜索改走 `maps_around_search(keywords="酒店", location=质心, radius=5000)`，由编排器直连 `mcp_tool.client`，**可不再让 HotelAgent 自己选 keywords**。
   - 若保留 HotelAgent：把质心和「请 around_search 此坐标」写进 prompt，白名单加上 `maps_around_search`。
3. 规划规则：全程同一家酒店，写在 `TripPlan` 级或每天复制同一对象；末日 `departure_slot=morning` 时最后一晚仍是这家（或 tips 提示可退一晚，本批不自动删晚）。
4. 住宿晚数 = `days - 1`（下午抵达当天算一晚）；`hotel_total = price_per_night * nights` 由后处理重写。
5. Validator：同一行程内若出现两家不同酒店名 → 弱提醒「短途建议一订到底」；坐标与当日景点质心直线距离 > 15km → 强（可修）。

**验收**

- 日志/中间结果里酒店搜索带 around 坐标，而不是仅 `city=南京 keywords=酒店`。
- 3 日行程 `hotel_total ≈ 单价 × 2`。

---

### 2.3 流水线依赖与提示词收口

**问题**

当前是「中心编排 + 文本黑板」，但黑板上酒店/餐饮与景点无因果关系。批次 2 改完后必须同步 prompt，否则 Planner 仍会忽略候选表。

**方案**

1. `planner_input` 结构固定为四块 Markdown：用户需求（含时段/节奏）→ 景点候选 → **正餐候选（按日或按簇）** → 酒店候选（含坐标）→ 天气。
2. 明确「不在候选中的 POI 禁止出现」；校验器负责兜底。
3. README / `docs/interview-map.html` 架构图把酒店从「与景点并行」改为「依赖景点簇」。面试叙事也要改：不是四个完全独立的搜索，而是有数据依赖的流水线。

**验收**

- 单测或快照：给定伪造的三份候选表，Planner 解析后的店名 ⊆ 候选名（可用 Fake LLM 或对 `_post_process` 注入计划来测校验，不强制测 LLM）。

---

## 批次 3 · 更像规划师（表单开始真正改产出）

在批次 1–2 的模型与校验上加约束。可先改表单与 Schema，规则分条打开。

### 3.1 必去 / 不去硬约束

**问题**

`notes` 是 500 字软文本，模型常忽略「必须去中山陵 / 不去夫子庙夜市」。

**方案**

1. `TripRequest`：
   - `must_see: list[str]` 最多 5，每项 ≤ 40 字
   - `avoid: list[str]` 最多 5
2. 首页两个可增删的标签输入（回车生成 tag）。
3. 景点 Agent prompt：必须先 `maps_text_search` 每个 must_see，再搜偏好。
4. Validator 强规则：must_see 每个名字在全部 `attractions`（含 backup 不算）中按包含匹配至少一次；avoid 命中则强，带其它候选修复。
5. 修不掉（高德搜不到该 must_see）：warning「未找到必去点 X，未编入行程」。

**验收**

- 必去「中山陵」→ 行程中出现该名或高德官方全名。
- 不去「某某夜市」→ 主行程不含该名。

---

### 3.2 节奏档直接改每天上限

**问题**

「不想太赶」只在 notes 里；Validator `_MAX_PER_DAY = 3` 全局写死，亲子与特种兵同一把尺。

**方案**

1. `TripRequest.pace: Literal["easy","standard","packed"] = "standard"`
2. 映射：
   - easy：每天最多 2 景，相邻驾车阈值 10km（或步行场景见 3.4）
   - standard：3 景 / 15km（现状）
   - packed：4 景 / 20km
3. `_MAX_PER_DAY` 与距离阈值改为 `validate_plan(..., pace)` 入参，不要全局常量一刀切。
4. 首日/末日上限在 1.1 的时段规则上再取 min(节奏上限, 时段上限)。
5. 首页三档芯片，默认标准。

**验收**

- easy 下 Validator 对 3 个景点的天出强问题；standard 回归旧行为。

---

### 3.3 雨天室内规则（确定性，不靠提示词）

**问题**

Planner 被要求雨天排室内，Validator 不检查。天气 Agent 输出城市预报，与景点类型无关联。

**方案**

1. 维护一份小词表（博物馆/展览馆/美术馆/室内街区/商场/水族馆 vs 公园/登山/户外）。景点候选行里已有「类型」列，Planner 输出可增加可选 `category`，没有则用名称+类型词表猜，猜不准则跳过该点。
2. 若当日 `Weather.condition` 匹配 `雨|雪|暴`：室内或半室内景点 < 当天景点数的一半 → 弱提醒；easy 档升为强（优先把 backup 里室内点换上，或 `swap-backup` 同类逻辑自动试一次）。
3. 不为此再调 LLM，除非强问题走到现有定向修复。

**验收**

- Fake 天气「中雨」+ 三个公园名 → 出现 warnings 或修复。
- 晴天不误报。

---

### 3.4 偏好真正改搜索；市内交通方式

**问题**

偏好标签有「美食探店 / 购物血拼 / 亲子游玩」，AttractionSearch 仍是 `keywords=景点` 或笼统偏好。市内一律按驾车 15km + 打车费。

**方案**

1. 标签 → 搜索词路由表（写在 `prompts.py` 或 `preference_routing.py`，编排器在调用景点/餐饮搜索前展开）：
   - 美食探店 → 批次 2 的餐饮 around 必跑，且 Planner 每天至少 1 次「特色正餐」
   - 购物血拼 → 额外 `maps_text_search(keywords="步行街"/"商场")`
   - 博物馆控 → keywords 优先博物馆/故居
   - 亲子 → 节奏默认 easy（若用户没手改）、避免夜场词
2. `TripRequest.transit_mode: Literal["walk","transit","taxi","drive"] = "taxi"`
   - walk：相邻点直线 > 2km 即过远；交通费回写 0，tips 写步行
   - transit：仍可拉驾车距离作参考，但文案改「公交/地铁，费用未计入」；`transport_total` 不填打车费，避免误导
   - taxi/drive：保持批次 1.3
3. 结果页路线说明随模式切换文案；walk 可不画驾车折线，只打点。

**验收**

- 只选「博物馆控」时，景点候选表以馆/故居为主（可用 Agent 单测 mock MCP 返回）。
- `transit_mode=walk` 时预算交通为 0，且 15km 驾车规则不再当强问题。

---

### 3.5 出发城市（可选，与 1.1 衔接）

**问题**

无出发地时无法提示「第一程是高铁还是飞机」，也选不了车站/机场附近酒店。完整接驳（时刻表）本轮仍不做。

**方案（轻量）**

1. `origin: str = ""` 可选。填了则 tips 增加「请自行安排 {origin} → {destination} 的大交通；首日按时段预留」。
2. 若 origin == destination（本地游）：首日 `arrival_slot` 默认 morning 且可隐藏。
3. 不调票务 API。

---

## 数据模型变更一览

实施时 **Pydantic 与 `frontend/src/types/index.ts` 必须同 PR 改完**。字段均设默认值，保证库里旧 `plan_json` / `request` 能加载。

| 模型 | 新增字段 | 默认 |
|---|---|---|
| `TripRequest` | `arrival_slot`, `departure_slot` | `""` |
| `TripRequest` | `pace` | `"standard"` |
| `TripRequest` | `must_see`, `avoid` | `[]` |
| `TripRequest` | `transit_mode` | `"taxi"` |
| `TripRequest` | `origin` | `""` |
| `Attraction` | `start_time`, `end_time`, `open_time_text` | `""` |
| `Meal` | `location` 改为规划阶段必填（正餐）；早餐可空 | 已有 Optional |
| `TripPlan` | `budget_note` | 固定说明文案 |

MySQL `trips.request` / `plan` 是 JSON，**不必改表结构**。

---

## 关键代码落点

| 能力 | 主要改动文件 |
|---|---|
| 请求字段 / 校验 | `backend/app/models/schemas.py`，`frontend/src/types/index.ts`，`Home.vue` |
| 流水线顺序与拼上下文 | `backend/app/agents/trip_planner.py`，`prompts.py` |
| 钟点调度 | 新增 `backend/app/services/schedule_service.py`，接入 `api/main.py`，改 `ical_service.py` |
| 交通费回写 | `api/main.py`（`compute_day_routes` 之后），`Result.vue` 预算文案 |
| 餐饮/酒店候选 | `trip_planner.py` 直连 `mcp_tool.client`；可选 `geo.py` 质心 |
| 规则 | `backend/app/agents/validator.py`，`tests/test_validator.py` |
| 时间线 UI | `Result.vue` |

---

## 测试与回归

批次 1 必须带上：

- `test_schemas.py`：新字段缺省时旧 JSON 能 `model_validate`
- 调度器纯函数单测（不打高德）
- 预算回写：构造假 `DayRoute.taxi_cost` 断言 `transport_total`

批次 2：

- 餐厅坐标偏差 / 核不到
- 酒店晚数 × 单价
- MCP 白名单若新增餐饮工具：`test_mcp_allowlist.py`

批次 3：

- must_see / avoid / pace 上限 / 雨天词表
- FakeClient，继续不打真实 API

CI 现有 MySQL + Redis 服务容器保持；本轮不引入新外部依赖。

---

## 推荐实施顺序（单人）

1. Schema + 前端类型 + 首页时段（1.1 的输入部分）——先打通数据，Planner 规则可随后。
2. 交通费回写 + 预算「估算」文案（1.3、1.4）——半日级，立刻消除自相矛盾。
3. `schedule_service` + 结果页钟点 + iCal（1.2）。
4. Validator 首末日密度（1.1 的校验部分）。
5. 酒店改 around + 一订到底（2.2），再餐饮候选（2.1）。
6. 节奏 / 必去不去 / 雨天 / 交通方式（批次 3）。

完成 1.3+1.4 即可单独部署一版，作为本轮的最小可见交付。
