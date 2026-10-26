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

const deliveredAsset = (sceneId: string, backdrop: StoryBackdrop, focus: ApprenticeStoryAsset['staticFallback']['focus'], treatment: ApprenticeStoryAsset['staticFallback']['treatment'], fileName: string, posterFileName: string, durationSeconds: number): ApprenticeStoryAsset => ({
  ...pendingAsset(sceneId, backdrop, focus, treatment, fileName, posterFileName, durationSeconds),
  video: { src: `/story/${fileName}`, poster: `/story/posters/${posterFileName}`, durationSeconds },
})

// The supplied six chapter videos are mapped to the corresponding story beats.
export const apprenticeStoryAssets: Record<string, ApprenticeStoryAsset> = {
  'wake-apothecary': deliveredAsset('wake-apothecary', 'apothecary', 'ceiling', 'warm', 'section-one-1.mp4', 'section-one-1-story.jpg', 26.5),
  'injury-memory': pendingAsset('injury-memory', 'memory', 'stairs', 'memory', '', '', 0),
  'meet-master': pendingAsset('meet-master', 'master', 'master', 'warm', '', '', 0),
  'master-offer': deliveredAsset('master-offer', 'apothecary', 'master', 'warm', 'section-one-1.mp4', 'section-one-1-story.jpg', 26.5),
  'herb-dialogue': deliveredAsset('herb-dialogue', 'apothecary', 'lanterns', 'jade', 'section-two-1.mp4', 'section-two-1-story.jpg', 14.7),
  'prescription-counter': deliveredAsset('prescription-counter', 'counter', 'counter', 'jade', 'section-three-1.mp4', 'section-three-1-story.jpg', 14.9),
  'choose-keepsake': deliveredAsset('choose-keepsake', 'shelves', 'lanterns', 'warm', 'section-four-1.mp4', 'section-four-1-story.jpg', 15.2),
  'make-rule': deliveredAsset('make-rule', 'shelves', 'lanterns', 'warm', 'section-five-1.mp4', 'section-five-1-story.jpg', 10),
  'apprentice-complete': deliveredAsset('apprentice-complete', 'apothecary', 'lanterns', 'warm', 'section-six-1.mp4', 'section-six-1-story.jpg', 10.2),
}

const fallbackAsset = pendingAsset('fallback', 'apothecary', 'ceiling', 'warm', '', '', 0)
export const storyAssetForScene = (sceneId: string): ApprenticeStoryAsset => apprenticeStoryAssets[sceneId] ?? fallbackAsset

export type StoryVideoFixtureMode = 'ready' | 'failed' | null
export const fullStoryVideo = { src: '/story/section-all.mp4', poster: '/story/posters/section-all-story.jpg', durationSeconds: 91.5 } as const

export const storyAssetForStage = (sceneId: string, fixtureMode: StoryVideoFixtureMode = null): ApprenticeStoryAsset => {
  const asset = storyAssetForScene(sceneId)
  if (!import.meta.env.DEV || !fixtureMode) return asset
  const fileName = fixtureMode === 'ready' ? 'valid-video.mp4' : 'missing-video.mp4'
  return { ...asset, video: { src: `/story/__fixtures__/${fileName}`, poster: '/story/__fixtures__/valid-video_poster.png', durationSeconds: 1 } }
}
