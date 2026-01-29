export type ChatMessage = {
  id: string;
  role: "system" | "user" | "assistant";
  createdAt: number;
} & (
    | { content: string; image?: string }
    | { content?: string; image: string }
  );

export interface PendingScreenshot {
  id: string;
  base64: string;
}
