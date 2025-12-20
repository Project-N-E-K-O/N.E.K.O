import React, {
  useState,
  useEffect,
  useCallback,
  useRef,
  forwardRef,
  useImperativeHandle,
} from "react";
import { AlertDialog } from "./AlertDialog";
import { ConfirmDialog } from "./ConfirmDialog";
import { PromptDialog } from "./PromptDialog";
import { tOrDefault, useT } from "../i18n";
import "./Modal.css";

// 对话框类型
type DialogType = "alert" | "confirm" | "prompt";

// 对话框配置接口
interface AlertConfig {
  type: "alert";
  message: string;
  title?: string | null;
  okText?: string;
}

interface ConfirmConfig {
  type: "confirm";
  message: string;
  title?: string | null;
  okText?: string;
  cancelText?: string;
  danger?: boolean;
}

interface PromptConfig {
  type: "prompt";
  message: string;
  defaultValue?: string;
  placeholder?: string;
  title?: string | null;
  okText?: string;
  cancelText?: string;
}

type DialogConfig = AlertConfig | ConfirmConfig | PromptConfig;

// 对话框状态
interface DialogState {
  isOpen: boolean;
  config: DialogConfig | null;
  resolve: ((value: any) => void) | null;
}

export interface ModalHandle {
  alert: (message: string, title?: string | null) => Promise<boolean>;
  confirm: (
    message: string,
    title?: string | null,
    options?: { okText?: string; cancelText?: string; danger?: boolean }
  ) => Promise<boolean>;
  prompt: (message: string, defaultValue?: string, title?: string | null) => Promise<string | null>;
}

const Modal = forwardRef<ModalHandle | null, {}>(function ModalComponent(_, ref) {
  const [dialogState, setDialogState] = useState<DialogState>({
    isOpen: false,
    config: null,
    resolve: null,
  });
  const t = useT();

  // 使用 ref 跟踪最新的 dialogState，以便在清理函数中访问最新值
  const dialogStateRef = useRef<DialogState>(dialogState);

  // 当 dialogState 改变时更新 ref
  useEffect(() => {
    dialogStateRef.current = dialogState;
  }, [dialogState]);

  // 创建对话框的通用函数
  const createDialog = useCallback((config: DialogConfig): Promise<any> => {
    return new Promise((resolve) => {
      setDialogState({
        isOpen: true,
        config,
        resolve,
      });
    });
  }, []);

  // 关闭对话框
  const closeDialog = useCallback(() => {
    setDialogState((prev) => {
      if (prev.resolve && prev.config) {
        // 根据类型返回默认值
        if (prev.config.type === "prompt") {
          prev.resolve(null);
        } else if (prev.config.type === "confirm") {
          prev.resolve(false);
        } else {
          prev.resolve(true);
        }
      }
      return {
        isOpen: false,
        config: null,
        resolve: null,
      };
    });
  }, []);

  // 处理确认
  const handleConfirm = useCallback((value?: any) => {
    setDialogState((prev) => {
      if (prev.resolve) {
        if (prev.config?.type === "prompt") {
          prev.resolve(value || "");
        } else {
          prev.resolve(true);
        }
      }
      return {
        isOpen: false,
        config: null,
        resolve: null,
      };
    });
  }, []);

  // 处理取消
  const handleCancel = useCallback(() => {
    setDialogState((prev) => {
      if (prev.resolve) {
        if (prev.config?.type === "prompt") {
          prev.resolve(null);
        } else {
          prev.resolve(false);
        }
      }
      return {
        isOpen: false,
        config: null,
        resolve: null,
      };
    });
  }, []);

  const getDefaultTitle = useCallback((type: DialogType): string => {
    switch (type) {
      case "alert":
        return tOrDefault(t, "common.alert", "提示");
      case "confirm":
        return tOrDefault(t, "common.confirm", "确认");
      case "prompt":
        return tOrDefault(t, "common.input", "输入");
      default:
        return "提示";
    }
  }, [t]);

  // React 内部直接调用的 API，供 ref 或自定义 hook 使用
  const showAlert = useCallback(
    (message: string, title: string | null = null): Promise<boolean> => {
      return createDialog({
        type: "alert",
        message,
        title: title !== null ? title : getDefaultTitle("alert"),
      });
    },
    [createDialog, getDefaultTitle]
  );

  const showConfirm = useCallback(
    (
      message: string,
      title: string | null = null,
      options: { okText?: string; cancelText?: string; danger?: boolean } = {}
    ): Promise<boolean> => {
      return createDialog({
        type: "confirm",
        message,
        title: title !== null ? title : getDefaultTitle("confirm"),
        okText: options.okText,
        cancelText: options.cancelText,
        danger: options.danger || false,
      });
    },
    [createDialog, getDefaultTitle]
  );

  const showPrompt = useCallback(
    (message: string, defaultValue: string = "", title: string | null = null): Promise<string | null> => {
      return createDialog({
        type: "prompt",
        message,
        defaultValue,
        title: title !== null ? title : getDefaultTitle("prompt"),
      });
    },
    [createDialog, getDefaultTitle]
  );

  useImperativeHandle(
    ref,
    () => ({
      alert: showAlert,
      confirm: showConfirm,
      prompt: showPrompt,
    }),
    [showAlert, showConfirm, showPrompt]
  );

  // 卸载时关闭未完成的对话框，避免悬挂的 Promise
  useEffect(() => {
    return () => {
      if (!dialogStateRef.current.isOpen) return;

      const { resolve, config } = dialogStateRef.current;

      if (resolve && config) {
        if (config.type === "prompt") {
          resolve(null);
        } else if (config.type === "confirm") {
          resolve(false);
        } else {
          resolve(true);
        }
      }

      dialogStateRef.current = {
        isOpen: false,
        config: null,
        resolve: null,
      };
    };
  }, []);

  // 渲染对话框
  const renderDialog = () => {
    if (!dialogState.config || !dialogState.isOpen) return null;

    const { config } = dialogState;

    switch (config.type) {
      case "alert":
        return (
          <AlertDialog
            isOpen={dialogState.isOpen}
            onClose={closeDialog}
            title={config.title || undefined}
            message={config.message}
            okText={config.okText}
            onConfirm={handleConfirm}
          />
        );

      case "confirm":
        return (
          <ConfirmDialog
            isOpen={dialogState.isOpen}
            onClose={closeDialog}
            title={config.title || undefined}
            message={config.message}
            okText={config.okText}
            cancelText={config.cancelText}
            danger={config.danger}
            onConfirm={handleConfirm}
            onCancel={handleCancel}
          />
        );

      case "prompt":
        return (
          <PromptDialog
            isOpen={dialogState.isOpen}
            onClose={handleCancel}
            title={config.title || undefined}
            message={config.message}
            defaultValue={config.defaultValue}
            placeholder={config.placeholder}
            okText={config.okText}
            cancelText={config.cancelText}
            onConfirm={handleConfirm}
            onCancel={handleCancel}
          />
        );

      default:
        return null;
    }
  };

  return <>{renderDialog()}</>;
});

export { Modal };

// 导出默认函数，用于在 HTML 中直接挂载
export default Modal;


