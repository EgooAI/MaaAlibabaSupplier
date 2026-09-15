"use client";

import { Button, Result } from "antd";
import { useEffect } from "react";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <Result
      status="error"
      title="页面加载失败"
      subTitle={error.message || "请重试或返回首页"}
      extra={[
        <Button key="retry" type="primary" onClick={() => reset()}>
          重试
        </Button>,
        <Button key="home" href="/">
          返回首页
        </Button>,
      ]}
    />
  );
}
