import { ref } from 'vue'

// Shared between the two <CodingAgentOnboarding section="..."> instances on the
// page so the agent card and the prompt card stay in sync.
export const selectedAgentId = ref<string>('')
