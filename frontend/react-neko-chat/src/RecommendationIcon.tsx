type RecommendationIconKind = 'music' | 'news' | 'video' | 'personal' | 'game';

/** Blue-and-white artwork shared with the visual language of N.E.K.O's toolbar. */
export default function RecommendationIcon({ kind }: { kind: RecommendationIconKind }) {
  const artwork = {
    music: <><path d="M10 4.5 20 2.8v14.1c0 2-1.9 3.3-4 3.3s-3.5-1.1-3.5-2.8 1.7-3.1 3.8-3.1c.5 0 1 .1 1.4.2V8l-5.1.9v10c0 2-1.9 3.3-4 3.3s-3.5-1.1-3.5-2.8 1.7-3.1 3.8-3.1c.4 0 .8 0 1.1.1Z" /></>,
    news: <><rect x="4" y="2.5" width="16" height="19" rx="3.5" /><path d="M8 7h8M8 11h8M8 15h4M8 18h6" fill="none" /></>,
    video: <><rect x="2" y="4.5" width="20" height="15" rx="4" /><path d="m10 8.5 6 3.5-6 3.5Z" fill="white" stroke="none" /></>,
    personal: <><path d="M7 3.5h10a5 5 0 0 1 5 5v5a5 5 0 0 1-5 5H9l-5.5 3 .8-5A5 5 0 0 1 2 12.3V8.5a5 5 0 0 1 5-5Z" /><path d="M7 9h10M7 13h6" fill="none" /></>,
    game: <><path d="M7.4 6.5h9.2c2.3 0 3.5 1.6 4.1 3.9l1.6 6.3c.6 2.6-1.9 4-3.6 2.1l-2.5-2.7H7.8l-2.5 2.7c-1.7 1.9-4.2.5-3.6-2.1l1.6-6.3c.6-2.3 1.8-3.9 4.1-3.9Z" /><path d="M6 11.5h5M8.5 9v5M12 6.5V3" fill="none" /><circle cx="16" cy="10.5" r="1" fill="white" stroke="none" /><circle cx="18.5" cy="13" r="1" fill="white" stroke="none" /></>,
  };
  return <svg className="recommendation-neko-artwork" viewBox="0 0 24 24" aria-hidden="true" focusable="false"
    fill="currentColor" stroke="white" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">{artwork[kind]}</svg>;
}
