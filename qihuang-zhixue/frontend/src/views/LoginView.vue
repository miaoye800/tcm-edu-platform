<template>
  <main class="login-view">
    <section class="login-stage">
      <div class="brand-panel">
        <img :src="qihuangLogo" alt="岐黄智学" />
        <p class="eyebrow">中国软件杯 A3</p>
        <h1>岐黄智学</h1>
        <p>个性化中药学学习多智能体系统</p>
      </div>

      <GlassCard class="login-card" padding="lg">
        <div class="card-head">
          <div>
            <p class="eyebrow">账号入口</p>
            <h2>{{ mode === 'login' ? '登录' : '注册' }}</h2>
          </div>
          <el-segmented v-model="mode" :options="modeOptions" />
        </div>

        <el-form label-position="top" @submit.prevent="submit">
          <el-form-item label="用户名">
            <el-input v-model="form.username" autocomplete="username" placeholder="disciple_a" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              autocomplete="current-password"
              placeholder="demo1234"
              show-password
              type="password"
            />
          </el-form-item>
          <el-form-item v-if="mode === 'register'" label="显示名">
            <el-input v-model="form.displayName" placeholder="我的中药学档案" />
          </el-form-item>
          <el-button class="submit-button" native-type="submit" type="primary" :loading="auth.loading">
            {{ mode === 'login' ? '登录' : '创建账号' }}
          </el-button>
        </el-form>

        <div class="quick-row">
          <el-button :icon="User" :loading="auth.loading" @click="guestLogin">游客一键体验</el-button>
          <el-select v-model="preset" class="preset-select" placeholder="预置账号快速登录" @change="quickLogin">
            <el-option
              v-for="item in presets"
              :key="item.username"
              :label="item.label"
              :value="item.username"
            />
          </el-select>
        </div>

        <el-alert v-if="auth.error" type="warning" :title="auth.error" show-icon :closable="false" />
      </GlassCard>
    </section>
  </main>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User } from '@element-plus/icons-vue'
import qihuangLogo from '../assets/brand/qihuang-logo.png'
import GlassCard from '../components/ui/GlassCard.vue'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const mode = ref<'login' | 'register'>('login')
const preset = ref('')
const form = reactive({
  username: '',
  password: '',
  displayName: '',
})

const modeOptions = [
  { label: '登录', value: 'login' },
  { label: '注册', value: 'register' },
]

const presets = [
  { label: '弟子甲 · 清热待补', username: 'disciple_a', password: 'demo1234' },
  { label: '弟子乙 · 补虚待补', username: 'disciple_b', password: 'demo1234' },
  { label: '弟子丙 · 均衡样例', username: 'disciple_c', password: 'demo1234' },
]

const goAfterLogin = async () => {
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
  await router.replace(redirect)
}

const submit = async () => {
  try {
    if (mode.value === 'login') {
      await auth.login(form.username, form.password)
    } else {
      await auth.register(form.username, form.password, form.displayName)
    }
    await goAfterLogin()
  } catch {
    ElMessage.warning(auth.error || '账号操作失败')
  }
}

const guestLogin = async () => {
  try {
    await auth.guest()
    await goAfterLogin()
  } catch {
    ElMessage.warning(auth.error || '游客登录失败')
  }
}

const quickLogin = async (username: string) => {
  const item = presets.find((option) => option.username === username)
  if (!item) return
  form.username = item.username
  form.password = item.password
  try {
    await auth.login(item.username, item.password)
    await goAfterLogin()
  } catch {
    ElMessage.warning('请先在后端运行 python -m app.auth.seed 初始化预置账号')
  } finally {
    preset.value = ''
  }
}
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.login-view {
  display: grid;
  min-height: 100vh;
  place-items: center;
  padding: 24px;
  background:
    linear-gradient(135deg, rgba(22, 42, 38, 0.94), rgba(19, 31, 36, 0.9)),
    var(--qh-page-bg);
}

.login-stage {
  display: grid;
  grid-template-columns: minmax(280px, 0.9fr) minmax(360px, 460px);
  align-items: stretch;
  width: min(1040px, 100%);
  gap: 18px;
}

.brand-panel {
  @include glass-panel(28px);
  display: grid;
  align-content: center;
  min-height: 520px;
  padding: 34px;
}

.brand-panel img {
  width: 76px;
  height: 76px;
  object-fit: contain;
}

.eyebrow {
  margin: 20px 0 8px;
  color: var(--qh-color-jade);
  font-size: 13px;
  font-weight: 900;
}

h1,
h2 {
  margin: 0;
  font-family: var(--qh-font-display);
  font-weight: 900;
  line-height: 1.08;
}

h1 {
  @include text-sheen;
  font-size: clamp(46px, 7vw, 76px);
}

h2 {
  color: var(--qh-text);
  font-size: 32px;
}

.brand-panel p:last-child {
  margin: 14px 0 0;
  color: var(--qh-text-muted);
  font-size: 16px;
  line-height: 1.8;
}

.login-card {
  align-self: center;
}

.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 22px;
}

.submit-button {
  width: 100%;
  min-height: 42px;
  font-weight: 900;
}

.quick-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  margin: 16px 0;
}

.preset-select {
  min-width: 0;
}

@media (max-width: 860px) {
  .login-stage,
  .quick-row {
    grid-template-columns: 1fr;
  }

  .brand-panel {
    min-height: auto;
  }
}
</style>
