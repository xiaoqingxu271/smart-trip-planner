import type { Location } from '@/types'

/**
 * 高德 URI API 导航（官方 Web/APP 双端方案）：
 * - PC 固定网页版路线规划页（callnative=0），新标签页打开不丢行程页；
 * - 移动端 callnative=1 自动唤起高德 APP，未安装则由高德降级到网页/应用商店；
 * - 不传起点：由高德页面/APP 自行定位（PC 浏览器定位权限体验差）。
 * 行程坐标均来自高德 POI（GCJ-02），与 coordinate=gaode 匹配，无需转换。
 */
export function isMobileUA(): boolean {
  return /iPhone|iPad|iPod|Android|webOS|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)
}

export function buildNavUrl(name: string, loc: Location): string {
  const to = `${loc.longitude},${loc.latitude},${encodeURIComponent(name)}`
  return `https://uri.amap.com/navigation?to=${to}&mode=car&src=smart-trip-planner&coordinate=gaode&callnative=${isMobileUA() ? 1 : 0}`
}

/** 无坐标时的最终兜底：高德网页版搜索页（用户在结果中自行选择）。 */
export function buildSearchUrl(name: string, city: string): string {
  const params = new URLSearchParams({ query: name })
  if (city) params.set('city', city)
  return `https://www.amap.com/search?${params.toString()}`
}

/** PC 新标签页打开；移动端当前页跳转才能唤起 APP。 */
export function openExternal(url: string): void {
  if (isMobileUA()) window.location.href = url
  else window.open(url, '_blank')
}
