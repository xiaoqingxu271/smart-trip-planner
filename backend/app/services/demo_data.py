"""演示模式数据：未配置密钥时的端到端体验（北京 3 日示例行程）。

坐标为真实高德（GCJ-02）坐标，整体结构与服务端真实输出一致，
便于在没有任何密钥时也能完整体验前端全部功能。
"""
from __future__ import annotations

from ..models.schemas import (
    Attraction,
    Budget,
    DayPlan,
    Hotel,
    Location,
    Meal,
    TripPlan,
    Weather,
)


def build_demo_plan(start_dates: list[str]) -> TripPlan:
    dates = (start_dates + [""] * 3)[:3]

    def loc(lng: float, lat: float) -> Location:
        return Location(longitude=lng, latitude=lat)

    day1 = DayPlan(
        day=1,
        date=dates[0],
        theme="皇城中轴经典",
        attractions=[
            Attraction(
                name="天安门广场",
                description="世界上最大的城市广场之一，观看升旗仪式、瞻仰人民英雄纪念碑的必到之地。",
                location=loc(116.397499, 39.908722),
                address="北京市东城区东长安街",
                duration="1.5小时",
                ticket_price=0,
                recommended_reason="北京地标，中轴线的起点",
            ),
            Attraction(
                name="故宫博物院",
                description="明清两代皇宫，世界上现存规模最大、保存最完整的木质结构古建筑群。",
                location=loc(116.397026, 39.918056),
                address="北京市东城区景山前街4号",
                duration="3.5小时",
                ticket_price=60,
                recommended_reason="来北京必看的世界文化遗产",
            ),
            Attraction(
                name="景山公园",
                description="登上万春亭可俯瞰故宫全景与北京中轴线，日落时分尤其壮观。",
                location=loc(116.397129, 39.928618),
                address="北京市西城区景山西街44号",
                duration="1.5小时",
                ticket_price=2,
                recommended_reason="俯瞰紫禁城全景的最佳机位",
            ),
        ],
        meals=[
            Meal(type="breakfast", restaurant="护国寺小吃（王府井店）", cuisine="京味小吃", specialty="豆汁儿、焦圈、豌豆黄", cost=25),
            Meal(type="lunch", restaurant="四季民福烤鸭店（故宫店）", cuisine="北京烤鸭", specialty="酥香嫩烤鸭、芥末鸭掌", cost=150),
            Meal(type="dinner", restaurant="局气（南锣鼓巷店）", cuisine="创意京菜", specialty="局气豆腐、兔爷土豆泥", cost=110),
        ],
        hotel=Hotel(
            name="全季酒店（北京天安门广场店）",
            location=loc(116.393, 39.902),
            address="北京市东城区珠市口东大街2号",
            price_per_night=480,
            rating=4.7,
            hotel_type="舒适型",
        ),
        weather=Weather(date=dates[0][-5:] or "10-01", day_temp=24, night_temp=13, condition="晴"),
        daily_budget=780,
        backup_attractions=[
            Attraction(
                name="中山公园",
                description="毗邻故宫的古典坛庙园林，古柏参天，游人较少，适合替代排队景点。",
                location=loc(116.4032, 39.9136),
                address="北京市东城区中华路4号",
                duration="1小时",
                ticket_price=3,
                recommended_reason="备选原因：紧邻故宫，约满/排队时的顺路替代",
            )
        ],
    )

    day2 = DayPlan(
        day=2,
        date=dates[1],
        theme="皇家园林湖光",
        attractions=[
            Attraction(
                name="颐和园",
                description="中国现存规模最大的皇家园林，昆明湖、万寿山与长廊构成经典画卷。",
                location=loc(116.275, 39.999),
                address="北京市海淀区新建宫门路19号",
                duration="4小时",
                ticket_price=30,
                recommended_reason="皇家园林之冠，湖光山色",
            ),
            Attraction(
                name="圆明园遗址公园",
                description="清代大型皇家园林遗址，西洋楼断壁残垣诉说历史，夏秋荷花尤美。",
                location=loc(116.310, 40.008),
                address="北京市海淀区清华西路28号",
                duration="2.5小时",
                ticket_price=10,
                recommended_reason="历史厚重感与园林之美并存",
            ),
            Attraction(
                name="南锣鼓巷",
                description="北京最古老的街区之一，胡同肌理保存完整，文创小店与小吃云集。",
                location=loc(116.403, 39.940),
                address="北京市东城区南锣鼓巷胡同",
                duration="2小时",
                ticket_price=0,
                recommended_reason="老北京胡同烟火气",
            ),
        ],
        meals=[
            Meal(type="breakfast", restaurant="庆丰包子铺（南锣鼓巷店）", cuisine="中式快餐", specialty="猪肉大葱包子、炒肝", cost=20),
            Meal(type="lunch", restaurant="那家小馆（颐和园店）", cuisine="满族官府菜", specialty="秘制酥皮虾、皇坛子", cost=130),
            Meal(type="dinner", restaurant="东来顺（王府井店）", cuisine="老北京铜锅涮肉", specialty="手切鲜羊肉、糖蒜", cost=140),
        ],
        hotel=Hotel(
            name="全季酒店（北京天安门广场店）",
            location=loc(116.393, 39.902),
            address="北京市东城区珠市口东大街2号",
            price_per_night=480,
            rating=4.7,
            hotel_type="舒适型",
        ),
        weather=Weather(date=dates[1][-5:] or "10-02", day_temp=22, night_temp=12, condition="多云"),
        daily_budget=660,
        backup_attractions=[
            Attraction(
                name="紫竹院公园",
                description="以竹造景的免费公园，幽静宜人，游览强度低。",
                location=loc(116.3257, 39.9445),
                address="北京市海淀区中关村南大街35号",
                duration="1.5小时",
                ticket_price=0,
                recommended_reason="备选原因：免费公园，行程紧张或约满时的轻松替代",
            )
        ],
    )

    day3 = DayPlan(
        day=3,
        date=dates[2],
        theme="祭坛文脉拾遗",
        attractions=[
            Attraction(
                name="天坛公园",
                description="明清皇帝祭天祈谷之地，祈年殿三重蓝色琉璃檐是北京最上镜的建筑之一。",
                location=loc(116.411, 39.882),
                address="北京市东城区天坛路东里7号",
                duration="2.5小时",
                ticket_price=34,
                recommended_reason="古代祭祀建筑的巅峰之作",
            ),
            Attraction(
                name="中国国家博物馆",
                description="系统展示中华五千年文明的国家最高历史艺术殿堂，镇馆之宝云集。",
                location=loc(116.401, 39.905),
                address="北京市东城区东长安街16号",
                duration="3小时",
                ticket_price=0,
                recommended_reason="免费预约，一次看尽中华文明",
            ),
            Attraction(
                name="前门大街",
                description="京城中轴线上的百年商业街，老字号云集，大栅栏胡同群近在咫尺。",
                location=loc(116.398, 39.899),
                address="北京市东城区前门大街",
                duration="2小时",
                ticket_price=0,
                recommended_reason="老字号与京味文化一条街",
            ),
        ],
        meals=[
            Meal(type="breakfast", restaurant="老磁器口豆汁店", cuisine="京味小吃", specialty="豆汁、焦圈、麻豆腐", cost=18),
            Meal(type="lunch", restaurant="便宜坊烤鸭店（鲜鱼口店）", cuisine="焖炉烤鸭", specialty="花香酥焖炉烤鸭", cost=135),
            Meal(type="dinner", restaurant="门框胡同百年卤煮", cuisine="京味卤煮", specialty="卤煮火烧、炸酱面", cost=60),
        ],
        hotel=Hotel(
            name="全季酒店（北京天安门广场店）",
            location=loc(116.393, 39.902),
            address="北京市东城区珠市口东大街2号",
            price_per_night=480,
            rating=4.7,
            hotel_type="舒适型",
        ),
        weather=Weather(date=dates[2][-5:] or "10-03", day_temp=21, night_temp=11, condition="多云转晴"),
        daily_budget=630,
        backup_attractions=[
            Attraction(
                name="正阳门城楼",
                description="中轴线上保存最完整的城楼，登楼可眺望前门大街全景。",
                location=loc(116.3989, 39.8994),
                address="北京市东城区前门大街天安门广场南端",
                duration="1小时",
                ticket_price=0,
                recommended_reason="备选原因：就在前门大街旁，约满/排队时的顺路替代",
            )
        ],
    )

    plan = TripPlan(
        destination="北京",
        days=3,
        summary=(
            "三天时间沿北京中轴线与皇家园林展开：第一天打卡天安门—故宫—景山的经典中轴线路线；"
            "第二天前往西北郊的颐和园与圆明园，晚上逛南锣鼓巷感受胡同夜色；"
            "第三天上午登天坛祈年殿，下午在国家博物馆纵览中华文明，最后到前门大街收尾，"
            "烤鸭、涮肉、小吃一网打尽。"
        ),
        daily_plans=[day1, day2, day3],
        budget=Budget(
            attraction_total=170,
            hotel_total=1440,
            meal_total=838,
            transport_total=200,
            grand_total=2648,
        ),
        tips=[
            "故宫、国家博物馆需提前在官方渠道实名预约，节假日票源紧张。",
            "天安门广场看升旗需凌晨排队，可提前查询当日升旗时间。",
            "市内出行推荐地铁+步行，高峰期打车拥堵明显。",
            "秋季北京昼夜温差大，建议携带薄外套。",
        ],
        demo=True,
    )
    return plan
