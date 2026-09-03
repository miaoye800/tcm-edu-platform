export type AlchemyVariant = 'night' | 'paper'
export type AlchemyHeat = 'gentle' | 'fierce' | 'complete'

export interface AlchemyVisualConfig {
  flameIntensity: number
  particleCount: number
  flameSpeed: number
  haloRadius: number
  breathSeconds: number
  mascotMotion: number
  bubbleFrequency: number
  palette: Record<string, string>
}

export const alchemyVariants: Record<AlchemyVariant, AlchemyVisualConfig> = {
  night: {
    flameIntensity: 0.78, particleCount: 7, flameSpeed: 1.65, haloRadius: 42, breathSeconds: 3.6, mascotMotion: 1, bubbleFrequency: 1,
    palette: { page: '#090c0d', panel: '#101518', ink: '#efe5c9', muted: '#9d9b8d', gold: '#e5b75e', goldBright: '#fff0b0', jade: '#78a99b', fire: '#dc741d', fireDeep: '#7d2c12', seal: '#ad4537', reflection: '#9e4e19' },
  },
  paper: {
    flameIntensity: 0.36, particleCount: 4, flameSpeed: 2.05, haloRadius: 24, breathSeconds: 4.5, mascotMotion: .72, bubbleFrequency: .86,
    palette: { page: '#e9dfc8', panel: '#f3ead7', ink: '#27332d', muted: '#69695f', gold: '#b98b36', goldBright: '#d5b35c', jade: '#5f8f77', fire: '#ad3f32', fireDeep: '#6e2422', seal: '#9d302b', reflection: '#b78356' },
  },
}

export const heatForStep = (index: number): AlchemyHeat => index >= 4 ? 'complete' : index >= 2 ? 'fierce' : 'gentle'
