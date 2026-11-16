import { inBrowser } from 'vitepress'
import { ref } from 'vue'

// Shared between the <CodingAgentOnboarding section="..."> instances on the
// page so the route cards, agent card, and prompt card stay in sync.
export const selectedAgentId = ref<string>('')

// Which onboarding route the prompt card generates text for.
export const selectedRoute = ref<'A' | 'B'>('A')

// Module-level refs keep their value across the sequential per-page renders
// of an SSR build, which leaks one page's selection into the next page's
// markup and causes hydration mismatches. Always reset to the deterministic
// defaults on the server; client-side navigation keeps the user's choice.
export function resetSharedStateForSSR() {
  if (!inBrowser) {
    selectedAgentId.value = ''
    selectedRoute.value = 'A'
  }
}
