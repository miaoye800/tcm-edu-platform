/** Stage 4 only: prefix prevents utility collisions; preflight stays disabled. */
module.exports = {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  prefix: 'tw-',
  corePlugins: { preflight: false },
  theme: {
    colors: {
      jade: 'var(--qh-color-jade)',
      'jade-d': 'var(--qh-color-jade-d)',
      blue: 'var(--qh-color-blue)',
      gold: 'var(--qh-color-gold)',
      coral: 'var(--qh-color-coral)',
      cream: 'var(--qh-color-cream)',
      text: 'var(--qh-text)',
      muted: 'var(--qh-text-muted)',
      border: 'var(--qh-border)',
    },
    borderRadius: { sm: 'var(--qh-radius-sm)', card: 'var(--qh-radius-card)', 'card-sm': 'var(--qh-radius-card-sm)', pill: 'var(--qh-radius-pill)' },
    boxShadow: { soft: 'var(--qh-shadow-soft)', deep: 'var(--qh-shadow-deep)', inner: 'var(--qh-inner-highlight)' },
    spacing: { 1: 'var(--qh-space-1)', 2: 'var(--qh-space-2)', 3: 'var(--qh-space-3)', 4: 'var(--qh-space-4)', 5: 'var(--qh-space-5)', 6: 'var(--qh-space-6)', 8: 'var(--qh-space-8)', 10: 'var(--qh-space-10)' },
    extend: {},
  },
}
