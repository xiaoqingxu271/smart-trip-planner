"""TripPlannerAgent：多 Agent 协作的行程规划流水线。

协作流程（与书中一致，五步）：
1. AttractionSearchAgent 调用 MCP 工具搜索景点
2. WeatherQueryAgent 查询目的地天气
3. HotelAgent 搜索候选酒店
4. 将用户需求与前三步结果拼接，构建 PlannerAgent 的输入
5. 解析 Planner 输出的 JSON，经 Pydantic 校验生成 TripPlan

三个工具型 Agent 各持有一个带工具白名单的 MCPTool 包装（最小权限视图），
共享同一个 MCP 服务器子进程。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import date
from typing import Callable

from hello_agents import HelloAgentsLLM, SimpleAgent
from pydantic import ValidationError

from ..config import Settings
from ..models.schemas import DayPlan, Feedback, TripPlan, TripRequest
from ..storage.cache import Cache
from .mcp_tool import MCPStdioClient, MCPTool
from .prompts import (
    ATTRACTION_AGENT_PROMPT,
    HOTEL_AGENT_PROMPT,
    PLANNER_AGENT_PROMPT,
    WEATHER_AGENT_PROMPT,
)
from .validator import search_candidates, validate_plan

logger = logging.getLogger(__name__)

# 进度回调：progress(stage, message, extra)。stage 取值见 plan() 内的各 notify 调用。
ProgressCallback = Callable[[str, str, dict | None], None]


class PlannerError(Exception):
    pass


# 各搜索型 Agent 的 MCP 工具白名单（最小权限：系统提示词里声明的工具才可调用）。
# 高德 MCP 全部是只读查询，白名单的意义在于收窄模型的行为面，并在将来
# 更换 MCP 服务器（可能带非只读工具）时兜底。
ATTRACTION_TOOLS = ["maps_text_search", "maps_search_detail", "maps_around_search"]
WEATHER_TOOLS = ["maps_weather"]
HOTEL_TOOLS = ["maps_text_search"]


class TripPlannerAgent:
    """编排 4 个专用 Agent，完成 端到端 的行程规划。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._cache = Cache(settings)  # opentime 等校验数据的 Redis 缓存
        # 共享的 LLM 与 MCP 连接（三个搜索型 Agent 复用同一 MCP 服务器子进程）
        self.llm = HelloAgentsLLM(
            model=settings.llm_model_id,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            temperature=settings.llm_temperature,
        )
        # 单个 MCP 服务器子进程；确定性代码（校验器/重规划候选/地理编码）经
        # self.mcp_tool.client 直连，显式指定工具名，不经过 LLM，不受白名单约束
        self.mcp_client = MCPStdioClient(
            command=settings.mcp_server_command,
            args=settings.mcp_args_list,
            env={"AMAP_MAPS_API_KEY": settings.amap_api_key},
        )
        self.mcp_tool = MCPTool(
            name="amap_mcp",
            description="高德地图工具集（服务端直连句柄，不注册给任何 Agent）",
            client=self.mcp_client,
            auto_expand=True,
        )

        # 每个 Agent 一个带白名单的 MCPTool 包装（共享同一子进程）
        self.attraction_agent = SimpleAgent(
            name="AttractionSearchAgent",
            llm=self.llm,
            system_prompt=ATTRACTION_AGENT_PROMPT,
        )
        self.attraction_agent.add_tool(MCPTool(
            name="amap_attraction",
            description="高德地图工具集（景点搜索白名单）",
            client=self.mcp_client,
            allowlist=ATTRACTION_TOOLS,
        ))

        self.weather_agent = SimpleAgent(
            name="WeatherQueryAgent",
            llm=self.llm,
            system_prompt=WEATHER_AGENT_PROMPT,
        )
        self.weather_agent.add_tool(MCPTool(
            name="amap_weather",
            description="高德地图工具集（天气查询白名单）",
            client=self.mcp_client,
            allowlist=WEATHER_TOOLS,
        ))

        self.hotel_agent = SimpleAgent(
            name="HotelAgent",
            llm=self.llm,
            system_prompt=HOTEL_AGENT_PROMPT,
        )
        self.hotel_agent.add_tool(MCPTool(
            name="amap_hotel",
            description="高德地图工具集（酒店搜索白名单）",
            client=self.mcp_client,
            allowlist=HOTEL_TOOLS,
        ))

        # 规划专家只做信息整合，不调用任何外部工具
        self.planner_agent = SimpleAgent(
            name="PlannerAgent",
            llm=self.llm,
            system_prompt=PLANNER_AGENT_PROMPT,
        )

    # ---------- 对外入口 ----------

    def plan(self, request: TripRequest, progress: ProgressCallback | None = None) -> TripPlan:
        def notify(stage: str, message: str, extra: dict | None = None) -> None:
            if progress is None:
                return
            try:
                progress(stage, message, extra)
            except Exception:  # noqa: BLE001
                logger.warning("进度回调异常（忽略）", exc_info=True)

        notify("started", "已受理，多智能体流水线启动")
        user_brief = self._format_request(request)

        logger.info("[TripPlanner] ① 搜索景点：%s", request.destination)
        attractions_text = self._run_step(
            self.attraction_agent,
            f"目的地：{request.destination}\n用户需求：{user_brief}\n请搜索并整理景点候选清单。",
        )
        n_candidates = sum(1 for line in attractions_text.splitlines() if line.count("|") >= 4)
        notify("attractions", f"景点搜索完成，共 {n_candidates} 个候选", {"count": n_candidates})

        logger.info("[TripPlanner] ② 查询天气")
        weather_text = self._run_step(
            self.weather_agent,
            f"目的地：{request.destination}，出发日期：{request.start_date or date.today().isoformat()}"
            f"\n请查询该城市天气预报并按格式整理。",
        )
        notify("weather", "目的地天气预报已获取")

        logger.info("[TripPlanner] ③ 推荐酒店")
        hotel_text = self._run_step(
            self.hotel_agent,
            f"目的地：{request.destination}\n用户需求：{user_brief}\n请搜索并整理酒店候选清单。",
        )
        notify("hotel", "酒店候选已就绪")

        logger.info("[TripPlanner] ④ 生成行程计划")
        notify("planning", "正在整合候选信息，规划每日行程…")
        planner_input = (
            f"## 用户需求\n{user_brief}\n\n"
            f"## 候选景点清单\n{attractions_text}\n\n"
            f"## 天气预报\n{weather_text}\n\n"
            f"## 候选酒店清单\n{hotel_text}\n\n"
            "请根据以上信息，严格按照系统提示词中的 JSON Schema 输出完整行程计划，只输出 JSON。"
        )
        planner_output = self._run_step(self.planner_agent, planner_input)

        logger.info("[TripPlanner] ⑤ 解析结果")
        plan = self._parse_plan(planner_output, request)
        notify("planned", "行程初稿已生成")

        notify("validating", "正在校验真实性/闭馆日/动线/密度…")
        plan = self._post_process(request, plan)
        notify("validated", "校验与自动修复完成")
        return plan

    def replan(self, request: TripRequest, plan: TripPlan, feedbacks: list[Feedback]) -> TripPlan:
        """带约束重规划。

        二期起默认局部重规划：只让 LLM 重写受反馈影响的天（确定性合并回原计划），
        未受影响的天逐字保留——更快、更稳、不会误伤满意的安排。
        酒店被反馈时影响所有天，自动退化为全量重规划。
        """
        target_labels = {"attraction": "景点", "hotel": "酒店", "meal": "餐厅"}
        affected_days = self._affected_days(plan, feedbacks)
        all_days = {d.day for d in plan.daily_plans}
        partial = bool(affected_days) and affected_days != all_days

        avoid_lines: list[str] = []
        sections: list[str] = []
        for fb in feedbacks:
            label = target_labels[fb.target]
            avoid_lines.append(f"- {label}「{fb.name}」（原因：{fb.reason}）")
            if fb.target == "attraction":
                attr = next((a for d in plan.daily_plans for a in d.attractions if a.name == fb.name), None)
                loc = attr.location.model_dump() if attr else None
                cands = search_candidates(self.mcp_tool.client, fb.name, loc, "景点", plan.destination)
            elif fb.target == "hotel":
                hotel = next((d.hotel for d in plan.daily_plans if d.hotel and d.hotel.name == fb.name), None)
                loc = hotel.location.model_dump() if hotel and hotel.location else None
                cands = search_candidates(self.mcp_tool.client, fb.name, loc, "酒店", plan.destination)
            else:
                cands = search_candidates(self.mcp_tool.client, fb.name, None, "美食", plan.destination)
            lines = [
                f"{n}. {c['name']} | {c['address']} | {c['longitude']},{c['latitude']}"
                for n, c in enumerate(cands, 1)
            ]
            sections.append(
                f"### {label}「{fb.name}」的替代候选（真实 POI，坐标可直接使用）：\n"
                + ("\n".join(lines) if lines else "（未搜到周边候选，请用同城其他真实知名 POI 替代，禁止编造坐标）")
            )

        logger.info(
            "[TripPlanner] 重规划：%d 条反馈约束，受影响天 %s，模式=%s",
            len(feedbacks), sorted(affected_days) if affected_days else "全部", "局部" if partial else "全量",
        )

        if partial:
            days_text = "、".join(str(d) for d in sorted(affected_days))
            replan_input = (
                "## 任务\n用户对行程部分天的安排提出反馈。只需重新规划受影响的天，其余天保持不变。\n"
                f"受影响的天：第 {days_text} 天\n"
                "用户反馈（必须替换）：\n" + "\n".join(avoid_lines)
                + "\n\n## 完整原行程 JSON（未受影响的天仅作上下文参考，禁止在输出中出现）\n"
                + plan.model_dump_json()
                + "\n\n## 替代候选（真实搜索结果）\n"
                + "\n\n".join(sections)
                + "\n\n## 要求\n"
                f"1. 只输出第 {days_text} 天的内容，JSON 结构为 {{\"daily_plans\": [ ... ]}}，数组元素数必须等于受影响天数\n"
                "2. 每个元素的 day/date/theme/weather/hotel 字段原样保留原行程中该天的值（hotel 被反馈时除外）\n"
                "3. 被反馈的安排必须替换为候选清单中的真实 POI（名称/坐标/地址原样使用）；未反馈的景点/餐饮原样保留\n"
                "4. 只输出 JSON，不要输出其他文字"
            )
            output = self._run_step(self.planner_agent, replan_input)
            data = self._extract_json(output)
            if not data or not data.get("daily_plans"):
                raise PlannerError("重规划输出解析失败")
            try:
                new_days = [DayPlan.model_validate(d) for d in data["daily_plans"]]
            except ValidationError as e:
                raise PlannerError(f"重规划结果校验失败: {str(e)[:300]}") from e
            new_days = [d for d in new_days if d.day in affected_days]
            if not new_days:
                raise PlannerError("重规划结果中不含受影响的天")
            plan = self._merge_days(plan, new_days)
        else:
            replan_input = (
                "## 任务\n在既有行程基础上做最小改动。用户对以下安排提出反馈，必须替换：\n"
                + "\n".join(avoid_lines)
                + "\n\n## 原行程 JSON\n"
                + plan.model_dump_json()
                + "\n\n## 替代候选（真实搜索结果）\n"
                + "\n\n".join(sections)
                + "\n\n## 要求\n"
                "1. 只替换被反馈的安排，日期结构、未被反馈的景点/餐饮/酒店/天气尽量原样保留\n"
                "2. 替代方案的名称/坐标/地址必须来自候选清单\n"
                "3. 保持与原行程相同的 JSON 结构，只输出 JSON"
            )
            output = self._run_step(self.planner_agent, replan_input)
            data = self._extract_json(output)
            if not data:
                raise PlannerError("重规划输出解析失败")
            data["destination"] = request.destination
            data["days"] = request.days
            new_plan = TripPlan.model_validate(data)
            if not new_plan.daily_plans:
                raise PlannerError("重规划结果为空")
            plan = new_plan
        return self._post_process(request, plan)

    @staticmethod
    def _affected_days(plan: TripPlan, feedbacks: list[Feedback]) -> set[int]:
        days: set[int] = set()
        for fb in feedbacks:
            for d in plan.daily_plans:
                if fb.target == "attraction" and any(a.name == fb.name for a in d.attractions):
                    days.add(d.day)
                elif fb.target == "hotel" and d.hotel and d.hotel.name == fb.name:
                    days.add(d.day)
                elif fb.target == "meal" and any(m.restaurant == fb.name for m in d.meals):
                    days.add(d.day)
        return days

    @staticmethod
    def _merge_days(original: TripPlan, new_days: list[DayPlan]) -> TripPlan:
        """把重写的天确定性合并回原计划，未受影响的天逐字保留。"""
        by_day = {d.day: d for d in new_days}
        merged = original.model_copy(deep=True)
        merged.daily_plans = [by_day.get(d.day, d) for d in merged.daily_plans]
        # 受影响天的花费变了，重算总计（分项保持估算口径）
        if merged.budget and any(d.daily_budget for d in merged.daily_plans):
            merged.budget.grand_total = round(sum(d.daily_budget for d in merged.daily_plans), 2)
        return merged

    # ---------- 校验与修复 ----------

    def _post_process(self, request: TripRequest, plan: TripPlan) -> TripPlan:
        """确定性校验 + 一次定向修复。

        strong 问题（真闭馆/超远/过密）触发修复；weak（如园区内部分院落周一关闭）
        仅作为提醒。修复后仍存在的问题写入 warnings 前端展示。
        """
        try:
            issues = validate_plan(plan, request, self.mcp_tool.client, self._cache)
        except Exception as e:  # noqa: BLE001
            logger.warning("[TripPlanner] 校验异常（跳过）", exc_info=True)
            return plan
        if not issues:
            return plan

        strong = [i for i in issues if i.get("strong", True)]
        warnings = [i["text"] for i in issues if not i.get("strong", True)]
        if strong:
            logger.info("[TripPlanner] 校验发现 %d 个强问题，尝试自动修复", len(strong))
            candidates = self._gather_repair_candidates(plan, strong)
            try:
                output = self._run_step(
                    self.planner_agent, self._build_repair_input(plan, strong, candidates)
                )
                data = self._extract_json(output)
                if data:
                    data["destination"] = request.destination
                    data["days"] = request.days
                    repaired = TripPlan.model_validate(data)
                    if repaired.daily_plans:
                        plan = repaired
                        logger.info("[TripPlanner] 自动修复完成")
            except Exception as e:  # noqa: BLE001
                logger.warning("[TripPlanner] 自动修复失败（保留原计划）", exc_info=True)
            try:
                remaining = validate_plan(plan, request, self.mcp_tool.client, self._cache)
            except Exception:  # noqa: BLE001
                remaining = []
            warnings += [i["text"] for i in remaining if i.get("strong", True)]
        plan.warnings = warnings
        return plan

    def _gather_repair_candidates(self, plan: TripPlan, issues: list[dict]) -> str:
        """为闭馆/距离问题搜索真实替代候选（密度问题由 LLM 调整天数分配即可）。"""
        sections: list[str] = []
        seen: set[str] = set()
        for i in issues:
            if i["kind"] == "density" or not i.get("location") or i["name"] in seen:
                continue
            seen.add(i["name"])
            cands = search_candidates(self.mcp_tool.client, i["name"], i["location"], "景点", plan.destination)
            lines = [
                f"{n}. {c['name']} | {c['address']} | {c['longitude']},{c['latitude']}"
                for n, c in enumerate(cands, 1)
            ]
            sections.append(
                f"### 替换「{i['name']}」的候选（真实 POI）：\n"
                + ("\n".join(lines) if lines else "（周边未搜到候选，可从城市其他真实知名景点中替代）")
            )
        return "\n\n".join(sections) if sections else "（无需替换 POI，仅需调整景点在天数间的分配）"

    @staticmethod
    def _build_repair_input(plan: TripPlan, issues: list[dict], candidates: str) -> str:
        return (
            "你之前生成的行程存在以下问题，请定向修复：\n"
            + "\n".join(f"- {i['text']}" for i in issues)
            + "\n\n## 当前行程 JSON\n"
            + plan.model_dump_json()
            + "\n\n## 修复要求\n"
            "1. 只修改与问题相关的部分，其余日期结构、景点、餐饮、酒店尽量保持不变\n"
            "2. 闭馆/距离问题：用候选清单中的真实景点替换（名称/坐标/地址原样使用），安排在同一天\n"
            "3. 密度问题：把多余景点移到相邻天（每天不超过 3 个）\n"
            "4. 严格按系统提示词中的 JSON Schema 输出完整 JSON，只输出 JSON\n\n"
            "## 替代候选（真实搜索结果）\n"
            + candidates
        )

    # ---------- 内部步骤 ----------

    @staticmethod
    def _run_step(agent: SimpleAgent, input_text: str) -> str:
        """执行单个 Agent，失败时给出可读错误。"""
        try:
            return agent.run(input_text)
        except Exception as e:  # noqa: BLE001
            raise PlannerError(f"Agent [{agent.name}] 执行失败: {e}") from e

    @staticmethod
    def _format_request(request: TripRequest) -> str:
        parts = [
            f"目的地：{request.destination}",
            f"出行天数：{request.days} 天",
            f"出发日期：{request.start_date or date.today().isoformat()}",
        ]
        if request.budget:
            parts.append(f"总预算：{request.budget:.0f} 元")
        if request.preferences:
            parts.append(f"旅行偏好：{'、'.join(request.preferences)}")
        if request.group_type:
            parts.append(f"出行类型：{request.group_type}")
        if request.notes:
            parts.append(f"特殊要求：{request.notes}")
        return "\n".join(parts)

    def _parse_plan(self, raw: str, request: TripRequest) -> TripPlan:
        """从 Planner 输出中提取 JSON 并校验；失败带错误重试一次。"""
        data, err = self._try_parse(raw, request)
        if data is not None:
            return data

        logger.warning("[TripPlanner] 首次解析失败（%s），要求模型修复后重试", err)
        retry_input = (
            f"你上一次的输出无法通过校验，错误信息：{err}\n\n"
            "上一次输出：\n" + raw[:6000] + "\n\n"
            "请修正以上问题，严格按 JSON Schema 重新输出完整 JSON，只输出 JSON 本身。"
        )
        retry_output = self._run_step(self.planner_agent, retry_input)
        data, err2 = self._try_parse(retry_output, request)
        if data is not None:
            return data
        raise PlannerError(f"行程计划解析失败：{err2}")

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """兼容 ```json 围栏、前后缀文本等情况。"""
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        candidates = []
        if fenced:
            candidates.append(fenced.group(1))
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])
        for candidate in candidates:
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
        return None

    def _try_parse(self, raw: str, request: TripRequest) -> tuple[TripPlan | None, str]:
        try:
            data = self._extract_json(raw)
            if data is None:
                return None, "输出中未找到合法 JSON"
            # 保证与请求一致的关键字段
            data["destination"] = request.destination
            data["days"] = request.days
            plan = TripPlan.model_validate(data)
            if not plan.daily_plans:
                return None, "daily_plans 为空"
            return plan, ""
        except (ValidationError, ValueError) as e:
            return None, str(e)[:800]


# ---------- 模块级单例（首个真实请求时惰性创建，避免无谓启动 MCP） ----------

_planner: TripPlannerAgent | None = None
# 锁本身必须在模块导入时创建：若惰性创建，两个线程可能各自拿到一把新锁
# 而同时通过判断，创建出两个 TripPlannerAgent（两个 MCP 子进程，其一泄漏）
_planner_lock = threading.Lock()


def get_planner(settings: Settings) -> TripPlannerAgent:
    global _planner
    with _planner_lock:
        if _planner is None:
            _planner = TripPlannerAgent(settings)
        return _planner
