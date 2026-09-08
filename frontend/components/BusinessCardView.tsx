import { Card, Descriptions, Space, Tag, Typography } from "antd";
import { getCardStatusLabel, getCardTypeLabel } from "@/domain/cards/cardModel";
import type { BusinessCard } from "@/types/cards";

export function BusinessCardView({ card, compact = false, onClick }: { card: BusinessCard; compact?: boolean; onClick?: () => void }) {
  return (
    <Card
      hoverable={Boolean(onClick)}
      onClick={onClick}
      className="h-full"
      styles={{ body: { padding: compact ? 14 : 20 } }}
    >
      <div className="mb-4 h-2 rounded-full" style={{ background: card.coverTone }} />
      <Space orientation="vertical" size={compact ? 6 : 10} className="w-full">
        <Space wrap>
          <Tag color="blue">{getCardTypeLabel(card.type)}</Tag>
          {card.status ? <Tag color={card.status === "published" ? "green" : card.status === "reviewing" ? "purple" : "default"}>{getCardStatusLabel(card.status)}</Tag> : null}
        </Space>
        <Typography.Title level={compact ? 5 : 4} className="!mb-0">
          {card.title}
        </Typography.Title>
        <Typography.Paragraph ellipsis={compact ? { rows: 2 } : false} className="!mb-0">
          {card.summary}
        </Typography.Paragraph>
        <Space wrap>
          {card.tags.map((tag) => (
            <Tag key={tag}>{tag}</Tag>
          ))}
        </Space>
        {!compact && (
          <Descriptions size="small" column={1} items={card.details.map((item) => ({ key: item.label, label: item.label, children: item.value }))} />
        )}
      </Space>
    </Card>
  );
}
