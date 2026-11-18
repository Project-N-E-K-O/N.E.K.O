import { useRecommendationExposure } from './useRecommendationExposure';
import { isDirectScreenObservation, type ChatMessage, type MessageAction, type MessageBlock } from './message-schema';

type ScreenObservation = { message: ChatMessage; block: Extract<MessageBlock, { type: 'proactive_recommendation' }> };

function findScreenObservations(messages: ChatMessage[], turnId?: string): ScreenObservation[] {
  if (!turnId) return [];
  return messages.flatMap(message => message.turnId === turnId
    ? message.blocks.flatMap(block => block.type === 'proactive_recommendation' && isDirectScreenObservation(block)
      ? [{ message, block }] : [])
    : []);
}

// The observation card used to sit right after its turn's spoken line, so the
// last visible assistant bubble of that turn is where it is now seen.
export function getScreenObservationAnchorIds(visibleMessages: ChatMessage[], allMessages: ChatMessage[]): Set<string> {
  const turns = new Set(allMessages.flatMap(message =>
    message.turnId && message.blocks.some(isDirectScreenObservation) ? [message.turnId] : []));
  const anchors = new Map<string, string>();
  for (const message of visibleMessages) {
    if (message.role === 'assistant' && message.turnId && turns.has(message.turnId)) anchors.set(message.turnId, message.id);
  }
  return new Set(anchors.values());
}

function ExposureMarker({ observation, interactive, onAction }: {
  observation: ScreenObservation; interactive: boolean;
  onAction?: (message: ChatMessage, action: MessageAction) => void;
}) {
  const root = useRecommendationExposure<HTMLSpanElement>(observation.message, observation.block.recommendationId,
    interactive, 'proactive_recommendation_shown', onAction);
  return <span ref={root} className="screen-observation-exposure" aria-hidden="true"
    data-recommendation-id={observation.block.recommendationId} />;
}

export default function ScreenObservationExposure({ messages, turnId, interactive = true, onAction }: {
  messages: ChatMessage[]; turnId?: string; interactive?: boolean;
  onAction?: (message: ChatMessage, action: MessageAction) => void;
}) {
  const observations = findScreenObservations(messages, turnId);
  if (!observations.length) return null;
  return <>{observations.map(observation => <ExposureMarker key={observation.block.recommendationId}
    observation={observation} interactive={interactive} onAction={onAction} />)}</>;
}
