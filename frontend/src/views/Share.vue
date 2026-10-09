<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getShareDetail, imgProxy } from '@/services/api'
import { openExternal } from '@/utils/amapNav'
import type { TripPlan } from '@/types'
import AppIcon from '@/components/AppIcon.vue'
import AmapNavButton from '@/components/AmapNavButton.vue'

const route = useRoute()
const loading = ref(true)
const error = ref('')
const plan = ref<TripPlan | null>(null)

const MEAL_LABEL: Record<string, string> = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐' }

function money(n?: number | null): string {
  return `¥${(n ?? 0).toLocaleString('zh-CN')}`
}

function needTicket(t: number, has?: boolean): boolean {
  return has === true || (has === undefined && t > 0)
}

onMounted(async () => {
  try {
    plan.value = (await getShareDetail(String(route.params.id))).plan
  } catch (e: any) {
    error.value = e?.message || '分享内容加载失败'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="share-page">
    <a-spin :spinning="loading" tip="加载中…">
      <a-result v-if="error" status="warning" :title="error">
        <template #extra>
          <a-button @click="() => (window.location.href = '/')">回到首页</a-button>
        </template>
      </a-result>

      <div v-else-if="plan" class="share-body">
        <header class="share-header">
          <p class="eyebrow">SHARED TRIP</p>
          <h1>{{ plan.destination }} · {{ plan.days }} 天行程</h1>
          <p v-if="plan.summary" class="summary">{{ plan.summary }}</p>
          <div class="header-actions">
            <a-button
              v-if="plan.amap_map_url"
              type="primary"
              @click="openExternal(plan.amap_map_url)"
            >
              <AppIcon name="map" :size="14" color="#fff" /> 打开高德地图
            </a-button>
          </div>
        </header>

        <section v-for="day in plan.daily_plans" :key="day.day" class="day-card">
          <div class="day-title">
            <span class="day-badge">第 {{ day.day }} 天</span>
            <span v-if="day.date" class="day-date">{{ day.date }}</span>
            <span v-if="day.theme" class="day-theme">{{ day.theme }}</span>
          </div>

          <div class="poi-list">
            <div v-for="(a, i) in day.attractions" :key="i" class="poi">
              <img v-if="a.image_url" :src="imgProxy(a.image_url)!" :alt="a.name" class="poi-img" />
              <span v-else class="poi-ico"><AppIcon name="landmark" :size="20" color="#93bfa6" /></span>
              <div class="poi-info">
                <div class="poi-name">
                  {{ a.name }}
                  <a-tag v-if="needTicket(a.ticket_price, a.has_ticket)" color="orange">需门票</a-tag>
                  <a-tag v-else color="green">免费</a-tag>
                  <AmapNavButton :name="a.name" :location="a.location" :city="plan!.destination" />
                </div>
                <div v-if="a.description" class="poi-desc">{{ a.description }}</div>
              </div>
            </div>
          </div>

          <div class="meal-line">
            <span v-for="m in day.meals" :key="m.type" class="meal-chip">
              {{ MEAL_LABEL[m.type] || m.type }}：{{ m.restaurant }}
            </span>
          </div>

          <div v-if="day.hotel" class="hotel-line">
            <AppIcon name="hotel" :size="14" color="#3f8765" />
            {{ day.hotel.name }}<template v-if="day.hotel.price_from != null"> 起 ¥{{ day.hotel.price_from }}</template>
          </div>
        </section>

        <section v-if="plan.budget" class="budget-card">
          <p class="eyebrow">ESTIMATED COST</p>
          <h2>费用估算</h2>
          <p class="grand">估算总计 <b>{{ money(plan.budget.grand_total) }}</b></p>
          <p v-if="plan.budget_note" class="note">{{ plan.budget_note }}</p>
        </section>

        <footer class="share-footer">
          <a-button @click="() => (window.location.href = '/')">用「智能旅行助手」生成属于你的行程</a-button>
        </footer>
      </div>
    </a-spin>
  </div>
</template>

<style scoped>
.share-page {
  max-width: 720px;
  margin: 0 auto;
  padding: 24px 16px 48px;
  color: #22312b;
}
.share-header h1 {
  font-size: 26px;
  margin: 4px 0 8px;
}
.eyebrow {
  letter-spacing: 2px;
  font-size: 12px;
  color: #6b7d72;
  margin: 0;
}
.summary {
  color: #4a5d52;
  line-height: 1.6;
}
.header-actions {
  margin: 12px 0;
}
.day-card {
  background: #f6f8f6;
  border: 1px solid #e2e8e3;
  border-radius: 12px;
  padding: 16px;
  margin: 16px 0;
}
.day-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.day-badge {
  background: #275c45;
  color: #fff;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 13px;
}
.day-date,
.day-theme {
  color: #6b7d72;
  font-size: 13px;
}
.poi-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.poi {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.poi-img {
  width: 56px;
  height: 56px;
  border-radius: 8px;
  object-fit: cover;
}
.poi-ico {
  width: 56px;
  height: 56px;
  border-radius: 8px;
  background: #eef3ef;
  display: flex;
  align-items: center;
  justify-content: center;
}
.poi-name {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-weight: 600;
}
.poi-desc {
  color: #5a6b60;
  font-size: 13px;
  margin-top: 2px;
}
.meal-line {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.meal-chip {
  background: #fff;
  border: 1px solid #e2e8e3;
  border-radius: 6px;
  padding: 3px 8px;
  font-size: 12px;
  color: #4a5d52;
}
.hotel-line {
  margin-top: 10px;
  color: #3f8765;
  font-size: 13px;
}
.budget-card {
  background: #f6f8f6;
  border-radius: 12px;
  padding: 16px;
  margin: 16px 0;
}
.grand {
  font-size: 18px;
}
.note {
  color: #6b7d72;
  font-size: 13px;
}
.share-footer {
  text-align: center;
  margin-top: 24px;
}
</style>