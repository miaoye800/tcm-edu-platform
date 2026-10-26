<template>
  <section class="story-stage" :class="[`is-${asset.backdrop}`, `focus-${asset.staticFallback.focus}`, `treatment-${asset.staticFallback.treatment}`]" :aria-label="scene.title">
    <div v-if="isStatic" class="stage-light" aria-hidden="true" />
    <video v-if="shouldRenderVideo" ref="videoElement" :key="`${asset.video?.src}-${scene.sceneId}`" class="story-video" :class="{ 'is-ready': mediaState === 'video-ready' }" :src="asset.video?.src" :poster="asset.video?.poster" :autoplay="!paused && !holdAtEnd" :muted="muted" playsinline preload="metadata" aria-label="剧情演出视频" @canplay="onVideoReady" @timeupdate="onVideoTimeUpdate" @error="onVideoError" />
    <div v-if="isStatic" class="apothecary-shelves" aria-hidden="true"><i v-for="index in 18" :key="index" /></div>
    <div v-if="isStatic" class="stage-figure" aria-hidden="true"><span /><span /><span /></div>
    <div class="video-status" :class="{ 'is-fallback': isStatic }" role="status">{{ statusCopy }}</div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storyAssetForStage, type StoryVideoFixtureMode } from '../../config/apprenticeStoryAssets'
import { deriveStoryMediaState, mediaStatusCopy, readInitialReducedMotionPreference, reducedMotionQuery, shouldRenderStoryVideo } from './storyStageMedia'
import type { ApprenticeScene } from '../../config/apprenticeStory'

const props = defineProps<{ scene: ApprenticeScene; videoFixture?: StoryVideoFixtureMode; muted?: boolean; paused?: boolean; holdAtEnd?: boolean; pauseAfterSeconds?: number }>()
const emit = defineEmits<{ quizIntroComplete: []; 'playback-time': [seconds: number] }>()
const reducedMotion = ref(readInitialReducedMotionPreference())
const didFail = ref(false)
const didLoad = ref(false)
const videoElement = ref<HTMLVideoElement | null>(null)
let motionQuery: MediaQueryList | null = null
let didCompleteQuizIntro = false
let quizIntroFallbackTimer: ReturnType<typeof setTimeout> | undefined

const asset = computed(() => storyAssetForStage(props.scene.sceneId, props.videoFixture ?? null))
const mediaState = computed(() => deriveStoryMediaState(asset.value.video, reducedMotion.value, didFail.value, didLoad.value))
const shouldRenderVideo = computed(() => shouldRenderStoryVideo(asset.value.video, reducedMotion.value, didFail.value))
const isStatic = computed(() => !['video-loading', 'video-ready'].includes(mediaState.value))
const statusCopy = computed(() => mediaStatusCopy(mediaState.value))
const resetMedia = () => {
  didFail.value = false
  didLoad.value = false
  didCompleteQuizIntro = false
  if (quizIntroFallbackTimer) clearTimeout(quizIntroFallbackTimer)
  quizIntroFallbackTimer = undefined
}
const applyPlaybackState = () => {
  const video = videoElement.value
  if (!video) return
  if (props.holdAtEnd) { video.pause(); return }
  if (props.paused) { video.pause(); return }
  void video.play().catch(() => undefined)
}
const completeQuizIntro = () => {
  if (props.pauseAfterSeconds === undefined || didCompleteQuizIntro) return
  didCompleteQuizIntro = true
  if (quizIntroFallbackTimer) clearTimeout(quizIntroFallbackTimer)
  videoElement.value?.pause()
  emit('quizIntroComplete')
}
const onVideoTimeUpdate = (event: Event) => emit('playback-time', (event.currentTarget as HTMLVideoElement).currentTime)
const onVideoReady = () => {
  didLoad.value = true
  const video = videoElement.value
  if (props.holdAtEnd) {
    if (Number.isFinite(video?.duration) && video && video.duration > 0) video.currentTime = Math.max(0, video.duration - 0.05)
    video?.pause()
    return
  }
  if (!video || props.pauseAfterSeconds === undefined || props.paused) { applyPlaybackState(); return }
  const pauseAtQuestion = () => {
    if (video.currentTime < props.pauseAfterSeconds!) return
    video.removeEventListener('timeupdate', pauseAtQuestion)
    completeQuizIntro()
  }
  video.addEventListener('timeupdate', pauseAtQuestion)
  void video.play().catch(completeQuizIntro)
}
const onVideoError = () => { didFail.value = true; completeQuizIntro() }
const syncMotionPreference = () => { reducedMotion.value = Boolean(motionQuery?.matches) }

watch(() => [props.scene.sceneId, props.videoFixture], resetMedia)
watch(() => [props.paused, props.holdAtEnd], applyPlaybackState)
onMounted(() => {
  if (props.pauseAfterSeconds !== undefined) {
    if (!shouldRenderVideo.value) completeQuizIntro()
    else quizIntroFallbackTimer = setTimeout(completeQuizIntro, 8000)
  }
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
  motionQuery = window.matchMedia(reducedMotionQuery)
  syncMotionPreference()
  motionQuery.addEventListener('change', syncMotionPreference)
})
onBeforeUnmount(() => {
  if (quizIntroFallbackTimer) clearTimeout(quizIntroFallbackTimer)
  motionQuery?.removeEventListener('change', syncMotionPreference)
  const video = videoElement.value
  if (video) video.pause()
})
</script>

<style scoped lang="scss">
.story-stage { position:relative; display:grid; min-height:100%; isolation:isolate; overflow:hidden; color:var(--qh-text); background:var(--qh-color-night); }
.story-stage::before { position:absolute; inset:0; z-index:-3; background:linear-gradient(90deg, var(--qh-color-ink), transparent 48%, var(--qh-color-ink)); content:""; }
.story-video { position:absolute; inset:0; z-index:-2; width:100%; height:100%; object-fit:contain; object-position:center; opacity:0; transition:opacity var(--qh-duration-md) ease; }
.story-video.is-ready { opacity:1; }
.stage-light { position:absolute; top:-30%; left:33%; width:36%; aspect-ratio:1; border-radius:50%; background:var(--qh-color-gold); filter:blur(96px); opacity:.17; }
.apothecary-shelves { position:absolute; inset:13% 0 12% auto; display:grid; grid-template-columns:repeat(3,minmax(38px,1fr)); gap:5px; width:46%; opacity:.55; transform:perspective(600px) rotateY(-11deg); }
.apothecary-shelves i { min-height:34px; border:1px solid var(--qh-color-paper-line); background:var(--qh-color-night-soft); box-shadow:inset 0 -8px var(--qh-color-ink); }
.stage-figure { position:absolute; right:16%; bottom:0; display:grid; width:min(29vw,330px); height:70%; align-content:end; opacity:.7; }
.stage-figure span:first-child { justify-self:center; width:22%; aspect-ratio:1; border:1px solid var(--qh-color-cream-line); border-radius:50%; background:var(--qh-color-night-raised); }
.stage-figure span:nth-child(2) { justify-self:center; width:58%; height:49%; border-radius:48% 48% 6px 6px; background:var(--qh-color-night-raised); }
.stage-figure span:last-child { justify-self:center; width:94%; height:13%; border-radius:50%; background:var(--qh-color-ink); filter:blur(10px); }
.is-memory { filter:saturate(.48); }.is-memory .stage-light { left:18%; background:var(--qh-color-blue); }.is-counter .stage-figure { display:none; }.is-counter .apothecary-shelves { inset:18% 0 auto auto; }.is-shelves .stage-light,.treatment-jade .stage-light { left:63%; background:var(--qh-color-jade); }.focus-stairs .stage-figure { right:5%; transform:rotate(12deg); }.focus-lanterns .stage-light { top:4%; left:58%; opacity:.28; }
.video-status { display:none; }
@media (max-width:700px) { .apothecary-shelves { width:62%; }.stage-figure { right:8%; width:48vw; }.video-status { top:58px; right:var(--qh-space-3); font-size:11px; } }
@media (prefers-reduced-motion: reduce) { .story-video { display:none; } }
</style>
