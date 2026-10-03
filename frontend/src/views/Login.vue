<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import axios from 'axios'
import {
  getAppStatus,
  loginUser,
  registerUser,
  setAccessCode,
} from '@/services/api'

const router = useRouter()
// 后端 health 探测的登录形态：user = 账号密码；password = 访问码
const mode = ref<'user' | 'password'>('password')
const activeTab = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const accessCode = ref('')
const loading = ref(false)

onMounted(async () => {
  try {
    const status = await getAppStatus()
    mode.value = status.auth_mode === 'user' ? 'user' : 'password'
  } catch {
    /* 后端不可达时保持默认形态 */
  }
})

async function submitUser() {
  if (!username.value || !password.value || loading.value) return
  loading.value = true
  try {
    if (activeTab.value === 'register') {
      const r = await registerUser(username.value, password.value)
      message.success(`欢迎，${r.username}！`)
    } else {
      await loginUser(username.value, password.value)
    }
    router.replace('/')
  } catch (err) {
    message.error((err as Error).message || '操作失败')
  } finally {
    loading.value = false
  }
}

async function submitCode() {
  if (!accessCode.value || loading.value) return
  loading.value = true
  try {
    // 用原始 axios 探测（绕过全局拦截器的 401 跳转）：能通过鉴权中间件即密码正确
    await axios.get('/api/trip/history', {
      params: { limit: 1 },
      headers: { 'X-Access-Code': accessCode.value },
      timeout: 10000,
    })
    setAccessCode(accessCode.value)
    router.replace('/')
  } catch (err: unknown) {
    const status = (err as { response?: { status?: number } })?.response?.status
    message.error(status === 401 ? '访问密码不正确' : '服务暂不可用，请稍后再试')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <a-card class="login-card">
      <template #title>🌍 智能旅行助手</template>

      <template v-if="mode === 'user'">
        <a-tabs v-model:active-key="activeTab">
          <a-tab-pane key="login" tab="登录" />
          <a-tab-pane key="register" tab="注册账号" />
        </a-tabs>
        <a-input
          v-model:value="username"
          placeholder="用户名（3-32 位字母/数字/下划线）"
          size="large"
          @press-enter="submitUser"
        />
        <a-input-password
          v-model:value="password"
          placeholder="密码（至少 6 位）"
          size="large"
          class="login-gap"
          @press-enter="submitUser"
        />
        <a-button type="primary" block size="large" :loading="loading" class="login-btn" @click="submitUser">
          {{ activeTab === 'register' ? '注册并进入' : '登录' }}
        </a-button>
      </template>

      <template v-else>
        <p class="login-tip">本站已开启访问保护，请输入访问密码继续</p>
        <a-input-password
          v-model:value="accessCode"
          placeholder="访问密码"
          size="large"
          @press-enter="submitCode"
        />
        <a-button type="primary" block size="large" :loading="loading" class="login-btn" @click="submitCode">
          进入
        </a-button>
      </template>
    </a-card>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(160deg, #e8f4fd 0%, #f5f6fa 100%);
}
.login-card {
  width: 360px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
}
.login-tip {
  color: #888;
  margin-bottom: 12px;
}
.login-gap {
  margin-top: 12px;
}
.login-btn {
  margin-top: 16px;
}
</style>
