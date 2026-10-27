<template>
  <main class="story-kit">
    <header class="story-kit__head">
      <p class="story-kit__kicker">收徒剧情 · 组件画廊</p>
      <h1 class="story-kit__title">所有组件 × 所有状态</h1>
      <p class="story-kit__note">
        这一页是视觉契约的可执行版本。改这里能看到全局效果；验收只看这一页，不用逐个场景点。
      </p>
    </header>

    <section class="story-kit__section">
      <h2 class="story-kit__label">舞台 StoryStageFrame</h2>
      <div class="story-kit__stage-demo">
        <StoryStageFrame :scene="demoScene" muted :paused="false" />
      </div>
    </section>

    <section class="story-kit__section">
      <h2 class="story-kit__label">名字牌 StoryNamePlate</h2>
      <div class="story-kit__row">
        <StoryNamePlate name="药庐师尊" tone="master" />
        <StoryNamePlate name="弟子" tone="disciple" />
        <StoryNamePlate name="旁白" tone="narration" />
      </div>
    </section>

    <section class="story-kit__section">
      <h2 class="story-kit__label">对话框 StoryDialogueBox</h2>
      <div class="story-kit__dialog-demo">
        <StoryDialogueBox
          name="药庐师尊"
          text="你且看看，这四张身份牌，哪一张像你？选定之后，药圃的门就为你开了。"
          :speed-ms="18"
        />
      </div>
    </section>

    <section class="story-kit__section">
      <h2 class="story-kit__label">选项药丸 StoryChoicePill</h2>
      <div class="story-kit__pills">
        <StoryChoicePill label="默认态" />
        <StoryChoicePill label="选中态" state="selected" />
        <StoryChoicePill label="提交中" state="committing" />
        <StoryChoicePill label="禁用态" state="disabled" disabled />
        <StoryChoicePill label="自己作答" icon="brush" />
      </div>
    </section>

    <section class="story-kit__section">
      <h2 class="story-kit__label">选项栈 StoryChoiceStack</h2>
      <div class="story-kit__stack-demo">
        <StoryChoiceStack :options="demoOptions" label="身份回答" selected-id="clinic" @select="onPick" />
      </div>
    </section>

    <section class="story-kit__section">
      <h2 class="story-kit__label">弟子气泡 StoryDiscipleBubble</h2>
      <div class="story-kit__stack-demo">
        <StoryDiscipleBubble placeholder="说说你的判断：这味药是哪一样功效……" :autofocus="false" />
      </div>
    </section>

    <section class="story-kit__section">
      <h2 class="story-kit__label">标本卡 StorySpecimenCard（认药态 · 不显药名）</h2>
      <div class="story-kit__row">
        <StorySpecimenCard image="/story/herbs/jinyinhua.jpg" :index="1" :total="5" />
        <StorySpecimenCard :index="2" :total="5" />
      </div>
    </section>

    <section class="story-kit__section">
      <h2 class="story-kit__label">章节条 StoryChapterBar</h2>
      <div class="story-kit__stage-demo is-short">
        <StoryChapterBar chapter="二 · 识药　药圃初认" meta="药圃图鉴 01 / 05" @exit="onExit" />
      </div>
    </section>

    <section class="story-kit__section">
      <h2 class="story-kit__label">纸面表单 StoryPaperForm</h2>
      <StoryPaperForm kicker="师嘱" title="处方笺" prompt="依病例写下你的抓药方，方中每一味都会留下可追溯的记录。">
        <p class="story-kit__placeholder">屏 3 / 屏 5 的内容占位</p>
      </StoryPaperForm>
    </section>

    <section class="story-kit__section">
      <h2 class="story-kit__label">提示 StoryToast</h2>
      <div class="story-kit__stage-demo is-short">
        <StoryToast :message="toastMessage" />
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import StoryStageFrame from '../components/story/StoryStageFrame.vue'
import StoryNamePlate from '../components/story/StoryNamePlate.vue'
import StoryDialogueBox from '../components/story/StoryDialogueBox.vue'
import StoryChoicePill from '../components/story/StoryChoicePill.vue'
import StoryChoiceStack, { type StoryChoiceOption } from '../components/story/StoryChoiceStack.vue'
import StoryDiscipleBubble from '../components/story/StoryDiscipleBubble.vue'
import StorySpecimenCard from '../components/story/StorySpecimenCard.vue'
import StoryChapterBar from '../components/story/StoryChapterBar.vue'
import StoryPaperForm from '../components/story/StoryPaperForm.vue'
import StoryToast from '../components/story/StoryToast.vue'
import { apprenticeScenes } from '../config/apprenticeStory'

const demoScene = apprenticeScenes[0]
const toastMessage = ref('已记录你的选择')
const demoOptions: StoryChoiceOption[] = [
  { id: 'exam', label: '考前冲刺', index: '甲' },
  { id: 'clinic', label: '临床学徒', index: '乙' },
  { id: 'curious', label: '草木好奇者', index: '丙' },
  { id: 'note', label: '自己说说', icon: 'brush' },
]

const onPick = (id: string) => {
  toastMessage.value = `选中 ${id}`
}

const onExit = () => {
  toastMessage.value = '演示环境，不执行跳转'
}
</script>

<style scoped lang="scss">
.story-kit {
  min-height: 100dvh;
  padding: var(--qh-space-10) var(--qh-space-8) 96px;
  background: var(--story-stage);
  color: var(--story-cream);
  font-family: var(--qh-font-body);
}

.story-kit__head {
  max-width: 1200px;
  margin: 0 auto var(--qh-space-10);
}

.story-kit__kicker {
  margin: 0 0 var(--qh-space-2);
  color: var(--story-gold);
  font-size: var(--qh-text-footnote);
  letter-spacing: 0.16em;
}

.story-kit__title {
  margin: 0 0 var(--qh-space-3);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-display);
  font-weight: 600;
  letter-spacing: 0.04em;
}

.story-kit__note {
  max-width: 640px;
  margin: 0;
  color: var(--story-cream-soft);
  font-size: var(--qh-text-ui);
  line-height: 1.8;
  opacity: 0.72;
}

.story-kit__section {
  max-width: 1200px;
  margin: 0 auto var(--qh-space-10);
  padding-top: var(--qh-space-6);
  border-top: 1px solid var(--story-stage-rail);
}

.story-kit__label {
  margin: 0 0 var(--qh-space-4);
  color: var(--story-cream-soft);
  font-size: var(--qh-text-footnote);
  font-weight: 400;
  letter-spacing: 0.12em;
}

.story-kit__row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: var(--qh-space-4);
}

.story-kit__pills {
  display: flex;
  flex-wrap: wrap;
  gap: var(--qh-space-3);
}

.story-kit__stage-demo {
  position: relative;
  height: 260px;
  overflow: hidden;
  border-radius: var(--qh-radius-panel);
  background: var(--story-stage);
}

.story-kit__stage-demo.is-short {
  height: 120px;
}

.story-kit__dialog-demo {
  max-width: 720px;
  padding-top: var(--qh-space-6);
}

.story-kit__stack-demo {
  display: flex;
  max-width: 420px;
  flex-direction: column;
  gap: var(--qh-space-2);
}

.story-kit__placeholder {
  margin: 0;
  padding: var(--qh-space-6);
  border-radius: var(--qh-radius-media);
  background: var(--story-cream-soft);
  color: var(--story-ink-faint);
  font-size: var(--qh-text-footnote);
  text-align: center;
}
</style>
