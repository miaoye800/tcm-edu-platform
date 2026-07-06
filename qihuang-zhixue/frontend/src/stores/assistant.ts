import { defineStore } from 'pinia'

export const useAssistantStore = defineStore('assistant', {
  state: () => ({
    open: false,
  }),
  actions: {
    toggle() {
      this.open = !this.open
    },
    show() {
      this.open = true
    },
    hide() {
      this.open = false
    },
  },
})
