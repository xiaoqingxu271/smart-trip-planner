<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import AMapLoader from '@amap/amap-jsapi-loader'
import { exportElementAsPdf, exportElementAsPng } from '@/utils/exporter'
import { openExternal } from '@/utils/amapNav'
import { getTripRoutes, getAppConfig, geocode, getTripDetail, imgProxy, replanTrip, createShare, starTrip, swapBackup, tripIcalUrl, updateTrip } from '@/services/api'
import type { AppConfig, Attraction, DayRoute, Feedback, TripPlan } from '@/types'
import AppIcon from '@/components/AppIcon.vue'
import AmapNavButton from '@/components/AmapNavButton.vue'

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
const DAY_COLORS = ['#e2694f', '#3f8765', '#4c7fb0', '#8e6bb5', '#e89b3c', '#2fa39a', '#c25644']

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

/** 转义 LLM 产出的地名，防止拼进 innerHTML 时注入标签/脚本（批次 A3）。 */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function renderMarkers() {
  if (!mapInstance || !AMapNS) return
  mapInstance.clearMap()
  const markers = allAttractions.value.map((attr, i) => {
    const marker = new AMapNS.Marker({
      position: [attr.location.longitude, attr.location.latitude],
      content: `<div style="width:26px;height:26px;border-radius:50%;background:#2f7254;color:#fff;
        display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;
        border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.3);">${i + 1}</div>`,
      offset: new AMapNS.Pixel(-13, -13),
      title: escapeHtml(attr.name),
    })
    marker.setLabel({
      content: `<span style="font-size:12px;color:#1c2b24;background:#fff;padding:1px 6px;
        border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.15);white-space:nowrap;">${escapeHtml(attr.name)}</span>`,
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
  { id: 'sec-budget', name: '费用估算' },
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
  // 有服务端 ID 时同步回数据库；服务端会重跑校验与重算（路线/钟点/交通费），
  // 返回的修复后计划覆盖本地，保证时间/预算与实际一致
  if (tripId.value) {
    try {
      const repaired = await updateTrip(tripId.value, tripPlan.value)
      tripPlan.value = repaired
      sessionStorage.setItem('tripPlan', JSON.stringify(repaired))
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
  breakfast: { icon: 'coffee', label: '早餐' },
  lunch: { icon: 'utensils', label: '午餐' },
  dinner: { icon: 'soup', label: '晚餐' },
}

function weatherIcon(condition: string): string {
  if (!condition) return 'cloud-sun'
  if (condition.includes('雷')) return 'cloud-lightning'
  if (condition.includes('雨')) return 'cloud-rain'
  if (condition.includes('雪')) return 'cloud-snow'
  if (condition.includes('雾') || condition.includes('霾')) return 'cloud-fog'
  if (condition.includes('阴')) return 'cloud'
  if (condition.includes('多云')) return 'cloud-sun'
  return 'sun'
}

function money(n?: number | null): string {
  return `¥${(n ?? 0).toLocaleString('zh-CN')}`
}

/** 是否需购票：新数据看 has_ticket，老数据（无该字段、ticket_price>0）回退推断。 */
function needTicket(attr: Attraction): boolean {
  return attr.has_ticket === true || (attr.has_ticket === undefined && attr.ticket_price > 0)
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
    message.success(next ? '已收藏到首页「我的收藏」' : '已取消收藏')
  } catch (e) {
    message.error(`操作失败：${(e as Error).message}`)
  }
}

async function shareTrip() {
  if (!tripId.value) return
  try {
    const { share_url } = await createShare(tripId.value)
    await navigator.clipboard.writeText(share_url)
    message.success('分享链接已复制到剪贴板')
  } catch (e) {
    message.error(`分享失败：${(e as Error).message}`)
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
      <button type="button" class="icon-btn" title="返回首页" @click="router.push({ name: 'home' })">
        <AppIcon name="arrow-left" :size="17" />
      </button>
      <div class="topbar-brand">
        <span class="brand-mark"><AppIcon name="compass" :size="17" color="#fff" /></span>
        <div class="topbar-title">
          <strong>{{ (working ?? tripPlan)?.destination }}</strong>
          <span class="topbar-days">{{ (working ?? tripPlan)?.days }} 天行程</span>
        </div>
      </div>
      <div class="topbar-actions">
        <template v-if="editing">
          <a-button type="primary" @click="saveEdit">保存修改</a-button>
          <a-button @click="cancelEdit">取消编辑</a-button>
        </template>
        <template v-else>
          <a-button v-if="tripId" :type="starred ? 'primary' : 'default'" :ghost="starred" @click="toggleStar">
            <AppIcon name="star" :size="14" :color="starred ? '#e89b3c' : undefined" :filled="starred" />
            {{ starred ? '已收藏' : '收藏' }}
          </a-button>
          <a-button type="primary" ghost @click="enterEdit">
            <AppIcon name="pencil" :size="14" />
            编辑行程
          </a-button>
          <a-button :loading="exporting" @click="exportPDF">
            <AppIcon name="file-text" :size="14" />
            导出 PDF
          </a-button>
          <a-button :loading="exporting" @click="exportImage">
            <AppIcon name="image" :size="14" />
            导出长图
          </a-button>
          <a-button @click="exportIcal">
            <AppIcon name="calendar-plus" :size="14" />
            存入日历
          </a-button>
          <a-button v-if="tripPlan?.amap_map_url" @click="openExternal(tripPlan!.amap_map_url!)">
            <AppIcon name="map" :size="14" />
            打开高德地图
          </a-button>
          <a-button v-if="tripId" @click="shareTrip">
            分享
          </a-button>
        </template>
      </div>
    </header>

    <div v-if="tripPlan" class="page-body">
      <!-- 侧边导航 -->
      <nav class="side-nav no-export">
        <button
          v-for="s in sections"
          :key="s.id"
          type="button"
          class="nav-item"
          :class="{ active: activeSection[0] === s.id }"
          @click="scrollTo(s.id)"
        >
          <span class="nav-dot"></span>{{ s.name }}
        </button>
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
            此行程是根据你的反馈对原行程重新规划生成的新版本。
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
          <template #message>以下问题未能完全自动修复，请留意（可标记后重新规划）：</template>
          <template #description>
            <ul style="margin: 0; padding-left: 18px">
              <li v-for="(w, i) in tripPlan.warnings" :key="i">{{ w }}</li>
            </ul>
          </template>
        </a-alert>

        <!-- 行程概览：沉浸式 Hero -->
        <section id="sec-overview" class="hero-banner">
          <div class="hb-badges">
            <span class="hb-badge"><AppIcon name="map-pin" :size="12" color="rgba(255,255,255,0.92)" />{{ tripPlan.destination }}</span>
            <span class="hb-badge"><AppIcon name="calendar-days" :size="12" color="rgba(255,255,255,0.92)" />共 {{ tripPlan.days }} 天</span>
          </div>
          <h1 class="hb-title">{{ tripPlan.destination }} · {{ tripPlan.days }} 天行程</h1>
          <p class="hb-summary">{{ tripPlan.summary }}</p>
          <div class="hb-themes">
            <span v-for="(d, i) in tripPlan.daily_plans" :key="i" class="hb-theme">D{{ d.day }} {{ d.theme }}</span>
          </div>
        </section>

        <!-- 每日安排：时间线 -->
        <section id="sec-days" class="sec">
          <p class="eyebrow">DAILY ITINERARY</p>
          <h2 class="sec-title">每日安排</h2>

          <div class="timeline">
            <div v-for="(day, dayIdx) in working?.daily_plans ?? tripPlan.daily_plans" :key="day.day" class="day-block">
              <div class="day-rail">
                <div class="day-badge">D{{ day.day }}</div>
                <div class="rail-line"></div>
              </div>

              <div class="day-card">
                <div class="day-header">
                  <div class="day-title">
                    <h3 class="day-theme">{{ day.theme }}</h3>
                    <span class="day-date">{{ day.date }}</span>
                  </div>
                  <div v-if="day.weather" class="day-weather">
                    <AppIcon :name="weatherIcon(day.weather.condition)" :size="14" />
                    {{ day.weather.condition }}
                    {{ day.weather.day_temp }}℃ / {{ day.weather.night_temp }}℃
                  </div>
                </div>

                <div v-if="routeFor(day.day)" class="day-route">
                  <AppIcon name="car" :size="14" />
                  全程 {{ fmtKm(routeFor(day.day)!.distance_m) }} · 车程约 {{ fmtDuration(routeFor(day.day)!.duration_s) }} · 打车约 ¥{{ routeFor(day.day)!.taxi_cost }}
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
                      <div v-else class="attr-image-placeholder"><AppIcon name="landmark" :size="30" color="#93bfa6" /></div>
                    </div>
                    <div class="attr-info">
                      <div class="attr-name-row">
                        <span class="attr-name">{{ attr.name }}</span>
                        <a-tag v-if="needTicket(attr)" color="orange">需门票（以官网为准）</a-tag>
                        <a-tag v-else color="green">免费</a-tag>
                        <a-tag v-if="attr.start_time && attr.end_time" color="blue">{{ attr.start_time }}–{{ attr.end_time }}</a-tag>
                        <a-tag><AppIcon name="clock" :size="11" color="#67756d" /> {{ attr.duration }}</a-tag>
                        <AmapNavButton :name="attr.name" :location="attr.location" :city="tripPlan.destination" />
                        <a-dropdown v-if="tripId && !editing" class="no-export">
                          <a-button size="small" type="text" class="fb-btn">有问题？</a-button>
                          <template #overlay>
                            <a-menu @click="({ key }) => addFeedback('attraction', attr.name, key as string)">
                              <a-menu-item v-for="r in FEEDBACK_OPTIONS.attraction" :key="r">{{ r }}</a-menu-item>
                            </a-menu>
                          </template>
                        </a-dropdown>
                        <a-dropdown v-if="tripId && !editing && (day.backup_attractions?.length ?? 0) > 0" class="no-export">
                          <a-button size="small" type="text" class="fb-btn">
                            <AppIcon name="clover" :size="12" color="#6b4fa0" />
                            换备选
                          </a-button>
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
                      <p v-if="attr.recommended_reason" class="attr-reason">
                        <AppIcon name="lightbulb" :size="13" color="#a3661a" />
                        {{ attr.recommended_reason }}
                      </p>
                      <p v-if="attr.address" class="attr-address">
                        <AppIcon name="map-pin" :size="12" color="#8a978f" />
                        {{ attr.address }}
                      </p>
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

                <a-button v-if="editing" type="dashed" block class="no-export add-btn" @click="openAddModal(dayIdx)">
                  ＋ 为第 {{ day.day }} 天添加景点
                </a-button>

                <!-- Plan B 备选 -->
                <div v-if="(day.backup_attractions?.length ?? 0) > 0 && !editing" class="backup-row no-export">
                  <span class="backup-label"><AppIcon name="clover" :size="14" color="#6b4fa0" />Plan B</span>
                  <span class="backup-hint">遇约满/排队可换：</span>
                  <span
                    v-for="b in day.backup_attractions"
                    :key="b.name"
                    class="btag"
                    :title="b.description"
                  >{{ b.name }}</span>
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
                    <span v-else class="meal-icon"><AppIcon :name="MEAL_META[meal.type]?.icon ?? 'utensils'" :size="18" color="#c97f22" /></span>
                    <div>
                      <div class="meal-name">
                        {{ meal.restaurant }}
                        <AmapNavButton :name="meal.restaurant" :location="meal.location" :city="tripPlan.destination" />
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
                        <template v-if="meal.cost"> · 人均 {{ money(meal.cost) }}（估算）</template>
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
                  <span v-else class="hotel-icon"><AppIcon name="hotel" :size="20" color="#3f8765" /></span>
                  <div>
                    <div class="hotel-name">
                      {{ day.hotel.name }}
                      <a-rate :value="day.hotel.rating / 1" disabled allow-half style="font-size: 12px; margin-left: 8px" />
                      <AmapNavButton :name="day.hotel.name" :location="day.hotel.location" :city="tripPlan.destination" />
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
                      {{ day.hotel.hotel_type }} ·
                      <template v-if="day.hotel.price_from != null">
                        起 {{ money(day.hotel.price_from) }}<template v-if="day.hotel.price_source">（来源：携程广告价）</template>
                      </template>
                      <template v-else>{{ money(day.hotel.price_per_night) }}/晚（估算）</template>
                      <template v-if="day.hotel.address"> · {{ day.hotel.address }}</template>
                    </div>
                  </div>
                </div>

                <div class="day-budget">当日预估 <b>{{ money(day.daily_budget) }}</b></div>
              </div>
            </div>
          </div>
        </section>

        <!-- 费用预算：Bento 统计卡 -->
        <section id="sec-budget" class="sec">
          <p class="eyebrow">ESTIMATED COST</p>
          <h2 class="sec-title">费用估算</h2>
          <div v-if="tripPlan.budget" class="budget-grid">
            <div class="b-item">
              <span class="b-ico"><AppIcon name="ticket" :size="18" color="#275c45" /></span>
              <span class="b-label">门票</span>
              <span class="b-value">{{ tripPlan.budget.attraction_total > 0 ? money(tripPlan.budget.attraction_total) : '以官网为准' }}</span>
            </div>
            <div class="b-item">
              <span class="b-ico"><AppIcon name="hotel" :size="18" color="#275c45" /></span>
              <span class="b-label">住宿（估算）</span>
              <span class="b-value">{{ money(tripPlan.budget.hotel_total) }}</span>
            </div>
            <div class="b-item">
              <span class="b-ico"><AppIcon name="utensils" :size="18" color="#275c45" /></span>
              <span class="b-label">餐饮（估算）</span>
              <span class="b-value">{{ money(tripPlan.budget.meal_total) }}</span>
            </div>
            <div class="b-item">
              <span class="b-ico"><AppIcon name="car" :size="18" color="#275c45" /></span>
              <span class="b-label">市内交通（路线估价）</span>
              <span class="b-value">{{ money(tripPlan.budget.transport_total) }}</span>
            </div>
            <div class="b-item b-total">
              <span class="b-label">估算总计</span>
              <span class="b-value">{{ money(tripPlan.budget.grand_total) }}</span>
              <span class="b-note">含门票 / 住宿 / 餐饮 / 市内交通</span>
            </div>
          </div>
          <p v-if="tripPlan.budget_note" class="budget-note">{{ tripPlan.budget_note }}</p>
          <p v-else-if="!tripPlan.budget" class="muted">暂无预算信息</p>
        </section>

        <!-- 地图总览：导出时隐藏（地图 Canvas 与 html2canvas 存在兼容性问题） -->
        <section id="sec-map" class="sec">
          <p class="eyebrow">MAP</p>
          <h2 class="sec-title">地图总览</h2>
          <div v-show="!exporting" class="map-wrap">
            <div v-if="mapError" class="map-error">
              <a-alert type="info" show-icon :message="mapError" />
            </div>
            <div v-show="!mapError" id="map-container" class="map-container">
              <div v-if="mapLoading" class="map-loading"><a-spin tip="地图加载中…" /></div>
            </div>
          </div>
          <div v-if="exporting" class="map-export-note">
            <AppIcon name="map" :size="14" color="#8a978f" />
            地图请在应用内查看（导出模式暂不包含地图）
          </div>
        </section>

        <!-- 实用贴士 -->
        <section id="sec-tips" class="sec sec-last">
          <p class="eyebrow">TIPS</p>
          <h2 class="sec-title">实用贴士</h2>
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
          <AppIcon name="refresh-cw" :size="13" />
          按反馈重新规划
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
  background:
    radial-gradient(1000px 400px at 90% -4%, rgba(220, 235, 225, 0.8) 0%, transparent 60%),
    var(--bg);
  padding-bottom: 56px;
}

/* ---------- 顶栏 ---------- */
.topbar {
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 24px;
  background: rgba(243, 246, 241, 0.88);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid rgba(228, 233, 226, 0.8);
}

.icon-btn {
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  border-radius: 50%;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink-700);
  font-size: 16px;
  cursor: pointer;
  transition: all 0.15s;
}

.icon-btn:hover {
  border-color: var(--brand-400);
  color: var(--brand-700);
  transform: translateX(-2px);
}

.topbar-brand {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.brand-mark {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--brand-600), var(--brand-500));
  box-shadow: 0 3px 8px rgba(47, 114, 84, 0.26);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
}

.topbar-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.topbar-title strong {
  font-size: 16px;
  font-weight: 700;
  color: var(--ink-900);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.topbar-days {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--brand-600);
  background: var(--brand-50);
  border: 1px solid var(--brand-100);
  border-radius: 999px;
  padding: 1px 10px;
}

.topbar-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.topbar-actions :deep(.app-icon) {
  margin-right: 5px;
}

.feedback-bar :deep(.app-icon) {
  margin-right: 4px;
}

/* ---------- 布局 ---------- */
.page-body {
  display: flex;
  gap: 22px;
  max-width: 1240px;
  margin: 24px auto 0;
  padding: 0 20px;
  align-items: flex-start;
}

.side-nav {
  position: sticky;
  top: 76px;
  width: 148px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 14px;
  border: none;
  border-radius: 12px;
  background: transparent;
  color: var(--ink-500);
  font-size: 14px;
  text-align: left;
  cursor: pointer;
  transition: all 0.18s;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.85);
  color: var(--ink-900);
}

.nav-item.active {
  background: #fff;
  color: var(--brand-700);
  font-weight: 600;
  box-shadow: var(--shadow-sm);
}

.nav-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ink-300);
  flex-shrink: 0;
  transition: all 0.18s;
}

.nav-item.active .nav-dot {
  background: var(--brand-500);
  box-shadow: 0 0 0 3px var(--brand-100);
}

.content {
  flex: 1;
  min-width: 0;
  background: var(--card);
  border: 1px solid rgba(228, 233, 226, 0.9);
  border-radius: var(--radius-xl);
  padding: 26px 28px 32px;
  box-shadow: var(--shadow-md);
}

/* ---------- 区块标题 ---------- */
.sec {
  margin-top: 44px;
}

.sec-last {
  margin-bottom: 0;
}

.sec-title {
  font-size: 24px;
  font-weight: 800;
  color: var(--ink-900);
  margin: 8px 0 18px;
}

.muted {
  color: var(--ink-400);
}

/* ---------- 概览 Hero ---------- */
.hero-banner {
  position: relative;
  overflow: hidden;
  border-radius: 24px;
  padding: 32px 36px 30px;
  color: #fff;
  background:
    radial-gradient(240px 240px at 88% 6%, rgba(242, 179, 76, 0.55) 0%, transparent 70%),
    radial-gradient(420px 300px at 108% 96%, rgba(255, 255, 255, 0.1) 0%, transparent 70%),
    linear-gradient(140deg, #1f4636 0%, #2f7254 62%, #3f8765 100%);
  margin-bottom: 8px;
}

.hb-badges {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.hb-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.14);
  border: 1px solid rgba(255, 255, 255, 0.22);
  border-radius: 999px;
  padding: 3px 12px;
}

.hb-title {
  font-size: 34px;
  font-weight: 800;
  margin: 0 0 12px;
  letter-spacing: 0.01em;
}

.hb-summary {
  font-size: 14.5px;
  line-height: 1.9;
  color: rgba(255, 255, 255, 0.88);
  max-width: 760px;
  margin: 0 0 18px;
}

.hb-themes {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.hb-theme {
  font-size: 12.5px;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.13);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 999px;
  padding: 4px 14px;
}

/* ---------- 每日时间线 ---------- */
.timeline {
  display: flex;
  flex-direction: column;
}

.day-block {
  display: flex;
  gap: 18px;
}

.day-rail {
  width: 52px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.day-badge {
  width: 46px;
  height: 46px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--brand-600), var(--brand-400));
  color: #fff;
  font-size: 15px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 6px 14px rgba(47, 114, 84, 0.3);
  margin-top: 4px;
}

.rail-line {
  flex: 1;
  width: 0;
  border-left: 2px dashed var(--brand-200);
  margin: 8px 0 8px;
}

.day-block:last-child .rail-line {
  display: none;
}

.day-card {
  flex: 1;
  min-width: 0;
  background: #fafcf9;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  padding: 20px 22px;
  margin-bottom: 18px;
}

.day-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}

.day-title {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.day-theme {
  font-size: 19px;
  font-weight: 700;
  color: var(--ink-900);
  margin: 0;
}

.day-date {
  color: var(--ink-400);
  font-size: 13px;
}

.day-weather {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--ink-700);
  font-size: 13px;
  font-weight: 600;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 4px 12px;
}

.day-route {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--brand-700);
  font-size: 13px;
  font-weight: 600;
  background: var(--brand-50);
  border: 1px solid var(--brand-100);
  border-radius: 10px;
  padding: 6px 12px;
  width: fit-content;
  margin-bottom: 14px;
}

/* ---------- 景点 ---------- */
.attraction-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 14px;
}

.attraction {
  display: flex;
  gap: 14px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  margin-bottom: 0;
  position: relative;
  background: #fff;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.attraction:hover {
  border-color: var(--brand-200);
  box-shadow: var(--shadow-sm);
}

.attr-index {
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--brand-600);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 2px;
}

.attr-image {
  width: 132px;
  height: 96px;
  border-radius: 10px;
  overflow: hidden;
  flex-shrink: 0;
  background: var(--brand-50);
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
  background: linear-gradient(135deg, var(--brand-50), var(--amber-100));
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
  font-weight: 700;
  color: var(--ink-900);
}

.attr-desc {
  margin: 8px 0 4px;
  color: var(--ink-500);
  line-height: 1.7;
  font-size: 13.5px;
}

.attr-reason {
  display: flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
  margin: 0 0 6px;
  color: var(--amber-700);
  font-size: 13px;
  background: var(--amber-100);
  border-radius: 8px;
  padding: 6px 10px;
}

.attr-address {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 0;
  color: var(--ink-400);
  font-size: 12.5px;
}

.attr-ops {
  display: flex;
  flex-direction: column;
  gap: 6px;
  justify-content: center;
}

.add-btn {
  margin-bottom: 14px;
}

/* ---------- Plan B ---------- */
.backup-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  background: #f7f3fb;
  border: 1px dashed #d9c9ee;
  border-radius: 12px;
  padding: 8px 14px;
  margin-bottom: 14px;
}

.backup-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  font-weight: 700;
  color: #6b4fa0;
}

.backup-hint {
  font-size: 12px;
  color: var(--ink-400);
}

.btag {
  font-size: 12.5px;
  font-weight: 600;
  color: #6b4fa0;
  background: #fff;
  border: 1px solid #e2d6f2;
  border-radius: 999px;
  padding: 3px 12px;
}

/* ---------- 餐饮 / 酒店 ---------- */
.meal-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 10px;
  margin: 14px 0;
}

.meal {
  display: flex;
  gap: 10px;
  background: #fdf8ec;
  border: 1px solid #f1e2c4;
  border-radius: 12px;
  padding: 10px 12px;
}

.meal-icon {
  font-size: 20px;
}

.meal-img {
  width: 46px;
  height: 46px;
  border-radius: 10px;
  object-fit: cover;
  flex-shrink: 0;
  display: block;
  background: #fff;
}

.meal-name {
  font-weight: 700;
  font-size: 13.5px;
  color: var(--ink-900);
}

.meal-meta {
  color: var(--amber-700);
  font-size: 12px;
  line-height: 1.6;
}

.hotel {
  display: flex;
  gap: 12px;
  background: var(--brand-50);
  border: 1px solid var(--brand-100);
  border-radius: 12px;
  padding: 10px 12px;
  margin-bottom: 12px;
}

.hotel-icon {
  font-size: 20px;
}

.hotel-img {
  width: 68px;
  height: 50px;
  border-radius: 10px;
  object-fit: cover;
  flex-shrink: 0;
  display: block;
  background: #fff;
}

.hotel-name {
  font-weight: 700;
  font-size: 13.5px;
  color: var(--ink-900);
}

.hotel-meta {
  color: var(--brand-700);
  font-size: 12px;
  line-height: 1.6;
}

.day-budget {
  text-align: right;
  color: var(--ink-400);
  font-size: 13px;
}

.day-budget b {
  color: var(--amber-700);
  font-size: 15px;
  margin-left: 4px;
}

/* ---------- 预算 Bento ---------- */
.budget-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr) 1.5fr;
  gap: 14px;
}

.b-item {
  background: #fafcf9;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.b-ico {
  width: 38px;
  height: 38px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid var(--line);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  margin-bottom: 6px;
}

.b-label {
  font-size: 13px;
  color: var(--ink-500);
}

.b-value {
  font-size: 21px;
  font-weight: 800;
  color: var(--ink-900);
}

.b-total {
  background: linear-gradient(140deg, var(--brand-800) 0%, var(--brand-600) 100%);
  border: none;
  color: #fff;
  justify-content: center;
}

.b-total .b-label {
  color: rgba(255, 255, 255, 0.75);
}

.b-total .b-value {
  color: #fff;
  font-size: 30px;
}

.b-note {
  font-size: 11.5px;
  color: rgba(255, 255, 255, 0.55);
}

.budget-note {
  margin: 12px 2px 0;
  font-size: 12.5px;
  color: var(--ink-400);
}

/* ---------- 地图 ---------- */
.map-container {
  position: relative;
  width: 100%;
  height: 440px;
  border-radius: 16px;
  overflow: hidden;
  border: 1px solid var(--line);
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
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--ink-400);
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
  background: #fafcf9;
  border: 1px dashed var(--line);
  border-radius: 12px;
}

/* ---------- 贴士 ---------- */
.tips {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.tips li {
  position: relative;
  background: #fafcf9;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px 14px 12px 40px;
  color: var(--ink-700);
  font-size: 13.5px;
  line-height: 1.7;
}

.tips li::before {
  content: '✓';
  position: absolute;
  left: 14px;
  top: 12px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--brand-100);
  color: var(--brand-700);
  font-size: 11px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ---------- 反馈与重规划 ---------- */
.fb-btn {
  padding: 0 4px;
  font-size: 12px;
  color: var(--ink-400);
}

.fb-btn:hover {
  color: var(--brand-600) !important;
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
  background: var(--brand-900);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 16px;
  padding: 12px 18px;
  box-shadow: var(--shadow-lg);
}

.fb-label {
  font-size: 13px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.9);
}

.feedback-bar :deep(.ant-tag) {
  background: rgba(255, 255, 255, 0.12);
  border-color: rgba(255, 255, 255, 0.2);
  color: #fff;
}

.loading-body {
  text-align: center;
  padding: 16px 0 8px;
}

.loading-title {
  margin: 18px 0 6px;
  font-size: 16px;
  font-weight: 600;
  color: var(--ink-900);
}

.loading-hint {
  color: var(--ink-400);
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
  .topbar-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .content {
    padding: 18px 16px 24px;
    border-radius: var(--radius-lg);
  }
  .hero-banner {
    padding: 24px 22px;
    border-radius: var(--radius-lg);
  }
  .hb-title {
    font-size: 26px;
  }
  .budget-grid {
    grid-template-columns: 1fr 1fr;
  }
  .b-total {
    grid-column: 1 / -1;
  }
  .tips {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .result-page {
    padding-bottom: 24px;
  }
  .page-body {
    padding: 0 12px;
    margin-top: 16px;
  }
  .map-container {
    height: 260px;
  }
  .attraction {
    flex-wrap: wrap;
  }
  .day-block {
    gap: 12px;
  }
  .day-rail {
    width: 40px;
  }
  .day-badge {
    width: 38px;
    height: 38px;
    font-size: 13px;
  }
  .day-card {
    padding: 14px;
  }
}
</style>
