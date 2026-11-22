"use client";

import { ExportOutlined } from "@ant-design/icons";
import { App, Button, Card, Descriptions, Drawer, Space, Tooltip } from "antd";
import type { ConversationDetail } from "@/types/chatCanonical";
import type { PortraitTag } from "@/types/portrait";
import { formatEmpty } from "@/components/empty";
import { useAccount } from "@/features/account/AccountProvider";
import { PortraitEditor } from "./PortraitEditor";
import { CustomerQualityDetails } from "../customer/CustomerQuality";

type CustomerInfoContentProps = {
  conversation: ConversationDetail;
  onGotoContact?: () => void;
  onSaved?: () => void;
  portraitTags: PortraitTag[];
  onTagCreated?: (tag: PortraitTag) => void;
  onPortraitTagsChanged?: () => void;
};

type CustomerInfoProps = Omit<CustomerInfoContentProps, "conversation"> & {
  conversation?: ConversationDetail;
  open: boolean;
  onClose: () => void;
};

export function CustomerInfo({ conversation, open, onClose, ...contentProps }: CustomerInfoProps) {
  return (
    <Drawer title="客户详情" size={520} open={open} onClose={onClose} destroyOnHidden>
      {conversation ? <CustomerInfoContent {...contentProps} conversation={conversation} /> : null}
    </Drawer>
  );
}

function CustomerInfoContent({ conversation, onGotoContact, onSaved, portraitTags, onTagCreated, onPortraitTagsChanged }: CustomerInfoContentProps) {
  const { message } = App.useApp();
  const { snapshot, blocked } = useAccount();
  const { customer } = conversation;

  async function copyIdentity(text: string | undefined) {
    if (!text?.trim()) return;
    try {
      await navigator.clipboard.writeText(text);
      message.success("已复制");
    } catch {
      message.error("复制失败，请手动复制");
    }
  }

  return (
    <Space orientation="vertical" className="w-full" size="middle">
      <CustomerQualityDetails behavior={customer.d90} />
      <PortraitEditor
        conversation={conversation}
        tags={portraitTags}
        onTagCreated={onTagCreated}
        onSaved={onSaved}
        onTagsChanged={onPortraitTagsChanged}
      />
      <Card title="身份标识" extra={onGotoContact ? <Button type="primary" size="small" icon={<ExportOutlined />} disabled={blocked || !snapshot?.capabilities.operate_client} onClick={onGotoContact}>在被控客户端中跳转</Button> : null}>
        <div className="flex min-w-0 flex-col gap-2">
          {[
            { label: "Ali ID", text: customer.aliId },
            { label: "会员 ID", text: customer.memberId },
            { label: "登录 ID", text: customer.loginId },
            { label: "加密 ID", text: customer.encryptAccountId },
          ].map(({ label, text }) => (
            <Tooltip key={label} title={text?.trim() ? "单击以复制" : undefined} trigger={["hover", "focus"]}>
              <button
                type="button"
                aria-label={`复制${label}`}
                className="flex w-full min-w-0 items-center gap-3 rounded text-left text-sm outline-offset-2 focus-visible:outline-blue-500 enabled:cursor-pointer"
                disabled={!text?.trim()}
                onClick={() => void copyIdentity(text)}
              >
                <span className="w-20 shrink-0 whitespace-nowrap text-slate-500">{label}</span>
                <span className="min-w-0 flex-1 truncate">{formatEmpty(text)}</span>
              </button>
            </Tooltip>
          ))}
        </div>
      </Card>
      <Card title="客户信息">
        <Descriptions size="small" column={1} items={[
          { key: "name", label: "姓名", children: formatEmpty(customer.firstName || customer.lastName ? `${customer.firstName ?? ""} ${customer.lastName ?? ""}`.trim() : customer.name) },
          { key: "company", label: "公司", children: formatEmpty(customer.company) },
          { key: "country", label: "国家", children: formatEmpty(customer.country) },
          { key: "register", label: "注册时间", children: formatEmpty(customer.registerDate) },
          { key: "email", label: "邮箱", children: formatEmpty(customer.email) },
          { key: "mobile", label: "手机", children: formatEmpty(customer.mobile) },
          { key: "phone", label: "电话", children: formatEmpty(customer.phone) },
          { key: "availability", label: "在线时间", children: formatEmpty(customer.availability) },
        ]} />
      </Card>
      <Card title="标签与可用性">
        <Descriptions size="small" column={1} items={[
          { key: "quality", label: "质量等级", children: formatEmpty(customer.qualityTag) },
          { key: "growth", label: "成长等级", children: formatEmpty(customer.growthLevel) },
          { key: "industries", label: "偏好行业", children: customer.industries?.length ? customer.industries.join("、") : "—" },
          { key: "joining", label: "加入年限", children: formatEmpty(customer.joiningYears) },
          { key: "potential", label: "潜力分", children: formatEmpty(customer.potentialScore) },
          { key: "recent", label: "近期联系", children: customer.recentContact === undefined ? "—" : customer.recentContact ? "是" : "否" },
          { key: "emailv", label: "邮箱验证", children: customer.emailValidated === undefined ? "—" : customer.emailValidated ? "已验证" : "未验证" },
        ]} />
      </Card>
    </Space>
  );
}
