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
import AppIcon from '@/components/AppIcon.vue'

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
  <div class="login-page">
    <!-- 左侧品牌视觉面板 -->
    <aside class="visual">
      <svg class="visual-art" viewBox="0 0 600 900" preserveAspectRatio="xMidYMax slice" aria-hidden="true">
        <circle cx="452" cy="238" r="86" fill="#f2b34c" opacity="0.95" />
        <circle cx="452" cy="238" r="130" fill="#f2b34c" opacity="0.18" />
        <polygon points="0,760 170,520 320,760" fill="#0f2b21" opacity="0.9" />
        <polygon points="200,760 400,470 620,760" fill="#16352a" opacity="0.9" />
        <polygon points="60,760 220,580 400,760" fill="#1f4636" opacity="0.85" />
        <rect y="748" width="600" height="152" fill="#0f2b21" opacity="0.92" />
      </svg>

      <div class="visual-body">
        <div class="brand-row">
          <span class="brand-mark"><AppIcon name="compass" :size="20" color="#fff" /></span>
          <span class="brand-name">智能旅行助手</span>
        </div>
        <h1 class="visual-title">把攻略交给智能体，<br />把时间留给风景。</h1>
        <p class="visual-sub">四个智能体协作，为你规划一场省心的旅行。</p>

        <ul class="agent-list">
          <li><span class="agent-ico"><AppIcon name="mountain" :size="16" color="rgba(255,255,255,0.92)" /></span>景点搜索 Agent，挖掘真实值得一去的目的地</li>
          <li><span class="agent-ico"><AppIcon name="cloud-sun" :size="16" color="rgba(255,255,255,0.92)" /></span>天气查询 Agent，避开坏天气安排节奏</li>
          <li><span class="agent-ico"><AppIcon name="hotel" :size="16" color="rgba(255,255,255,0.92)" /></span>酒店推荐 Agent，匹配预算与位置</li>
          <li><span class="agent-ico"><AppIcon name="calendar-days" :size="16" color="rgba(255,255,255,0.92)" /></span>行程规划 Agent，串起每一天的动线</li>
        </ul>
      </div>
      <p class="visual-foot">多智能体框架驱动</p>
    </aside>

    <!-- 右侧表单 -->
    <main class="panel">
      <div class="form-wrap">
        <template v-if="mode === 'user'">
          <p class="form-eyebrow">WELCOME BACK</p>
          <h2 class="form-title">{{ activeTab === 'login' ? '欢迎回来' : '创建你的账号' }}</h2>
          <p class="form-sub">{{ activeTab === 'login' ? '登录后继续规划你的下一场旅行' : '注册一个账号，行程自动云端保存' }}</p>

          <div class="seg">
            <button
              type="button"
              class="seg-btn"
              :class="{ active: activeTab === 'login' }"
              @click="activeTab = 'login'"
            >登录</button>
            <button
              type="button"
              class="seg-btn"
              :class="{ active: activeTab === 'register' }"
              @click="activeTab = 'register'"
            >注册账号</button>
          </div>

          <div class="field">
            <label class="field-label">用户名</label>
            <a-input
              v-model:value="username"
              placeholder="3-32 位字母 / 数字 / 下划线"
              size="large"
              @press-enter="submitUser"
            />
          </div>
          <div class="field">
            <label class="field-label">密码</label>
            <a-input-password
              v-model:value="password"
              placeholder="至少 6 位"
              size="large"
              @press-enter="submitUser"
            />
          </div>

          <button type="button" class="cta" :disabled="loading" @click="submitUser">
            <span v-if="!loading">{{ activeTab === 'register' ? '注册并进入' : '登录' }} →</span>
            <span v-else>正在进入…</span>
          </button>
        </template>

        <template v-else>
          <p class="form-eyebrow">PRIVATE ACCESS</p>
          <h2 class="form-title">访问验证</h2>
          <p class="form-sub">本站已开启访问保护，请输入访问密码继续</p>
          <div class="field">
            <label class="field-label">访问密码</label>
            <a-input-password
              v-model:value="accessCode"
              placeholder="访问密码"
              size="large"
              @press-enter="submitCode"
            />
          </div>
          <button type="button" class="cta" :disabled="loading" @click="submitCode">
            <span v-if="!loading">进入 →</span>
            <span v-else>正在验证…</span>
          </button>
        </template>
      </div>
      <p class="panel-foot">© 2026 智能旅行助手</p>
    </main>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  grid-template-columns: minmax(420px, 46%) 1fr;
  background: var(--bg);
}

/* ---------- 左侧视觉面板 ---------- */
.visual {
  position: relative;
  overflow: hidden;
  background: linear-gradient(175deg, #1f4636 0%, #16352a 58%, #0f2b21 100%);
  color: #fff;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  padding: 56px 56px 36px;
}

.visual-art {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.visual-body {
  position: relative;
  max-width: 460px;
}

.brand-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: auto;
}

.brand-mark {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.18);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
}

.brand-name {
  font-weight: 600;
  letter-spacing: 0.06em;
}

.visual-title {
  font-size: 38px;
  line-height: 1.32;
  font-weight: 700;
  margin: 0 0 14px;
}

.visual-sub {
  color: rgba(255, 255, 255, 0.72);
  font-size: 15px;
  margin: 0 0 34px;
}

.agent-list {
  list-style: none;
  margin: 0;
  padding: 26px 0 0;
  border-top: 1px solid rgba(255, 255, 255, 0.14);
  display: flex;
  flex-direction: column;
  gap: 14px;
  font-size: 14px;
  color: rgba(255, 255, 255, 0.85);
}

.agent-list li {
  display: flex;
  align-items: center;
  gap: 12px;
}

.agent-ico {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.16);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
}

.visual-foot {
  position: relative;
  margin: 40px 0 0;
  font-size: 12px;
  letter-spacing: 0.14em;
  color: rgba(255, 255, 255, 0.45);
}

/* ---------- 右侧表单 ---------- */
.panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
}

.form-wrap {
  width: 100%;
  max-width: 400px;
}

.form-eyebrow {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.22em;
  color: var(--brand-500);
  margin: 0 0 10px;
}

.form-title {
  font-size: 30px;
  font-weight: 700;
  margin: 0 0 8px;
  color: var(--ink-900);
}

.form-sub {
  color: var(--ink-500);
  margin: 0 0 24px;
  font-size: 14px;
}

.seg {
  display: flex;
  background: #e9efe7;
  border-radius: 999px;
  padding: 4px;
  margin-bottom: 24px;
}

.seg-btn {
  flex: 1;
  height: 38px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--ink-500);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.seg-btn.active {
  background: #fff;
  color: var(--brand-700);
  box-shadow: var(--shadow-sm);
}

.field {
  margin-bottom: 16px;
}

.field-label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-700);
  margin-bottom: 6px;
}

.cta {
  width: 100%;
  height: 50px;
  margin-top: 10px;
  border: none;
  border-radius: 999px;
  background: linear-gradient(135deg, var(--brand-600), var(--brand-500));
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.02em;
  cursor: pointer;
  box-shadow: 0 8px 20px rgba(47, 114, 84, 0.32);
  transition: transform 0.15s, box-shadow 0.2s, filter 0.2s;
}

.cta:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 10px 26px rgba(47, 114, 84, 0.4);
  filter: brightness(1.05);
}

.cta:disabled {
  opacity: 0.65;
  cursor: not-allowed;
}

.panel-foot {
  margin-top: 40px;
  font-size: 12px;
  color: var(--ink-300);
}

/* ---------- 响应式 ---------- */
@media (max-width: 900px) {
  .login-page {
    grid-template-columns: 1fr;
  }
  .visual {
    display: none;
  }
}
</style>
