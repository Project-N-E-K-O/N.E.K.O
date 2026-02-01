export { Button } from "./src/Button";
export type { ButtonProps, ButtonVariant, ButtonSize } from "./src/Button";

export { default as StatusToast } from "./src/StatusToast";
export type { StatusToastHandle } from "./src/StatusToast";

export { QrMessageBox } from "./src/QrMessageBox";
export type { QrMessageBoxProps } from "./src/QrMessageBox";

export { default as Modal } from "./src/Modal";
export type { ModalHandle } from "./src/Modal";
export { AlertDialog } from "./src/Modal/AlertDialog";
export { ConfirmDialog } from "./src/Modal/ConfirmDialog";
export { PromptDialog } from "./src/Modal/PromptDialog";
export { BaseModal } from "./src/Modal/BaseModal";
export type { BaseModalProps } from "./src/Modal/BaseModal";
export type { AlertDialogProps } from "./src/Modal/AlertDialog";
export type { ConfirmDialogProps } from "./src/Modal/ConfirmDialog";
export type { PromptDialogProps } from "./src/Modal/PromptDialog";

export * from "./src/Live2DRightToolbar";

export * from "./src/chat";

// i18n adapter (Provider -> window.t -> fallback)
export { I18nProvider, useT, tOrDefault } from "./src/i18n";
export type { TFunction, I18nProviderProps } from "./src/i18n";

