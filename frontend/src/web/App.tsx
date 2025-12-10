import "./styles.css";
import { useCallback } from "react";
import { Button } from "@project_neko/components";
import { createRequestClient, WebTokenStorage } from "@project_neko/request";

// 创建一个简单的请求客户端；若无需鉴权，可忽略 token，默认存储在 localStorage
const request = createRequestClient({
  baseURL: "http://localhost:48911",
  storage: new WebTokenStorage(),
  refreshApi: async () => {
    // 示例中不做刷新，实际可按需实现
    throw new Error("refreshApi not implemented");
  },
  returnDataOnly: true
});

function App() {
  const handleClick = useCallback(async () => {
    try {
      const data = await request.get("/api/config/page_config", {
        params: { lanlan_name: "test" }
      });
      // 将返回结果展示在控制台或弹窗
      console.log("page_config:", data);
    } catch (err: any) {
      console.error("请求失败", err);
    }
  }, []);

  return (
    <main className="app">
      <header className="app__header">
        <h1>N.E.K.O 前端主页</h1>
        <p>单页应用，无路由 / 无 SSR</p>
      </header>
      <section className="app__content">
        <div className="card">
          <h2>开始使用</h2>
          <ol>
            <li>在此处挂载你的组件或业务入口。</li>
            <li>如需调用接口，可在 <code>@common</code> 下封装请求。</li>
            <li>构建产物输出到 <code>static/bundles/react_web.js</code>，模板引用即可。</li>
          </ol>
          <div style={{ marginTop: "16px" }}>
            <Button label="请求 page_config" onClick={handleClick} />
          </div>
        </div>
      </section>
    </main>
  );
}

export default App;

