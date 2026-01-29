// 轻量声明，便于在未安装 node_modules 时通过类型检查。

declare module "vite/client" {
  const value: unknown;
  export default value;
}

declare module "react/jsx-runtime" {
  export * from "react";
  const jsx: any;
  const jsxs: any;
  const Fragment: any;
  export { jsx, jsxs, Fragment };
}

declare namespace JSX {
  interface IntrinsicElements {
    [elemName: string]: any;
  }
}

