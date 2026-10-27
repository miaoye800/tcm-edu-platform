export type StoryBackdrop = 'apothecary' | 'memory' | 'master' | 'counter' | 'shelves'

export type StoryVideoResource = {
  src: string
  poster: string
  durationSeconds: number
  // The second the stage rests on *after* the film has finished. Only the clips
  // that fade to black over their last second need it: the question is asked on
  // this stage, and a dead black screen reads as a broken player rather than a
  // finished shot. The film itself always plays to its own `ended` first.
  restFrameSeconds?: number
}

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

const deliveredAsset = (sceneId: string, backdrop: StoryBackdrop, focus: ApprenticeStoryAsset['staticFallback']['focus'], treatment: ApprenticeStoryAsset['staticFallback']['treatment'], fileName: string, posterFileName: string, durationSeconds: number, restFrameSeconds?: number): ApprenticeStoryAsset => ({
  ...pendingAsset(sceneId, backdrop, focus, treatment, fileName, posterFileName, durationSeconds),
  video: {
    src: `/story/${fileName}`,
    poster: `/story/posters/${posterFileName}`,
    durationSeconds,
    ...(restFrameSeconds === undefined ? {} : { restFrameSeconds }),
  },
})

// The supplied six chapter videos are mapped to the corresponding story beats.
// section-one is a single continuous shot that carries the whole prologue, so all
// three prologue beats point at it. The <video> element is keyed by source, which
// means it survives those beat changes and the shot never restarts mid-scene.
// injury-memory keeps the apothecary backdrop on purpose: the stage's `is-memory`
// filter would desaturate the picture in the middle of the same shot.
// Every shot plays to its own `ended` event; nothing truncates playback early.
// section-three/four/five then fade to black, so they declare where the stage
// comes to rest once the fade is over - measured on the frame where the picture
// is still readable, a little before the fade starts.
export const apprenticeStoryAssets: Record<string, ApprenticeStoryAsset> = {
  'wake-apothecary': deliveredAsset('wake-apothecary', 'apothecary', 'ceiling', 'warm', 'section-one-1.mp4', 'section-one-1-story.jpg', 27.8),
  'injury-memory': deliveredAsset('injury-memory', 'apothecary', 'stairs', 'memory', 'section-one-1.mp4', 'section-one-1-story.jpg', 27.8),
  'meet-master': deliveredAsset('meet-master', 'master', 'master', 'warm', 'section-one-1.mp4', 'section-one-1-story.jpg', 27.8),
  'master-offer': deliveredAsset('master-offer', 'apothecary', 'master', 'warm', 'section-one-1.mp4', 'section-one-1-story.jpg', 27.8),
  'herb-dialogue': deliveredAsset('herb-dialogue', 'apothecary', 'lanterns', 'jade', 'section-two-1.mp4', 'section-two-1-story.jpg', 14.6),
  'prescription-counter': deliveredAsset('prescription-counter', 'counter', 'counter', 'jade', 'section-three-1.mp4', 'section-three-1-story.jpg', 14.9, 13.8),
  'choose-keepsake': deliveredAsset('choose-keepsake', 'shelves', 'lanterns', 'warm', 'section-four-1.mp4', 'section-four-1-story.jpg', 15.4, 13.9),
  'make-rule': deliveredAsset('make-rule', 'shelves', 'lanterns', 'warm', 'section-five-1.mp4', 'section-five-1-story.jpg', 9.6, 8.9),
  'apprentice-complete': deliveredAsset('apprentice-complete', 'apothecary', 'lanterns', 'warm', 'section-six-1.mp4', 'section-six-1-story.jpg', 10.2),
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
