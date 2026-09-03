import type { StoryVideoResource } from '../../config/apprenticeStoryAssets'

export type StoryMediaState = 'static-pending' | 'video-loading' | 'video-ready' | 'video-failed' | 'static-reduced-motion'

export type StoryMatchMedia = (query: string) => Pick<MediaQueryList, 'matches'>

export const reducedMotionQuery = '(prefers-reduced-motion: reduce)'

export const readInitialReducedMotionPreference = (
  matchMediaFn: StoryMatchMedia | undefined = typeof window === 'undefined' || typeof window.matchMedia !== 'function'
    ? undefined
    : window.matchMedia.bind(window),
): boolean => Boolean(matchMediaFn?.(reducedMotionQuery).matches)

export const deriveStoryMediaState = (video: StoryVideoResource | undefined, reducedMotion: boolean, didFail: boolean, didLoad: boolean): StoryMediaState => {
  if (!video) return 'static-pending'
  if (reducedMotion) return 'static-reduced-motion'
  if (didFail) return 'video-failed'
  return didLoad ? 'video-ready' : 'video-loading'
}

export const shouldRenderStoryVideo = (video: StoryVideoResource | undefined, reducedMotion: boolean, didFail: boolean): boolean => Boolean(video && !reducedMotion && !didFail)

export const mediaStatusCopy = (state: StoryMediaState): string => ({
  'static-pending': '静态演出 · 视频资源待接入',
  'video-loading': '视频演出加载中',
  'video-ready': '视频演出已接入',
  'video-failed': '视频未能播放，已切换静态演出',
  'static-reduced-motion': '静态演出 · 已遵循减少动态设置',
}[state])
