<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import axios from 'axios'
import { setAccessCode } from '@/services/api'

const router = useRouter()
const password = ref('')
const loading = ref(false)

async function submit() {
  if (!password.value || loading.value) return
  loading.value = true
  try {
    // 用原始 axios 探测（绕过全局拦截器的 401 跳转）：能通过鉴权中间件即密码正确，
    // 路由层的 503（如 MySQL 不可用）不影响密码校验结果
    await axios.get('/api/trip/history', {
      params: { limit: 1 },
      headers: { 'X-Access-Code': password.value },
      timeout: 10000,
    })
    setAccessCode(password.value)
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
      <p class="login-tip">本站已开启访问保护，请输入访问密码继续</p>
      <a-input-password
        v-model:value="password"
        placeholder="访问密码"
        size="large"
        @press-enter="submit"
      />
      <a-button type="primary" block size="large" :loading="loading" class="login-btn" @click="submit">
        进入
      </a-button>
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
.login-btn {
  margin-top: 16px;
}
</style>
