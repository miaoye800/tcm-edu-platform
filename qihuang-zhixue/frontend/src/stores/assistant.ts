import { defineStore } from 'pinia'

/**
 * 助手入口。
 *
 * 从「开一个抽屉」改成「跳一个页面」：对话页要三栏、要会话历史、要在刷新后
 * 恢复方案卡，抽屉装不下这些。`open` 还留着——顶栏与侧栏都调 `show()`，
 * 将来若要恢复侧滑只要改这一个 action。
 */
export const useAssistantStore = defineStore('assistant', {
  state: () => ({
    open: false,
  }),
  actions: {
    toggle() {
      void this.show()
    },
    async show() {
      this.open = true
      // 动态引入：router 会 import AppShell → AppSidebar → 本 store，
      // 顶层静态引入会形成循环，构建时不一定报错，运行时可能拿到未初始化的 router。
      const { default: router } = await import('../router')
      if (router.currentRoute.value.path !== '/assistant') {
        await router.push('/assistant')
      }
    },
    hide() {
      this.open = false
    },
  },
})
