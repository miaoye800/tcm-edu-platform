# Frontend Working Agreement

## Tokens And Scope

- All colors, radii, shadows, typography, and spacing must reference `src/styles/tokens.scss`. Add a named token before using a new design value; do not add literal hex values to component CSS.
- Element Plus theme values belong in the token layer. Do not style Element Plus globally from a feature component.
- Every Vue component style block must use `scoped`. Global styles are limited to `tokens.scss`, `reset.scss`, and the prefixed Tailwind utility entry.
- Use code-made SVG/CSS for decorative visuals. Do not add bitmap assets, except approved official Xiaoqi illustrations.

## Motion

- New animation durations and easing values belong in a feature configuration object (for example `src/config/stage4Visual.ts`), not scattered in templates or CSS.
- Default interaction motion is 160-320ms with `ease`/`easeOut`; ambient motion is 1.8-6s and must remain visually restrained.
- Every new animation must have a `prefers-reduced-motion: reduce` alternative that removes looping motion and preserves the final readable state.
- Use GSAP for timeline/path choreography, `motion-v` for component entry/exit, and CSS only for contained decorative motion.

## Tailwind

- Tailwind is opt-in for new or deliberately refactored components only. Do not bulk-rewrite existing templates into utilities.
- Use only the `tw-` prefixed utilities. Tailwind preflight remains disabled because `reset.scss` owns the application reset.
- Tailwind theme values must map to existing CSS variables; its config must not introduce hex color literals or a parallel token system.

## Async States

- Empty, loading, and error surfaces must use `StateEmpty`, `StateLoading`, and `StateError` from `src/components/state`.
- Configure their icon, copy, action label, and size through props. Do not introduce local spinners or one-off empty/error artwork.
- The standard loading metaphor is a restrained meridian trace, never a rotating loader.

## Safety

- Keep feature-specific visual parameters in a typed configuration object and pass them into the component as CSS variables or props.
- Before changing shared styles, run the Stage 4 baseline capture and compare existing routes. No visual changes to existing pages are acceptable without an explicit, documented exception.
- Keep development-only experiments behind `import.meta.env.DEV`; production routes and auth guards must remain unaffected.
