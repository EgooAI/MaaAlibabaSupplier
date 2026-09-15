import { Button, Result } from "antd";
import Link from "next/link";

export default function NotFound() {
  return (
    <Result
      status="404"
      title="页面不存在"
      subTitle="请检查地址或返回首页"
      extra={
        <Link href="/">
          <Button type="primary">返回首页</Button>
        </Link>
      }
    />
  );
}
