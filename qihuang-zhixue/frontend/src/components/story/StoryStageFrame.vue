<template>
  <div
    class="story-stage-frame"
    :class="[`is-${asset.backdrop}`, `treatment-${asset.staticFallback.treatment}`]"
    :aria-label="scene.title"
  >
    <video
      v-if="shouldRenderVideo"
      ref="videoElement"
      :key="asset.video?.src"
      class="story-stage-frame__video"
      :class="{ 'is-ready': mediaState === 'video-ready' }"
      :src="asset.video?.src"
      :poster="asset.video?.poster"
      :autoplay="!paused"
      :muted="muted"
      playsinline
      preload="metadata"
      aria-label="剧情演出视频"
      @canplay="onVideoReady"
      @timeupdate="onVideoTimeUpdate"
      @ended="onVideoEnded"
      @error="onVideoError"
    />

    <div v-if="isStatic" class="story-stage-frame__static" aria-hidden="true">
      <span class="story-stage-frame__pool" />
      <span class="story-stage-frame__cabinet">
        <i v-for="index in 9" :key="index" />
      </span>
    </div>

    <span class="story-stage-frame__veil" :class="{ 'is-on': resting }" aria-hidden="true" />
    <span class="story-stage-frame__vignette" aria-hidden="true" />
    <p class="story-sr" role="status">{{ statusCopy }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storyAssetForStage, type StoryVideoFixtureMode } from '../../config/apprenticeStoryAssets'
import type { ApprenticeScene } from '../../config/apprenticeStory'
import {
  deriveStoryMediaState,
  mediaStatusCopy,
  readInitialReducedMotionPreference,
  reducedMotionQuery,
  shouldRenderStoryVideo,
} from './storyStageMedia'

const props = defineProps<{
  scene: ApprenticeScene
  videoFixture?: StoryVideoFixtureMode
  muted?: boolean
  paused?: boolean
}>()
// `played` is false when the scene has no usable video (static fallback), so the
// parent can tell "the film finished" apart from "there was never a film".
const emit = defineEmits<{ videoEnded: [played: boolean]; 'playback-time': [seconds: number] }>()

const reducedMotion = ref(readInitialReducedMotionPreference())
const didFail = ref(false)
const didLoad = ref(false)
const didEnd = ref(false)
// Covers the seek back to the rest frame. The film has already faded to black by
// then, so the veil is invisible when it goes up; it only hides the instant the
// picture jumps from the black last frame to the readable one, then dissolves.
const resting = ref(false)
// The clip that has already played through. The prologue's three beats share one
// source, so a beat change must re-announce the end instead of restarting the shot.
const finishedSrc = ref('')
const videoElement = ref<HTMLVideoElement | null>(null)
let motionQuery: MediaQueryList | null = null

const asset = computed(() => storyAssetForStage(props.scene.sceneId, props.videoFixture ?? null))
const mediaState = computed(() => deriveStoryMediaState(asset.value.video, reducedMotion.value, didFail.value, didLoad.value))
const shouldRenderVideo = computed(() => shouldRenderStoryVideo(asset.value.video, reducedMotion.value, didFail.value))
const isStatic = computed(() => !['video-loading', 'video-ready'].includes(mediaState.value))
const statusCopy = computed(() => mediaStatusCopy(mediaState.value))

// The clock is only ever reported upward. Nothing here truncates a shot: every
// film plays to its own `ended` event, so the last line of dialogue is always
// heard before the interaction layer appears.
const announceEnded = (played: boolean) => {
  if (didEnd.value) return
  didEnd.value = true
  if (played) finishedSrc.value = asset.value.video?.src ?? ''
  emit('videoEnded', played)
}

const applyPlaybackState = () => {
  const video = videoElement.value
  if (!video) return
  // Once the shot is over the stage belongs to the question. A `canplay` after
  // the rest-frame seek must not be mistaken for "the film is ready" and replay
  // the whole chapter behind the answer.
  if (didEnd.value) {
    video.pause()
    return
  }
  if (props.paused) {
    video.pause()
    return
  }
  if (video.ended) return
  void video.play().catch(() => {
    // Autoplay with sound can be refused. A muted retry is always allowed, so the
    // film still runs instead of dropping the viewer straight onto the question.
    video.muted = true
    void video.play().catch(() => announceEnded(false))
  })
}

const onVideoTimeUpdate = (event: Event) => {
  emit('playback-time', (event.currentTarget as HTMLVideoElement).currentTime)
}

// Some chapters fade to black over their last second. The question is asked on
// this stage, so once the fade is done the shot is parked on its last readable
// frame - the film is never cut short, it just comes to rest somewhere useful.
const restFrame = computed(() => asset.value.video?.restFrameSeconds ?? 0)

const holdRestFrame = (video: HTMLVideoElement) => {
  const rest = restFrame.value
  if (!rest || rest >= video.duration || video.currentTime < rest) return
  resting.value = true
  const uncover = () => {
    if (!resting.value) return
    requestAnimationFrame(() => { resting.value = false })
  }
  video.addEventListener('seeked', uncover, { once: true })
  video.currentTime = rest
  // A browser that refuses the seek still has to drop the veil.
  window.setTimeout(uncover, 400)
}

const onVideoEnded = () => {
  // Freeze on the last frame: the interaction layer appears above it, so the
  // stage must not rewind or fade out.
  const video = videoElement.value
  video?.pause()
  announceEnded(true)
  if (video) holdRestFrame(video)
}

const onVideoReady = () => {
  didLoad.value = true
  const video = videoElement.value
  if (video?.ended) {
    announceEnded(true)
    return
  }
  applyPlaybackState()
}

const onVideoError = () => {
  didFail.value = true
  announceEnded(false)
}

const syncMotionPreference = () => {
  reducedMotion.value = Boolean(motionQuery?.matches)
}

const syncScene = () => {
  didFail.value = false
  didEnd.value = false
  resting.value = false
  void nextTick(() => {
    const video = videoElement.value
    const src = asset.value.video?.src ?? ''
    if (!shouldRenderVideo.value || !video) {
      didLoad.value = false
      announceEnded(false)
      return
    }
    // A surviving element is already loaded. Clearing this would drop the
    // `is-ready` class, fade the picture to zero and never bring it back - the
    // stage only fades in on `canplay`, which will not fire twice.
    didLoad.value = video.readyState >= 2
    if (src && src === finishedSrc.value) {
      announceEnded(true)
      return
    }
    if (video.ended) announceEnded(true)
  })
}

watch(() => [props.scene.sceneId, props.videoFixture], syncScene)
watch(() => props.paused, applyPlaybackState)

onMounted(() => {
  syncScene()
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
  motionQuery = window.matchMedia(reducedMotionQuery)
  syncMotionPreference()
  motionQuery.addEventListener('change', syncMotionPreference)
})

onBeforeUnmount(() => {
  motionQuery?.removeEventListener('change', syncMotionPreference)
  videoElement.value?.pause()
})
</script>

<style scoped lang="scss">
.story-stage-frame {
  position: absolute;
  inset: 0;
  isolation: isolate;
  overflow: hidden;
  background: var(--story-stage);
}

.story-stage-frame__video {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  opacity: 0;
  filter: saturate(0.94);
  transition: opacity var(--qh-duration-md) var(--qh-ease);
}

.story-stage-frame__video.is-ready {
  opacity: 1;
}

.story-stage-frame__static {
  position: absolute;
  inset: 0;
}

.story-stage-frame__pool {
  position: absolute;
  top: 18%;
  left: 34%;
  width: 34%;
  aspect-ratio: 1;
  border-radius: 50%;
  background: var(--story-gold);
  filter: blur(90px);
  opacity: 0.12;
}

.story-stage-frame__cabinet {
  position: absolute;
  right: 6%;
  bottom: 18%;
  display: grid;
  grid-template-columns: repeat(3, minmax(34px, 1fr));
  gap: 6px;
  width: min(30%, 260px);
  opacity: 0.6;
}

.story-stage-frame__cabinet i {
  min-height: 30px;
  border: 1px solid var(--story-stage-rail);
  background: var(--story-stage);
}

.story-stage-frame__veil {
  position: absolute;
  inset: 0;
  background: var(--story-stage);
  opacity: 0;
  pointer-events: none;
  transition: opacity calc(var(--qh-duration-md) * 1.5) var(--qh-ease);
}

.story-stage-frame__veil.is-on {
  opacity: 1;
  transition: none;
}

.story-stage-frame__vignette {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    linear-gradient(180deg, var(--story-stage-veil), transparent 26%),
    linear-gradient(0deg, var(--story-stage-veil), transparent 34%);
}

.is-memory .story-stage-frame__video {
  filter: saturate(0.5);
}

.is-memory .story-stage-frame__pool {
  left: 20%;
  opacity: 0.08;
}

.treatment-jade .story-stage-frame__pool {
  left: 62%;
  background: var(--story-jade);
}

@media (prefers-reduced-motion: reduce) {
  .story-stage-frame__video {
    display: none;
  }
}
</style>
