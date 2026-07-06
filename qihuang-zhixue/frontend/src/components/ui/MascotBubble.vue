<template>
  <aside class="mascot-dock" :class="{ 'is-open': assistant.open }">
    <button class="pet-button" type="button" aria-label="唤起学习助手小岐" @click="assistant.toggle">
      <span class="pet-shadow" />
      <img :src="xiaoqiFull" alt="学习助手小岐" />
      <span class="pet-ledge" />
      <span class="live-dot" />
      <span class="pet-tip">小岐</span>
    </button>

    <section v-if="assistant.open" class="pet-panel" aria-live="polite">
      <div class="pet-panel__head">
        <strong>学习助手·小岐</strong>
        <button type="button" aria-label="收起小岐" @click="assistant.hide">
          <el-icon><Close /></el-icon>
        </button>
      </div>
      <p>学习助手能力正在接入中。后续这里会承载真实答疑、复盘和资源推荐。</p>
      <button class="pet-action" type="button" disabled>敬请期待</button>
    </section>
  </aside>
</template>

<script setup lang="ts">
import { Close } from '@element-plus/icons-vue'
import xiaoqiFull from '../../assets/mascot/xiaoqi-full.png'
import { useAssistantStore } from '../../stores/assistant'

const assistant = useAssistantStore()
</script>

<style scoped lang="scss">
@use "../../styles/tokens.scss" as *;

.mascot-dock {
  position: fixed;
  right: 18px;
  bottom: 14px;
  z-index: 40;
  display: grid;
  justify-items: end;
  gap: 12px;
  pointer-events: none;
}

.pet-button,
.pet-panel {
  pointer-events: auto;
}

.pet-button {
  position: relative;
  display: grid;
  width: 116px;
  height: 112px;
  place-items: center;
  overflow: hidden;
  border: 0;
  border-radius: 28px 28px 0 0;
  background: transparent;
  cursor: pointer;
  filter: drop-shadow(0 18px 24px rgba(0, 0, 0, 0.38));
  transition:
    width 220ms ease,
    height 220ms ease,
    transform 220ms ease,
    filter 220ms ease;
  animation: bob 5.6s ease-in-out infinite;
}

.pet-button:focus-visible {
  @include focus-ring;
}

.pet-button:hover,
.pet-button:focus-visible,
.is-open .pet-button {
  width: 164px;
  height: 206px;
  overflow: visible;
  transform: translateY(-6px);
  filter: drop-shadow(0 26px 36px rgba(0, 0, 0, 0.46));
}

.pet-button img {
  position: absolute;
  right: -12px;
  bottom: -44px;
  z-index: 1;
  width: 132px;
  height: auto;
  object-fit: contain;
  transform-origin: 50% 80%;
  transition:
    right 220ms ease,
    bottom 220ms ease,
    width 220ms ease;
  animation: wave 9s ease-in-out infinite;
}

.pet-button:hover img,
.pet-button:focus-visible img,
.is-open .pet-button img {
  right: 2px;
  bottom: 8px;
  width: 158px;
}

.pet-shadow {
  position: absolute;
  right: 8px;
  bottom: 2px;
  z-index: 0;
  width: 104px;
  height: 20px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.34);
  filter: blur(7px);
}

.pet-ledge {
  position: absolute;
  right: 2px;
  bottom: 0;
  z-index: 2;
  width: 112px;
  height: 42px;
  border: 1px solid rgba(143, 208, 173, 0.22);
  border-bottom: 0;
  border-radius: 24px 24px 0 0;
  background:
    linear-gradient(180deg, rgba(33, 50, 45, 0.9), rgba(17, 29, 32, 0.86)),
    rgba(12, 25, 24, 0.8);
  box-shadow: var(--qh-inner-highlight);
  backdrop-filter: blur(14px);
}

.live-dot {
  position: absolute;
  right: 12px;
  top: 28px;
  z-index: 3;
  width: 10px;
  height: 10px;
  border: 2px solid rgba(17, 29, 32, 0.9);
  border-radius: 50%;
  background: var(--qh-color-jade);
  animation: live 1.8s ease-out infinite;
}

.pet-tip {
  position: absolute;
  right: 42px;
  bottom: 10px;
  z-index: 3;
  color: var(--qh-color-jade);
  font-size: 12px;
  font-weight: 900;
  opacity: 0.92;
  transition: opacity 160ms ease;
}

.pet-button:hover .pet-tip,
.pet-button:focus-visible .pet-tip,
.is-open .pet-tip {
  opacity: 0;
}

.pet-panel {
  @include glass-panel(22px);
  order: -1;
  width: min(330px, calc(100vw - 32px));
  padding: 16px;
  transform-origin: right bottom;
}

.pet-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.pet-panel__head strong {
  font-family: var(--qh-font-display);
  font-size: 17px;
  font-weight: 900;
}

.pet-panel__head button {
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  border-radius: 10px;
  color: var(--qh-text);
  background: rgba(238, 245, 239, 0.08);
  cursor: pointer;
}

.pet-panel p {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.8;
}

.pet-action {
  width: 100%;
  min-height: 40px;
  margin-top: 14px;
  border-radius: var(--qh-radius-sm);
  color: #10201d;
  font-weight: 900;
  background: linear-gradient(135deg, var(--qh-color-jade), var(--qh-color-blue));
  cursor: pointer;
}

.pet-action:disabled {
  cursor: not-allowed;
  opacity: 0.72;
}

@media (max-width: 720px) {
  .mascot-dock {
    right: 10px;
    bottom: 8px;
  }

  .pet-button {
    width: 96px;
    height: 96px;
  }

  .pet-button img {
    right: -10px;
    bottom: -38px;
    width: 112px;
  }

  .pet-button:hover,
  .pet-button:focus-visible,
  .is-open .pet-button {
    width: 138px;
    height: 178px;
  }

  .pet-button:hover img,
  .pet-button:focus-visible img,
  .is-open .pet-button img {
    width: 136px;
  }
}
</style>
