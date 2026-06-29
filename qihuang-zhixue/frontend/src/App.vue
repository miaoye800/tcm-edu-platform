<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

type HealthResponse = {
  status: string
  service: string
}

const loading = ref(false)
const healthResult = ref<HealthResponse | null>(null)

const checkHealth = async (): Promise<void> => {
  loading.value = true

  try {
    const response = await fetch('/health')

    if (!response.ok) {
      throw new Error(`后端响应异常：${response.status}`)
    }

    healthResult.value = (await response.json()) as HealthResponse
  } catch (error) {
    const message = error instanceof Error ? error.message : '健康检查失败'
    ElMessage.error(message)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="page-shell">
    <section class="workspace">
      <div class="intro">
        <p class="eyebrow">中医学个性化学习多智能体系统</p>
        <h1>岐黄智学</h1>
      </div>

      <el-button type="primary" :loading="loading" @click="checkHealth">
        调用后端 /health
      </el-button>

      <el-alert
        v-if="healthResult"
        class="result"
        title="后端连接成功"
        type="success"
        :closable="false"
      >
        <pre>{{ JSON.stringify(healthResult, null, 2) }}</pre>
      </el-alert>
    </section>
  </main>
</template>
