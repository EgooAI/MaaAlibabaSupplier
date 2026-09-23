import { Button, Card, Descriptions, Space, Tag, Typography } from "antd";
import { StatusTag } from "@/components/StatusTag";
import type { BusinessCard } from "@/types/cards";

export function BusinessCardView({ card, compact = false, onClick }: { card: BusinessCard; compact?: boolean; onClick?: () => void }) {
  const tags = card.tags ?? [];
  const details = card.details ?? [];
  return (
    <Card
      hoverable={Boolean(onClick)}
      onClick={onClick}
      className="h-full"
      styles={{ body: { padding: compact ? 14 : 20 } }}
    >
      <div className="mb-4 h-2 rounded-full" style={{ background: card.coverTone ?? "#e6f4ff" }} />
      <Space orientation="vertical" size={compact ? 6 : 10} className="w-full">
        {card.status ? <StatusTag status={card.status} /> : null}
        <Typography.Title level={compact ? 5 : 4} className="!mb-0">
          {card.title || "未命名卡片"}
        </Typography.Title>
        <Typography.Paragraph ellipsis={compact ? { rows: 2 } : false} className="!mb-0">
          {card.summary || "暂无摘要"}
        </Typography.Paragraph>
        {card.link ? (
          <Button
            type="link"
            size="small"
            className="!px-0"
            href={card.link.href}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(event) => event.stopPropagation()}
          >
            {card.link.label}
          </Button>
        ) : null}
        <Space wrap>
          {tags.map((tag) => (
            <Tag key={tag}>{tag}</Tag>
          ))}
        </Space>
        {!compact && details.length ? (
          <Descriptions size="small" column={1} items={details.map((item) => ({ key: item.label, label: item.label, children: item.value }))} />
        ) : null}
      </Space>
    </Card>
  );
}
