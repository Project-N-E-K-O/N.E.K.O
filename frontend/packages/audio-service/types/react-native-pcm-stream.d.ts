declare module "react-native-pcm-stream" {
  export type PlaybackState = "IDLE" | "PLAYING" | "PAUSED" | "COMPLETED";

  export type PlaybackStats = {
    state: PlaybackState;
    isPlaying: boolean;
    totalDuration: number;
    playedDuration: number;
    remainingDuration: number;
    progress: number;
  };

  export type OnAudioFrameEventPayload = {
    pcm: Uint8Array;
    ts?: number;
    seq?: number;
  };

  export type OnAmplitudeUpdateEventPayload = {
    amplitude: number;
  };

  export type PCMStreamModuleEvents = {
    onError?: (params: { message?: string | null; state?: string }) => void;
    onPlaybackStart?: (params: { state: string }) => void;
    onPlaybackStop?: (params: { state: string; totalDuration: number; playedDuration: number }) => void;
    onPlaybackPaused?: (params: { state: string }) => void;
    onPlaybackResumed?: (params: { state: string }) => void;
    onPlaybackProgress?: (params: {
      playedDuration: number;
      totalDuration: number;
      progress: number;
      remainingDuration: number;
    }) => void;
    onAmplitudeUpdate?: (params: OnAmplitudeUpdateEventPayload) => void;
    onAudioFrame?: (params: OnAudioFrameEventPayload) => void;
  };

  export type PCMStreamModuleSpec = {
    initPlayer(sampleRate?: number): void;
    playPCMChunk(chunk: Uint8Array): void;
    stopPlayback(): void;

    getPlaybackState(): PlaybackState;
    isPlaying(): boolean;
    getTotalDuration(): number;
    getPlayedDuration(): number;
    getRemainingDuration(): number;
    getProgress(): number;
    getPlaybackStats(): PlaybackStats;

    startRecording(sampleRate?: number, frameSize?: number, targetRate?: number): void;
    stopRecording(): void;

    addListener<E extends keyof PCMStreamModuleEvents>(
      eventName: E,
      listener: NonNullable<PCMStreamModuleEvents[E]>
    ): { remove: () => void };
  };

  const PCMStream: PCMStreamModuleSpec;
  export default PCMStream;
}

