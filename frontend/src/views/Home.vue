<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  deleteTrip,
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

const router = useRouter()

const destinationSuggestions = ['北京', '上海', '西安', '成都', '杭州', '重庆', '广州', '三亚', '厦门', '青岛']
const preferenceOptions = ['人文历史', '自然风光', '美食探店', '购物血拼', '亲子游玩', '网红打卡', '休闲度假', '博物馆控']
const groupOptions = ['独自出行', '情侣出游', '家庭亲子', '朋友结伴']

const today = new Date().toISOString().slice(0, 10)

const form = reactive<TripRequest>({
  destination: '',
  start_date: today,
  days: 3,
  budget: null,
  preferences: [],
  group_type: '情侣出游',
  notes: '',
})

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
  { key: 'seed', label: '✨ 示例作品', hint: '内置的精选行程案例' },
  { key: 'starred', label: '⭐ 我的收藏', hint: '结果页点星收藏的好规划' },
  { key: 'recent', label: '🕘 最近规划', hint: '你最近生成的行程' },
]
const activeTab = ref<HistoryFilter>('seed')
const galleryLists = reactive<Record<HistoryFilter, TripSummary[]>>({
  recent: [], starred: [], seed: [],
})
const galleryLoading = ref(false)
const galleryError = ref('')

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
  } else {
    form.preferences.push(tag)
  }
}

function disabledDate(current: Date) {
  return current && current.getTime() < new Date(today).getTime()
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
</script>

<template>
  <div class="home">
    <div class="hero">
      <div v-if="username" class="user-chip">
        👤 {{ username }}
        <a class="user-logout" @click="handleLogout">退出</a>
      </div>
      <h1 class="hero-title">🧭 智能旅行助手</h1>
      <p class="hero-subtitle">
        基于 HelloAgents 多智能体框架 · 景点搜索 / 天气查询 / 酒店推荐 / 行程规划
        四个智能体为你量身定制旅行计划
      </p>
    </div>

    <div class="form-card">
      <a-form layout="vertical" size="large">
        <a-row :gutter="16">
          <a-col :xs="24" :md="10">
            <a-form-item label="目的地" required>
              <a-auto-complete
                v-model:value="form.destination"
                :options="destinationSuggestions.map((d) => ({ value: d }))"
                placeholder="想去哪座城市？如：北京"
                allow-clear
              />
            </a-form-item>
          </a-col>
          <a-col :xs="24" :md="7">
            <a-form-item label="出发日期" required>
              <a-date-picker
                v-model:value="form.start_date"
                value-format="YYYY-MM-DD"
                :disabled-date="disabledDate"
                style="width: 100%"
              />
            </a-form-item>
          </a-col>
          <a-col :xs="12" :md="4">
            <a-form-item label="行程天数">
              <a-input-number v-model:value="form.days" :min="1" :max="7" style="width: 100%" addon-after="天" />
            </a-form-item>
          </a-col>
          <a-col :xs="12" :md="3">
            <a-form-item label="总预算(元)">
              <a-input-number v-model:value="form.budget" :min="0" :step="500" style="width: 100%" placeholder="选填" />
            </a-form-item>
          </a-col>
        </a-row>

        <a-form-item label="出行类型">
          <a-radio-group v-model:value="form.group_type" option-type="button" :options="groupOptions" />
        </a-form-item>

        <a-form-item label="旅行偏好（可多选）">
          <div class="pref-tags">
            <a-checkable-tag
              v-for="tag in preferenceOptions"
              :key="tag"
              :checked="form.preferences.includes(tag)"
              @change="togglePreference(tag)"
            >
              {{ tag }}
            </a-checkable-tag>
          </div>
        </a-form-item>

        <a-form-item label="特殊要求（选填）">
          <a-textarea
            v-model:value="form.notes"
            placeholder="例如：不想太赶、希望多安排博物馆、有老人同行节奏放慢……"
            :rows="3"
          />
        </a-form-item>

        <a-button type="primary" size="large" block :loading="submitting" class="submit-btn" @click="handleSubmit">
          ✨ 开始规划我的旅行
        </a-button>
      </a-form>
    </div>

    <a-modal :open="submitting" :closable="false" :keyboard="false" :mask-closable="false" :footer="null" centered>
      <div class="loading-body">
        <a-spin size="large" />
        <a-progress
          :percent="progressPercent"
          :show-info="false"
          status="active"
          class="loading-progress"
        />
        <p class="loading-title">{{ loadingTitle }}</p>
        <p class="loading-hint">任务在后台执行，通常需要 10~90 秒；中途关闭页面行程也不会丢</p>
      </div>
    </a-modal>

    <!-- 精选行程（作品集） -->
    <section class="gallery">
      <div class="gallery-head">
        <h2 class="gallery-title">🗓 精选行程</h2>
        <a-tabs v-model:active-key="activeTab" size="large">
          <a-tab-pane v-for="t in galleryTabs" :key="t.key" :tab="t.label" />
        </a-tabs>
        <p class="gallery-hint">{{ galleryTabs.find((t) => t.key === activeTab)?.hint }}</p>
      </div>

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
          <div v-for="trip in galleryLists[activeTab]" :key="trip.id" class="trip-card" @click="openTrip(trip.id)">
            <div class="trip-cover">
              <img v-if="coverSrc(trip.cover_url)" :src="coverSrc(trip.cover_url)!" :alt="trip.destination" loading="lazy" />
              <div v-else class="trip-cover-fallback">🏞️</div>
              <span class="trip-days">{{ trip.days }} 天</span>
              <span v-if="trip.starred" class="trip-star">⭐</span>
            </div>
            <div class="trip-body">
              <div class="trip-title-row">
                <span class="trip-dest">{{ trip.destination }}</span>
                <span class="trip-budget">{{ trip.grand_total ? `¥${trip.grand_total.toLocaleString('zh-CN')}` : '预算待定' }}</span>
              </div>
              <div class="trip-themes">
                <a-tag v-for="t in trip.themes" :key="t" color="blue" style="margin-inline-end: 0">{{ t }}</a-tag>
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
          </div>
        </div>
      </a-spin>
    </section>
  </div>
</template>

<style scoped>
.home {
  min-height: 100vh;
  background:
    radial-gradient(1200px 500px at 20% -10%, #e6f4ff 0%, transparent 60%),
    radial-gradient(1000px 500px at 90% 0%, #fff7e6 0%, transparent 55%),
    #f5f7fa;
  padding: 48px 16px 64px;
}

.hero {
  text-align: center;
  margin-bottom: 32px;
  position: relative;
}

.user-chip {
  position: absolute;
  top: 0;
  right: 0;
  color: #595959;
  font-size: 13px;
  background: #fff;
  border: 1px solid #e8ecf2;
  border-radius: 16px;
  padding: 4px 12px;
  box-shadow: 0 1px 4px rgba(31, 45, 88, 0.08);
}

.user-logout {
  margin-left: 8px;
  color: #1677ff;
}

.hero-title {
  font-size: 40px;
  margin: 0 0 12px;
  font-weight: 700;
  background: linear-gradient(90deg, #1677ff, #722ed1);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.hero-subtitle {
  color: #595959;
  font-size: 15px;
  max-width: 640px;
  margin: 0 auto;
  line-height: 1.8;
}

.form-card {
  max-width: 860px;
  margin: 0 auto;
  background: #fff;
  border-radius: 16px;
  padding: 32px 32px 24px;
  box-shadow: 0 8px 32px rgba(31, 45, 88, 0.08);
}

.pref-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.submit-btn {
  height: 48px;
  font-size: 16px;
  margin-top: 8px;
}

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
}

.loading-hint {
  color: #8c8c8c;
  font-size: 13px;
  margin: 0;
}

/* ---------- 精选行程（作品集） ---------- */
.gallery {
  max-width: 1080px;
  margin: 48px auto 0;
}

.gallery-head {
  display: flex;
  align-items: center;
  gap: 8px 24px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.gallery-title {
  font-size: 24px;
  font-weight: 700;
  margin: 0;
  white-space: nowrap;
}

.gallery-head :deep(.ant-tabs) {
  flex: 1;
  min-width: 320px;
}

.gallery-head :deep(.ant-tabs-nav) {
  margin: 0;
}

.gallery-hint {
  color: #8c8c8c;
  font-size: 13px;
  margin: -6px 0 20px;
}

.gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 20px;
}

.trip-card {
  background: #fff;
  border-radius: 14px;
  overflow: hidden;
  cursor: pointer;
  box-shadow: 0 4px 18px rgba(31, 45, 88, 0.07);
  transition:
    transform 0.2s,
    box-shadow 0.2s;
}

.trip-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 10px 28px rgba(31, 45, 88, 0.14);
}

.trip-cover {
  position: relative;
  height: 150px;
  background: linear-gradient(135deg, #e6f4ff, #f9f0ff);
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
  font-size: 40px;
}

.trip-days {
  position: absolute;
  left: 10px;
  top: 10px;
  background: rgba(22, 119, 255, 0.92);
  color: #fff;
  border-radius: 6px;
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 600;
}

.trip-star {
  position: absolute;
  right: 10px;
  top: 10px;
  background: rgba(255, 255, 255, 0.92);
  border-radius: 50%;
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
}

.trip-body {
  padding: 14px 16px 12px;
}

.trip-title-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.trip-dest {
  font-size: 17px;
  font-weight: 700;
}

.trip-budget {
  color: #cf1322;
  font-weight: 600;
  font-size: 14px;
}

.trip-themes {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin: 8px 0 6px;
}

.trip-themes :deep(.ant-tag) {
  font-size: 12px;
  line-height: 18px;
  padding: 0 6px;
}

.trip-summary {
  color: #595959;
  font-size: 13px;
  line-height: 1.6;
  margin: 0 0 10px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.trip-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.trip-date {
  color: #bfbfbf;
  font-size: 12px;
}

@media (max-width: 640px) {
  .hero-title {
    font-size: 28px;
  }
  .form-card {
    margin: 0 12px;
    padding: 16px;
  }
  .gallery {
    padding: 0 12px;
  }
  .gallery-grid {
    grid-template-columns: 1fr;
  }
}
</style>
