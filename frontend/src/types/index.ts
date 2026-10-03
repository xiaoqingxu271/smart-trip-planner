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
  recommended_reason: string
  image_url?: string | null
}

export interface Meal {
  type: 'breakfast' | 'lunch' | 'dinner'
  restaurant: string
  cuisine: string
  specialty: string
  cost: number
  image_url?: string | null
}

export interface Hotel {
  name: string
  location?: Location | null
  address: string
  price_per_night: number
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
  demo: boolean
  trip_id?: number | null
  warnings?: string[]
  parent_id?: number | null
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
