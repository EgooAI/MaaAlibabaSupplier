"use client";

import { Button, Card, Descriptions, Drawer, Space, Tag } from "antd";
import { stageLabel } from "@/domain/chat/chatModel";
import type { ConversationDetail } from "@/types/chatCanonical";

export function CustomerInfo({ conversation, open, onClose, onGotoContact }: { conversation?: ConversationDetail; open: boolean; onClose: () => void; onGotoContact?: () => void }) {
  return (
    <Drawer title="客户详情" size={520} open={open} onClose={onClose} destroyOnHidden>
      {conversation ? <CustomerInfoContent conversation={conversation} onGotoContact={onGotoContact} /> : null}
    </Drawer>
  );
}

function CustomerInfoContent({ conversation, onGotoContact }: { conversation: ConversationDetail; onGotoContact?: () => void }) {
  const { customer } = conversation;
  const value = (text: string | number | undefined) => (text === undefined || text === null || String(text).trim() === "" ? "—" : String(text));
  const d90 = customer.d90;

  return (
    <Space orientation="vertical" className="w-full" size="middle">
      <Card title="身份" extra={onGotoContact ? <Button size="small" onClick={onGotoContact}>跳转到该联系人</Button> : null}>
        <Descriptions size="small" column={1} items={[
          { key: "ali", label: "Ali ID", children: value(customer.aliId) },
          { key: "member", label: "会员 ID", children: value(customer.memberId) },
          { key: "login", label: "登录 ID", children: value(customer.loginId) },
          { key: "encrypt", label: "加密 ID", children: value(customer.encryptAccountId) },
        ]} />
      </Card>
      <Card title="客户信息">
        <Descriptions size="small" column={1} items={[
          { key: "name", label: "姓名", children: value(customer.firstName || customer.lastName ? `${customer.firstName ?? ""} ${customer.lastName ?? ""}`.trim() : customer.name) },
          { key: "company", label: "公司", children: value(customer.company) },
          { key: "country", label: "国家", children: value(customer.country) },
          { key: "register", label: "注册时间", children: value(customer.registerDate) },
          { key: "email", label: "邮箱", children: value(customer.email) },
          { key: "mobile", label: "手机", children: value(customer.mobile) },
          { key: "phone", label: "电话", children: value(customer.phone) },
          { key: "stage", label: "阶段", children: stageLabel(customer.stage) },
          { key: "availability", label: "在线时间", children: value(customer.availability) },
        ]} />
        <div className="mt-3">
          {customer.tags.map((tag) => <Tag key={tag} color="blue">{tag}</Tag>)}
        </div>
      </Card>
      <Card title="近 90 天行为">
        <Descriptions size="small" column={1} items={[
          { key: "views", label: "浏览", children: value(d90?.productViews) },
          { key: "inquiry", label: "有效询盘", children: value(d90?.validInquiries) },
          { key: "replied", label: "已回复询盘", children: value(d90?.repliedInquiries) },
          { key: "rfq", label: "有效 RFQ", children: value(d90?.validRfqs) },
          { key: "login", label: "登录天数", children: value(d90?.loginDays) },
          { key: "spam", label: "垃圾询盘", children: value(d90?.spamInquiries) },
          { key: "black", label: "拉黑", children: value(d90?.blacklisted) },
        ]} />
      </Card>
      <Card title="标签与可用性">
        <Descriptions size="small" column={1} items={[
          { key: "quality", label: "质量等级", children: value(customer.qualityTag) },
          { key: "growth", label: "成长等级", children: value(customer.growthLevel) },
          { key: "industries", label: "偏好行业", children: customer.industries?.length ? customer.industries.join("、") : "—" },
          { key: "joining", label: "加入年限", children: value(customer.joiningYears) },
          { key: "potential", label: "潜力分", children: value(customer.potentialScore) },
          { key: "recent", label: "近期联系", children: customer.recentContact === undefined ? "—" : customer.recentContact ? "是" : "否" },
          { key: "emailv", label: "邮箱验证", children: customer.emailValidated === undefined ? "—" : customer.emailValidated ? "已验证" : "未验证" },
        ]} />
      </Card>
    </Space>
  );
}
