class HelloPlugin:
    def run(self, message: str | None = None, **kwargs):
        # 和你之前的 testPlugin 一样，返回个简单结构
        return {
            "hello": message or "world",
            "extra": kwargs,
        }
