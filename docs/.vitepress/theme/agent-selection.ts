import { ref } from 'vue'

// Shared between the <CodingAgentOnboarding section="..."> instances on the
// page so the agent card and the prompt card stay in sync.
export const selectedAgentId = ref<string>('')

// Which onboarding route the prompt card generates text for.
export const selectedRoute = ref<'A' | 'B'>('A')
