import { createRouter, createWebHistory } from 'vue-router'
import { getAppStatus, getAccessCode } from '@/services/api'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/Home.vue'),
    },
    {
      path: '/result',
      name: 'result',
      component: () => import('@/views/Result.vue'),
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/Login.vue'),
    },
  ],
})

// 访问码守卫：后端开启 APP_PASSWORD 后，无凭证访问任何页面先去登录。
// health 免鉴权可安全探测；后端不可达时放行，由页面自身的请求报错。
router.beforeEach(async (to) => {
  if (to.name === 'login') return true
  try {
    const status = await getAppStatus()
    if (status.auth_required && !getAccessCode()) return { name: 'login' }
  } catch {
    /* 后端不可达时放行 */
  }
  return true
})

export default router
