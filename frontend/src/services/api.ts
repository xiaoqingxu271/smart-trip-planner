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

// ---------- 异步规划任务：提交 → 轮询进度 → 取结果 ----------

export interface PlanStage {
  stage: string
  message?: string
  count?: number
}

export interface PlanJobState {
  job_id: string
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  stage?: string
  message?: string
  count?: number
  plan?: TripPlan
  error?: string
}

/** 提交异步规划任务。Redis 不可用时后端降级为同步执行（mode=sync 直接带结果）。 */
export async function submitPlanJob(
  data: TripRequest,
): Promise<{ mode: 'job'; job_id: string } | { mode: 'sync'; plan: TripPlan }> {
  return request.post('/trip/plan/async', data)
}

/**
 * 高层封装：提交 + 每 1.5s 轮询 + 阶段回调，成功返回行程。
 * 任务在后台执行，轮询期间关闭页面不影响服务端继续跑。
 */
export async function planJobPoll(data: TripRequest, onStage: (s: PlanStage) => void): Promise<TripPlan> {
  const sub = await submitPlanJob(data)
  if (sub.mode === 'sync') {
    onStage({ stage: 'done', message: '规划完成' })
    return sub.plan
  }

  const deadline = Date.now() + 10 * 60 * 1000
  let lastStage = ''
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 1500))
    let state: PlanJobState
    try {
      state = await request.get(`/trip/jobs/${sub.job_id}`)
    } catch {
      continue // 单次轮询失败（网络抖动/瞬时错误）继续重试
    }
    if (state.status === 'running' && state.stage && state.stage !== lastStage) {
      lastStage = state.stage
      onStage({ stage: state.stage, message: state.message, count: state.count })
    }
    if (state.status === 'succeeded') return state.plan as TripPlan
    if (state.status === 'failed') throw new Error(state.error || '规划失败')
  }
  throw new Error('规划超时（超过 10 分钟），任务仍在后台执行，完成后可在「最近规划」中查看')
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
