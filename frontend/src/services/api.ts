import axios from 'axios'
import type { AppConfig, Feedback, GeocodeResult, TripDetail, TripPlan, TripRequest, TripRoutes, TripSummary } from '@/types'
/**
 * 多智能体流水线（景点搜索 → 天气 → 酒店 → 规划）通常需要 10-60 秒，
 * 超时设置为 2 分钟。
 */
const request = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

// ---------- 访问码（后端配置 APP_PASSWORD 后启用；存 localStorage） ----------

const ACCESS_CODE_KEY = 'trip_access_code'

export function getAccessCode(): string {
  return localStorage.getItem(ACCESS_CODE_KEY) ?? ''
}

export function setAccessCode(code: string): void {
  localStorage.setItem(ACCESS_CODE_KEY, code)
}

request.interceptors.request.use((config) => {
  const code = getAccessCode()
  if (code) config.headers['X-Access-Code'] = code
  console.log(`[API] → ${config.method?.toUpperCase()} ${config.url}`)
  return config
})

request.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const detail = error?.response?.data?.detail
    const message = typeof detail === 'string' ? detail : (detail && JSON.stringify(detail)) || error.message
    console.error(`[API] ✕ ${error?.config?.url}:`, message)
    // 访问码缺失/失效：清凭证回登录页（登录页自身的探测失败不跳转，由页面提示）
    if (error?.response?.status === 401 && !window.location.pathname.startsWith('/login')) {
      localStorage.removeItem(ACCESS_CODE_KEY)
      window.location.href = '/login'
    }
    return Promise.reject(new Error(message))
  },
)

// ---------- 图片代理（<img> 无法带请求头，访问码走查询参数） ----------

export function imgProxy(url: string | null | undefined): string | null {
  if (!url) return null
  const code = getAccessCode()
  const suffix = code ? `&code=${encodeURIComponent(code)}` : ''
  return `/api/utils/image?u=${encodeURIComponent(url)}${suffix}`
}

// ---------- 应用状态（health 免鉴权，前端据此探测是否需要访问码） ----------

let appStatusCache: { auth_required: boolean } | null = null

export async function getAppStatus(): Promise<{ auth_required: boolean }> {
  if (!appStatusCache) {
    appStatusCache = await request.get('/health')
  }
  return appStatusCache
}

export async function planTrip(data: TripRequest): Promise<TripPlan> {
  return request.post('/trip/plan', data)
}

// ---------- 流式规划（SSE）：按阶段推送真实进度 ----------

export interface PlanStage {
  stage: string
  message?: string
  count?: number
}

/**
 * POST + fetch 流读取的 SSE 消费：保留 JSON 请求体与访问码头，
 * 限流/锁冲突等错误在流开始前以普通 HTTP 状态码返回，可直接解析 detail。
 */
export async function planTripStream(
  data: TripRequest,
  onStage: (s: PlanStage) => void,
): Promise<TripPlan> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const code = getAccessCode()
  if (code) headers['X-Access-Code'] = code

  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 300000) // 5 分钟兜底
  let resp: Response
  try {
    resp = await fetch('/api/trip/plan/stream', {
      method: 'POST',
      headers,
      body: JSON.stringify(data),
      signal: controller.signal,
    })
  } catch (e) {
    window.clearTimeout(timeout)
    throw new Error('无法连接规划服务，请确认后端已启动')
  }

  if (!resp.ok || !resp.body) {
    window.clearTimeout(timeout)
    if (resp.status === 401) {
      localStorage.removeItem(ACCESS_CODE_KEY)
      if (!window.location.pathname.startsWith('/login')) window.location.href = '/login'
    }
    let detail = `HTTP ${resp.status}`
    try {
      const j = await resp.json()
      detail = typeof j.detail === 'string' ? j.detail : detail
    } catch { /* 非 JSON 错误体 */ }
    throw new Error(detail)
  }

  try {
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let plan: TripPlan | null = null
    let streamError: string | null = null

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const raw = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        const lines = raw.split('\n')
        const event = lines.find((l) => l.startsWith('event: '))?.slice(7) ?? 'message'
        const dataLine = lines.find((l) => l.startsWith('data: '))
        if (!dataLine) continue
        const payload = JSON.parse(dataLine.slice(6))
        if (event === 'progress') onStage(payload as PlanStage)
        else if (event === 'done') plan = payload.plan as TripPlan
        else if (event === 'error') streamError = String(payload.message ?? '规划失败')
      }
    }
    if (streamError) throw new Error(streamError)
    if (!plan) throw new Error('规划流提前结束，未收到结果')
    return plan
  } finally {
    window.clearTimeout(timeout)
  }
}

export async function getAppConfig(): Promise<AppConfig> {
  return request.get('/config')
}

export async function geocode(address: string, city = ''): Promise<GeocodeResult> {
  return request.post('/utils/geocode', { address, city })
}

// ---------- 历史与作品集 ----------

export type HistoryFilter = 'recent' | 'starred' | 'seed'

export async function getHistory(filter: HistoryFilter, limit = 12): Promise<TripSummary[]> {
  return request.get('/trip/history', { params: { filter, limit } })
}

export async function getTripDetail(id: number): Promise<TripDetail> {
  return request.get(`/trip/history/${id}`)
}

export async function starTrip(id: number, starred: boolean): Promise<void> {
  await request.post(`/trip/history/${id}/star`, { starred })
}

export async function updateTrip(id: number, plan: TripPlan): Promise<TripPlan> {
  return request.put(`/trip/history/${id}`, plan)
}

// 带约束重规划：把用户反馈（约满/没房/排队久…）交给智能体做最小改动，返回新版本
export async function replanTrip(body: { trip_id: number; feedbacks: Feedback[] }): Promise<TripPlan> {
  return request.post('/trip/replan', body)
}

// 一键启用 Plan B：把某天的主景点与备选对调
export async function swapBackup(id: number, body: { day: number; original: string; backup: string }): Promise<TripPlan> {
  return request.post(`/trip/history/${id}/swap-backup`, body)
}

export async function deleteTrip(id: number): Promise<void> {
  await request.delete(`/trip/history/${id}`)
}

// ---------- 每日真实路线 / iCal 导出 ----------

export async function getTripRoutes(id: number): Promise<TripRoutes> {
  return request.get(`/trip/history/${id}/routes`)
}

/** iCal 下载地址：<a>/window.open 无法带请求头，访问码走查询参数。 */
export function tripIcalUrl(id: number): string {
  const code = getAccessCode()
  return `/api/trip/history/${id}/ical${code ? `?code=${encodeURIComponent(code)}` : ''}`
}
