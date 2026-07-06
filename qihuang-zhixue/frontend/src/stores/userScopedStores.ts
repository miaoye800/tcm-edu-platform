import { useLearningStore } from './learning'
import { useProfileStore } from './profile'

export const resetUserScopedStores = () => {
  useProfileStore().$reset()
  useLearningStore().$reset()
}
