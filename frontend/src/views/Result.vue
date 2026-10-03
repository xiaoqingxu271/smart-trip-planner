<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import AMapLoader from '@amap/amap-jsapi-loader'
import { exportElementAsPdf, exportElementAsPng } from '@/utils/exporter'
import { getTripRoutes, getAppConfig, geocode, getTripDetail, imgProxy, replanTrip, starTrip, swapBackup, tripIcalUrl, updateTrip } from '@/services/api'
import type { AppConfig, Attraction, DayRoute, Feedback, TripPlan } from '@/types'

const route = useRoute()
const router = useRouter()

// 各类 POI 可选的反馈原因
const FEEDBACK_OPTIONS: Record<'attraction' | 'hotel' | 'meal', string[]> = {
  attraction: ['预约满了', '不想去', '距离太远'],
  hotel: ['没房了', '不想住这家'],
  meal: ['排队太久', '不想吃这家'],
}

const tripPlan = ref<TripPlan | null>(null)
const appConfig = ref<AppConfig | null>(null)
const editing = ref(false)
const working = ref<TripPlan | null>(null) // 编辑态的深拷贝，取消时可整体丢弃
const exporting = ref(false)

// 服务端持久化状态（收藏）
const tripId = ref<number | null>(null)
const starred = ref(false)

// 用户反馈与重规划
const feedbacks = ref<Feedback[]>([])
const replanning = ref(false)

function addFeedback(target: 'attraction' | 'hotel' | 'meal', name: string, reason: string) {
  if (feedbacks.value.some((f) => f.name === name)) {
    message.info(`「${name}」已在重规划清单中`)
    return
  }
  feedbacks.value.push({ target, name, reason })
  message.success(`已标记「${name}」：${reason}，标记完成后点击下方"重新规划"`)
}

async function doReplan() {
  if (!tripId.value || !feedbacks.value.length) return
  replanning.value = true
  try {
    const plan = await replanTrip({ trip_id: tripId.value, feedbacks: feedbacks.value })
    tripPlan.value = plan
    tripId.value = plan.trip_id ?? null
    starred.value = false
    sessionStorage.setItem('tripPlan', JSON.stringify(plan))
    feedbacks.value = []
    message.success('已根据反馈生成新版本行程')
    window.scrollTo({ top: 0, behavior: 'smooth' })
    await nextTick()
    renderMarkers()
  } catch (e) {
    message.error(`重新规划失败：${(e as Error).message}`, 6)
  } finally {
    replanning.value = false
  }
}

/** 一键启用 Plan B：本地先对调（演示模式无服务端 ID 也生效），有 ID 则同步入库 */
async function enableBackup(dayIdx: number, attrName: string, backupName: string) {
  const plan = working.value ?? tripPlan.value
  if (!plan) return
  const day = plan.daily_plans[dayIdx]
  const ai = day.attractions.findIndex((a) => a.name === attrName)
  const bi = (day.backup_attractions ?? []).findIndex((b) => b.name === backupName)
  if (ai < 0 || bi < 0) return
  ;[day.attractions[ai], day.backup_attractions![bi]] = [day.backup_attractions![bi], day.attractions[ai]]
  if (tripId.value) {
    try {
      const updated = await swapBackup(tripId.value, { day: day.day, original: attrName, backup: backupName })
      tripPlan.value = updated
      sessionStorage.setItem('tripPlan', JSON.stringify(updated))
    } catch (e) {
      message.warning(`本地已换，云端同步失败：${(e as Error).message}`)
    }
  } else {
    sessionStorage.setItem('tripPlan', JSON.stringify(tripPlan.value))
  }
  message.success(`已启用备选：${backupName}`)
  await nextTick()
  renderMarkers()
}

// ---------- 地图 ----------
let AMapNS: any = null
let mapInstance: any = null
const mapLoading = ref(false)
const mapError = ref('')

// ---------- 每日真实驾车路线（按需加载，失败静默降级为不画线） ----------
const dayRoutes = ref<DayRoute[]>([])
const DAY_COLORS = ['#c0392b', '#2980b9', '#27ae60', '#8e44ad', '#e67e22', '#16a085', '#d35400']

function routeFor(day: number): DayRoute | undefined {
  return dayRoutes.value.find((r) => r.day === day)
}

function fmtKm(meters: number): string {
  return meters >= 1000 ? `${(meters / 1000).toFixed(1)} 公里` : `${meters} 米`
}

function fmtDuration(seconds: number): string {
  const mins = Math.round(seconds / 60)
  return mins >= 60 ? `${Math.floor(mins / 60)}小时${mins % 60 ? `${mins % 60}分` : ''}` : `${mins}分钟`
}

async function loadRoutes() {
  if (!tripId.value || !appConfig.value?.amap_js_key) return
  try {
    const data = await getTripRoutes(tripId.value)
    dayRoutes.value = data.routes
    drawRoutes()
  } catch {
    /* 路线是增强信息，失败不影响主流程 */
  }
}

const allAttractions = computed<Attraction[]>(() => {
  const plan = working.value ?? tripPlan.value
  if (!plan) return []
  return plan.daily_plans.flatMap((d) => d.attractions)
})

async function initMap() {
  const plan = working.value ?? tripPlan.value
  if (!plan) return
  if (!appConfig.value?.amap_js_key) {
    mapError.value = '未配置高德地图 JS API Key（backend/.env 中 AMAP_JS_KEY），无法展示地图。'
    return
  }
  mapLoading.value = true
  mapError.value = ''
  try {
    // JS API 2.0 起需要配合安全密钥
    ;(window as any)._AMapSecurityConfig = { securityJsCode: appConfig.value.amap_js_secret }
    if (!AMapNS) {
      AMapNS = await AMapLoader.load({ key: appConfig.value.amap_js_key, version: '2.0', plugins: [] })
    }
    if (mapInstance) {
      mapInstance.destroy()
      mapInstance = null
    }
    mapInstance = new AMapNS.Map('map-container', { zoom: 11, viewMode: '2D' })
    renderMarkers()
    loadRoutes()
  } catch (e) {
    mapError.value = `地图加载失败：${(e as Error).message}`
  } finally {
    mapLoading.value = false
  }
}

function renderMarkers() {
  if (!mapInstance || !AMapNS) return
  mapInstance.clearMap()
  const markers = allAttractions.value.map((attr, i) => {
    const marker = new AMapNS.Marker({
      position: [attr.location.longitude, attr.location.latitude],
      content: `<div style="width:26px;height:26px;border-radius:50%;background:#1677ff;color:#fff;
        display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;
        border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.3);">${i + 1}</div>`,
      offset: new AMapNS.Pixel(-13, -13),
      title: attr.name,
    })
    marker.setLabel({
      content: `<span style="font-size:12px;color:#262626;background:#fff;padding:1px 6px;
        border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.15);white-space:nowrap;">${attr.name}</span>`,
      direction: 'top',
      offset: new AMapNS.Pixel(0, -8),
    })
    return marker
  })
  if (markers.length) {
    mapInstance.add(markers)
    mapInstance.setFitView(markers, false, [60, 60, 60, 60])
  }
  drawRoutes()
}

function drawRoutes() {
  if (!mapInstance || !AMapNS || !dayRoutes.value.length) return
  const polylines = dayRoutes.value.flatMap((r, i) =>
    r.legs.map((leg) => {
      return new AMapNS.Polyline({
        path: leg.polyline.map(([lng, lat]) => [lng, lat]),
        strokeColor: DAY_COLORS[(r.day - 1) % DAY_COLORS.length],
        strokeWeight: 5,
        strokeOpacity: 0.85,
        showDir: true, // 行进方向箭头
        bubble: true,
        isOutline: true,
        outlineColor: '#ffffff',
      })
    }),
  )
  mapInstance.add(polylines)
}

// ---------- 侧边导航 ----------
const sections = [
  { id: 'sec-overview', name: '行程概览' },
  { id: 'sec-days', name: '每日安排' },
  { id: 'sec-budget', name: '费用预算' },
  { id: 'sec-map', name: '地图总览' },
  { id: 'sec-tips', name: '实用贴士' },
]
const activeSection = ref<string[]>(['sec-overview'])

function scrollTo(id: string) {
  activeSection.value = [id]
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function onScroll() {
  for (let i = sections.length - 1; i >= 0; i--) {
    const el = document.getElementById(sections[i].id)
    if (el && el.getBoundingClientRect().top <= 120) {
      activeSection.value = [sections[i].id]
      return
    }
  }
}

// ---------- 行程编辑 ----------
function enterEdit() {
  // 深拷贝一份作为编辑草稿，取消编辑时直接丢弃即可还原
  working.value = JSON.parse(JSON.stringify(tripPlan.value))
  editing.value = true
}

function cancelEdit() {
  working.value = null
  editing.value = false
}

async function saveEdit() {
  if (!working.value) return
  tripPlan.value = working.value
  sessionStorage.setItem('tripPlan', JSON.stringify(tripPlan.value))
  editing.value = false
  // 有服务端 ID 时同步回数据库；失败只影响云端副本，本地仍生效
  if (tripId.value) {
    try {
      await updateTrip(tripId.value, tripPlan.value)
      message.success('行程已保存并同步到服务器')
    } catch (e) {
      message.warning(`本地已保存，但同步服务器失败：${(e as Error).message}`)
    }
  } else {
    message.success('行程已更新')
  }
  await nextTick()
  renderMarkers()
}

function removeAttraction(dayIdx: number, attrIdx: number) {
  working.value?.daily_plans[dayIdx].attractions.splice(attrIdx, 1)
}

function moveAttraction(dayIdx: number, attrIdx: number, dir: -1 | 1) {
  const list = working.value?.daily_plans[dayIdx].attractions
  if (!list) return
  const target = attrIdx + dir
  if (target < 0 || target >= list.length) return
  ;[list[attrIdx], list[target]] = [list[target], list[attrIdx]]
}

const addModalOpen = ref(false)
const addSubmitting = ref(false)
const addForm = reactive({ name: '', address: '', duration: '2小时', ticket_price: 0, description: '' })
let addTargetDay = 0

function openAddModal(dayIdx: number) {
  addTargetDay = dayIdx
  addForm.name = ''
  addForm.address = ''
  addForm.duration = '2小时'
  addForm.ticket_price = 0
  addForm.description = ''
  addModalOpen.value = true
}

async function confirmAdd() {
  if (!addForm.name.trim()) {
    message.warning('请填写景点名称')
    return
  }
  if (!addForm.address.trim()) {
    message.warning('请填写景点地址（用于在地图上标注）')
    return
  }
  addSubmitting.value = true
  try {
    const geo = await geocode(addForm.address.trim(), tripPlan.value?.destination ?? '')
    working.value?.daily_plans[addTargetDay].attractions.push({
      name: addForm.name.trim(),
      description: addForm.description.trim(),
      location: { longitude: geo.longitude, latitude: geo.latitude },
      address: geo.formatted_address || addForm.address.trim(),
      duration: addForm.duration || '2小时',
      ticket_price: Number(addForm.ticket_price) || 0,
      recommended_reason: '手动添加',
      image_url: null,
    })
    addModalOpen.value = false
    message.success(`已添加：${addForm.name.trim()}`)
  } catch (e) {
    message.error(`地理编码失败：${(e as Error).message}`)
  } finally {
    addSubmitting.value = false
  }
}

// ---------- 导出（逻辑在 utils/exporter.ts，这里只处理 UI 状态） ----------
const exportTarget = ref<HTMLElement | null>(null)

async function exportPDF() {
  exporting.value = true
  await nextTick()
  try {
    const el = exportTarget.value
    if (!el) throw new Error('导出内容未就绪')
    await exportElementAsPdf(el, `${tripPlan.value?.destination ?? '旅行'}行程计划.pdf`)
    message.success('PDF 已导出')
  } catch (e) {
    message.error(`导出失败：${(e as Error).message}`)
  } finally {
    exporting.value = false
  }
}

async function exportImage() {
  exporting.value = true
  await nextTick()
  try {
    const el = exportTarget.value
    if (!el) throw new Error('导出内容未就绪')
    await exportElementAsPng(el, `${tripPlan.value?.destination ?? '旅行'}行程计划.png`)
    message.success('长图已导出')
  } catch (e) {
    message.error(`导出失败：${(e as Error).message}`)
  } finally {
    exporting.value = false
  }
}

// ---------- 展示辅助 ----------
function exportIcal() {
  if (!tripId.value) {
    message.warning('演示行程尚未入库，暂不支持导出日历')
    return
  }
  // Content-Disposition 触发下载；访问码已由 tripIcalUrl 拼进查询参数
  window.open(tripIcalUrl(tripId.value))
}

const MEAL_META: Record<string, { icon: string; label: string }> = {
  breakfast: { icon: '🥟', label: '早餐' },
  lunch: { icon: '🍜', label: '午餐' },
  dinner: { icon: '🍲', label: '晚餐' },
}

function weatherEmoji(condition: string): string {
  if (!condition) return '🌤'
  if (condition.includes('雷')) return '⛈'
  if (condition.includes('雨')) return '🌧'
  if (condition.includes('雪')) return '❄️'
  if (condition.includes('雾') || condition.includes('霾')) return '🌫'
  if (condition.includes('阴')) return '☁️'
  if (condition.includes('多云')) return '⛅'
  return '☀️'
}

function money(n?: number | null): string {
  return `¥${(n ?? 0).toLocaleString('zh-CN')}`
}

/**
 * POI 图片统一走后端同源代理：绕开高德 CDN 的防盗链/混合内容/偶发加载失败，
 * 同时让 html2canvas 导出不再有跨域污染问题。
 */
function imgSrc(url?: string | null): string | null {
  return imgProxy(url)
}

async function toggleStar() {
  if (!tripId.value) return
  const next = !starred.value
  try {
    await starTrip(tripId.value, next)
    starred.value = next
    message.success(next ? '已收藏到首页「我的收藏」⭐' : '已取消收藏')
  } catch (e) {
    message.error(`操作失败：${(e as Error).message}`)
  }
}

// ---------- 生命周期 ----------
onMounted(async () => {
  const routeId = route.query.id ? Number(route.query.id) : null
  if (routeId) {
    // 从作品集/历史进入：按 ID 从服务端加载
    try {
      const detail = await getTripDetail(routeId)
      tripPlan.value = detail.plan
      tripId.value = detail.id
      starred.value = detail.starred
      sessionStorage.setItem('tripPlan', JSON.stringify(detail.plan))
    } catch (e) {
      message.error(`行程加载失败：${(e as Error).message}`)
      router.replace({ name: 'home' })
      return
    }
  } else {
    const cached = sessionStorage.getItem('tripPlan')
    if (!cached) {
      message.warning('请先生成行程计划')
      router.replace({ name: 'home' })
      return
    }
    tripPlan.value = JSON.parse(cached)
    tripId.value = tripPlan.value?.trip_id ?? null
  }

  window.addEventListener('scroll', onScroll, { passive: true })
  try {
    appConfig.value = await getAppConfig()
  } catch {
    mapError.value = '无法获取应用配置，地图不可用。'
    return
  }
  await nextTick()
  initMap()
})

onBeforeUnmount(() => {
  window.removeEventListener('scroll', onScroll)
  if (mapInstance) {
    mapInstance.destroy()
    mapInstance = null
  }
})
</script>

<template>
  <div class="result-page" :class="{ exporting }">
    <!-- 顶部操作栏 -->
    <header class="topbar no-export">
      <a-button @click="router.push({ name: 'home' })">← 返回首页</a-button>
      <div class="topbar-title">✈️ {{ (working ?? tripPlan)?.destination }} · {{ (working ?? tripPlan)?.days }}天行程</div>
      <div class="topbar-actions">
        <template v-if="editing">
          <a-button type="primary" @click="saveEdit">保存修改</a-button>
          <a-button @click="cancelEdit">取消编辑</a-button>
        </template>
        <template v-else>
          <a-button v-if="tripId" :type="starred ? 'primary' : 'default'" ghost @click="toggleStar">
            {{ starred ? '⭐ 已收藏' : '☆ 收藏' }}
          </a-button>
          <a-button type="primary" ghost @click="enterEdit">✏️ 编辑行程</a-button>
          <a-button :loading="exporting" @click="exportPDF">📄 导出 PDF</a-button>
          <a-button :loading="exporting" @click="exportImage">🖼 导出长图</a-button>
          <a-button @click="exportIcal">📅 存入日历</a-button>
        </template>
      </div>
    </header>

    <div v-if="tripPlan" class="page-body">
      <!-- 侧边导航 -->
      <nav class="side-nav no-export">
        <a-menu v-model:selected-keys="activeSection" mode="inline" style="border: none; background: transparent">
          <a-menu-item v-for="s in sections" :key="s.id" @click="scrollTo(s.id)">
            {{ s.name }}
          </a-menu-item>
        </a-menu>
      </nav>

      <!-- 主内容：导出的范围 -->
      <main id="trip-content" ref="exportTarget" class="content">
        <a-alert
          v-if="tripPlan.demo"
          type="warning"
          show-icon
          class="no-export"
          message="当前为演示模式数据"
          description="后端未配置 LLM / 高德 / Unsplash 密钥，展示的是内置示例行程。在 backend/.env 配置密钥后即可体验真实的多智能体规划。"
          style="margin-bottom: 16px"
        />

        <a-alert
          v-if="tripPlan.parent_id"
          type="info"
          show-icon
          class="no-export"
          style="margin-bottom: 16px"
        >
          <template #message>
            🔁 此行程是根据你的反馈对原行程重新规划生成的新版本。
            <a @click="router.push({ path: '/result', query: { id: String(tripPlan.parent_id) } })">查看上一版</a>
          </template>
        </a-alert>

        <a-alert
          v-if="tripPlan.warnings?.length"
          type="warning"
          show-icon
          class="no-export"
          style="margin-bottom: 16px"
        >
          <template #message>⚠️ 以下问题未能完全自动修复，请留意（可标记后重新规划）：</template>
          <template #description>
            <ul style="margin: 0; padding-left: 18px">
              <li v-for="(w, i) in tripPlan.warnings" :key="i">{{ w }}</li>
            </ul>
          </template>
        </a-alert>

        <!-- 行程概览 -->
        <section id="sec-overview" class="card">
          <h2 class="card-title">📍 行程概览</h2>
          <div class="overview-meta">
            <a-tag color="blue">目的地：{{ tripPlan.destination }}</a-tag>
            <a-tag color="blue">共 {{ tripPlan.days }} 天</a-tag>
            <a-tag v-for="(d, i) in tripPlan.daily_plans" :key="i" color="cyan">D{{ d.day }} {{ d.theme }}</a-tag>
          </div>
          <p class="summary">{{ tripPlan.summary }}</p>
        </section>

        <!-- 每日安排 -->
        <section id="sec-days">
          <h2 class="card-title">🗓 每日安排</h2>
          <div v-for="(day, dayIdx) in working?.daily_plans ?? tripPlan.daily_plans" :key="day.day" class="card day-card">
            <div class="day-header">
              <div class="day-title">
                <span class="day-badge">D{{ day.day }}</span>
                <span class="day-theme">{{ day.theme }}</span>
                <span class="day-date">{{ day.date }}</span>
              </div>
              <div v-if="day.weather" class="day-weather">
                {{ weatherEmoji(day.weather.condition) }} {{ day.weather.condition }}
                {{ day.weather.day_temp }}℃ / {{ day.weather.night_temp }}℃
              </div>
            </div>
            <div v-if="routeFor(day.day)" class="day-route">
              🚗 全程 {{ fmtKm(routeFor(day.day)!.distance_m) }} · 车程约 {{ fmtDuration(routeFor(day.day)!.duration_s) }} · 打车约 ¥{{ routeFor(day.day)!.taxi_cost }}
            </div>

            <!-- 景点 -->
            <div class="attraction-list">
              <div v-for="(attr, attrIdx) in day.attractions" :key="attrIdx" class="attraction">
                <div class="attr-index">{{ attrIdx + 1 }}</div>
                <div class="attr-image">
                  <img
                    v-if="attr.image_url"
                    :src="imgSrc(attr.image_url)!"
                    :alt="attr.name"
                    loading="lazy"
                    @error="attr.image_url = null"
                  />
                  <div v-else class="attr-image-placeholder">🏛️</div>
                </div>
                <div class="attr-info">
                  <div class="attr-name-row">
                    <span class="attr-name">{{ attr.name }}</span>
                    <a-tag v-if="attr.ticket_price > 0" color="orange">门票 {{ money(attr.ticket_price) }}</a-tag>
                    <a-tag v-else color="green">免费</a-tag>
                    <a-tag color="default">⏱ {{ attr.duration }}</a-tag>
                    <a-dropdown v-if="tripId && !editing" class="no-export">
                      <a-button size="small" type="text" class="fb-btn">有问题？</a-button>
                      <template #overlay>
                        <a-menu @click="({ key }) => addFeedback('attraction', attr.name, key as string)">
                          <a-menu-item v-for="r in FEEDBACK_OPTIONS.attraction" :key="r">{{ r }}</a-menu-item>
                        </a-menu>
                      </template>
                    </a-dropdown>
                    <a-dropdown v-if="tripId && !editing && (day.backup_attractions?.length ?? 0) > 0" class="no-export">
                      <a-button size="small" type="text" class="fb-btn">☘ 换备选</a-button>
                      <template #overlay>
                        <a-menu @click="({ key }) => enableBackup(dayIdx, attr.name, key as string)">
                          <a-menu-item v-for="b in day.backup_attractions" :key="b.name">
                            {{ b.name }}（{{ b.recommended_reason || '备选' }}）
                          </a-menu-item>
                        </a-menu>
                      </template>
                    </a-dropdown>
                  </div>
                  <p class="attr-desc">{{ attr.description }}</p>
                  <p v-if="attr.recommended_reason" class="attr-reason">💡 {{ attr.recommended_reason }}</p>
                  <p v-if="attr.address" class="attr-address">📍 {{ attr.address }}</p>
                </div>
                <div v-if="editing" class="attr-ops no-export">
                  <a-button size="small" :disabled="attrIdx === 0" @click="moveAttraction(dayIdx, attrIdx, -1)">↑</a-button>
                  <a-button
                    size="small"
                    :disabled="attrIdx === day.attractions.length - 1"
                    @click="moveAttraction(dayIdx, attrIdx, 1)"
                  >↓</a-button>
                  <!-- 编辑态下删除可随时通过"取消编辑"整体还原，无需二次确认 -->
                  <a-button size="small" danger @click="removeAttraction(dayIdx, attrIdx)">删除</a-button>
                </div>
              </div>
            </div>

            <a-button v-if="editing" type="dashed" block class="no-export" @click="openAddModal(dayIdx)">
              ＋ 为第 {{ day.day }} 天添加景点
            </a-button>

            <!-- Plan B 备选 -->
            <div v-if="(day.backup_attractions?.length ?? 0) > 0 && !editing" class="backup-row no-export">
              <span class="backup-label">☘ Plan B</span>
              <span class="backup-hint">遇约满/排队可换：</span>
              <a-tag
                v-for="b in day.backup_attractions"
                :key="b.name"
                color="purple"
                :title="b.description"
              >{{ b.name }}</a-tag>
            </div>

            <!-- 三餐 -->
            <div class="meal-row">
              <div v-for="meal in day.meals" :key="meal.type" class="meal">
                <img
                  v-if="meal.image_url"
                  :src="imgSrc(meal.image_url)!"
                  :alt="meal.restaurant"
                  loading="lazy"
                  @error="meal.image_url = null"
                  class="meal-img"
                />
                <span v-else class="meal-icon">{{ MEAL_META[meal.type]?.icon }}</span>
                <div>
                  <div class="meal-name">
                    {{ meal.restaurant }}
                    <a-dropdown v-if="tripId && !editing" class="no-export">
                      <a-button size="small" type="text" class="fb-btn">有问题？</a-button>
                      <template #overlay>
                        <a-menu @click="({ key }) => addFeedback('meal', meal.restaurant, key as string)">
                          <a-menu-item v-for="r in FEEDBACK_OPTIONS.meal" :key="r">{{ r }}</a-menu-item>
                        </a-menu>
                      </template>
                    </a-dropdown>
                  </div>
                  <div class="meal-meta">
                    {{ MEAL_META[meal.type]?.label }}
                    <template v-if="meal.cuisine"> · {{ meal.cuisine }}</template>
                    <template v-if="meal.cost"> · 人均 {{ money(meal.cost) }}</template>
                    <template v-if="meal.specialty"> · 推荐 {{ meal.specialty }}</template>
                  </div>
                </div>
              </div>
            </div>

            <!-- 酒店 -->
            <div v-if="day.hotel" class="hotel">
              <img
                v-if="day.hotel.image_url"
                :src="imgSrc(day.hotel.image_url)!"
                :alt="day.hotel.name"
                loading="lazy"
                @error="day.hotel.image_url = null"
                class="hotel-img"
              />
              <span v-else class="hotel-icon">🏨</span>
              <div>
                <div class="hotel-name">
                  {{ day.hotel.name }}
                  <a-rate :value="day.hotel.rating / 1" disabled allow-half style="font-size: 12px; margin-left: 8px" />
                  <a-dropdown v-if="tripId && !editing" class="no-export">
                    <a-button size="small" type="text" class="fb-btn">有问题？</a-button>
                    <template #overlay>
                      <a-menu @click="({ key }) => addFeedback('hotel', day.hotel!.name, key as string)">
                        <a-menu-item v-for="r in FEEDBACK_OPTIONS.hotel" :key="r">{{ r }}</a-menu-item>
                      </a-menu>
                    </template>
                  </a-dropdown>
                </div>
                <div class="hotel-meta">
                  {{ day.hotel.hotel_type }} · {{ money(day.hotel.price_per_night) }}/晚
                  <template v-if="day.hotel.address"> · {{ day.hotel.address }}</template>
                </div>
              </div>
            </div>

            <div class="day-budget">当日预估：{{ money(day.daily_budget) }}</div>
          </div>
        </section>

        <!-- 费用预算 -->
        <section id="sec-budget" class="card">
          <h2 class="card-title">💰 费用预算</h2>
          <a-row v-if="tripPlan.budget" :gutter="16" align="middle">
            <a-col :xs="12" :md="5"><a-statistic title="门票" :value="tripPlan.budget.attraction_total" prefix="¥" /></a-col>
            <a-col :xs="12" :md="5"><a-statistic title="住宿" :value="tripPlan.budget.hotel_total" prefix="¥" /></a-col>
            <a-col :xs="12" :md="5"><a-statistic title="餐饮" :value="tripPlan.budget.meal_total" prefix="¥" /></a-col>
            <a-col :xs="12" :md="5"><a-statistic title="市内交通" :value="tripPlan.budget.transport_total" prefix="¥" /></a-col>
            <a-col :xs="24" :md="4">
              <a-statistic
                title="预算总计"
                :value="tripPlan.budget.grand_total"
                prefix="¥"
                :value-style="{ color: '#cf1322', fontSize: '28px', fontWeight: 700 }"
              />
            </a-col>
          </a-row>
          <p v-else class="muted">暂无预算信息</p>
        </section>

        <!-- 地图总览：导出时隐藏（地图 Canvas 与 html2canvas 存在兼容性问题） -->
        <section id="sec-map" class="card">
          <h2 class="card-title">🗺 地图总览</h2>
          <div v-show="!exporting" class="map-wrap">
            <div v-if="mapError" class="map-error">
              <a-alert type="info" show-icon :message="mapError" />
            </div>
            <div v-show="!mapError" id="map-container" class="map-container">
              <div v-if="mapLoading" class="map-loading"><a-spin tip="地图加载中…" /></div>
            </div>
          </div>
          <div v-if="exporting" class="map-export-note">🗺 地图请在应用内查看（导出模式暂不包含地图）</div>
        </section>

        <!-- 实用贴士 -->
        <section id="sec-tips" class="card">
          <h2 class="card-title">💡 实用贴士</h2>
          <ul class="tips">
            <li v-for="(tip, i) in tripPlan.tips" :key="i">{{ tip }}</li>
          </ul>
          <p v-if="!tripPlan.tips.length" class="muted">暂无贴士</p>
        </section>
      </main>
    </div>

    <!-- 反馈托盘：标记问题后一键重规划 -->
    <transition name="fade-slide">
      <div v-if="feedbacks.length && !editing" class="feedback-bar no-export">
        <span class="fb-label">已标记问题：</span>
        <a-tag
          v-for="(f, i) in feedbacks"
          :key="i"
          closable
          color="orange"
          @close="feedbacks.splice(i, 1)"
        >
          {{ f.name }} · {{ f.reason }}
        </a-tag>
        <a-button type="primary" size="small" :loading="replanning" @click="doReplan">
          🔄 按反馈重新规划
        </a-button>
        <a-button size="small" @click="feedbacks = []">清除</a-button>
      </div>
    </transition>

    <!-- 重规划弹窗 -->
    <a-modal :open="replanning" :closable="false" :keyboard="false" :mask-closable="false" :footer="null" centered>
      <div class="loading-body">
        <a-spin size="large" />
        <p class="loading-title">正在根据你的反馈重新规划…</p>
        <p class="loading-hint">智能体会搜索真实替代候选，只重排受影响的天，通常需要 30~90 秒</p>
      </div>
    </a-modal>

    <!-- 添加景点弹窗 -->
    <a-modal
      v-model:open="addModalOpen"
      title="添加景点"
      :confirm-loading="addSubmitting"
      ok-text="地理编码并添加"
      @ok="confirmAdd"
    >
      <a-form layout="vertical">
        <a-form-item label="景点名称" required>
          <a-input v-model:value="addForm.name" placeholder="如：国家大剧院" />
        </a-form-item>
        <a-form-item label="景点地址" required>
          <a-input v-model:value="addForm.address" placeholder="如：北京市西城区西长安街2号" />
        </a-form-item>
        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="建议游览时长">
              <a-input v-model:value="addForm.duration" placeholder="如：2小时" />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="门票价格(元)">
              <a-input-number v-model:value="addForm.ticket_price" :min="0" style="width: 100%" />
            </a-form-item>
          </a-col>
        </a-row>
        <a-form-item label="简介">
          <a-textarea v-model:value="addForm.description" :rows="2" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.result-page {
  min-height: 100vh;
  background: #f5f7fa;
  padding-bottom: 48px;
}

/* ---------- 顶栏 ---------- */
.topbar {
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 24px;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid #e8ecf2;
}

.topbar-title {
  flex: 1;
  font-size: 17px;
  font-weight: 600;
}

.topbar-actions {
  display: flex;
  gap: 8px;
}

/* ---------- 布局 ---------- */
.page-body {
  display: flex;
  gap: 20px;
  max-width: 1200px;
  margin: 20px auto 0;
  padding: 0 16px;
  align-items: flex-start;
}

.side-nav {
  position: sticky;
  top: 76px;
  width: 140px;
  flex-shrink: 0;
}

.content {
  flex: 1;
  min-width: 0;
  background: #fff;
  border-radius: 16px;
  padding: 24px;
  box-shadow: 0 4px 24px rgba(31, 45, 88, 0.06);
}

/* ---------- 卡片 ---------- */
.card {
  padding: 20px 0;
  border-bottom: 1px dashed #e8ecf2;
}

.card:last-child {
  border-bottom: none;
}

.card-title {
  font-size: 20px;
  margin: 0 0 16px;
  font-weight: 700;
}

.overview-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}

.summary {
  line-height: 1.9;
  color: #434343;
  margin: 0;
}

/* ---------- 每日 ---------- */
.day-card {
  padding: 20px;
  margin-bottom: 16px;
  border: 1px solid #eef1f6;
  border-radius: 12px;
}

.day-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 8px;
}

.day-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.day-badge {
  background: linear-gradient(135deg, #1677ff, #4096ff);
  color: #fff;
  border-radius: 8px;
  padding: 2px 10px;
  font-weight: 700;
}

.day-theme {
  font-size: 17px;
  font-weight: 600;
}

.day-date {
  color: #8c8c8c;
  font-size: 13px;
}

.day-weather {
  color: #595959;
  font-size: 14px;
  background: #f6f8fb;
  border-radius: 8px;
  padding: 4px 10px;
}

.day-route {
  margin-top: 10px;
  color: #1677ff;
  font-size: 13px;
  background: #e8f4fd;
  border-radius: 8px;
  padding: 5px 10px;
  width: fit-content;
}

/* ---------- 景点 ---------- */
.attraction {
  display: flex;
  gap: 14px;
  padding: 14px;
  border: 1px solid #f0f2f7;
  border-radius: 12px;
  margin-bottom: 12px;
  position: relative;
}

.attr-index {
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #1677ff;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 2px;
}

.attr-image {
  width: 120px;
  height: 90px;
  border-radius: 8px;
  overflow: hidden;
  flex-shrink: 0;
  background: #f0f2f5;
}

.attr-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.attr-image-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 30px;
  background: linear-gradient(135deg, #e6f4ff, #f9f0ff);
}

.attr-info {
  flex: 1;
  min-width: 0;
}

.attr-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.attr-name {
  font-size: 16px;
  font-weight: 600;
}

.attr-desc {
  margin: 8px 0 4px;
  color: #595959;
  line-height: 1.7;
  font-size: 13.5px;
}

.attr-reason {
  margin: 0 0 4px;
  color: #d46b08;
  font-size: 13px;
}

.attr-address {
  margin: 0;
  color: #8c8c8c;
  font-size: 12.5px;
}

.attr-ops {
  display: flex;
  flex-direction: column;
  gap: 6px;
  justify-content: center;
}

/* ---------- 餐饮 / 酒店 ---------- */
.meal-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
  margin: 12px 0;
}

.meal {
  display: flex;
  gap: 10px;
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 10px;
  padding: 10px 12px;
}

.meal-icon {
  font-size: 20px;
}

.meal-img {
  width: 44px;
  height: 44px;
  border-radius: 8px;
  object-fit: cover;
  flex-shrink: 0;
  display: block;
  background: #fff;
}

.meal-name {
  font-weight: 600;
  font-size: 13.5px;
}

.meal-meta {
  color: #8c6d1f;
  font-size: 12px;
  line-height: 1.6;
}

.hotel {
  display: flex;
  gap: 10px;
  background: #f9f0ff;
  border: 1px solid #efdbff;
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 10px;
}

.hotel-icon {
  font-size: 20px;
}

.hotel-img {
  width: 64px;
  height: 48px;
  border-radius: 8px;
  object-fit: cover;
  flex-shrink: 0;
  display: block;
  background: #fff;
}

.hotel-name {
  font-weight: 600;
  font-size: 13.5px;
}

.hotel-meta {
  color: #722ed1;
  font-size: 12px;
  line-height: 1.6;
}

.day-budget {
  text-align: right;
  color: #8c8c8c;
  font-size: 13px;
}

/* ---------- 地图 ---------- */
.map-container {
  position: relative;
  width: 100%;
  height: 420px;
  border-radius: 12px;
  overflow: hidden;
}

.map-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fafcff;
  z-index: 5;
}

.map-error {
  padding: 8px 0;
}

.map-export-note {
  color: #8c8c8c;
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
  background: #fafcff;
  border-radius: 8px;
}

/* ---------- 贴士 ---------- */
.tips {
  margin: 0;
  padding-left: 20px;
  line-height: 2.1;
  color: #434343;
}

.muted {
  color: #8c8c8c;
}

/* ---------- 反馈与重规划 ---------- */
.fb-btn {
  padding: 0 4px;
  font-size: 12px;
  color: #8c8c8c;
}

.fb-btn:hover {
  color: #1677ff;
}

.feedback-bar {
  position: fixed;
  left: 50%;
  transform: translateX(-50%);
  bottom: 24px;
  z-index: 200;
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 90vw;
  flex-wrap: wrap;
  background: #fff;
  border: 1px solid #ffd591;
  border-radius: 12px;
  padding: 10px 16px;
  box-shadow: 0 8px 28px rgba(31, 45, 88, 0.18);
}

.fb-label {
  font-size: 13px;
  font-weight: 600;
  color: #d46b08;
}

.backup-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  background: #f9f0ff;
  border: 1px dashed #d3adf7;
  border-radius: 10px;
  padding: 8px 12px;
  margin-bottom: 12px;
}

.backup-label {
  font-size: 13px;
  font-weight: 700;
  color: #722ed1;
}

.backup-hint {
  font-size: 12px;
  color: #8c8c8c;
}

.loading-body {
  text-align: center;
  padding: 16px 0 8px;
}

.loading-title {
  margin: 18px 0 6px;
  font-size: 16px;
  font-weight: 600;
}

.loading-hint {
  color: #8c8c8c;
  font-size: 13px;
  margin: 0;
}

.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.25s ease;
}

.fade-slide-enter-from,
.fade-slide-leave-to {
  opacity: 0;
  transform: translate(-50%, 20px);
}

/* ---------- 导出模式：隐藏交互元素 ---------- */
.exporting .no-export {
  display: none !important;
}

@media (max-width: 900px) {
  .side-nav {
    display: none;
  }
  .topbar {
    flex-wrap: wrap;
    padding: 10px 14px;
  }
  .topbar-title {
    font-size: 15px;
  }
  .topbar-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
}

@media (max-width: 640px) {
  .result-page {
    padding-bottom: 24px;
  }
  .result-body,
  .content {
    padding: 12px;
  }
  .content {
    border-radius: 0;
  }
  .card {
    padding: 14px;
  }
  .map-container {
    height: 260px;
  }
  .attraction {
    flex-wrap: wrap;
  }
  .hero-subtitle {
    font-size: 13px;
  }
}
</style>
