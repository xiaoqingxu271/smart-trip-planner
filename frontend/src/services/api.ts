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

// ---------- 会话 Token（AUTH_MODE=user 多用户模式；存 localStorage） ----------

const TOKEN_KEY = 'trip_token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? ''
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

request.interceptors.request.use((config) => {
  const code = getAccessCode()
  if (code) config.headers['X-Access-Code'] = code
  const token = getToken()
  if (token) config.headers['Authorization'] = `Bearer ${token}`
  console.log(`[API] → ${config.method?.toUpperCase()} ${config.url}`)
  return config
})

request.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const detail = error?.response?.data?.detail
    const message = typeof detail === 'string' ? detail : (detail && JSON.stringify(detail)) || error.message
    console.error(`[API] ✕ ${error?.config?.url}:`, message)
    // 访问码缺失/会话过期：清凭证回登录页（登录页自身的探测失败不跳转，由页面提示）
    if (error?.response?.status === 401 && !window.location.pathname.startsWith('/login')) {
      localStorage.removeItem(ACCESS_CODE_KEY)
      clearToken()
      window.location.href = '/login'
    }
    return Promise.reject(new Error(message))
  },
)

// ---------- 图片代理（<img> 无法带请求头，访问码/Token 走查询参数） ----------

export function imgProxy(url: string | null | undefined): string | null {
  if (!url) return null
  const params = new URLSearchParams({ u: url })
  const code = getAccessCode()
  if (code) params.set('code', code)
  const token = getToken()
  if (token) params.set('token', token)
  return `/api/utils/image?${params.toString()}`
}

// ---------- 应用状态（health 免鉴权，前端据此选择登录形态） ----------

export interface AppStatus {
  auth_required: boolean
  auth_mode: 'none' | 'user'
}

let appStatusCache: AppStatus | null = null

export async function getAppStatus(): Promise<AppStatus> {
  if (!appStatusCache) {
    appStatusCache = await request.get('/health')
  }
  return appStatusCache
}

// ---------- 注册 / 登录 / 会话 ----------

export interface AuthResult {
  token: string
  user_id: number
  username: string
}

export async function registerUser(username: string, password: string): Promise<AuthResult> {
  const r: AuthResult = await request.post('/auth/register', { username, password })
  setToken(r.token)
  return r
}

export async function loginUser(username: string, password: string): Promise<AuthResult> {
  const r: AuthResult = await request.post('/auth/login', { username, password })
  setToken(r.token)
  return r
}

export async function logoutUser(): Promise<void> {
  try {
    await request.post('/auth/logout')
  } finally {
    clearToken()
  }
}

export async function getMe(): Promise<{ user_id: number; username: string }> {
  return request.get('/auth/me')
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

// ---------- POI 名称解析（导航兜底：餐厅/酒店无坐标时点击实时查询） ----------

export interface PoiResult {
  name: string
  longitude: number
  latitude: number
  address?: string
}

export async function resolvePoi(keyword: string, city: string): Promise<PoiResult> {
  return request.get('/utils/poi', { params: { keyword, city } })
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

/** iCal 下载地址：<a>/window.open 无法带请求头，访问码/Token 走查询参数。 */
export function tripIcalUrl(id: number): string {
  const params = new URLSearchParams()
  const code = getAccessCode()
  if (code) params.set('code', code)
  const token = getToken()
  if (token) params.set('token', token)
  const qs = params.toString()
  return `/api/trip/history/${id}/ical${qs ? `?${qs}` : ''}`
}
