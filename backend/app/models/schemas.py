"""数据模型：自底向上设计

Location → Attraction / Meal / Hotel → Weather / Budget → DayPlan → TripPlan
前端 src/types/index.ts 与本文件一一对应。
"""
import re
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

# 自由文本统一上限：这些模型同时校验「用户输入」和「LLM 输出」，
# 上限一方面防止超长用户输入撑爆提示词/数据库（成本攻击面），
# 另一方面约束 LLM 不至于输出失控长文本。取值留足余量（demo 数据最长 122 字符）。
NameText = Annotated[str, StringConstraints(max_length=100)]
DescText = Annotated[str, StringConstraints(max_length=500)]
AddrText = Annotated[str, StringConstraints(max_length=200)]
ShortText = Annotated[str, StringConstraints(max_length=50)]
UrlText = Annotated[str, StringConstraints(max_length=500)]


class Location(BaseModel):
    """经纬度坐标（GCJ-02，高德坐标系）"""
    longitude: float = Field(..., ge=-180, le=180, description="经度")
    latitude: float = Field(..., ge=-90, le=90, description="纬度")


class Attraction(BaseModel):
    """景点/游览点"""
    name: NameText = Field(..., description="景点名称")
    description: DescText = Field("", description="景点介绍")
    location: Location = Field(..., description="经纬度坐标")
    address: AddrText = Field("", description="详细地址")
    duration: ShortText = Field("2小时", description="建议游览时长")
    ticket_price: float = Field(0, ge=0, description="门票价格（元）。无稳定真实源，生成时一律置 0，页面不展示精确票价")
    has_ticket: bool = Field(False, description="是否需购票（免费/公园为 False）")
    ticket_from: str = Field("", description="门票价格来源（如官网/平台；空表示未知）")
    nav_url: str = Field("", description="单点导航唤端链接（高德 URI API）")
    recommended_reason: Annotated[str, StringConstraints(max_length=200)] = Field("", description="推荐理由")
    image_url: Optional[UrlText] = Field(None, description="景点配图（Unsplash）")
    # 钟点由规划后的确定性调度器（schedule_service）填写，Planner 不编造钟点
    start_time: str = Field("", description="开始时间 HH:MM，如 14:30；历史行程可能为空")
    end_time: str = Field("", description="结束时间 HH:MM")
    open_time_text: str = Field("", description="高德开放时间原文（展示用）")


class Meal(BaseModel):
    """餐饮"""
    type: Literal["breakfast", "lunch", "dinner"] = Field(..., description="餐次")
    restaurant: NameText = Field(..., description="餐厅名称")
    cuisine: ShortText = Field("", description="菜系/类型")
    specialty: Annotated[str, StringConstraints(max_length=100)] = Field("", description="推荐菜品")
    cost: float = Field(0, ge=0, description="人均消费（元）")
    cost_note: str = Field("", description="餐费口径说明（如「人均（估算）无真实报价」）")
    image_url: Optional[UrlText] = Field(None, description="餐厅配图")
    # 批次 2.1 起：午餐/晚餐坐标由 Planner 从候选餐厅清单产出（导航必需）；早餐可空
    location: Optional[Location] = Field(None, description="经纬度坐标（早餐可为空）")


class Hotel(BaseModel):
    """酒店"""
    name: NameText = Field(..., description="酒店名称")
    location: Optional[Location] = Field(None, description="经纬度坐标")
    address: AddrText = Field("", description="详细地址")
    price_per_night: float = Field(..., ge=0, description="每晚价格（元）")
    price_from: Optional[float] = Field(None, description="真实起价（元），来自价格源的广告/最低价，无源为 None")
    price_source: str = Field("", description="价格来源标识，如 ctrip_advertised；空表示无真实源")
    rating: float = Field(4.5, ge=0, le=5, description="评分（0-5）")
    hotel_type: ShortText = Field("", description="酒店类型")
    image_url: Optional[UrlText] = Field(None, description="酒店配图")


class Weather(BaseModel):
    """天气预报"""
    date: ShortText = Field("", description="日期，如 10-01")
    day_temp: int = Field(0, description="白天温度（℃）")
    night_temp: int = Field(0, description="夜间温度（℃）")
    condition: ShortText = Field("", description="天气现象，如 晴 / 多云 / 小雨")

    @field_validator("day_temp", "night_temp", mode="before")
    @classmethod
    def parse_temperature(cls, v):
        """高德返回 '16°C' 之类的字符串，容错解析为整数。"""
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        if isinstance(v, str):
            m = re.search(r"-?\d+", v)
            if m:
                return int(m.group())
        return 0


class Budget(BaseModel):
    """预算明细（元）"""
    attraction_total: float = Field(0, ge=0, description="门票总费用")
    hotel_total: float = Field(0, ge=0, description="住宿总费用")
    meal_total: float = Field(0, ge=0, description="餐饮总费用")
    transport_total: float = Field(0, ge=0, description="市内交通总费用")
    grand_total: float = Field(0, ge=0, description="预算总计")

    @model_validator(mode="after")
    def calc_grand_total(self):
        """总计与分项求和保持一致（分项均为 0 时信任模型给出的总计）。"""
        items_sum = (
            self.attraction_total + self.hotel_total
            + self.meal_total + self.transport_total
        )
        if items_sum > 0:
            self.grand_total = round(items_sum, 2)
        return self


class DayPlan(BaseModel):
    """单日行程"""
    day: int = Field(..., ge=1, description="第几天")
    date: ShortText = Field("", description="日期，如 2026-10-01")
    theme: ShortText = Field("", description="当日主题")
    attractions: list[Attraction] = Field(default_factory=list, max_length=20, description="当日景点")
    backup_attractions: list[Attraction] = Field(default_factory=list, max_length=20, description="Plan B 备选景点（约满/排队时可启用）")
    meals: list[Meal] = Field(default_factory=list, max_length=10, description="当日三餐")
    hotel: Optional[Hotel] = Field(None, description="当日酒店")
    weather: Optional[Weather] = Field(None, description="当日天气")
    daily_budget: float = Field(0, ge=0, description="当日预估花费（元）")


# 总预算最低标准（元/天）：防止「预算过低 + 天数过多」这类无法执行的组合
MIN_BUDGET_PER_DAY = 200.0


class TripRequest(BaseModel):
    """行程规划请求"""
    destination: str = Field(..., min_length=1, max_length=50, description="目的地城市，如 北京")
    start_date: str = Field("", max_length=10, description="出发日期 YYYY-MM-DD，留空取今天")
    days: int = Field(3, ge=1, le=7, description="行程天数 1-7")
    budget: Optional[float] = Field(None, gt=0, description="总预算（元），可选")
    preferences: list[Annotated[str, StringConstraints(max_length=30)]] = Field(
        default_factory=list, max_length=10, description="旅行偏好标签"
    )
    group_type: str = Field("", max_length=20, description="出行类型：独自/情侣/家庭/朋友")
    notes: str = Field("", max_length=500, description="其他特殊要求")
    # 首末日程（批次 1.1）：留空视为「全天可玩」，向后兼容旧请求
    arrival_slot: Literal["morning", "afternoon", "evening", ""] = Field("", description="首日抵达时段")
    departure_slot: Literal["morning", "afternoon", "evening", ""] = Field("", description="末日离开时段")
    # 节奏（批次 3.2）：直接决定每天景点上限与相邻动线阈值
    pace: Literal["easy", "standard", "packed"] = Field("standard", description="行程节奏")
    # 必去 / 不去硬约束（批次 3.1）
    must_see: list[Annotated[str, StringConstraints(max_length=40)]] = Field(
        default_factory=list, max_length=5, description="必须包含的地点（最多 5 个）"
    )
    avoid: list[Annotated[str, StringConstraints(max_length=40)]] = Field(
        default_factory=list, max_length=5, description="排除的地点（最多 5 个）"
    )
    # 市内交通方式（批次 3.4）
    transit_mode: Literal["walk", "transit", "taxi", "drive"] = Field("taxi", description="市内交通方式")
    # 出发城市（批次 3.5，可选）：只用于生成大交通提示，不调票务 API
    origin: str = Field("", max_length=50, description="出发城市（可选）")

    @model_validator(mode="after")
    def check_budget_floor(self):
        """总预算须满足最低标准（每天 MIN_BUDGET_PER_DAY 元），否则拒绝规划。"""
        if self.budget is not None and self.days:
            floor = self.days * MIN_BUDGET_PER_DAY
            if self.budget < floor:
                raise ValueError(
                    f"总预算过低：{self.days} 天行程至少需要 {floor:.0f} 元"
                    f"（最低 {MIN_BUDGET_PER_DAY:.0f} 元/天），请调高预算或减少天数"
                )
        return self


class TripPlan(BaseModel):
    """完整行程计划"""
    destination: str = Field(..., max_length=50, description="目的地")
    days: int = Field(..., ge=1, le=31, description="天数")
    summary: DescText = Field("", description="行程总览介绍")
    daily_plans: list[DayPlan] = Field(default_factory=list, max_length=31, description="每日计划")
    budget: Optional[Budget] = Field(None, description="预算明细")
    tips: list[Annotated[str, StringConstraints(max_length=200)]] = Field(
        default_factory=list, max_length=20, description="实用贴士"
    )
    # 预算口径说明（批次 1.4）：页面与 PDF 共用，标明门票/住宿/餐饮为估算、交通为路线估价
    budget_note: str = Field(
        "门票以官网为准；住宿、餐饮为估算；交通为高德打车估价", description="预算说明（估算口径）"
    )
    demo: bool = Field(False, description="是否为演示模式数据")
    trip_id: Optional[int] = Field(None, description="入库后的行程 ID（真实模式自动保存）")
    warnings: list[Annotated[str, StringConstraints(max_length=300)]] = Field(
        default_factory=list, max_length=20, description="校验器发现的遗留问题（已尽力自动修复）"
    )
    parent_id: Optional[int] = Field(None, description="重规划版本链：指向被反馈的原行程")
    # 高德唤端（批次 D）：一键打开高德地图查看目的地；「专属地图多 POI 导入 APP」需官方 MCP，见 docs
    amap_map_url: str = Field("", description="一键打开高德地图（目的地）链接")


class Feedback(BaseModel):
    """用户对行程中某个安排的反馈（触发带约束重规划）"""
    target: Literal["attraction", "hotel", "meal"] = Field(..., description="反馈对象类型")
    name: str = Field(..., min_length=1, max_length=100, description="POI 名称")
    reason: str = Field(..., min_length=1, max_length=200, description="原因：预约满了/没房了/排队太久/不想去…")


class ReplanBody(BaseModel):
    trip_id: int = Field(..., description="要重规划的行程 ID")
    feedbacks: list[Feedback] = Field(..., min_length=1, max_length=10, description="反馈约束列表")


class TripSummary(BaseModel):
    """历史/作品集卡片摘要"""
    id: int
    destination: str
    days: int
    grand_total: Optional[float] = None
    starred: bool
    is_seed: bool
    created_at: str
    cover_url: Optional[str] = None
    themes: list[str] = Field(default_factory=list)
    summary: str = ""
