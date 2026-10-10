/**
 * 与后端 app/models/schemas.py 一一对应的 TypeScript 类型定义。
 * Optional[...] 对应 `?`。
 */

export interface Location {
  longitude: number
  latitude: number
}

export interface Attraction {
  name: string
  description: string
  location: Location
  address: string
  duration: string
  ticket_price: number
  // 是否需购票（批量 C 起；老数据可能没有）
  has_ticket?: boolean
  ticket_from?: string
  nav_url?: string
  recommended_reason: string
  image_url?: string | null
  // 后端确定性调度器填写的钟点；老数据可能没有
  start_time?: string
  end_time?: string
  open_time_text?: string
}

export interface Meal {
  type: 'breakfast' | 'lunch' | 'dinner'
  restaurant: string
  cuisine: string
  specialty: string
  cost: number
  cost_note?: string
  image_url?: string | null
  // 后端图片增强时由 POI 搜索富化；老数据可能没有（导航时前端实时解析兜底）
  location?: Location | null
}

export interface Hotel {
  name: string
  location?: Location | null
  address: string
  price_per_night: number
  price_from?: number | null
  price_source?: string
  rating: number
  hotel_type: string
  image_url?: string | null
}

export interface Weather {
  date: string
  day_temp: number
  night_temp: number
  condition: string
}

export interface Budget {
  attraction_total: number
  hotel_total: number
  meal_total: number
  transport_total: number
  grand_total: number
}

export interface DayPlan {
  day: number
  date: string
  theme: string
  attractions: Attraction[]
  backup_attractions?: Attraction[]
  meals: Meal[]
  hotel?: Hotel | null
  weather?: Weather | null
  daily_budget: number
}

export interface TripPlan {
  destination: string
  days: number
  summary: string
  daily_plans: DayPlan[]
  budget?: Budget | null
  tips: string[]
  budget_note?: string
  demo: boolean
  trip_id?: number | null
  warnings?: string[]
  parent_id?: number | null
  amap_map_url?: string
}

export interface Feedback {
  target: 'attraction' | 'hotel' | 'meal'
  name: string
  reason: string
}

export interface TripSummary {
  id: number
  destination: string
  days: number
  grand_total?: number | null
  starred: boolean
  is_seed: boolean
  created_at: string
  cover_url?: string | null
  themes: string[]
  summary: string
}

export interface TripDetail {
  id: number
  starred: boolean
  is_seed: boolean
  shared: boolean
  created_at: string
  request: TripRequest
  plan: TripPlan
}

export interface TripRequest {
  destination: string
  start_date: string
  days: number
  budget?: number | null
  preferences: string[]
  group_type: string
  notes: string
  arrival_slot?: '' | 'morning' | 'afternoon' | 'evening'
  departure_slot?: '' | 'morning' | 'afternoon' | 'evening'
  pace?: 'easy' | 'standard' | 'packed'
  must_see?: string[]
  avoid?: string[]
  transit_mode?: 'walk' | 'transit' | 'taxi' | 'drive'
  origin?: string
}

export interface AppConfig {
  amap_js_key: string
  amap_js_secret: string
  demo_mode: boolean
}

export interface GeocodeResult {
  longitude: number
  latitude: number
  formatted_address: string
}

// ---------- 每日真实路线（/api/trip/history/{id}/routes） ----------

export interface RouteLeg {
  from: string
  to: string
  polyline: [number, number][]
  distance: number | null
  duration: number | null
  taxi_cost: number | null
}

export interface DayRoute {
  day: number
  theme?: string
  distance_m: number
  duration_s: number
  taxi_cost: number
  legs: RouteLeg[]
}

export interface TripRoutes {
  routes: DayRoute[]
}
