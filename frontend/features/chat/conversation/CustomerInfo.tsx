"use client";

import { Card, Descriptions, Drawer, Space, Tag } from "antd";
import { stageLabel } from "@/domain/chat/chatModel";
import type { ConversationDetail } from "@/types/chatCanonical";

export function CustomerInfo({ conversation, open, onClose }: { conversation?: ConversationDetail; open: boolean; onClose: () => void }) {
  return (
    <Drawer title="客户详情" size={520} open={open} onClose={onClose} destroyOnHidden>
      {conversation ? <CustomerInfoContent conversation={conversation} /> : null}
    </Drawer>
  );
}

function CustomerInfoContent({ conversation }: { conversation: ConversationDetail }) {
  const { customer } = conversation;
  const value = (text: string | undefined) => text?.trim() || "—";

  return (
    <Space orientation="vertical" className="w-full" size="middle">
      <Card title="客户信息">
        <Descriptions size="small" column={1} items={[
          { key: "name", label: "姓名", children: value(customer.name) },
          { key: "company", label: "公司", children: value(customer.company) },
          { key: "country", label: "国家", children: value(customer.country) },
          { key: "email", label: "邮箱", children: value(customer.email) },
          { key: "phone", label: "电话", children: value(customer.phone) },
          { key: "stage", label: "阶段", children: stageLabel(customer.stage) },
          { key: "availability", label: "在线时间", children: value(customer.availability) },
        ]} />
        <div className="mt-3">
          {customer.tags.map((tag) => <Tag key={tag} color="blue">{tag}</Tag>)}
        </div>
      </Card>
    </Space>
  );
}
