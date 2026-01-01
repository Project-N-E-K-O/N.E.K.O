export interface ChatMessage {
  id: string;
  role: "system" | "user" | "assistant";
  content?: string;
  image?: string;
  createdAt: number;
}

export interface PendingScreenshot {
  id: string;
  base64: string;
}
