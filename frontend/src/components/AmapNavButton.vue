<script setup lang="ts">
import { ref } from 'vue'
import { resolvePoi } from '@/services/api'
import { buildNavUrl, buildSearchUrl, openExternal } from '@/utils/amapNav'
import type { Location } from '@/types'
import AppIcon from './AppIcon.vue'

/**
 * 卡片导航按钮：有坐标直接跳高德 URI API 导航（PC 网页版 / 手机唤起 APP）；
 * 无坐标（餐厅 / 老数据）先实时解析 POI，失败再降级为高德网页搜索。
 */
const props = defineProps<{
  name: string
  location?: Location | null
  city?: string
}>()

const resolving = ref(false)

async function go() {
  if (props.location) {
    openExternal(buildNavUrl(props.name, props.location))
    return
  }
  resolving.value = true
  try {
    const poi = await resolvePoi(props.name, props.city ?? '')
    openExternal(buildNavUrl(poi.name, { longitude: poi.longitude, latitude: poi.latitude }))
  } catch {
    openExternal(buildSearchUrl(props.name, props.city ?? ''))
  } finally {
    resolving.value = false
  }
}
</script>

<template>
  <a-tooltip title="在高德地图中导航">
    <a-button size="small" type="text" class="nav-btn no-export" :loading="resolving" @click="go">
      <AppIcon v-if="!resolving" name="car" :size="13" color="#3f8765" />
      导航
    </a-button>
  </a-tooltip>
</template>

<style scoped>
.nav-btn {
  padding: 0 4px;
  font-size: 12px;
  color: #3f8765;
}

.nav-btn:hover {
  color: #275c45 !important;
}
</style>
