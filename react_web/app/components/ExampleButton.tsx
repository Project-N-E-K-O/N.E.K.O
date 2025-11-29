import { useState } from "react";
import "./ExampleButton.css";

interface ExampleButtonProps {
  buttonText?: string;
  onSave?: (text1: string, text2: string) => void;
}

export function ExampleButton({ 
  buttonText = "打开 Modal", 
  onSave 
}: ExampleButtonProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [text1, setText1] = useState("");
  const [text2, setText2] = useState("");

  const handleOpen = () => {
    setIsOpen(true);
  };

  const handleClose = () => {
    setIsOpen(false);
    // 关闭时清空输入
    setText1("");
    setText2("");
  };

  const handleSave = () => {
    if (onSave) {
      onSave(text1, text2);
    }
    handleClose();
  };

  return (
    <>
      <button
        onClick={handleOpen}
        className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors"
      >
        {buttonText}
      </button>

      {isOpen && (
        <div className="fixed inset-0 flex items-center justify-center bg-black bg-opacity-50" style={{ zIndex: 100000 }}>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-md mx-4 p-6">
            <h2 className="text-xl font-semibold mb-4 text-gray-900 dark:text-white">
              Modal 对话框
            </h2>

            <div className="space-y-4 mb-6">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  文本框 1
                </label>
                <input
                  type="text"
                  value={text1}
                  onChange={(e) => setText1(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:text-white"
                  placeholder="请输入内容..."
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  文本框 2
                </label>
                <input
                  type="text"
                  value={text2}
                  onChange={(e) => setText2(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:text-white"
                  placeholder="请输入内容..."
                />
              </div>
            </div>

            <div className="flex justify-end gap-3">
              <button
                onClick={handleClose}
                className="px-4 py-2 bg-gray-200 dark:bg-gray-700 text-gray-800 dark:text-gray-200 rounded-lg hover:bg-gray-300 dark:hover:bg-gray-600 transition-colors"
              >
                关闭
              </button>
              <button
                onClick={handleSave}
                className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors"
              >
                保存
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

