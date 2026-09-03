export type StoryBackdrop = 'apothecary' | 'memory' | 'master' | 'counter' | 'shelves'

export type StoryVideoResource = { src: string; poster: string; durationSeconds: number }

export type ApprenticeStoryAsset = {
  sceneId: string
  backdrop: StoryBackdrop
  staticFallback: { focus: 'ceiling' | 'stairs' | 'master' | 'counter' | 'lanterns'; treatment: 'warm' | 'memory' | 'jade' }
  delivery: { fileName: string; posterFileName: string; durationSeconds: number }
  video?: StoryVideoResource
}

const pendingAsset = (sceneId: string, backdrop: StoryBackdrop, focus: ApprenticeStoryAsset['staticFallback']['focus'], treatment: ApprenticeStoryAsset['staticFallback']['treatment'], fileName: string, posterFileName: string, durationSeconds: number): ApprenticeStoryAsset => ({
  sceneId, backdrop, staticFallback: { focus, treatment }, delivery: { fileName, posterFileName, durationSeconds },
})

// Formal story segments intentionally remain pending until externally produced MP4 files are delivered.
export const apprenticeStoryAssets: Record<string, ApprenticeStoryAsset> = {
  'wake-apothecary': pendingAsset('wake-apothecary', 'apothecary', 'ceiling', 'warm', 'seg_01_wake.mp4', 'seg_01_wake_poster.png', 4),
  'injury-memory': pendingAsset('injury-memory', 'memory', 'stairs', 'memory', 'seg_02_rain-steps.mp4', 'seg_02_rain-steps_poster.png', 3),
  'meet-master': pendingAsset('meet-master', 'master', 'master', 'warm', 'seg_03_meet-master.mp4', 'seg_03_meet-master_poster.png', 5),
  'master-offer': pendingAsset('master-offer', 'master', 'master', 'warm', 'seg_04_offer-apprentice.mp4', 'seg_04_offer-apprentice_poster.png', 5),
  'prescription-counter': pendingAsset('prescription-counter', 'counter', 'counter', 'jade', 'seg_05-counter-scale.mp4', 'seg_05-counter-scale_poster.png', 5),
  'apprentice-complete': pendingAsset('apprentice-complete', 'apothecary', 'lanterns', 'warm', 'seg_06-lanterns.mp4', 'seg_06-lanterns_poster.png', 5),
}

const fallbackAsset = pendingAsset('fallback', 'apothecary', 'ceiling', 'warm', '', '', 0)
export const storyAssetForScene = (sceneId: string): ApprenticeStoryAsset => apprenticeStoryAssets[sceneId] ?? fallbackAsset

export type StoryVideoFixtureMode = 'ready' | 'failed' | null

export const storyAssetForStage = (sceneId: string, fixtureMode: StoryVideoFixtureMode = null): ApprenticeStoryAsset => {
  const asset = storyAssetForScene(sceneId)
  if (!import.meta.env.DEV || !fixtureMode) return asset
  const fileName = fixtureMode === 'ready' ? 'valid-video.mp4' : 'missing-video.mp4'
  return { ...asset, video: { src: `/story/__fixtures__/${fileName}`, poster: '/story/__fixtures__/valid-video_poster.png', durationSeconds: 1 } }
}
