# 收徒剧情媒体交付目录

已接入 `section-one-1.mp4` 至 `section-six-1.mp4` 六段剧情视频，以及 `section-all.mp4` 完整版。页面使用 `posters/` 中从各段视频抽取的本地帧作为加载 poster；它们是技术 poster，不是额外创作的正式封面。

分段视频目前按剧情顺序映射：第一段为初醒演出；第二段对应识药；第三至六段依次对应抓药、药宝、立约和礼成。第一段中的雨阶和师徒相遇节点暂时没有独立素材，保留静态背景。完整合辑 `section-all.mp4` 只保留在本机（页面不引用它，且仍是 540p 旧版，与现在的 4K 分段不一致），不随仓库发布。

替换媒体时，保持 `src/config/apprenticeStoryAssets.ts` 中的段落映射和 poster 路径同步更新。不要用开发夹具替代正式资源。

识药图片位于 `herbs/`，用于逐题大图识别。照片来自 Wikimedia Commons，均以 CC0 1.0 发布；网页使用的 JPEG 长边为 1920px。原图、作者和许可页面：

- `taizishen.jpg` — Fumikas Sagisavas，[Dried pseudostellaria heterophylla.jpg](https://commons.wikimedia.org/wiki/File:Dried_pseudostellaria_heterophylla.jpg)
- `dangshen.jpg` — Fumikas Sagisavas，[Dried Codonopsis pilosula HC.jpg](https://commons.wikimedia.org/wiki/File:Dried_Codonopsis_pilosula_HC.jpg)
- `huangqi.jpg` — Fumikas Sagisavas，[Dried astragalus slices.jpg](https://commons.wikimedia.org/wiki/File:Dried_astragalus_slices.jpg)
- `chenpi.jpg` — Fumikas Sagisavas，[Tangerine peels.jpg](https://commons.wikimedia.org/wiki/File:Tangerine_peels.jpg)
- `jinyinhua.jpg` — Father Pitt，[Lonicera japonica, Montour Trail, 2024-09-24.jpg](https://commons.wikimedia.org/wiki/File:Lonicera_japonica,_Montour_Trail,_2024-09-24.jpg)

开发验证夹具仅位于 `__fixtures__/`，只会在 `import.meta.env.DEV` 且 URL 显式带 `storyVideoFixture=ready|failed` 时被解析，绝不会进入正式剧情资源映射。
