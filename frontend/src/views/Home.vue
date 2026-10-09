<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  deleteTrip,
  deleteAccount,
  getHistory,
  getMe,
  getAppStatus,
  getToken,
  imgProxy,
  logoutUser,
  planJobPoll,
  type HistoryFilter,
  type PlanStage,
} from '@/services/api'
import type { TripRequest, TripSummary } from '@/types'
import AppIcon from '@/components/AppIcon.vue'

const router = useRouter()

const destinationSuggestions = ['北京', '上海', '西安', '成都', '杭州', '重庆', '广州', '三亚', '厦门', '青岛']
const preferenceOptions = ['人文历史', '自然风光', '美食探店', '购物血拼', '亲子游玩', '网红打卡', '休闲度假', '博物馆控']
const groupOptions = ['独自出行', '情侣出游', '家庭亲子', '朋友结伴']

const slotOptions = [
  { value: '', label: '全天可玩' },
  { value: 'morning', label: '上午' },
  { value: 'afternoon', label: '下午' },
  { value: 'evening', label: '傍晚' },
]
const paceOptions = [
  { value: 'easy', label: '轻松 · 每天≤2景' },
  { value: 'standard', label: '标准 · 每天≤3景' },
  { value: 'packed', label: '紧凑 · 每天≤4景' },
]
const transitOptions = [
  { value: 'taxi', label: '打车' },
  { value: 'transit', label: '公交地铁' },
  { value: 'walk', label: '步行' },
  { value: 'drive', label: '自驾' },
]

const today = new Date().toISOString().slice(0, 10)

const form = reactive<TripRequest>({
  destination: '',
  start_date: today,
  days: 3,
  budget: null,
  preferences: [],
  group_type: '情侣出游',
  notes: '',
  arrival_slot: '',
  departure_slot: '',
  pace: 'standard',
  must_see: [],
  avoid: [],
  transit_mode: 'taxi',
  origin: '',
})

const paceTouched = ref(false)

const submitting = ref(false)
const loadingTitle = ref('')
const progressPercent = ref(0)
const username = ref('')

// 流式规划各阶段对应的进度条百分比
const STAGE_PERCENT: Record<string, number> = {
  started: 8,
  attractions: 25,
  weather: 42,
  hotel: 58,
  planning: 72,
  planned: 82,
  validating: 90,
  validated: 94,
  images: 97,
}

// ---------- 精选行程（作品集） ----------
const galleryTabs: { key: HistoryFilter; label: string; hint: string }[] = [
  { key: 'seed', label: '示例作品', hint: '内置的精选行程案例' },
  { key: 'starred', label: '我的收藏', hint: '结果页点星收藏的好规划' },
  { key: 'recent', label: '最近规划', hint: '你最近生成的行程' },
]
const activeTab = ref<HistoryFilter>('seed')
const galleryLists = reactive<Record<HistoryFilter, TripSummary[]>>({
  recent: [], starred: [], seed: [],
})
const galleryLoading = ref(false)
const galleryError = ref('')
// 封面图加载失败的行程 id → 展示 emoji 占位，避免出现破图图标
const brokenCovers = reactive<Record<number, boolean>>({})

function coverSrc(url?: string | null): string | null {
  return imgProxy(url)
}

function fmtDate(iso: string): string {
  return iso.replace('T', ' ')
}

async function loadGallery() {
  galleryLoading.value = true
  galleryError.value = ''
  const results = await Promise.allSettled(
    galleryTabs.map((t) => getHistory(t.key, 12)),
  )
  results.forEach((r, i) => {
    if (r.status === 'fulfilled') {
      galleryLists[galleryTabs[i].key] = r.value
    } else {
      galleryError.value = `历史服务暂不可用：${(r.reason as Error).message}`
    }
  })
  galleryLoading.value = false
}

function openTrip(id: number) {
  router.push({ path: '/result', query: { id: String(id) } })
}

async function removeTrip(id: number) {
  try {
    await deleteTrip(id)
    galleryLists.recent = galleryLists.recent.filter((t) => t.id !== id)
    message.success('已删除')
  } catch (e) {
    message.error(`删除失败：${(e as Error).message}`)
  }
}

function togglePreference(tag: string) {
  const idx = form.preferences.indexOf(tag)
  if (idx >= 0) {
    form.preferences.splice(idx, 1)
    if (tag === '亲子游玩' && !paceTouched.value && form.pace === 'easy') {
      form.pace = 'standard'
    }
  } else {
    form.preferences.push(tag)
    // 亲子：若用户未手动改节奏，默认切到轻松（批次 3.4）
    if (tag === '亲子游玩' && !paceTouched.value) {
      form.pace = 'easy'
    }
  }
}

function setPace(value: string) {
  form.pace = value as TripRequest['pace']
  paceTouched.value = true
}

function disabledDate(current: { format: (f: string) => string } | null) {
  // antd-vue 4 传入的是 dayjs 对象；用 YYYY-MM-DD 字符串比较，避免时区问题
  return !!current && current.format('YYYY-MM-DD') < today
}

async function handleSubmit() {
  if (!form.destination.trim()) {
    message.warning('请先填写目的地城市')
    return
  }
  if (!form.start_date) {
    message.warning('请选择出发日期')
    return
  }

  submitting.value = true
  progressPercent.value = 5
  loadingTitle.value = '正在连接规划服务…'

  try {
    // 异步规划任务：进度条与文案来自后端各 Agent 的真实完成事件；
    // 任务在服务端后台执行，即使关闭页面，完成后行程也会自动入历史
    const plan = await planJobPoll({ ...form, destination: form.destination.trim() }, (s: PlanStage) => {
      if (s.message) loadingTitle.value = s.message
      const p = STAGE_PERCENT[s.stage]
      if (p) progressPercent.value = p
    })
    sessionStorage.setItem('tripPlan', JSON.stringify(plan))
    progressPercent.value = 100
    message.success('行程规划完成！')
    router.push({ name: 'result' })
  } catch (err) {
    message.error(`行程规划失败：${(err as Error).message}`, 6)
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  loadGallery()
  loadUserChip()
})

async function loadUserChip() {
  try {
    const status = await getAppStatus()
    if (status.auth_mode === 'user' && getToken()) {
      const me = await getMe()
      username.value = me.username
    }
  } catch {
    /* token 失效由拦截器统一处理 */
  }
}

async function handleLogout() {
  await logoutUser()
  window.location.href = '/login'
}

async function handleDeleteAccount() {
  Modal.confirm({
    title: '注销账号',
    content: '将永久删除你的账号与全部行程数据（含分享链接），此操作不可恢复。确定继续吗？',
    okText: '确认注销',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await deleteAccount()
        message.success('账号已注销')
        window.location.href = '/login'
      } catch (e) {
        message.error(`注销失败：${(e as Error).message}`)
      }
    },
  })
}
</script>

<template>
  <div class="home">
    <!-- 顶部导航 -->
    <header class="nav">
      <div class="nav-inner">
        <div class="nav-brand">
          <span class="brand-mark"><AppIcon name="compass" :size="18" color="#fff" /></span>
          <span class="brand-text">智能旅行助手</span>
        </div>
        <div v-if="username" class="user-chip">
          <span class="avatar">{{ username.slice(0, 1).toUpperCase() }}</span>
          <span class="uname">{{ username }}</span>
          <a class="logout" @click="handleLogout">退出</a>
          <a class="logout danger" @click="handleDeleteAccount">注销</a>
        </div>
      </div>
    </header>

    <!-- Hero -->
    <section class="hero">
      <p class="eyebrow">多智能体 · 旅行规划</p>
      <h1 class="hero-title">把攻略交给智能体，把时间留给<span class="hl">风景</span></h1>
      <p class="hero-sub">
        景点搜索、天气查询、酒店推荐、行程规划四个智能体协作，约一分钟为你生成一份贴合偏好、可编辑、可导出的完整行程。
      </p>
      <div class="agent-chips">
        <span class="agent-chip"><AppIcon name="mountain" :size="14" color="#275c45" />景点搜索</span>
        <span class="agent-chip"><AppIcon name="cloud-sun" :size="14" color="#275c45" />天气查询</span>
        <span class="agent-chip"><AppIcon name="hotel" :size="14" color="#275c45" />酒店推荐</span>
        <span class="agent-chip"><AppIcon name="calendar-days" :size="14" color="#275c45" />行程规划</span>
      </div>
    </section>

    <!-- 规划搜索卡 -->
    <section class="planner">
      <div class="planner-card">
        <div class="bar-grid">
          <div class="bar-field">
            <label class="bar-label"><AppIcon name="map-pin" :size="13" color="#8a978f" />目的地</label>
            <a-auto-complete
              v-model:value="form.destination"
              :options="destinationSuggestions.map((d) => ({ value: d }))"
              placeholder="想去哪座城市？"
              allow-clear
            />
          </div>
          <div class="bar-field">
            <label class="bar-label"><AppIcon name="calendar-days" :size="13" color="#8a978f" />出发日期</label>
            <a-date-picker
              v-model:value="form.start_date"
              value-format="YYYY-MM-DD"
              :disabled-date="disabledDate"
            />
          </div>
          <div class="bar-field">
            <label class="bar-label"><AppIcon name="clock" :size="13" color="#8a978f" />行程天数</label>
            <div class="unit-wrap">
              <a-input-number v-model:value="form.days" :min="1" :max="7" />
              <span class="unit">天</span>
            </div>
          </div>
          <div class="bar-field">
            <label class="bar-label"><AppIcon name="wallet" :size="13" color="#8a978f" />总预算</label>
            <div class="unit-wrap">
              <a-input-number v-model:value="form.budget" :min="0" :step="500" placeholder="选填" />
              <span class="unit">元</span>
            </div>
          </div>
        </div>

        <div class="opt-area">
          <div class="opt-group">
            <span class="opt-label">出行类型</span>
            <div class="chips">
              <button
                v-for="g in groupOptions"
                :key="g"
                type="button"
                class="chip"
                :class="{ on: form.group_type === g }"
                @click="form.group_type = g"
              >{{ g }}</button>
            </div>
          </div>

          <div class="opt-group">
            <span class="opt-label">旅行偏好 <em class="opt-optional">可多选</em></span>
            <div class="chips">
              <button
                v-for="tag in preferenceOptions"
                :key="tag"
                type="button"
                class="chip"
                :class="{ on: form.preferences.includes(tag) }"
                @click="togglePreference(tag)"
              >{{ tag }}</button>
            </div>
          </div>

          <div class="opt-group">
            <span class="opt-label">特殊要求 <em class="opt-optional">选填</em></span>
            <a-textarea
              v-model:value="form.notes"
              placeholder="例如：不想太赶、希望多安排博物馆、有老人同行节奏放慢……"
              :rows="2"
              auto-size
              class="notes-input"
            />
          </div>

          <div class="opt-group">
            <span class="opt-label">首日抵达 <em class="opt-optional">选填</em></span>
            <div class="chips">
              <button
                v-for="s in slotOptions"
                :key="s.value"
                type="button"
                class="chip"
                :class="{ on: form.arrival_slot === s.value }"
                @click="form.arrival_slot = s.value as TripRequest['arrival_slot']"
              >{{ s.label }}</button>
            </div>
          </div>

          <div class="opt-group">
            <span class="opt-label">末日离开 <em class="opt-optional">选填</em></span>
            <div class="chips">
              <button
                v-for="s in slotOptions"
                :key="s.value"
                type="button"
                class="chip"
                :class="{ on: form.departure_slot === s.value }"
                @click="form.departure_slot = s.value as TripRequest['departure_slot']"
              >{{ s.label }}</button>
            </div>
          </div>

          <div class="opt-group">
            <span class="opt-label">行程节奏</span>
            <div class="chips">
              <button
                v-for="p in paceOptions"
                :key="p.value"
                type="button"
                class="chip"
                :class="{ on: form.pace === p.value }"
                @click="setPace(p.value)"
              >{{ p.label }}</button>
            </div>
          </div>

          <div class="opt-group">
            <span class="opt-label">市内交通</span>
            <div class="chips">
              <button
                v-for="t in transitOptions"
                :key="t.value"
                type="button"
                class="chip"
                :class="{ on: form.transit_mode === t.value }"
                @click="form.transit_mode = t.value as TripRequest['transit_mode']"
              >{{ t.label }}</button>
            </div>
          </div>

          <div class="opt-group">
            <span class="opt-label">出发城市 <em class="opt-optional">选填</em></span>
            <a-input v-model:value="form.origin" placeholder="本地游可留空；填了会提示大交通预留" class="opt-input" />
          </div>

          <div class="opt-group">
            <span class="opt-label">必去地点 <em class="opt-optional">最多5个</em></span>
            <a-select
              v-model:value="form.must_see"
              mode="tags"
              :open="false"
              :max-tag-count="5"
              placeholder="输入后回车添加，如：中山陵"
              class="opt-input"
            />
          </div>

          <div class="opt-group">
            <span class="opt-label">不去地点 <em class="opt-optional">最多5个</em></span>
            <a-select
              v-model:value="form.avoid"
              mode="tags"
              :open="false"
              :max-tag-count="5"
              placeholder="输入后回车添加，如：夫子庙夜市"
              class="opt-input"
            />
          </div>
        </div>

        <div class="bar-footer">
          <p class="footer-hint">生成约需 10~90 秒 · 任务后台执行，行程自动云端保存</p>
          <button type="button" class="cta" :disabled="submitting" @click="handleSubmit">
            <AppIcon name="sparkles" :size="17" color="#fff" />
            开始规划我的旅行
          </button>
        </div>
      </div>
    </section>

    <a-modal :open="submitting" :closable="false" :keyboard="false" :mask-closable="false" :footer="null" centered>
      <div class="loading-body">
        <a-spin size="large" />
        <a-progress
          :percent="progressPercent"
          :show-info="false"
          status="active"
          :stroke-color="{ '0%': '#2f7254', '100%': '#5f9f80' }"
          class="loading-progress"
        />
        <p class="loading-title">{{ loadingTitle }}</p>
        <p class="loading-hint">任务在后台执行，通常需要 10~90 秒；中途关闭页面行程也不会丢</p>
      </div>
    </a-modal>

    <!-- 精选行程（作品集） -->
    <section class="gallery">
      <div class="gallery-head">
        <div>
          <p class="eyebrow">CURATED TRIPS</p>
          <h2 class="gallery-title">精选行程</h2>
        </div>
        <div class="seg">
          <button
            v-for="t in galleryTabs"
            :key="t.key"
            type="button"
            class="seg-btn"
            :class="{ active: activeTab === t.key }"
            @click="activeTab = t.key"
          >{{ t.label }}</button>
        </div>
      </div>
      <p class="gallery-hint">{{ galleryTabs.find((t) => t.key === activeTab)?.hint }}</p>

      <a-spin :spinning="galleryLoading">
        <a-alert v-if="galleryError" type="warning" show-icon :message="galleryError" style="margin-bottom: 16px" />
        <a-empty
          v-if="!galleryLoading && !galleryLists[activeTab].length"
          :description="activeTab === 'starred'
            ? '还没有收藏，去结果页点亮星星吧'
            : activeTab === 'recent'
              ? '还没有规划过行程，从上方表单开始吧'
              : '暂无示例作品'"
        />
        <div class="gallery-grid">
          <article v-for="trip in galleryLists[activeTab]" :key="trip.id" class="trip-card" @click="openTrip(trip.id)">
            <div class="trip-cover">
              <img
                v-if="coverSrc(trip.cover_url) && !brokenCovers[trip.id]"
                :src="coverSrc(trip.cover_url)!"
                :alt="trip.destination"
                loading="lazy"
                @error="brokenCovers[trip.id] = true"
              />
              <div v-else class="trip-cover-fallback"><AppIcon name="mountain-snow" :size="40" color="#93bfa6" /></div>
              <span class="trip-days">{{ trip.days }} 天</span>
              <span v-if="trip.starred" class="trip-star"><AppIcon name="star" :size="15" color="#e89b3c" filled /></span>
            </div>
            <div class="trip-body">
              <div class="trip-title-row">
                <span class="trip-dest">{{ trip.destination }}</span>
                <span class="trip-budget">{{ trip.grand_total ? `¥${trip.grand_total.toLocaleString('zh-CN')}` : '预算待定' }}</span>
              </div>
              <div class="trip-themes">
                <span v-for="t in trip.themes" :key="t" class="ttag">{{ t }}</span>
              </div>
              <p class="trip-summary">{{ trip.summary }}</p>
              <div class="trip-footer">
                <span class="trip-date">{{ fmtDate(trip.created_at) }}</span>
                <a-button
                  v-if="activeTab === 'recent'"
                  size="small"
                  type="text"
                  danger
                  @click.stop="removeTrip(trip.id)"
                >删除</a-button>
              </div>
            </div>
          </article>
        </div>
      </a-spin>
    </section>

    <footer class="home-foot">© 2026 智能旅行助手 · 多智能体框架驱动</footer>
  </div>
</template>

<style scoped>
.home {
  min-height: 100vh;
  background:
    radial-gradient(1100px 460px at 12% -6%, rgba(220, 235, 225, 0.9) 0%, transparent 62%),
    radial-gradient(900px 420px at 96% 2%, rgba(253, 241, 220, 0.85) 0%, transparent 58%),
    var(--bg);
  padding-bottom: 56px;
}

/* ---------- 顶部导航 ---------- */
.nav {
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(10px);
  background: rgba(243, 246, 241, 0.85);
  border-bottom: 1px solid rgba(228, 233, 226, 0.8);
}

.nav-inner {
  max-width: 1160px;
  margin: 0 auto;
  padding: 12px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.nav-brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.brand-mark {
  width: 36px;
  height: 36px;
  border-radius: 11px;
  background: linear-gradient(135deg, var(--brand-600), var(--brand-500));
  box-shadow: 0 4px 10px rgba(47, 114, 84, 0.28);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
}

.brand-text {
  font-size: 17px;
  font-weight: 700;
  color: var(--brand-900);
}

.user-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 4px 14px 4px 5px;
  box-shadow: var(--shadow-sm);
}

.avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--brand-600);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.uname {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-700);
}

.logout {
  font-size: 12px;
  color: var(--ink-400);
}

.logout:hover {
  color: var(--coral-500);
}

.logout.danger {
  color: var(--coral-500);
}

/* ---------- Hero ---------- */
.hero {
  max-width: 820px;
  margin: 0 auto;
  text-align: center;
  padding: 72px 24px 44px;
}

.hero-title {
  font-size: 46px;
  line-height: 1.3;
  font-weight: 800;
  letter-spacing: 0.01em;
  color: var(--ink-900);
  margin: 18px 0 20px;
  white-space: nowrap;
}

.hl {
  position: relative;
  color: var(--brand-600);
  white-space: nowrap;
}

.hl::after {
  content: '';
  position: absolute;
  left: 2px;
  right: 2px;
  bottom: 4px;
  height: 12px;
  background: rgba(242, 179, 76, 0.45);
  border-radius: 6px;
  z-index: -1;
}

.hero-sub {
  font-size: 16px;
  line-height: 1.9;
  color: var(--ink-500);
  max-width: 600px;
  margin: 0 auto 26px;
}

.agent-chips {
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 10px;
}

.agent-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--brand-700);
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid var(--brand-100);
  border-radius: 999px;
  padding: 6px 16px;
  box-shadow: var(--shadow-sm);
}

/* ---------- 规划搜索卡 ---------- */
.planner {
  max-width: 980px;
  margin: 0 auto;
  padding: 0 24px;
}

.planner-card {
  background: var(--card);
  border: 1px solid rgba(228, 233, 226, 0.9);
  border-radius: var(--radius-xl);
  padding: 30px 34px 26px;
  box-shadow: var(--shadow-lg);
}

.bar-grid {
  display: grid;
  grid-template-columns: 1.5fr 1.2fr 0.9fr 1.1fr;
}

.bar-field {
  position: relative;
  padding: 2px 22px;
  border-left: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.bar-field:first-child {
  border-left: none;
  padding-left: 0;
}

.bar-field:last-child {
  padding-right: 0;
}

.bar-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--ink-400);
  margin-bottom: 2px;
}

.bar-field :deep(.ant-input),
.bar-field :deep(.ant-picker),
.bar-field :deep(.ant-input-number),
.bar-field :deep(.ant-select-selector) {
  border: none !important;
  box-shadow: none !important;
  background: transparent !important;
  padding-left: 0 !important;
  font-weight: 600;
  color: var(--ink-900);
  font-size: 15px;
}

.bar-field :deep(.ant-picker),
.bar-field :deep(.ant-select),
.bar-field :deep(.ant-input-number) {
  width: 100% !important;
}

.bar-field :deep(.ant-select-selection-placeholder),
.bar-field :deep(.ant-input::placeholder),
.bar-field :deep(.ant-picker-input > input::placeholder),
.bar-field :deep(.ant-input-number-input-wrap input::placeholder) {
  font-weight: 400;
  color: var(--ink-300);
}

.unit-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.unit-wrap :deep(.ant-input-number) {
  width: 100%;
}

.unit {
  position: absolute;
  right: 30px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-400);
  pointer-events: none;
}

/* 步进按钮常驻显示（默认仅悬停出现），为右侧预留固定 22px 列，避免与单位文字重叠 */
.unit-wrap :deep(.ant-input-number-handler-wrap) {
  opacity: 1 !important;
  background: transparent;
  border-inline-start: 1px solid var(--line);
}

.unit-wrap :deep(.ant-input-number-handler) {
  color: var(--ink-400);
  border-color: var(--line);
}

.unit-wrap :deep(.ant-input-number-input) {
  padding-right: 36px;
}

/* ---------- 选项区 ---------- */
.opt-area {
  margin-top: 22px;
  padding-top: 20px;
  border-top: 1px dashed var(--line);
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.opt-group {
  display: flex;
  align-items: flex-start;
  gap: 18px;
}

.opt-label {
  flex-shrink: 0;
  width: 100px;
  font-size: 13px;
  font-weight: 700;
  color: var(--ink-700);
  line-height: 34px;
  white-space: nowrap;
}

.opt-optional {
  font-style: normal;
  font-size: 11px;
  font-weight: 400;
  color: var(--ink-300);
  margin-left: 2px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip {
  height: 34px;
  padding: 0 16px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink-700);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

.chip:hover {
  border-color: var(--brand-300);
  color: var(--brand-700);
}

.chip.on {
  background: var(--brand-600);
  border-color: var(--brand-600);
  color: #fff;
  font-weight: 600;
  box-shadow: 0 3px 10px rgba(47, 114, 84, 0.28);
}

.notes-input {
  flex: 1;
  border-radius: 12px;
}

.opt-input {
  flex: 1;
  min-width: 0;
  max-width: 480px;
  border-radius: 12px;
}

.notes-input :deep(textarea) {
  background: #f7f9f5;
  border-radius: 12px;
}

.notes-input:focus-within {
  box-shadow: 0 0 0 3px var(--brand-100);
}

/* ---------- 底部 CTA ---------- */
.bar-footer {
  margin-top: 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.footer-hint {
  margin: 0;
  font-size: 13px;
  color: var(--ink-400);
}

.cta {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-width: 280px;
  height: 52px;
  border: none;
  border-radius: 999px;
  background: linear-gradient(135deg, var(--brand-600), var(--brand-500));
  color: #fff;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0.02em;
  cursor: pointer;
  box-shadow: 0 10px 24px rgba(47, 114, 84, 0.34);
  transition: transform 0.15s, box-shadow 0.2s, filter 0.2s;
}

.cta:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 14px 30px rgba(47, 114, 84, 0.42);
  filter: brightness(1.05);
}

.cta:disabled {
  opacity: 0.65;
  cursor: not-allowed;
}

/* ---------- 加载弹窗 ---------- */
.loading-body {
  text-align: center;
  padding: 16px 0 8px;
}

.loading-progress {
  margin-top: 18px;
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

/* ---------- 精选行程 ---------- */
.gallery {
  max-width: 1160px;
  margin: 64px auto 0;
  padding: 0 24px;
}

.gallery-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px 24px;
  flex-wrap: wrap;
}

.gallery-title {
  font-size: 30px;
  font-weight: 800;
  color: var(--ink-900);
  margin: 8px 0 0;
}

.seg {
  display: inline-flex;
  background: #e9efe7;
  border-radius: 999px;
  padding: 4px;
}

.seg-btn {
  height: 36px;
  padding: 0 20px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--ink-500);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.seg-btn.active {
  background: #fff;
  color: var(--brand-700);
  box-shadow: var(--shadow-sm);
}

.gallery-hint {
  color: var(--ink-400);
  font-size: 13px;
  margin: 10px 0 20px;
}

.gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 22px;
}

.trip-card {
  background: var(--card);
  border: 1px solid rgba(228, 233, 226, 0.9);
  border-radius: var(--radius-lg);
  overflow: hidden;
  cursor: pointer;
  box-shadow: var(--shadow-sm);
  transition: transform 0.2s, box-shadow 0.25s;
}

.trip-card:hover {
  transform: translateY(-5px);
  box-shadow: var(--shadow-md);
}

.trip-cover {
  position: relative;
  height: 168px;
  background: linear-gradient(135deg, var(--brand-100), var(--amber-100));
}

.trip-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.trip-cover-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 44px;
}

.trip-days {
  position: absolute;
  left: 12px;
  top: 12px;
  background: rgba(22, 53, 42, 0.82);
  color: #fff;
  border-radius: 999px;
  padding: 3px 11px;
  font-size: 12px;
  font-weight: 600;
  backdrop-filter: blur(4px);
}

.trip-star {
  position: absolute;
  right: 12px;
  top: 12px;
  background: rgba(255, 255, 255, 0.94);
  border-radius: 50%;
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  box-shadow: var(--shadow-sm);
}

.trip-body {
  padding: 16px 18px 14px;
}

.trip-title-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
}

.trip-dest {
  font-size: 18px;
  font-weight: 700;
  color: var(--ink-900);
}

.trip-budget {
  color: var(--amber-700);
  font-weight: 700;
  font-size: 14px;
  white-space: nowrap;
}

.trip-themes {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin: 9px 0 7px;
}

.ttag {
  font-size: 12px;
  line-height: 20px;
  padding: 0 8px;
  border-radius: 6px;
  background: var(--brand-50);
  color: var(--brand-700);
}

.trip-summary {
  color: var(--ink-500);
  font-size: 13px;
  line-height: 1.65;
  margin: 0 0 12px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.trip-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-top: 1px dashed var(--line);
  padding-top: 10px;
}

.trip-date {
  color: var(--ink-300);
  font-size: 12px;
}

.home-foot {
  text-align: center;
  margin-top: 72px;
  color: var(--ink-300);
  font-size: 12px;
  letter-spacing: 0.06em;
}

/* ---------- 响应式 ---------- */
@media (max-width: 860px) {
  .hero {
    padding-top: 52px;
  }
  .hero-title {
    font-size: 30px;
    white-space: normal;
  }
  .planner-card {
    padding: 22px 18px 20px;
    border-radius: var(--radius-lg);
  }
  .bar-grid {
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }
  .bar-field {
    border-left: none;
    padding: 10px 14px;
    background: #f7f9f5;
    border-radius: var(--radius-md);
  }
  .bar-field:first-child {
    padding-left: 14px;
  }
  .bar-field:last-child {
    padding-right: 14px;
  }
  .opt-group {
    flex-direction: column;
    gap: 10px;
  }
  .opt-label {
    line-height: 1.4;
  }
  .bar-footer {
    flex-direction: column;
    align-items: stretch;
  }
  .cta {
    width: 100%;
    min-width: 0;
  }
}

@media (max-width: 640px) {
  .gallery {
    padding: 0 16px;
  }
  .gallery-grid {
    grid-template-columns: 1fr;
  }
  .bar-grid {
    grid-template-columns: 1fr;
  }
}
</style>
