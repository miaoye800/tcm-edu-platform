# 收徒剧情媒体交付目录

正式资源尚未交付。本目录当前不含最终 `seg_*.mp4` 或正式 poster。

交付后，将经审核的 1080p、16:9、H.264/AAC MP4 和同画面 poster 放在本目录；随后在 `src/config/apprenticeStoryAssets.ts` 对应 `scene_id` 的 `video` 字段填入 `/story/<文件名>` 和 `/story/<poster文件名>`。不要用开发夹具替代正式资源。

开发验证夹具仅位于 `__fixtures__/`，只会在 `import.meta.env.DEV` 且 URL 显式带 `storyVideoFixture=ready|failed` 时被解析，绝不会进入正式剧情资源映射。
